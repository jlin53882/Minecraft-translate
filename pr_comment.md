## PR 更新說明（2026-05-19）

### 第四次 commit：按鈕註解覆蓋其餘 view

| View | 按鈕 | 說明 |
|------|------|------|
| `extractor_view.py` | `lang_button`、`book_button`、`dual_extract_button` | 註明 worker 執行緒 UI 更新模式 |
| `untranslated_checker.py` | `start_button` | 註明 disabled/enabled 由 worker 控制 |
| `qc_view.py` | `compare_start_button`、`compare_tsv_start_button` | 註明執行緒 UI 更新模式 |
| `config_view.py` | `add_model_button`、`add_key_button`、`btn_delete` | 模型/Key 新增與刪除操作 |
| `icon_preview_view.py` | `prev/next_page_btn`、`back_btn`、`pick_source/review_btn`、`load_btn`、`save_btn` | 分頁導航、資料夾選擇、載入/儲存狀態 |

### 前三次 commit 回顧

**Commit 1** - 核心 fix：跨執行緒 UI 更新（`page.run_task()`）
- `bundler_view.py` - closure `p=progress` 被 `run_task` positional arg 覆蓋 Bug 修復
- `merge_view.py` - `self.page` -> `self._page`
- `lm_view.py` - `loop()` UI 更新全改 `run_task`
- `qc_base.py` - `run()` UI 更新全改 `run_task`
- `rules_view.py` - `_initial_load()` UI 更新全改 `run_task`
- `lookup_view.py` - worker + `batch_lookup_clicked()` 全改 `run_task`
- 同時修了一個 `bundler_view.py` 缺少 `root_dir` 賦值的 Bug

**Commit 2** - 補上 `Flet 0.85 執行緒安全須知` docstring（6 個 view）

**Commit 3** - 按鈕註解（bundler/lm/lookup view）

### 測試結果
```
1559 passed, 0 skipped
```