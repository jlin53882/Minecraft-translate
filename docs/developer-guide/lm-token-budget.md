# 翻譯批次 token 預算

## 設計目的

只用「項目數」（`initial_batch_size_*`）決定批次大小時，長短內容被當成一樣大，
要撞到輸出截斷才縮小重送，被截斷的那次呼叫會白白消耗 RPD 與整批輸入 TPM。
因此切批時同時估算 token，在送出前就把批次控制在預算內。

## 行為

| 階段 | 內容 |
|---|---|
| 觀測 | `call_gemini_requests(..., meta_out=dict)` 回報 `finishReason`、`promptTokenCount`、`candidatesTokenCount`、`thoughtsTokenCount`；回傳值仍是字串 |
| 截斷診斷 | 截斷時 log 一行 `[截斷診斷]`：原因（`MAX_TOKENS` 或 `STOP` 但 JSON 壞）、送出筆數、估算與實際 token |
| 上限 | Gemini 送 `generationConfig.maxOutputTokens`、OpenAI Responses 送 `max_output_tokens`（預設 65536），避免模型重複輸出燒光額度 |
| 切批 | `lm_batch_budget.select_batch_size`：由前往後累加，項目數上限 / 輸出 token 預算 / 輸入 token 預算，先到者為準，永遠只取輸入的**前綴** |
| 學習 | 依 profile（lang / ftb / kubejs / md / patch）保存。`MAX_TOKENS` 截斷 → 輸出預算減半；連續成功 → 緩慢回升到設定值 |
| 校正 | 成功回應後，以實際輸出（`candidates` + 思考 token）用 EMA 校正「輸出 / 輸入」係數 |

三個切批位置共用同一份估算與學到的預算：`lm_translator_shared_loop`（外層）、
`lm_translator.translate_directory_generator`、`lm_translator_main.translate_batch_smart`（內層 state machine）。

### 估算方式（純本地，不呼叫 `countTokens`）

- ASCII 字元約 0.25 token、CJK 約 1.2 token、其他非 ASCII 約 0.5 token
- 每個項目加約 12 token 的 JSON 包裝
- 預期輸出 ≈ 原文 token × `output_token_factor`（預設 1.5）+ 包裝
- 輸入另外計入 system prompt、批次相關的官方／已審核術語提示，以及同一模組 session 保留的對話歷史；這些都只影響輸入預算，不會混入翻譯輸出 KPI。

### 什麼時候縮小學到的預算

| 情況 | 是否縮小 | 原因 |
|---|---|---|
| 回應被截斷，`finishReason=MAX_TOKENS` 或沒有 meta | 是 | token 上限造成 |
| 回應被截斷，`finishReason=STOP` | 否 | 模型正常結束卻產出壞 JSON，不是大小問題，交給既有的項目數縮小 |
| 漏翻，`finishReason=MAX_TOKENS` | 是 | |
| 漏翻，其他 | 否 | 多半是模型略過項目 |

### 終止保證與契約

- 每次截斷後重試的批次都嚴格變小：先縮小學到的預算；若縮小後批次沒變，才走既有的項目數縮小（有下限，縮到極限就放棄該批並回填原文、標記 `_untranslated`）。
- 回傳維持「輸入前綴、依位置對應」的契約（與翻譯結果的 completion contract 相同），不會錯位，不重翻、不漏翻。

## 設定（`lm_translator` 區段）

| 鍵 | 預設 | 說明 |
|---|---|---|
| `token_budget_enabled` | `true` | `false` = 回到舊行為，只看項目數 |
| `max_output_token_budget` | `65536` | 單批預期輸出 token 上限（保守值） |
| `max_input_token_budget` | `256000` | 單批輸入 token 上限，避免單次呼叫吃掉太多 TPM；ChatGPT 實際使用值還會取全域值與模型覆寫上限的較小值 |
| `max_output_tokens` | `65536` | 送給 Gemini／OpenAI Responses 的輸出上限；`0` = 不送，ChatGPT 使用 `1～15` 會被拒絕 |
| `output_token_factor` | `1.5` | 預期輸出 / 輸入係數，範圍 0.3~6.0，會依實際用量校正 |
| `budget_min_scale` | `0.0625` | 撞牆後輸出預算最多縮到設定值的幾倍 |
| `budget_recover_after` | `3` | 連續成功幾批後開始回升 |
| `budget_recover_factor` | `1.5` | 每次回升的倍率（> 1，最大 4） |
| `retry_same_as_source` | `true` | 同一批中譯文與原文相同的項目，在持久化前只重新確認一次 |

缺少的鍵由預設值補上；型別或範圍錯誤在載入設定時就會報錯（`ConfigValidationError`）。
`max_output_token_budget` 大於 `max_output_tokens` 只會警告。

### 注意：`max_output_tokens` 不可超過模型上限

65536 等於 Gemini 2.5 Flash 與 3.1 Flash Lite 的上限 65,536，但**高於** Gemini 2.0 Flash 的 8,192。
啟用會超限的模型時 API 會回 400；偵測到 `maxOutputTokens` 或 `max_output_tokens` 相關的 400 時會直接拋出明確錯誤
（請調低 `max_output_tokens`，或設為 `0`），不會一路縮小批次後把每一批都放棄。

## 已知限制

- 預算是程序內的記憶，重啟後從設定值重新開始學習。
- 學到的預算依 profile 保存，不分模型；同一 profile 換模型時不會重置。
- Token budget 欄位可在設定頁「批次與限制」編輯；same-as-source 確認開關位於「翻譯行為設定」的「基本設定」卡。

## 譯文與原文相同時重新確認

`lm_translator.retry_same_as_source` 預設為 `true`。每個成功批次中，完全等於原文且含有 alphabetic character 的項目會單獨組成候選 payload；不使用 whitelist、字串長度或格式 heuristic。第二次沿用同一 profile prompt、model、API key、temperature、structured-output contract 與同一模組的 client-side session history，並附加一次確認說明。相同批次內的「相同譯文」與「格式不一致」候選會合併為一次品質 retry；`lang`、`patchouli`、`ftbquests`、`kubejs` 與 `md` 都走共用 batch contract。

批次切分估算會在功能開啟時預留 retry prompt 的輸入 token；實際 retry 估算會包含追加後的 prompt、同批初始 user/model conversation history，以及帶有 `retry_reasons`／`format_issues` 的完整候選 payload。若超過 `max_input_token_budget` 就保留第一次結果，不發出 retry。結果依原 ID 與位置合併回第一批，processed/checkpoint 數量不變。此步驟在 shared loop 寫 Cache、Mod DB 建立 write-back 前完成。第二次仍相同會作為一般結果接受，並可正常寫入 Cache 與 Mod DB；技術錯誤或 structured response 無效時保留第一次結果。使用者取消仍依既有 cancellation contract 中止。

### 官方語系與模組術語

- `assets/official_locales/minecraft/` 固定保存 `en_us.json`、`zh_cn.json`、`zh_tw.json` 與完整性 `manifest.json`，不依 Minecraft 版本分資料夾。
- 語系合併對所有種類共用 `key + en_us + zh_cn` 的官方精確命中；沒有命中時才回到既有 OpenCC 與 `replace_rules.json`。既有輸出資料夾人工譯文與資料庫有效譯文仍優先保留。
- LLM 每批會把相關官方 Minecraft 術語加入中文提示詞。Mod DB 已審核譯文只在 `kind=lang`、同一模組範圍內加入，不會污染 Patchouli、指南或其他種類。
- Mod DB 批次以 `(mod_id, kind)` 分界；同一模組的不同批次共用一個 client-side session，模型收到的是同一段歷史，不同模組彼此隔離。批次內容與動態提示詞的 token 會計入輸入預算，但翻譯輸出 KPI 只計算原始 key/value 內容。

## 相關檔案

- `translation_tool/core/lm_batch_budget.py`：估算、切批、學習狀態
- `translation_tool/core/lm_api_client.py`：`maxOutputTokens`、`meta_out`
- `translation_tool/core/lm_translator_main.py`：截斷診斷、學習與重試
- 測試：`tests/test_lm_batch_budget.py`、`tests/test_lm_api_client_meta.py`、`tests/test_lm_token_budget_integration.py`、`tests/test_config_token_budget.py`

### Per-model 上限（PR-5）

`lm_translator.models.<model>.max_output_tokens` 是 optional override。未設定或為 `null` 時回退到全域 `lm_translator.max_output_tokens`；設為 `0` 時不送輸出上限。使用 ChatGPT Responses provider 時，非零值必須至少為 16；Gemini 仍依模型限制處理。舊版只含 `enabled` 的 models schema 不需要 migration。
- 根目錄 `conftest.py`：測試間重置學到的預算
