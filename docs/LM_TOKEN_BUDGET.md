# 翻譯批次 token 預算（issue #108）

## 為什麼

批次大小原本只看「項目數」（`initial_batch_size_*`）。長短內容被當成一樣大，
撞到截斷才縮小重送，被截斷的那次呼叫費用白花（消耗 RPD 與整批輸入 TPM）。
學到的大小也沒有保留，每次呼叫 `translate_batch_smart` 都從頭開始。

## 行為

| 階段 | 內容 |
|---|---|
| 觀測 | `call_gemini_requests(..., meta_out=dict)` 回報 `finishReason`、`promptTokenCount`、`candidatesTokenCount`、`thoughtsTokenCount`；回傳值仍是字串 |
| 截斷診斷 | 截斷時 log 一行 `[截斷診斷]`：原因（`MAX_TOKENS` 或 `STOP` 但 JSON 壞）、送出筆數、估算與實際 token |
| 上限 | 一律送 `generationConfig.maxOutputTokens`（預設 32768），避免模型重複輸出燒光額度 |
| 切批 | `lm_batch_budget.select_batch_size`：由前往後累加，項目數上限 / 輸出 token 預算 / 輸入 token 預算，先到者為準，永遠只取輸入的**前綴** |
| 學習 | 依 profile（lang / ftb / kubejs / md / patch）保存。`MAX_TOKENS` 截斷 → 輸出預算減半；連續成功 → 緩慢回升到設定值 |
| 校正 | 成功回應後，以實際輸出（`candidates` + 思考 token）用 EMA 校正「輸出 / 輸入」係數 |

三個切批位置共用同一份估算與學到的預算：`lm_translator_shared_loop`（外層）、
`lm_translator.translate_directory_generator`、`lm_translator_main.translate_batch_smart`（內層 state machine）。

### 估算方式（純本地，不呼叫 `countTokens`）

- ASCII 字元約 0.25 token、CJK 約 1.2 token、其他非 ASCII 約 0.5 token
- 每個項目加約 12 token 的 JSON 包裝
- 預期輸出 ≈ 原文 token × `output_token_factor`（預設 1.5）+ 包裝
- 輸入另外計入 system prompt

### 什麼時候縮小學到的預算

| 情況 | 是否縮小 | 原因 |
|---|---|---|
| 回應被截斷，`finishReason=MAX_TOKENS` 或沒有 meta | 是 | token 上限造成 |
| 回應被截斷，`finishReason=STOP` | 否 | 模型正常結束卻產出壞 JSON，不是大小問題，交給既有的項目數縮小 |
| 漏翻，`finishReason=MAX_TOKENS` | 是 | |
| 漏翻，其他 | 否 | 多半是模型略過項目 |

### 終止保證與契約

- 每次截斷後重試的批次都嚴格變小：先縮小學到的預算；若縮小後批次沒變，才走既有的項目數縮小（有下限，縮到極限就放棄該批並回填原文、標記 `_untranslated`）。
- 回傳維持「輸入前綴、依位置對應」的契約（與 #107 的 completion contract 相同），不會錯位，不重翻、不漏翻。

## 設定（`lm_translator` 區段）

| 鍵 | 預設 | 說明 |
|---|---|---|
| `token_budget_enabled` | `true` | `false` = 回到舊行為，只看項目數 |
| `max_output_token_budget` | `24000` | 單批預期輸出 token 上限（保守值） |
| `max_input_token_budget` | `60000` | 單批輸入 token 上限，避免單次呼叫吃掉太多 TPM |
| `max_output_tokens` | `32768` | 送給 API 的 `maxOutputTokens`；`0` = 不送 |
| `output_token_factor` | `1.5` | 預期輸出 / 輸入係數，範圍 0.3~6.0，會依實際用量校正 |
| `budget_min_scale` | `0.0625` | 撞牆後輸出預算最多縮到設定值的幾倍 |
| `budget_recover_after` | `3` | 連續成功幾批後開始回升 |
| `budget_recover_factor` | `1.5` | 每次回升的倍率（> 1，最大 4） |

缺少的鍵由預設值補上；型別或範圍錯誤在載入設定時就會報錯（`ConfigValidationError`）。
`max_output_token_budget` 大於 `max_output_tokens` 只會警告。

### 注意：`max_output_tokens` 不可超過模型上限

32768 低於 Gemini 2.5 Flash 與 3.1 Flash Lite 的 65,536，但**高於** Gemini 2.0 Flash 的 8,192。
啟用會超限的模型時 API 會回 400；偵測到 `maxOutputTokens` 相關的 400 時會直接拋出明確錯誤
（請調低 `max_output_tokens`，或設為 `0`），不會一路縮小批次後把每一批都放棄。

## 已知限制

- 預算是程序內的記憶，重啟後從設定值重新開始學習。
- 學到的預算依 profile 保存，不分模型；同一 profile 換模型時不會重置。
- 設定頁（`config_view`）尚未提供這些欄位，目前需直接編輯 `config.json`。

## 相關檔案

- `translation_tool/core/lm_batch_budget.py`：估算、切批、學習狀態
- `translation_tool/core/lm_api_client.py`：`maxOutputTokens`、`meta_out`
- `translation_tool/core/lm_translator_main.py`：截斷診斷、學習與重試
- 測試：`tests/test_lm_batch_budget.py`、`tests/test_lm_api_client_meta.py`、`tests/test_lm_token_budget_integration.py`、`tests/test_config_token_budget.py`

### Per-model 上限（PR-5）

`lm_translator.models.<model>.max_output_tokens` 是 optional override。未設定或為 `null` 時回退到全域 `lm_translator.max_output_tokens`；設為 `0` 時不送 `generationConfig.maxOutputTokens`。舊版只含 `enabled` 的 models schema 不需要 migration。
- 根目錄 `conftest.py`：測試間重置學到的預算
