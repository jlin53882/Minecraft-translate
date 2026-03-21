## 變更摘要

### PR2：Logging Merge / Extractor Integration

將最容易 freeze 的長任務頁面接入 PR1 的 `LogPresenter`，移除各 view 自行維護的 cursor 管理。

### 修改內容

| 檔案 | 變更 |
|------|------|
| `app/views/merge_view.py` | 移除 `_last_log_count` / `_MAX_UI_LOG_LINES`；改用 `LogPresenter(mode="append")` |
| `app/views/extractor_view.py` | `_append_log_line()` 支援 `LogEntry` 或 `str`；移除 `_last_rendered_log_count` |
| `app/views/extractor/extractor_actions.py` | presenter 接管 append；`update_stats_from_log` 只吃 `str`（caller 負責取 `.text`）|

### 關鍵接線
```python
# merge_view：presenter 接管所有 UI rendering
self.log_presenter.reset()
self.log_presenter.sync(self.log_view, snap["logs"])

# extractor_actions：presenter 接管渲染，caller 只做 stats side effect
new_entries = presenter.sync(view.log_view, logs)
for entry in new_entries:
    if entry.text.strip():
        update_stats_from_log(view, entry.text)  # 只吃 str，不再重複 append
```

> ⚠️ 修正：extractor poller 內 `presenter.sync()` 已經 append UI controls，
> `_append_log_line(entry)` 會造成重複渲染，已移除。

### 移除的舊邏輯
- `_last_log_count`（cursor 由 presenter._last_seq 接管）
- `_MAX_UI_LOG_LINES`（presenter 內部 truncate）
- `_last_rendered_log_count`

### 驗證清單
- [x] `python -m py_compile` 全模組通過
- [x] `uv run pytest -q tests/test_logging_presenter.py` — PASS
- [x] `git diff --stat`：3 檔，net -10 行（有刪減）
- [ ] `uv run pytest -q tests/test_merge_view_characterization.py`
- [ ] `uv run pytest -q tests/test_extractor_view_characterization.py`
- [ ] 手動驗證：Merge 長任務 UI 不凍住、不重複
- [ ] 手動驗證：Extractor summary 數字正確、modal 可正常開關
