# 批次事件、計時與日誌規則

機翻、Mod DB 譯文修復與智慧校對共用 `translation_tool/core/batch_events.py`（事件 schema、formatter、等級）、
`translation_tool/core/batch_timing.py`（計時欄位）與 `translation_tool/core/common_batch_executor.py`（批次生命週期與計時累計）。

## 事件接收與日誌顯示

- 事件文字只由 `format_batch_event` 產生；畫面／任務日誌用 `format_batch_event_for_log(event)` 取得 `(等級, 文字)`。
- 等級由 `batch_event_level` 決定：`attempt_failure`、`retry_wait`、`network_failure`、`network_retry_wait`、
  `provider_attempt_failure`、`validation_repair_start`、`validation_repair_failure` 為 warning；
  `validation_repair_complete`（有修復後仍無效的筆數）與 `batch_complete`（`invalid_count > 0`）為 warning；其餘為 info。
- formatter 不認得的事件種類（例如 `cancelled`、尚未寫 formatter 的新 kind）回傳 `None`，**不顯示**，
  避免產生只有 kind 名稱的通用行；取消與失敗結果由各流程自己的狀態訊息呈現。
- Mod DB 機翻（`moddb_translate_service`）與譯文修復（`moddb_retranslate_service`）都走同一組規則。

## 計時欄位的定義

| 欄位 | 定義 |
|---|---|
| `api_ms` | 一次 provider 請求的總時間**扣掉**期間的等待。成功由 `attempt_success` 累加；請求失敗或取消時，已花的 API 時間也會累加。 |
| `quota_wait_ms`、`retry_backoff_ms`、`network_retry_wait_ms` | 各自的等待量，由 `rate_limit_wait`、`retry_wait`、`network_retry_wait` 事件的 `wait_ms` 累加。 |
| `BatchMetrics.total_wait_ms` | 以上三者之和；所有「請求總時間 - 等待」的換算都使用它，不在呼叫端重寫加總公式。 |
| `run_record_ms` | 建立智慧校對 run 紀錄（`create_smart_review_run`）的耗時，只記在這個欄位。 |
| `history_write_ms` | 批次結果寫入歷史（JSON）的耗時，不含建立 run 紀錄。 |
| `elapsed_ms` | 批次（或事件所屬階段）自己經過的時間，**不是**等待量。 |

- 等待事件只讀 `wait_ms`（或舊欄位 `wait_seconds`）；沒有等待欄位時等待量是 0，不會改用批次的 `elapsed_ms`。
- `timing` 事件的 `field_name` 必須是 `TIMING_FIELDS` 之一；未知欄位只記一則警告並略過，不會丟例外，翻譯不受影響。
- `BatchHandle.timed_request()` 是量測單次 provider 請求的唯一入口，回傳 `RequestTiming(request_ms, wait_ms, api_ms)`。

## 呼叫翻譯入口與 telemetry

`invoke_translation_batch`（`translation_tool/core/lm_translator_shared_loop.py`）是所有流程呼叫 `translate_batch_smart`
的共用入口：依簽名傳入 `total`（keyword-only 的 `total` 以關鍵字傳入，其餘位置傳入）與 `event_callback`。
入口不接受 `event_callback` 時（舊測試替身）會退回只傳 batch／total，並記一則警告，說明這批的 provider 等待事件不會被記錄。

## 事件欄位命名（canonical 與舊欄位）

core 的 producer（`lm_translator_main`、`lm_api_client`、智慧校對的請求與修復）只發出 canonical 欄位：

| 事件 | canonical 欄位 |
|---|---|
| `retry_wait`、`rate_limit_wait`、`network_retry_wait` | `wait_ms`（計畫等待）、`retry_after_ms`（供應商建議，可為 `None`） |
| `retry_resume`、`rate_limit_resume`、`network_retry_resume` | `wait_ms` 與 `waited_ms`（**實際等待**）；`elapsed_ms` 保留給批次經過時間，兩者意義獨立 |
| 失敗與重試診斷 | `overload`、`rate_limit`、`timeout`、`connection`（定義見 `docs/developer-guide/provider-errors.md`） |

- `CommonBatchExecutor`／`BatchHandle.forward_provider_event` 直接接受 canonical 事件，不需要舊欄位。
- 舊名稱（`wait_seconds`、`elapsed_seconds`、`retry_after_seconds`、`provider_overload`、`quota_or_rate_limit` 等）
  只在**輸出邊界**由 `to_legacy_event` 補上，且只補缺值（不覆蓋既有欄位）；初始事件 dict 不可預放舊欄位別名。
- 讀取舊歷史資料或舊事件時一律先經 `canonicalize_event`；智慧校對持久化的 metrics 仍帶舊的 `*_seconds`
  欄位，供 `tools/smart_review_strategy_probe.py` 與既有歷史讀者使用。
- 契約測試 `tests/test_batch_event_producer_contract.py` 實際執行 producer，斷言事件鍵與舊欄位集合不相交。
