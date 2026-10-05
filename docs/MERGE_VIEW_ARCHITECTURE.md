# MERGE_VIEW_ARCHITECTURE.md

## 定位

MergeView（頁面標題「語系比對合併」）整合 en_us、zh_cn、zh_tw 三種來源，保留已完成的繁中、把 zh_cn 轉為繁體，只留下真正需要翻譯的條目，並依類型分類輸出（`lang_output`、`patchouli_output`、`other_output`、`errordata_output` 與待翻譯資料夾）。

支援兩種輸入模式（`input_mode_group` Radio）：
- **資料夾**（預設，`folder_panel` 顯示）：`folder_path_field` 選 Mod 來源資料夾 → `run_merge_folder_batch_service`
- **ZIP**（`zip_panel`，預設隱藏）：`selected_zips` 可多個 ZIP 依序合併 → `run_merge_zip_batch_service`

## 主要 UI 結構

控制項與版面的組裝在 `app/views/merge/merge_widgets.py`（`MergeWidgetsMixin`，由 `merge_view.py` 拆出，#114）；`MergeView` 保留事件處理、執行流程與日誌輪詢。

版面：`kit.page_header`「語系比對合併」→ 輸出資料夾說明方塊（`_info_container`）→ 左右兩欄 Row：

- 左欄：「輸入來源」卡片（`input_card`）、「輸出」卡片（`output_card`）
- 右欄：「合併規則」卡片（`rules_card`，內含一般選項 / zh_cn 處理 / Patchouli 進階設定 / 檔案合併(階段 2) 四個區塊）、「執行日誌」卡片

預設畫面（1360x900）：頁首副標下方為輸出資料夾說明方塊（標題「輸出資料夾說明」，列出「待翻譯整理需翻譯」key 數≥3 送機器翻譯、「待翻譯」key 數<3 不過濾）；左欄「輸入來源」卡為「輸入模式」Radio（ZIP 在左、資料夾在右，預設選資料夾）+ placeholder「選擇 Mod 來源資料夾」欄位與右側選資料夾按鈕；「輸出」卡為「輸出資料夾」欄位（右側選資料夾按鈕）、「開始合併」按鈕、狀態 chip（預設「尚未開始」）與進度條。右欄「合併規則」依序為一般選項（「只處理 lang 檔案」勾選、「zh 英文含量閾值」預設 2）、zh_cn 處理（「處理 zh_cn 檔案」開關，預設開）、Patchouli 進階設定（優先使用已有繁中開關預設關、「en_us 跳過門檻」預設 0.5）；檔案合併(階段 2) 與「執行日誌」卡位於右欄較下方，需捲動才可見。

| 區塊 | 元件 | 說明 |
|------|------|------|
| 輸入來源 | `input_mode_group` | ZIP / 資料夾 Radio（預設資料夾），切換 `zip_panel` / `folder_panel` 顯示 |
| | `pick_zip_button`、`zip_list_view` | 新增/列出已選 ZIP（`_refresh_zip_list` / `_remove_zip`） |
| 一般選項 | `only_lang_checkbox` | 只處理 lang 檔案（預設開），其他目錄跳過 |
| | `zh_en_letter_threshold_field` | zh_tw 英文含量閾值（預設 2） |
| zh_cn 處理 | `process_zh_cn_switch` | **主開關**（預設開）：關閉時所有 zh_cn 檔案跳過，並連動停用 Patchouli 開關、強制還原為 False（`_on_zh_cn_switch_changed`），同時顯示 `_zh_cn_disabled_note` |
| Patchouli 進階 | `patchouli_skip_zh_cn_switch` | 優先使用已有繁中、無則信任簡中（達門檻時跳過 en_us，預設關） |
| | `patchouli_threshold_field` | en_us 跳過門檻（有效翻譯比例，預設 0.5） |
| 檔案合併(階段 2) | `extracted_merge_switch` | 合併 `XX_extracted` 的 lang key 到 `assets/`（預設開；僅資料夾模式的服務會執行） |
| 輸出 | `output_dir_field`、`start_button`、`status_chip`、`progress_bar` | 輸出位置、開始按鈕與進度/狀態顯示 |
| 日誌 | `log_view` | **LogView widget**，`mode="append"`、`max_lines=2000` |

**注意**：舊版文件提到的 `skip_zh_cn_switch` 已不存在；現在以 `process_zh_cn_switch` 為全域主開關。

## config 雙向同步

`_on_merge_field_changed(key, value)` 在 UI 變更時：
1. 寫入 `config.json` 的 `lang_merger[key]`
2. `_broadcast_config_change_to_config_view()`：遍歷 view registry，找到有 `controls_map` + `load_config` 的 ConfigView，用 `load_config_into_view` 重新讀取（`set_view_registry` 由 main.py 注入）

欄位即時寫入（皆為 `lang_merger.*`）：`patchouli_skip_en_us_when_zh_cn_exists`、`patchouli_effective_translation_threshold`、`zh_en_letter_threshold`、`enable_extracted_to_assets_merge`。`only_lang_checkbox` 與 `process_zh_cn_switch` 不寫入 config，只在按下開始時傳給 service。

## 合併決策邏輯（`lang_merge_pipeline.py` 的 `_process_single_mod` → `lang_merge_dict.py` 的 `merge_lang_dicts`）

`_process_single_mod` 讀取來源、決定輸出路徑並寫檔；逐 key 決定最終 zh_tw 值與是否列為待翻譯的純函式是 `merge_lang_dicts`（階段 2 的 `merge_extracted_to_assets` 共用同一個）。優先序如下：

1. **人工 zh_tw 保護**：key 已存在於 output_dir 的既有 zh_tw.json 且值含 CJK → 直接保留（外部 ZIP 內的 zh_tw 視為普通來源，仍會套用替換規則再處理）。
2. **zh_tw（ZIP 來源）含中文** → `apply_replace_rules()`（字串）或 `recursive_translate_dict()`（結構）後採用。
3. **zh_cn 含中文** → 以 `recursive_translate_dict`（內部走 S2TW 轉繁）後採用。
4. **皆非中文（或 zh_tw/zh_cn 缺值）** → 依 en_us、zh_cn、zh_tw 順序取第一個非 None 的值作為英文來源（皆無則為空字串）：
   - 空字串（來源本身就是 `""`）→ **跳過**（非待翻譯內容）
   - `is_pure_english()`（有文字且全不含 CJK）→ 寫入 **pending**（`en_us.json` → must_translate_dir）
   - 含 CJK 但非純英文 → fallback `final_tw.setdefault(key, english_source)` 保留原值

輸出細節：
- final zh_tw.json **依 key 字典序排序**後寫出（JSON 用 `_write_bytes_atomic`，`.lang` 用 `_write_text_atomic` + `dump_lang_text`）
- pending 同樣排序後寫入 `must_translate_dir` 的 `en_us.json`（路徑與 final 相同、僅檔名替換）；pending 為空時刪除舊檔
- 輸出路徑自動剝離 ZIP 統一包裝前綴（單一頂層資料夾且非 assets/book/patchouli_books/resources 時）
- 檔案格式：來源為 `.lang` → 輸出 `.lang`，否則 `.json`（依 `base_path_hint` 判斷）
- CJK 判斷由 `lang_merge_dict.py` 的 `contains_cjk`（支援巢狀結構，字串部分以 `lru_cache` memoize）提供；純英文判斷 `is_pure_english` 需「至少一段文字」且「全部不含 CJK」

## Patchouli 有效翻譯判定（`lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness`）

- 對 book 根目錄下 `zh_tw/`、`zh_cn/` 各自掃描 `.json` / `.md` / `.txt` 檔；單一檔案的 CJK 字元佔比 >= 0.5（寫死）才算「有效檔案」（JSON 取所有字串值計算）
- 該語言有效檔案數 / 檔案總數 `ratio >= threshold`（預設 **0.5**）即該語言「effective」
- 結果以 `(book_root_lower, threshold)` 為 key 存 `_patchouli_eff_cache`（module-level，process 存活期間有效）
- 用途：`patchouli_skip_zh_cn_switch` 開啟時，zh_cn 已達 effective 的 book 對應 en_us 才跳過；`process_zh_cn_switch` 關閉時一律不啟用
- **注意**：threshold 同時是 service 的 `patchouli_threshold` 與 UI `patchouli_threshold_field` 的連動值（config `patchouli_effective_translation_threshold`，預設 0.5）

## Merge 呼叫鏈

```
[UI] start_merge()
  ├─ 驗證：folder 需 folder_path / zip 需 selected_zips、需輸出資料夾
  ├─ 鎖 UI + session.start() + _start_ui_poller()
  └─ _run_merge() [thread]
       ├─ folder → run_merge_folder_batch_service(input_dir, output_dir, session, ...)
       │     ├─ 階段 1：merge_zhcn_to_zhtw_from_folder
       │     └─ 階段 2：merge_extracted_to_assets（`enable_extracted_to_assets_merge` 開啟且階段 1 無錯誤時）
       └─ zip    → run_merge_zip_batch_service(zip_paths, output_dir, session, ...)
             └─ 逐 ZIP 呼叫 merge_zhcn_to_zhtw_from_zip；軟性錯誤不中止整批

共同參數：only_process_lang / process_zh_cn / patchouli_skip / patchouli_threshold / zh_en_threshold
service 結束時以 session.set_summary() 寫入統計摘要
```

## Poller 同步（_start_ui_poller）

輪詢在 **Flet event loop** 上執行：`_start_ui_poller()` 經 `self._poller`（`PollerHandle`）啟動 `_poll_merge(alive)`，每 0.1 秒 `await asyncio.sleep` 並呼叫 `_sync_ui_once()`（讀取 `session.snapshot()`）；不再另開 `time.sleep` 的 poll 執行緒。合併的背景工作 `_run_merge` 若丟出例外會把 session 轉為 ERROR（否則輪詢等不到結束）。

**Lifecycle（#114）**：`will_unmount()` 停止輪詢（idempotent）；`did_mount()` 在合併仍被追蹤（`_merge_tracking` 且 `_ui_stop` 未設定）時接續輪詢，合併於卸載期間結束時補上最終狀態與摘要（只顯示一次）。每次 `_sync_ui_once()` 的內容：
- `progress` → `progress_bar.value`
- `logs` → `log_view.sync_from_session(session)`（LogView 管理 append + truncate + scroll）
- 狀態：RUNNING→執行中 / DONE→任務完成 / ERROR→任務發生錯誤

**DONE 時**顯示 `_show_merge_summary()`：
- 優先讀 `snapshot()["summary"]`（service 統計，資料夾模式為 `success_folders` / `failed_folders`，ZIP 模式為 `success_zips` / `failed_zips`）
- fallback：掃 logs 解析 `[完成]` / `[錯誤]` 行數（`_merge_stats`）
- 以 `page.show_dialog()` 顯示對話框：成功/失敗數、輸出統計（`lang_output`、`assets`、待翻譯資料夾、整理資料夾、`patchouli_output`、`other_output`、`errordata_output`，數量為 0 的不列）、失敗項目詳細錯誤（截斷 80 字）；按鈕為「開啟輸出資料夾」與「關閉」（關閉後進度與狀態重置為尚未開始）
- 待翻譯與整理資料夾名稱取自 `lang_merger.pending_folder_name` / `lang_merger.pending_organized_folder_name`

終止：`status` 為 DONE 或 ERROR 時復原 UI（`start_button`/`zip_list_view` enabled）並設定 `_ui_stop` 停止輪詢；`_sync_ui_once` 內若 `_ui_stop` 已設定則直接略過，避免摘要重複彈出。「開啟輸出資料夾」走 `open_output_folder`（不等待外部程式）。

## MergeView 與 Session

- `TaskSession(max_logs=2000)`：任務日誌與進度狀態
- `log_view` 為 LogView widget（`mode="append"`）；清空須用公開的 `.clear()`（內部為 ft.Container，無 `.controls`，PR refactor/unified-log-view）

## 維護注意

1. `_merge_stats` 同時被 `_show_merge_summary` 與 fallback 解析使用，勿改名。對話框內容由 `merge_widgets.py` 的 `_merge_summary_output_block` / `_merge_summary_failed_block` / `_merge_summary_content` 組裝。
2. 新增設定欄位時，記得接 `_on_merge_field_changed` 以同步 config + ConfigView。
3. `_safe_int` / `_safe_float` 回 None 時用預設值（0.5 / 2）帶入 service。
