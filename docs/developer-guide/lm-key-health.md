# API Key / 模型健康狀態

> **同專案模式**：本專案假設所有 API Key 都屬於同一個 Google 專案。Gemini 的 RPD 配額算在
> 「專案 × 模型」，所以**每日配額耗盡記在「模型」上**（`ModelQuotaRegistry`），不是記在 key 上；
> **403 無權限仍記在 key 上**（`KeyHealthRegistry`）。下面「為什麼」與「行為」兩節先描述 key 層級
> 的機制（現在只用於 403），模型層級的行為見「模型每日配額（RPD）」一節。

## 模型每日配額（RPD）

所有 provider 請求也會經過 `ModelQuotaRegistry.reserve_request_slot()`：以
`(provider, model)` 為鍵，在程序內共享 `sleep_seconds_between_batches` 的最小間隔。
正常批次、內部縮批與品質 retry 使用同一個 slot；品質 retry 仍只嘗試一次，且先取得
同一模型的 quota claim／probe lease。429／503 只更新必要的 key／模型健康訊號，不會呼叫
會等待、縮批或切換模型的完整錯誤狀態機，也不會因此增加品質 retry 次數。等待中的取消會
回收尚未送出的 slot；已提交的 slot 不會回收或重用。取消中間等待者時，後面的未提交 slot
會依序前移；品質 retry 收到 `Retry-After` 時，所有尚未送出的 slot 會依新的冷卻時間重排。
正式 provider 路線把 shared loop 的額外 sleep 設為 `0`，避免與 scheduler 雙重等待；自訂
或測試 translator 仍可使用 shared loop 的明確 sleep 參數。

| 項目 | 說明 |
|---|---|
| 輪替 | 模型 1 回 429 RPD → 標記該模型今日耗盡 → 換模型 2 → … → 所有啟用的模型都耗盡才回傳 `ALL_KEYS_EXHAUSTED`。**不會因為 RPD 換 key**（額度共用，換了也一樣 429） |
| 記多久 | 到下一個**太平洋時間午夜 00:00**（`America/Los_Angeles`）：夏令 = 台灣 15:00、冬令 = 台灣 16:00，夏令切換由時區資料庫處理（`next_quota_reset()`）。沒有時區資料庫時重置時間不確定（UTC-7 或 UTC-8 的午夜）：較早的時間只是「可能已恢復」，到較晚的時間之前仍由單一探測租約確認（`quota_reset_window()`），不會全面放行；探測成功即恢復 |
| 跳過 | `_attempt_batch` 略過今日耗盡的模型；全部耗盡且都還沒輪到探測時**不送任何請求**直接回傳耗盡，並在日誌顯示距離重置還有多久 |
| 探測 | 耗盡期間每隔 10 分鐘（`DEFAULT_PROBE_INTERVAL_SEC`）發出**一張探測租約**（`claim(model, owner)`，`owner` 是一次翻譯呼叫）。租約跨越整個探測流程：探測遇到 RPM／503 時，持有者可重入並正常重試，其他 worker 在租約期間一律被擋下。結果：成功 → 立刻恢復（例如升級方案）；RPD → 重新計時；**放棄該模型改用其他模型（503 → 下一個模型、maxOutputTokens 不支援等）→ `release()` 收回租約並重新計時**（只有同一模型的 RPM／503 重試才保留租約，否則後續批次會每批都探測一次）；其他結束方式 → 呼叫結束時 `release_owner` 收回；**心跳續租**：探測請求飛行期間 `hold_probe_lease()` 會持續續租（每 1/3 租約長度一次），請求結束就停止，所以請求只要還活著就不會被別的 worker 搶到第二個探測，不必推算它「最久會花多久」（`requests` 的 timeout 不是整段 wall-clock 上限）。持有者異常結束（心跳也停了）則租約逾時；保底長度 = `worst_case_request_sec()`（連線最多 3 次嘗試 × `rate_limit.timeout` 加指數退避）+ 60 秒|
| 無法送請求 | 沒有任何模型能送請求時，**不會**縮小 batch 或把原文回填成已翻譯：有模型被配額擋住／探測租約在別人手上 → `ALL_KEYS_EXHAUSTED`；整個模型池都是 404（設定錯誤）→ `FAILED` 並提示檢查模型名稱。被釘住（503 overload 重試）的模型不論因為什麼被放棄（RPD、404、maxOutputTokens 不支援、非 overload 的 503、空回應），都經由 `_abandon_model()` 這個單一入口：解除釘選並把整個模型池加進這一輪的候選；404 的模型在同一次呼叫內不再重打 |
| 恢復 | 午夜自動恢復，或**收到 HTTP 200**（含「HTTP 200 但回應不是有效 JSON、最外層型別不對、或缺少 candidates/content/parts/text」：HTTP 成功之後的任何解析問題，用戶端都拋出 `GeminiResponseFormatError`，一樣代表沒有被配額拒絕；配額狀態代表「額度是否恢復」，不是「整個翻譯是否成功」：回應是空的、被截斷或格式不符時，額度紀錄一樣會清除，內容問題由 batch 流程自己處理）。只有「在耗盡紀錄之後才開始」的請求成功才能清除紀錄（`mark_ok(started_at=)`），避免併發時「耗盡前送出、之後才完成」的請求洗掉較新的紀錄 |
| 重啟 | 狀態只存在程序記憶體，重啟後第一次請求會再被 429 一次後重新記錄 |
| 設定 | 不受 `key_failure_cooldown_sec` 影響（該設定現在只管 403） |

## 設計目的

每個批次都是新的 `ApiKeyCycle`（共用迴圈與目錄翻譯都是「每批呼叫一次 `translate_batch_smart`」），
成功時會 `reset()`。如果失敗紀錄只存在單一 cycle 內，已確定失效的 key **在之後每一個批次**都會被再請求一次。
健康狀態因此存在跨批次的 registry，讓失效的 key 在冷卻期間不再被請求，避免浪費每日配額。

## 行為

| 項目 | 說明 |
|---|---|
| 記什麼 | 403 無權限（key 層級）。**不記** 429 RPM、503 overload、未知配額的 429。（429 每日配額用盡改記在模型上，見上一節） |
| 記多久 | 固定冷卻 `lm_translator.key_failure_cooldown_sec`（預設 3600 秒）。到期後下一次領取會再給它一次機會；成功就清除紀錄，再次失敗就重新冷卻。`0` = 不記憶（回到每批都重試的舊行為） |
| 識別 | 以 key 的 SHA-256 前 16 碼識別，不是 index。設定檔增減 / 調換 key 後狀態仍對得上；狀態與日誌只會出現遮罩後的字串（前 4 後 4） |
| 領取 | `ApiKeyCycle.claim()` 跳過「本輪已失敗」與「冷卻中」的 key；並發分散（ATK-009）不變 |
| 全部冷卻 | 每個 cycle 最多**試探一次**冷卻最快到期的那把。失敗 → `ALL_KEYS_EXHAUSTED`；成功 → 恢復 |
| 成功 | `ApiKeyCycle.record_success()`：清除本輪未標記失敗的 key 紀錄並開始新的 cycle；若品質 retry 在同一輪回報 403，該 key 的冷卻優先保留（`reset()` 本身不會清除共用狀態） |

## 給 UI 使用

```python
from translation_tool.core.lm_config_rules import get_key_health_snapshot

for h in get_key_health_snapshot():   # 依設定檔順序
    h.index, h.masked, h.status, h.reason, h.seconds_remaining, h.failures
```

`status`：`ok`（沒有失敗紀錄）、`cooling`（冷卻中，不會被領取）、`probing`（冷卻已到期、尚未確認恢復）。
`reason`：翻譯流程現在只會出現 `forbidden`（`rpd` 保留為 registry 的通用能力）。快照同時會清掉已不在設定檔內的 key 的紀錄。

## 與既有契約的關係

- 耗盡的定義 = 「所有 key 都在本輪實際嘗試且失敗」，另外加上「冷卻中的 key 在沒有健康 key 時仍會被試探一次」。
- 「失敗紀錄在 cycle 成功後清空、key0 重新有資格被領取」只在失敗記憶關閉時成立，由 `test_success_resets_failed_keys_when_failure_memory_is_disabled` 鎖定；預設（開啟）時失敗的 key 不會被下一批再次請求（`test_forbidden_key_is_not_requested_again_by_the_next_batch`）。

## 已知限制

- 狀態只存在程序記憶體裡，重啟後從頭開始（冷卻最長一小時，影響有限）。
- key 層級（403）的冷卻是固定秒數；模型層級的 RPD 才對齊太平洋時間午夜。
- 假設所有 key 同專案；若日後要支援多專案，RPD 需改記在（專案 × 模型）。
- 設定頁尚未提供 `key_failure_cooldown_sec` 欄位，目前需直接編輯 `config.json`。

## 品質重試與共用品質准入

品質 retry 的請求失敗時，只更新必要的 provider／模型／key 健康訊號，不會再次進入一般
批次的錯誤狀態機。ChatGPT Responses 的 429 與 `Retry-After` 會寫入同一個 provider/model
的 scheduler；Gemini 則讀取回應標頭或 `RetryInfo`，沒有可解析的值時使用本批間隔作為保守
延後。品質 retry 的健康更新是 best effort：更新狀態本身失敗時仍保留第一次有效譯文，
且不增加第二次品質 retry。

AI 回應、Mod DB 命中、一般快取命中與 `.md` 舊 `patchouli` fallback 都共用 token／指南
結構品質准入。未通過者回到 AI 待翻譯清單，不會寫入快取或 Mod DB；Patchouli 元件遺失
等硬格式問題不會因為來源是快取或資料庫而被繞過。

## 相關檔案

- `translation_tool/core/lm_key_health.py`：registry 與快照
- `translation_tool/core/lm_config_rules.py`：`ApiKeyCycle`、`get_key_health_snapshot`
- 測試：`tests/test_key_health.py`、`tests/test_key_health_integration.py`
