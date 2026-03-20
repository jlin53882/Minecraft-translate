## 變更摘要

### PR4：Full UI Logging Adoption — lm_view + Bundler/QC 決策記錄

`lm_view.py` 完成 LogPresenter tail mode 接入，修復一個潛在 bug。其餘三個候選檔案經過完整評估後，決定維持現況並說明原因。

---

## Phase 0 評估結果

### 評估方法
每個候選檔案回答三個問題：
1. 這個頁面的 log 是否來自背景長任務？
2. log 是否會持續累積（可能超過數十/數百行）？
3. 目前是否有 TaskSession？

### 決策表

| 檔案 | TaskSession | 長任務 | 持續累積 | **決策** | 原因 |
|------|------------|--------|---------|---------|------|
| `lm_view.py` | ✅ YES | ✅ YES | ✅ YES | **ADOPT** | 最適合接入的對象 |
| `bundler_view.py` | ❌ NO | ✅ YES | ✅ YES | **SKIP** | 無 TaskSession；worker thread 直接操作 UI；需較大重構 |
| `qc_base.py` | ❌ NO | ✅ YES | ✅ YES | **SKIP** | 同 bundler；worker thread pattern 需整體改寫 |
| `qc_view.py` | ❌ NO | ✅ YES | ✅ YES | **SKIP** | 使用 QCBase；繼承同樣架構問題 |

---

## 本次實作

### `lm_view.py` — ADOPT LogPresenter (tail mode)

**問題修復**：
- 舊程式碼 `snap["logs"]` 已經是 `list[LogEntry]`，但 poller 直接拿 `LogEntry` 當 `str` 用
  ```python
  # 舊（有 bug）
  for line in snap["logs"][-250:]:
      self.log_view.controls.append(ft.Text(line, ...))  # line 是 LogEntry！
  ```
- 改用 `LogPresenter.sync()` 後，`presenter._sync_tail()` 內部正確使用 `entry.text`

**變更點**：
- `app/views/lm_view.py`：
  - 新增 import：`from app.logging import LogPresenter, load_ui_logging_config`
  - `__init__` 新增 `self.log_presenter = LogPresenter(mode="tail", tail_lines=..., colorize=False, default_color=str(theme.GREY_100))`
  - `start_ui_timer()` poller loop 簡化為一行：`self.log_presenter.sync(self.log_view, snap["logs"])`

- `tests/test_lm_view_characterization.py`：
  - mock `_Session.snapshot()` 從 `{'logs': ['done']}` 改為 `{'logs': [LogEntry(...)]}`
  - 對齊新的 `list[LogEntry]` API

---

## 維持現況的原因

### `bundler_view.py`

**評估**：
- 目前無 TaskSession，worker thread (`bundling_worker`) 直接對 `log_view.controls` 做 `append` + `page.update()`
- 若要接入 LogPresenter，需要：
  1. 建立 TaskSession + LogPresenter
  2. 將 worker thread 的 `log_view.controls.append()` 改為寫入 session
  3. 將 `page.update()` 從 worker thread 移到 UI thread timer
  4. 確保 presenter.sync() 在 UI thread 被呼叫

**結論**：這是架構層面的重構，超出 PR4 的 small-change 範圍。建議在獨立的 refactoring PR 中處理。 SnackBar 維持獨立 helper，不屬於 logging layer。

---

### `qc_base.py` + `qc_view.py`

**評估**：
- `QCBase.task_worker()` 封裝了 worker thread 模式，直接對 `log_view.controls` 做 `append` + `page.update()`
- 多個 QC service 都使用這個 worker
- 若要接入 LogPresenter，需要將整個 worker 模式改為 session-driven，影響範圍大

**結論**：worker thread pattern 與 session-driven 架構不匹配。建議未來在 QC service 重構時一併處理。 SnackBar 維持獨立 helper，不屬於 logging layer。

---

## SnackBar / Dialog 決策（確認無變更）

以下 helper 明確不屬於 logging layer，**維持現況**：

- `lm_view._show_snack_bar()` — SnackBar，寫入 `log_info` 只是追蹤用途，不影響 TaskSession
- `bundler_view._show_snack_bar()` — 同上
- `qc_view._show_snack_bar()` — 同上
- `qc_base` 無 SnackBar helper

---

## 本次不做
- bundler / qc_base / qc_view 的 LogPresenter 接入（worker thread 架構問題，需較大重構）
- 高階 log filter / search / export UI

---

## 驗證清單
- [x] `python -m py_compile app/views/lm_view.py app/views/bundler_view.py app/views/qc_base.py app/views/qc_view.py` — ALL OK
- [x] `uv run pytest -q tests/test_lm_view_characterization.py` — **3/3 PASS**
- [x] `uv run pytest -q tests/test_bundler_view_characterization.py tests/test_qc_view_characterization.py tests/test_qc_base.py` — **14/14 PASS**
- [x] `uv run pytest -q tests/test_logging_presenter.py tests/test_logging_core.py` — **14/14 PASS**（2 pre-existing failures in test_task_session.py unrelated to this PR）
- [ ] 手動驗證：LM log panel tail 行為（最後 250 行）正常
- [ ] 手動驗證： SnackBar / dialog 行為無回歸
