# 寬鬆例外逐項盤點（#135）

> 由 `tools/gen_exception_inventory.py` 產生；`tests/test_exception_inventory.py` 檢查逐檔數量與程式碼一致。
> 範圍：`app/`、`translation_tool/`、`main.py` 內所有帶 `noqa: BLE001／S110／S112` 的位置。
> 命令列 QA 工具（`md_extract_qa.py`、`md_inject_qa.py`）的 `print` 為刻意保留，不在此表。

共 **258** 項；其中 **145** 項尚未在程式碼內寫明原因（以「分類」說明處理方式）。

| 分類 | 數量 | 意義 |
|---|---|---|
| 已記錄／回報 | 211 | 例外處理本身有 log、提示、回報錯誤事件或重新丟出；寬鬆捕捉是為了不中斷整批流程 |
| UI／畫面保護 | 32 | UI 層的畫面更新、icon 快取等；失敗只影響顯示，不影響資料 |
| 盡力而為（靜默） | 15 | 引擎層、只有 `pass`／`continue`／回傳常數；失敗不影響結果（例如進度回報、還原失敗時以原始例外為準） |

| 位置 | 規則 | 分類 | 原因／處理 |
|---|---|---|---|
| `app/icon_index.py:_iter_entries_from_lang_files`（第 67 行） | BLE001/S112 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_index.py:_process_single_jar`（第 112 行） | BLE001/S112 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_index.py:_process_single_jar`（第 152 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_index.py:build_icon_index`（第 206 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/icon_index.py:load_icon_index`（第 249 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/icon_reader.py:_ZipCache.get`（第 80 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_reader.py:_ZipCache.close_all`（第 95 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_reader.py:read_icon_bytes`（第 118 行） | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/services.py:run_untranslated_check_service`（第 41 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_variant_compare_service`（第 58 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_english_residue_check_service`（第 75 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_variant_compare_tsv_service`（第 92 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services_impl/cache/cache_services.py:cache_search_service`（第 129 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/cache/cache_services.py:cache_rebuild_index_service`（第 244 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/_task_runner.py:run_callable_task`（第 38 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/bundle_service.py:run_bundling_service`（第 100 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/extract_service.py:run_lang_extraction_service`（第 365 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/extract_service.py:run_book_extraction_service`（第 398 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/extract_service.py:run_dual_extraction_service`（第 431 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/lm_service.py:run_lm_translation_service`（第 85 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/lookup_service.py:run_batch_lookup_service`（第 74 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services_impl/pipelines/merge_service.py:run_merge_zip_batch_service`（第 142 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_zip_batch_service`（第 184 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_folder_batch_service`（第 294 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_folder_batch_service`（第 299 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_folder_batch_service`（第 343 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/shell/app_shell.py:read_app_version`（第 86 行） | BLE001 | UI／畫面保護 | 版本只是裝飾，讀不到不影響啟動 |
| `app/shell/app_shell.py:AppShell._on_resize`（第 775 行） | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/shell/app_shell.py:_default_mode`（第 803 行） | BLE001 | 已記錄／回報 | 設定壞掉時用預設深色 |
| `app/ui/snack.py:show_snack`（第 125 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/ui/snack.py:show_snack`（第 179 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/snack.py:show_snack`（第 184 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/snack.py:show_snack`（第 192 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/bundler_view.py:BundlerView._load_version_data`（第 160 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/bundler_view.py:BundlerView._bundling_worker`（第 749 行） | BLE001 | 已記錄／回報 | 背景執行緒邊界，錯誤顯示於日誌 |
| `app/views/cache_manager/cache_actions.py:run_cache_action.execute_work`（第 72 行） | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_manager/cache_history_store.py:history_load_active`（第 62 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_history_store.py:history_append_event`（第 116 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_history_store.py:history_load_recent`（第 141 行） | BLE001/S112 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_history_store.py:history_load_recent`（第 149 行） | BLE001/S112 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_query_panel.py:CacheQueryPanel._on_page_jump`（第 546 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_query_panel.py:CacheQueryPanel._on_page_size_change`（第 556 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_query_panel.py:CacheQueryPanel._on_apply_dst`（第 606 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_shard_panel.py:CacheShardPanel._load_shard_entry`（第 387 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_shard_panel.py:CacheShardPanel._on_shard_dst_apply`（第 538 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_shard_panel.py:CacheShardPanel._on_shard_dst_copy`（第 560 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._do_update`（第 1161 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._batch_refresh`（第 1168 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView.did_mount`（第 1189 行） | BLE001/S110 | UI／畫面保護 | 尚未完成掛載時略過 |
| `app/views/cache_view.py:CacheView._fetch_overview`（第 1202 行） | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_view.py:CacheView._finish_mount`（第 1226 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._finish_mount`（第 1234 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._dynamic_shard_list_height`（第 1249 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._dynamic_type_shard_panel_height`（第 1267 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._dynamic_shard_key_list_height`（第 1285 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._dynamic_shard_src_height`（第 1302 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._dynamic_shard_dst_height`（第 1319 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._dynamic_shard_key_panel_width`（第 1336 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_page_resized`（第 1353 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView.commit_ui`（第 1509 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._copy_logs`（第 1699 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._load_overview`（第 1896 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_rebuild_index.work`（第 1962 行） | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_view.py:CacheView._load_shard_rows`（第 2142 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._load_shard_keys`（第 2167 行） | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._set_shard_workspace_visible`（第 2328 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._load_shard_entry`（第 2383 行） | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_shard_dst_apply`（第 2536 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_shard_dst_copy`（第 2560 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_shard_apply_selected_history`（第 2768 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_apply_selected_history`（第 3272 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_page_jump`（第 3444 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_page_size_change`（第 3454 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_apply_dst`（第 3507 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_query_search._search`（第 3615 行） | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/dashboard_view.py:DashboardView._apply_on_ui`（第 302 行） | BLE001 | 已記錄／回報 | 沒有 event loop（測試）就直接套用 |
| `app/views/extractor/extractor_dialog.py:open_extractor_dialog.run_extraction`（第 506 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/extractor/extractor_dialog.py:open_preview_dialog.start_scan.do_scan`（第 1087 行） | BLE001 | 已記錄／回報 | 錯誤要回報到 UI |
| `app/views/icon_preview_row.py:_ensure_icon_size`（第 47 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:_follow_parent_chain`（第 312 行） | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:_extract_jar_icon`（第 534 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:_batch_extract_jar_icons`（第 579 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:_batch_extract_jar_icons._process_jar`（第 642 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:_batch_extract_jar_icons`（第 666 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:IconPreviewView._on_load_clicked._scan`（第 1296 行） | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/icon_preview_view.py:IconPreviewView._save_current_zh`（第 1846 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:IconPreviewView._load_entries`（第 1921 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:IconPreviewView._load_entries_from_jar_directory`（第 2002 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/lm_view.py:LMView.refresh_key_stat`（第 254 行） | BLE001 | UI／畫面保護 | 讀不到設定時只是不顯示 |
| `app/views/lookup_view.py:LookupView.single_lookup_worker`（第 216 行） | BLE001 | 已記錄／回報 | 失敗也要恢復按鈕並顯示原因 |
| `app/views/lookup_view.py:LookupView.batch_lookup_worker`（第 262 行） | BLE001 | 已記錄／回報 | 失敗也要恢復按鈕並顯示原因 |
| `app/views/merge_view.py:MergeView._broadcast_config_change_to_config_view`（第 109 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._on_merge_field_changed`（第 118 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._start_ui_poller._sync_ui`（第 820 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._start_ui_poller.poll`（第 828 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._close_dialog_overlay`（第 992 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/pipeline/pipeline_bundle_dialog.py:_load_version_data`（第 36 行） | BLE001 | UI／畫面保護 | 讀不到版本資料時使用空設定，UI 仍可開啟 |
| `app/views/pipeline/pipeline_extract_dialog.py:open_extract_dialog.show_preview_result.do_preview`（第 198 行） | BLE001 | 已記錄／回報 | 錯誤要顯示在對話框 |
| `app/views/pipeline/pipeline_one_click_dialog.py:_load_version_data`（第 42 行） | BLE001 | UI／畫面保護 | 讀不到版本資料時使用空設定，UI 仍可開啟 |
| `app/views/pipeline/pipeline_view.py:PipelineView._run_session_step`（第 494 行） | BLE001 | 已記錄／回報 | 背景步驟邊界：任何錯誤都轉成步驟失敗 |
| `app/views/pipeline/pipeline_view.py:PipelineView._on_one_click_execute.worker`（第 819 行） | BLE001 | 已記錄／回報 | 背景執行緒邊界，確保按鈕會恢復 |
| `app/views/qc_base.py:QCBase.task_worker.run`（第 110 行） | BLE001 | 已記錄／回報 | 背景執行緒需把錯誤回報到 UI |
| `app/views/qc_view.py:QCView._async_pick_file_or_directory`（第 344 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/qc_view.py:QCView._scroll_to_log`（第 367 行） | BLE001 | 已記錄／回報 | 捲動失敗不影響任務 |
| `app/views/rules/rules_actions.py:perform_reload`（第 57 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/rules/rules_actions.py:start_save_thread.worker`（第 81 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/rules_view.py:RulesView.on_test_change`（第 405 行） | BLE001 | 已記錄／回報 | 規則有問題時顯示原因，不讓頁面出錯 |
| `app/views/rules_view.py:RulesView._initial_load.run`（第 649 行） | BLE001 | 已記錄／回報 | 失敗要顯示在 UI |
| `app/views/rules_view.py:RulesView.save_rules_clicked._validate_then_save`（第 904 行） | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/translation/translation_actions.py:_safe_add_log`（第 22 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:_safe_page_update`（第 31 行） | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb`（第 66 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb.worker`（第 83 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb.worker`（第 89 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs`（第 122 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs.worker`（第 138 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs.worker`（第 144 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md`（第 177 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md.worker`（第 194 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md.worker`（第 200 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>`（第 35 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>`（第 40 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>`（第 45 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>`（第 50 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/checkers/english_residue_checker.py:check_english_residue_generator`（第 67 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/untranslated_checker.py:check_untranslated_generator`（第 93 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator.py:compare_variants_generator`（第 36 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator.py:compare_variants_generator`（第 129 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator`（第 37 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator`（第 53 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator`（第 89 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator`（第 106 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/ftb_translator.py:_translate_single_file`（第 95 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/ftb_translator.py:translate_directory_generator`（第 189 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/ftb_translator.py:run_ftb_pipeline`（第 365 行） | BLE001 | 已記錄／回報 | 預估批次數只是顯示資訊，失敗不影響翻譯 |
| `translation_tool/core/ftb_translator.py:run_ftb_pipeline`（第 405 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor.py:extract_dual_files_generator`（第 247 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/jar_processor.py:extract_dual_files_generator`（第 277 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/jar_processor_extract.py:extract_from_jar_impl`（第 205 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_extract.py:run_extraction_process_impl._scan_in_background`（第 262 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_extract.py:run_extraction_process_impl`（第 412 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_preview.py:_scan_single_jar_for_preview`（第 118 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/jar_processor_preview.py:preview_extraction_generator_impl`（第 270 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm._ProgressProxy.set_progress`（第 262 行） | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm._ProgressProxy.set_status`（第 269 行） | BLE001/S110 | 盡力而為（靜默） | UI 狀態回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm`（第 285 行） | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:run_kubejs_pipeline._count_pending_lang_keys`（第 403 行） | BLE001 | 已記錄／回報 | 壞檔不計入總數，但要留下紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness`（第 87 行） | BLE001 | 已記錄／回報 | 解析失敗的檔案不計入有效翻譯，已留 debug 紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness`（第 101 行） | BLE001 | 已記錄／回報 | 單一檔案失敗不中斷整批統計，已留 debug 紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 358 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 429 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 464 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 494 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 523 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 571 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 601 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 642 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl`（第 657 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl`（第 35 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl`（第 62 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl`（第 72 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl`（第 88 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_cleanup_single_mod_extracted`（第 263 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_safe_session_log`（第 273 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_extracted_assets.py:_cleanup_extracted_dirs`（第 308 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets`（第 400 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets`（第 421 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets`（第 442 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets`（第 461 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets`（第 482 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets`（第 532 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_io.py:quarantine_copy`（第 168 行） | BLE001 | 已記錄／回報 | 隔離副本寫入失敗不可中斷合併，但要留下紀錄 |
| `translation_tool/core/lang_merge_pending.py:export_filtered_pending_impl`（第 52 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod._safe_read_lang_json`（第 131 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod`（第 194 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod`（第 281 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_zip_io.py:quarantine_copy_from_zip`（第 198 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_zip`（第 78 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_zip`（第 281 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_zip`（第 352 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_folder`（第 403 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_folder`（第 538 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_folder`（第 577 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_api_client.py:call_gemini_requests`（第 153 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator.py:load_checkpoint`（第 139 行） | BLE001 | 已記錄／回報 | 損毀的 checkpoint 視為沒有，但要留下紀錄 |
| `translation_tool/core/lm_translator.py:translate_directory_generator`（第 535 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_handle_batch_error`（第 518 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_handle_batch_error`（第 549 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_attempt_batch`（第 707 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_scan.py:scan_translatable_files`（第 53 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lm_translator_scan.py:extract_items_parallel.process_file_task`（第 98 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop.emit_progress`（第 147 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop`（第 189 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop`（第 248 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop`（第 264 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop`（第 283 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop`（第 295 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop`（第 310 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/md_translation_progress.py:_ProgressProxy.set_progress`（第 27 行） | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/md_translation_stats.py:count_md_pending_docs`（第 49 行） | BLE001/S112 | 盡力而為（靜默） | 統計僅計入可解析的待翻譯檔，壞檔略過 |
| `translation_tool/core/md_translation_steps.py:step3_inject_impl`（第 292 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator`（第 208 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator`（第 227 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator`（第 241 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator`（第 370 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:_make_on_translated_item.on_translated_item`（第 167 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:_make_on_translated_item.on_translated_item`（第 181 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:_make_on_batch_flushed.on_batch_flushed`（第 195 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw.set_prog`（第 248 行） | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw._count_one`（第 297 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw`（第 370 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw`（第 527 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw`（第 661 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw`（第 680 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_snbt_extractor.py:walk_snbt_file`（第 72 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py:_read_snbt`（第 136 行） | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_extract.py:extract`（第 705 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw._count_one`（第 251 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw`（第 308 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw`（第 417 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw`（第 486 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw.on_translated_item`（第 546 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw.on_translated_item`（第 551 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw.on_batch_flushed`（第 559 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw`（第 610 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending`（第 157 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending`（第 284 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending`（第 313 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_translated_item`（第 336 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_translated_item`（第 351 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_batch_flushed`（第 359 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending`（第 413 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending`（第 462 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/utils/cache_overview.py:build_cache_overview`（第 66 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/utils/cache_overview.py:build_cache_overview`（第 89 行） | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/utils/cache_search.py:CacheSearchEngine.index_batch`（第 218 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/cache_search.py:build_index_entries`（第 592 行） | BLE001 | 已記錄／回報 | 單筆壞資料不影響整體索引 |
| `translation_tool/utils/cache_search_facade.py:CacheSearchFacade.is_search_index_current`（第 57 行） | BLE001 | 已記錄／回報 | 判斷失敗就當作需要重建 |
| `translation_tool/utils/cache_shards.py:_commit_numbered_shard`（第 299 行） | BLE001/S110 | 盡力而為（靜默） | （未寫原因；見分類） |
| `translation_tool/utils/cache_shards.py:_save_entries_to_active_shards`（第 369 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/cancellation.py:is_cancelled`（第 53 行） | BLE001 | 盡力而為（靜默） | 檢查函式出錯時視為未取消 |
| `translation_tool/utils/config_manager.py:save_config`（第 402 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/exceptions.py:_resolve_error_log_dir`（第 225 行） | BLE001 | 已記錄／回報 | 設定讀不到不可讓錯誤記錄本身失敗 |
| `translation_tool/utils/exceptions.py:_log_error_to_file`（第 261 行） | BLE001 | 已記錄／回報 | 記錄失敗不可中斷主流程 |
| `translation_tool/utils/jar_browser.py:_get_default_workers`（第 46 行） | BLE001/S110 | 盡力而為（靜默） | config 讀取失敗時不 blocking，直接用 fallback |
| `translation_tool/utils/jar_browser.py:_scan_single_jar`（第 117 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/log_unit.py:_get_caller_logger_name`（第 53 行） | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），退回預設 logger 名稱 |
| `translation_tool/utils/log_unit.py:_log`（第 74 行） | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），也不可中斷呼叫端 |
| `translation_tool/utils/log_unit.py:log_exception`（第 121 行） | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），也不可中斷呼叫端 |
| `translation_tool/utils/log_unit.py:progress`（第 153 行） | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷任務 |
| `translation_tool/utils/safe_json_loader.py:load_json_auto_encoding`（第 33 行） | BLE001/S112 | 盡力而為（靜默） | 逐一嘗試編碼；全部失敗才回傳 None |
| `translation_tool/utils/species_cache.py:<module>`（第 66 行） | BLE001 | 已記錄／回報 | 選用的第三方套件匯入可能出現各種失敗 |
| `translation_tool/utils/species_cache.py:query_wikipedia_and_update_cache`（第 168 行） | BLE001 | 已記錄／回報 | wikipedia 套件可能拋出多種執行期錯誤 |
| `translation_tool/utils/text_processor.py:load_replace_rules`（第 361 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:save_replace_rules`（第 408 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:load_custom_translations`（第 429 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:convert_snbt_file_inplace`（第 474 行） | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/ui_logging_handler.py:UISessionLogHandler.emit`（第 51 行） | BLE001/S110 | 盡力而為（靜默） | 在 handler 內記錄錯誤會遞迴 |
