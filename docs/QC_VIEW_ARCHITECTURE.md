# QC_VIEW_ARCHITECTURE.md

## 定位

QCView 是 **Quality Check（品質檢查）頁**，翻譯完成後的品質驗證工具，非翻譯管線必要環節。三張 Card 各自獨立，共用同一組 `progress_bar` + `log_view`。

## 檔案結構

- `app/views/qc_view.py` — 主視圖（約 348 行）
- `app/views/qc_base.py` — QCBase 共用執行緒任務執行器（約 95 行）
- `app/views/untranslated_checker.py` — UntranslatedChecker 元件（PR1 拆分）
- `app/services.py` — `run_untranslated_check_service` / `run_variant_compare_service` / `run_variant_compare_tsv_service`

## QCBase（task_worker）

```python
QCBase(page, progress_bar, log_view)   # 注入共用 progress_bar + LogView
task_worker(service_func, args_tuple, on_complete, controls_to_disable)
  → run() [thread]
      → for update in service_func(*args_tuple):
            ├─ update["log"] 逐行 → log_view.add(line, level="info")
            ├─ update["progress"] → progress_bar.value
            ├─ update["error"] → progress_bar.color = theme.ERROR
            └─ log_view._list_view.scroll_to(-1) + page.update()
      finally: 重置 progress_bar、恢復 disabled 控制項
      完成 → on_complete()
```

- `service_func` 須為 **generator**（逐步 yield update dict）
- `controls_to_disable` 任務期間自動禁用 UI；`on_complete` 回調恢復
- LogView 已統一為 LogView widget（level 顏色由 LogView 從 theme 取）

## 主要檢查項目（start_task 分派表）

| task_type | 服務函式 | 輸入 | 輸出 |
|-----------|----------|------|------|
| `untranslated`（備用） | `run_untranslated_check_service` | en_dir / tw_dir / out_dir | Key 缺失檢查報告 |
| `compare_json` | `run_variant_compare_service` | cn_dir / tw_dir / out_dir | JSON 資料夾簡繁差異報告 |
| `compare_tsv` | `run_variant_compare_tsv_service` | tsv_path / out_csv_path | TSV 簡繁差異 CSV |

`start_task(task_type)` 流程：
1. 清空 log、重置 progress_bar、`set_controls_disabled(True)`
2. 依 task_type 收集路徑（缺漏 → snack 錯誤 + 復原控制項）
3. `task_runner.task_worker(target_func, args, on_complete=復原控制項)`

## UI 架構

- **Card 1**：`untranslated_checker` 元件（Key 缺失檢查，`UntranslatedChecker(page, file_picker, task_runner)`）
- **Card 2**：簡繁差異比較 JSON 資料夾模式（`cn_dir_textfield` / `tw_dir_textfield_2` / `compare_out_dir_textfield` / `compare_start_button`）
- **Card 3**：簡繁差異比較 TSV 單檔案模式（`tsv_file_textfield` / `tsv_out_file_textfield` / `compare_tsv_start_button`）
- 共用：`progress_bar` + `log_view`（LogView widget，`mode="append"`、`max_lines=2000`）

## FilePicker 流程

`_create_pick_button(target_textfield, title, folder_mode, file_filter)` → `_pick_file_or_directory()`：
- 把目標寫入 `self._pending_pick` → `_page.run_task(_async_pick_file_or_directory)`
- folder_mode → `file_picker.get_directory_path()`；否則 `file_picker.pick_files(allow_multiple=False)`
- 結果寫回 target_textfield；取消 → snack「您已取消選擇」

## 維護注意

1. 新增檢查類型：加 UI 元件 + `start_task` 分派分支 + `set_controls_disabled` 清單。
2. `task_worker` 的 service 必須是 generator；若回傳 list 會 `TypeError: 'list' object is not iterable`。
3. `log_view._list_view.scroll_to` 直接碰內部屬性（LogView 為 ft.Container）。
