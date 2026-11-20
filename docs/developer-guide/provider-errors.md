# Provider 錯誤分類與 Retry-After

機翻（LM）、智慧校對與其修復請求共用同一份錯誤診斷定義，診斷旗標不會因為流程不同而互相矛盾。
重試、換 key、換模型、縮批等**決策**仍由各流程自己的狀態機負責，不由診斷旗標決定。

## 診斷旗標（`classify_provider_error`）

定義在 `translation_tool/core/common_batch_executor.py`，輸入例外與 HTTP 狀態，輸出：

| 旗標 | 意義 |
|---|---|
| `overload` | 供應商**服務過載**：HTTP 503（`service_unavailable`），或錯誤碼含 `overload`／`high_demand`／`too_many`。**不是**所有暫時性 5xx。 |
| `rate_limit` | 速率或配額受限：HTTP 429，或錯誤碼含 `quota`／`rate`／`limit`／`usage`。 |
| `timeout` | `requests.Timeout`、HTTP 408、HTTP 504。 |
| `connection` | 連線層錯誤。 |
| `retryable` | 一般而言值得重試：408／429／500／502／503／504、timeout、連線錯誤。只是診斷資訊。 |
| `retry_after_ms` | 例外的 `retry_after` 屬性優先，其次讀 `requests.HTTPError` 回應的 `Retry-After` header（見下節）。 |

### 決策表

| 狀態／錯誤 | `error_class` | overload | rate_limit | timeout | retryable |
|---|---|---|---|---|---|
| 408 | `deadline_exceeded` | 否 | 否 | 是 | 是 |
| 429 | `rate_limit` | 否 | 是 | 否 | 是 |
| 500、502 | `provider_transient` | 否 | 否 | 否 | 是 |
| 503 | `service_unavailable` | 是 | 否 | 否 | 是 |
| 504 | `provider_timeout` | 否 | 否 | 是 | 是 |
| 400／422／403／404 | 各自的 client 類別 | 否 | 否 | 否 | 否 |
| code `rate_limit_exceeded` | 依狀態 | 否 | 是 | 否 | 依狀態 |
| code `server_overloaded` | 依狀態 | 是 | 否 | 否 | 依狀態 |
| `requests.Timeout` | `timeout` | 否 | 否 | 是 | 是 |
| `requests.ConnectionError` | `connection_error` | 否 | 否 | 否 | 是 |

### 決策紀錄

- `overload` 維持 503 家族：500／502／504 不計入 overload，因為 overload 的 KPI 代表「供應商明確表示忙碌」，
  與一般的暫時性伺服器錯誤成因與對策不同。504 同時是 `timeout`。
- 智慧校對與 ChatGPT 的 `retry_wait`、`attempt_failure` 事件都使用這份分類，不再各自用 HTTP 狀態集合推算旗標。
- LM 狀態機另有自己的動作分類（`_classify_batch_error`，例如 `rate_limited`、`server_error`），
  那是「下一步做什麼」，與診斷旗標分開。

## Retry-After 解析

純解析在 `translation_tool/core/retry_after.py`，Gemini LM、智慧校對與 ChatGPT 共用；它不含任何等待政策。

- 來源優先序：HTTP header `Retry-After` > Google `RetryInfo.retryDelay`；header 無法解析時退回 body。
- header 接受非負秒數（可含小數）與 HTTP-date（過去的日期代表可立即重試，回傳 0）；`retryDelay` 接受 `34.9s` 或純數字。
- 小數秒不截斷；負數、`NaN`、`Infinity`、格式錯誤或非字串／數字型別一律視為無效（`None`）。
- 上限與中止政策由各流程自己決定：Gemini 503 超過 `GEMINI_503_MAX_RETRY_AFTER_SEC` 回報 PARTIAL、
  ChatGPT 超過 `CHATGPT_MAX_RETRY_AFTER_SEC` 中止、RPM 429 依解析值等待（沒有值時等 `RATE_LIMIT_DEFAULT_WAIT_SEC`）。
- Gemini 503 的等待量 `max(固定退避表, Retry-After)` 由 `gemini_503_wait_sec` 計算，LM 與智慧校對共用。

### 429 body 無法解析

`_parse_rate_limit_error` 回傳配額種類 `rpd`／`rpm`／`quota`；只有 `QuotaFailure` 或訊息明確表示配額耗盡時才回傳 `quota`。
合法 JSON 但沒有 RPD／RPM／明確配額耗盡訊號，以及 body 不是 Google error JSON 時，都回傳 `unparsed`。
`unparsed` 仍是 HTTP 429，只能保守當成速率限制：先等待（Retry-After 或 `RATE_LIMIT_DEFAULT_WAIT_SEC`）並重試，
連續 `UNPARSED_RATE_LIMIT_MAX_RETRIES` 次仍受限才對該 key 輪替；不會因未知的 429 body 把 key 立即標記為配額耗盡。
真正的 RPD（`PERDAY`／`DAILY`）仍記在模型上並換下一個模型。

## Gemini key 格式

`validate_api_keys` 與 `validate_api_keys_from_ui` 共用 `lm_config_rules._gemini_key_format_error`
（前綴 `AIza`／`AQ.`、至少 35 字元、僅英數字與 `-`、`_`）。`redaction` 的遮蔽規則刻意更寬，用來避免漏遮，
不可拿來判斷金鑰是否合法。

## ChatGPT 分享額度備援

`openai_codex_client.call_chatgpt_responses` 先用本程式自己登入的 ChatGPT OAuth 打 `https://api.openai.com/v1/responses`。
這條路屬於第三方「訂閱分享」額度；錯誤碼為 `subscription_sharing_usage_limit_exceeded`（`_SHARING_LIMIT_CODES`，
HTTP 200，錯誤在串流事件裡）時，改用本機 Codex CLI 的登入重試一次：

- 憑證來自 `codex_cli_auth.load_codex_cli_credentials`，讀 `$CODEX_HOME/auth.json`（預設 `~/.codex/auth.json`）。
  只讀不更新：refresh token 為單次輪替，由本程式更新會讓 Codex CLI 自己的登入失效，所以 access token 過期時只提示使用者先執行 `codex`。
- 備援請求打 `https://chatgpt.com/backend-api/codex/responses`，帶 `ChatGPT-Account-ID`，`originator` 與 `User-Agent` 為 `minecraft-translate`，不偽裝成 Codex。
  這條路不送 `max_output_tokens` 與 `service_tier`；本地輸出保護（`output_guard_tokens`）照常生效。
- 帳號必須相同：Codex CLI 登入的 email 要與本程式所選的 ChatGPT 帳號（任務釘選的 `profile_id`，未指定則為目前啟用帳號）相同；
  任一邊沒有 email 或不相同就不送出備援請求，避免把翻譯內容與額度用到別的帳號。
- 其他錯誤碼（額度耗盡、401 等）不切換，維持 `_handle_chatgpt_api_error` 的既有處理。
- 備援不可用（無登入檔、不是 ChatGPT 登入、token 過期）或備援也失敗時，拋出原本的分享額度錯誤，由 `_chatgpt_api_error_message` 轉成使用者訊息。
- 備援成功後，同一個任務（與帳號）的後續請求直接走 Codex 登入，不再每批先被 OAuth 路線拒絕一次；新任務不繼承，會重新先試 OAuth 路線。
  任務識別取自 `ui_mirror.current_task()`，沒有任務 context 時每次請求都重新判斷；只保留最近 `_STICKY_LIMIT` 個任務。
  沿用期間若 Codex 登入失效（`CodexCLIAuthError` 或 401），清除沿用狀態並改回一般登入重新判斷；逾時、5xx 等其他錯誤照原樣往上拋，由批次層既有的重試處理。
- 日誌：切換時 `log_warning`「嘗試改用本機 Codex CLI 登入重試」，備援不可用時 `log_warning`「且無法改用 Codex 登入」，
  備援成功時 `log_info`「已改用本機 Codex CLI 登入完成請求」（只在切換當下記一次，沿用期間不逐批記錄）。
