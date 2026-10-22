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

`_parse_rate_limit_error` 回傳配額種類 `rpd`／`rpm`／`quota`，body 不是 Google error JSON 時回傳 `unparsed`。
`unparsed` 仍是 HTTP 429，只能保守當成速率限制：先等待（Retry-After 或 `RATE_LIMIT_DEFAULT_WAIT_SEC`）並重試，
連續 `UNPARSED_RATE_LIMIT_MAX_RETRIES` 次仍受限才對該 key 輪替；不會因此把 key 當成每日配額耗盡。
真正的 RPD（`PERDAY`／`DAILY`）仍記在模型上並換下一個模型。

## Gemini key 格式

`validate_api_keys` 與 `validate_api_keys_from_ui` 共用 `lm_config_rules._gemini_key_format_error`
（前綴 `AIza`／`AQ.`、至少 35 字元、僅英數字與 `-`、`_`）。`redaction` 的遮蔽規則刻意更寬，用來避免漏遮，
不可拿來判斷金鑰是否合法。
