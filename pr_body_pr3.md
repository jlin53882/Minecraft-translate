## 變更摘要

### PR3：Logging Translation / Cleanup Convergence

翻譯長任務頁接入 tail mode presenter，清理舊補丁，完成整個 logging system 收斂。

---

## 本次實作

### Translation 接入 tail mode
- `app/views/translation/translation_actions.py`：
  - `start_ui_timer()` 改用 `LogPresenter(mode="tail", tail_lines=from_config)`
  - `tail_lines` 由 `load_ui_logging_config(load_config)` 動態取得（預設 250，與舊行為一致）
  - presenter 內部處理 tail rebuild + 顏色，poller 不再直接操作 `controls`
  - `colorize=False`（Translation 目前只有灰白色，保持現有外觀）

### Cleanup
- `_last_log_count` / `_last_rendered_log_count`：全部已清除（PR2 已處理）
- secondary views（bundler / lm / qc_base / qc_view）：維持現狀，**已列入 follow-up PR**

---

## 本次不做
- bundler / lm / qc 等 secondary views（已在 follow-up 規劃）
- `_append_log()` 等非 session-driven UI 事件 helper
- 高階 log filter / search / export UI

---

## 驗證清單
- [x] `python -m py_compile app/views/translation/translation_actions.py` — PASS
- [x] `uv run pytest -q tests/test_logging_*.py` — PASS
- [x] `uv run pytest -q tests/test_translation_view_characterization.py` — **3/3 PASS**
- [x] `uv run pytest -q tests/test_merge_view_characterization.py tests/test_extractor_view_characterization.py` — PASS
- [x] 29/29 全部自動測試通過
- [ ] 手動驗證：翻譯長任務只顯示 tail（250行），不會卡頓
- [ ] 手動驗證：merge / extractor / translation 三者 log 行為一致
