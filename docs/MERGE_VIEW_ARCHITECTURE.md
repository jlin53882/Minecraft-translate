# MERGE_VIEW_ARCHITECTURE.md

## 定位

MergeView 位於翻譯流程**第二步**（Translate → **Merge** → 寫回 JAR），負責將翻譯後的內容合併為繁體中文（zh_tw），並依類型分類輸出。

支援兩種輸入模式（`input_mode_group` Radio）：
- **資料夾**（預設）：`folder_path_field` 選 Mod 來源資料夾 → `run_merge_folder_batch_service`
- **ZIP**：`selected_zips` 可多個 ZIP 依序合併 → `run_merge_zip_batch_service`

## 主要 UI 結構

| 區塊 | 元件 | 說明 |
|------|------|------|
| 輸入模式 | `input_mode_group` | ZIP / 資料夾 Radio，切換 `zip_panel` / `folder_panel` 顯示 |
| ZIP 清單 | `pick_zip_button`、`zip_list_view` | 新增/列出已選 ZIP（`_refresh_zip_list` / `_remove_zip`） |
| 一般選項 | `only_lang_checkbox` | 只處理 `/lang/` 下檔案，其他目錄跳過 |
| zh_cn 全域處理 | `process_zh_cn_switch` | **主開關**：關閉時所有 `/zh_cn/` 路徑跳過，並連動停用 Patchouli 開關、強制還原為 False（`_on_zh_cn_switch_changed`） |
| Patchouli 進階 | `patchouli_skip_zh_cn_switch` | zh_cn 有有效翻譯（達門檻）時跳過對應 en_us |
| | `patchouli_threshold_field` | 有效翻譯比例閾值（預設 0.5） |
| | `zh_en_letter_threshold_field` | zh_tw 英文含量閾值，用於 `is_already_zh()`（預設 2） |
| 輸出 | `output_dir_field` | 合併結果輸出位置 |
| 執行狀態 | `status_chip`、`progress_bar`、`start_button` | 進度/狀態顯示 |
| 日誌 | `log_view` | **LogView widget**，`mode="append"`、`max_lines=2000`（取代 LogPresenter） |

**注意**：舊版文件提到的 `skip_zh_cn_switch` 已不存在；現在以 `process_zh_cn_switch` 為全域主開關。

## config 雙向同步

`_on_merge_field_changed(key, value)` 在 UI 變更時：
1. 寫入 `config.json` 的 `lang_merger[key]`
2. `_broadcast_config_change_to_config_view()`：遍歷 view registry，找到有 `controls_map` + `load_config` 的 ConfigView，用 `load_config_into_view` 重新讀取（`set_view_registry` 由 main.py 注入）

欄位即時寫入：`patchouli_skip_en_us_when_zh_cn_exists`、`patchouli_effective_translation_threshold`、`zh_en_letter_threshold`。

## Merge 呼叫鏈

```
[UI] start_merge()
  ├─ 驗證：folder 需 folder_path / zip 需 selected_zips、需輸出資料夾
  ├─ 鎖 UI + session.start() + _start_ui_poller()
  └─ _run_merge() [thread]
       ├─ folder → run_merge_folder_batch_service(...)
       └─ zip    → run_merge_zip_batch_service(...)
             ├─ zip_paths / output_dir
             ├─ only_process_lang / process_zh_cn
             └─ patchouli_skip / patchouli_threshold / zh_en_threshold
```

## Poller 同步（_start_ui_poller）

`poll()` 執行緒每 0.1 秒輪詢 `session.snapshot()`：
- `progress` → `progress_bar.value`
- `logs` → `log_view.sync_from_session(session)`（LogView 管理 append + truncate + scroll）
- 狀態：RUNNING→執行中 / DONE→任務完成 / ERROR→任務發生錯誤

**DONE 時**顯示 `_show_merge_summary()`：
- 優先讀 `snapshot()["summary"]`（service 統計）
- fallback：掃 logs 解析 `[完成]` / `[錯誤]` 行數（`_merge_stats`）
- 顯示成功/失敗 ZIP 數、輸出統計（lang_output / 待翻譯 / 待翻譯整理需翻譯 / patchouli / other / errordata）、失敗 ZIP 詳細錯誤（錯誤訊息截斷 80 字）

終止：`status in ("DONE","ERROR")` 時復原 UI（`start_button`/`zip_list_view` enabled）並 `break`。

## MergeView 與 Session

- `TaskSession(max_logs=2000)`：任務日誌與進度狀態
- `log_view` 為 LogView widget（`mode="append"`）；清空須用公開的 `.clear()`（內部為 ft.Container，無 `.controls`，PR refactor/unified-log-view）

## 維護注意

1. `_merge_stats` 同時被 `_show_merge_summary` 與 fallback 解析使用，勿改名。
2. 新增設定欄位時，記得接 `_on_merge_field_changed` 以同步 config + ConfigView。
3. `_safe_int` / `_safe_float` 回 None 時用預設值（0.5 / 2）帶入 service。
