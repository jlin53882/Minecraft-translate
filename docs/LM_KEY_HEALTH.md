# API Key 健康狀態（issue #113）

## 為什麼

某一把 key 已確定當天 RPD 耗盡後，**之後每一個批次**輪到它時仍會再請求一次、收到 429 才改用下一把。
每個批次都是新的 `ApiKeyCycle`（共用迴圈與目錄翻譯都是「每批呼叫一次 `translate_batch_smart`」），
成功時又會 `reset()`，所以失敗紀錄一直被清掉。常用模型的 RPD 只有 500，這些白打的請求很可惜。

## 行為

| 項目 | 說明 |
|---|---|
| 記什麼 | 429 的每日配額用盡（`PERDAY` / `DAILY`）與 403 無權限。**不記** 429 RPM、503 overload、未知配額的 429 |
| 記多久 | 固定冷卻 `lm_translator.key_failure_cooldown_sec`（預設 3600 秒）。到期後下一次領取會再給它一次機會；成功就清除紀錄，再次失敗就重新冷卻。`0` = 不記憶（回到每批都重試的舊行為） |
| 識別 | 以 key 的 SHA-256 前 16 碼識別，不是 index。設定檔增減 / 調換 key 後狀態仍對得上；狀態與日誌只會出現遮罩後的字串（前 4 後 4） |
| 領取 | `ApiKeyCycle.claim()` 跳過「本輪已失敗」與「冷卻中」的 key；並發分散（ATK-009）不變 |
| 全部冷卻 | 每個 cycle 最多**試探一次**冷卻最快到期的那把。失敗 → `ALL_KEYS_EXHAUSTED`；成功 → 恢復 |
| 成功 | `ApiKeyCycle.record_success()`：清除這把 key 的紀錄並開始新的 cycle（`reset()` 本身不會清除共用狀態） |

## 給 UI 使用

```python
from translation_tool.core.lm_config_rules import get_key_health_snapshot

for h in get_key_health_snapshot():   # 依設定檔順序
    h.index, h.masked, h.status, h.reason, h.seconds_remaining, h.failures
```

`status`：`ok`（沒有失敗紀錄）、`cooling`（冷卻中，不會被領取）、`probing`（冷卻已到期、尚未確認恢復）。
`reason`：`rpd` 或 `forbidden`。快照同時會清掉已不在設定檔內的 key 的紀錄。

## 與既有契約的關係

- 維持 #112 的 contract：耗盡 = 「所有 key 都在本輪實際嘗試且失敗」，另外加上「冷卻中的 key 在沒有健康 key 時仍會被試探一次」。
- #112 的測試 `test_success_resets_failed_keys_for_the_next_batch_of_the_same_call` 鎖定的是「失敗紀錄在 cycle 成功後清空、key0 重新有資格被領取」。
  這正是本 issue 要改變的行為，所以改成兩個測試：記憶關閉時維持舊行為；預設（開啟）時 key0 只被請求一次。

## 已知限制

- 狀態只存在程序記憶體裡，重啟後從頭開始（冷卻最長一小時，影響有限）。
- 冷卻是固定秒數，不是 Gemini 實際的 RPD 重置時間（太平洋時間午夜，且可能依方案不同）。刻意不依賴時區資料庫。
- 設定頁尚未提供 `key_failure_cooldown_sec` 欄位，目前需直接編輯 `config.json`。

## 相關檔案

- `translation_tool/core/lm_key_health.py`：registry 與快照
- `translation_tool/core/lm_config_rules.py`：`ApiKeyCycle`、`get_key_health_snapshot`
- 測試：`tests/test_key_health.py`、`tests/test_key_health_integration.py`
