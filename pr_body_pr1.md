## 變更摘要

### PR1：Logging Core Foundation

建立新的 logging SSOT，提供結構化日誌系統的底層基礎設施。

### 新增模組 `app/logging/`

| 檔案 | 職責 |
|------|------|
| `log_entry.py` | LogEntry 不可變資料類（seq/level/text/source/ts）|
| `task_session.py` | 新版 TaskSession，logs 改為 `deque[LogEntry]` |
| `log_presenter.py` | 兩種渲染模式：append（seq追蹤）/ tail（最後N筆）|
| `log_config.py` | ui_logging config 讀取 + normalize + defaults |
| `log_colors.py` | 等級 → 顏色對應收斂 |
| `__init__.py` | 統一出口 |

### 破壞性變更（已被相容層覆蓋）
- `snapshot()["logs"]` 新回傳 `list[LogEntry]`；但 `snapshot()["log_texts"]` 同時提供 `list[str]`，舊 caller 不會被破壞

### snapshot() 契約（兩 key 並存）

| Key | 型別 | 用途 |
|-----|------|------|
| `snapshot()["logs"]` | `list[LogEntry]` | **新 caller**（PR2/PR3 presenter 使用）|
| `snapshot()["log_texts"]` | `list[str]` | **backward compat**（舊 caller 可正常運行）|

### 相容層
- `app/task_session.py` 改為 re-export shim：`from app.logging.task_session import TaskSession`
- `add_log("text")` 不帶 level/source 時仍可運作（預設 info/ui）

### 測試
- `tests/test_logging_core.py` — 9 tests
- `tests/test_logging_presenter.py` — 6 tests
- `tests/test_logging_config.py` — 4 tests
- **20/20 PASS**

---

## 驗證清單（PR1 專用）

- [x] `python -m py_compile` 全模組通過
- [x] `uv run pytest -q tests/test_logging_core.py tests/test_logging_presenter.py tests/test_logging_config.py` — **20/20 PASS**
- [x] import 可用：`from app.logging.task_session import TaskSession`
- [x] backward compat：`add_log("text")` 不帶 level/source 仍可運作
- [x] LogPresenter.append mode：`_last_seq=-1` 確保首次 sync 全量渲染（含 seq=0）
- [x] LogPresenter.tail mode：正確只取最後 N 筆
- [x] config fallback：缺少 ui_logging 時以 defaults 填補
- [x] **snapshot 過渡相容**：`snapshot()["log_texts"]` 回傳 `list[str]`，舊 caller 仍可正常運行
- [x] **測試註解已修正**：test_append_mode_renders_all_first_time 註解已與實作一致
