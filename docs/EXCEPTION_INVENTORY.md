# 寬鬆例外逐項盤點（#135）

> 由 `tools/gen_exception_inventory.py` 產生；`tests/test_exception_inventory.py` 檢查整份文件與程式碼逐字一致（不含行號，避免普通修改造成漂移）。
> 範圍：`app/`、`translation_tool/`、`main.py` 內所有帶 `noqa: BLE001／S110／S112` 的位置。
> 命令列 QA 工具（`md_extract_qa.py`、`md_inject_qa.py`）的 `print` 為刻意保留，不在此表。

共 **266** 項；其中 **138** 項尚未在程式碼內寫明原因（以「分類」說明處理方式）。

| 分類 | 數量 | 意義 |
|---|---|---|
| 已記錄／回報 | 223 | 例外處理本身有 log、提示、回報錯誤事件或重新丟出；寬鬆捕捉是為了不中斷整批流程 |
| UI／畫面保護 | 28 | UI 層的畫面更新、icon 快取等；失敗只影響顯示，不影響資料 |
| 盡力而為（靜默） | 15 | 引擎層、只有 `pass`／`continue`／回傳常數；失敗不影響結果（例如進度回報、還原失敗時以原始例外為準） |

| 位置 | 規則 | 分類 | 原因／處理 |
|---|---|---|---|
| `app/icon_index.py:_iter_entries_from_lang_files` | BLE001/S112 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_index.py:_process_single_jar` | BLE001/S112 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_index.py:_process_single_jar` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_index.py:build_icon_index` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/icon_index.py:load_icon_index` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/icon_reader.py:_ZipCache.get` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_reader.py:_ZipCache.close_all` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_reader.py:read_icon_bytes` | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/services.py:run_untranslated_check_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_variant_compare_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_english_residue_check_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_variant_compare_tsv_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services_impl/cache/cache_services.py:cache_search_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/cache/cache_services.py:cache_rebuild_index_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/moddb_service.py:run_moddb_scan_service` | BLE001 | 已記錄／回報 | 背景任務：任何失敗都要回報到 session，不可讓執行緒默默結束 |
| `app/services_impl/pipelines/_task_runner.py:run_callable_task` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/bundle_service.py:run_bundling_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/extract_service.py:run_lang_extraction_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/extract_service.py:run_book_extraction_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/extract_service.py:run_dual_extraction_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/lm_service.py:run_lm_translation_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/lookup_service.py:run_batch_lookup_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services_impl/pipelines/merge_service.py:_merge_one_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_zip_batch_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:_run_extracted_stage2` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_folder_batch_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/pipelines/merge_service.py:run_merge_folder_batch_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/shell/app_shell.py:read_app_version` | BLE001 | UI／畫面保護 | 版本只是裝飾，讀不到不影響啟動 |
| `app/shell/app_shell.py:AppShell._on_resize` | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/shell/app_shell.py:_default_mode` | BLE001 | 已記錄／回報 | 設定壞掉時用預設深色 |
| `app/ui/snack.py:_clear_existing_snacks` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/ui/snack.py:_show_snack_dialog` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/snack.py:_show_snack_dialog` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/snack.py:show_snack` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/bundler_view.py:BundlerView._load_version_data` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/bundler_view.py:BundlerView._bundling_worker` | BLE001 | 已記錄／回報 | 背景執行緒邊界，錯誤顯示於日誌 |
| `app/views/cache_manager/cache_actions.py:run_cache_action.execute_work` | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_manager/cache_history_store.py:history_load_active` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_history_store.py:_append_mirror` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_history_store.py:_append_mirror` | BLE001 | 已記錄／回報 | 鏡像是衍生資料，失敗只記錄 |
| `app/views/cache_manager/cache_view_history.py:CacheHistoryMixin._on_shard_apply_selected_history` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_history.py:CacheHistoryMixin._on_apply_selected_history` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_overview.py:CacheOverviewMixin._copy_logs` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_overview.py:CacheOverviewMixin._load_overview` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_overview.py:CacheOverviewMixin._on_rebuild_index.work` | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_manager/cache_view_query.py:CacheQueryMixin._on_page_jump` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_query.py:CacheQueryMixin._on_page_size_change` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_query.py:CacheQueryMixin._on_apply_dst` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_query.py:CacheQueryMixin._on_query_search._search` | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._dynamic_shard_list_height` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._dynamic_type_shard_panel_height` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._dynamic_shard_key_list_height` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._dynamic_shard_key_panel_width` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._set_shard_workspace_visible` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._on_shard_dst_copy` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard_detail.py:CacheShardDetailMixin._dynamic_shard_src_height` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard_detail.py:CacheShardDetailMixin._dynamic_shard_dst_height` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard_detail.py:CacheShardDetailMixin._on_shard_dst_apply` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_query_panel.py:CacheQueryPanel._on_page_jump` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_query_panel.py:CacheQueryPanel._on_page_size_change` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_query_panel.py:CacheQueryPanel._on_apply_dst` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_shard_panel.py:CacheShardPanel._load_shard_entry` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_shard_panel.py:CacheShardPanel._on_shard_dst_apply` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_shard_panel.py:CacheShardPanel._on_shard_dst_copy` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._do_update` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._batch_refresh` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView.did_mount` | BLE001/S110 | UI／畫面保護 | 尚未完成掛載時略過 |
| `app/views/cache_view.py:CacheView._fetch_overview` | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_view.py:CacheView._finish_mount` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._finish_mount` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_page_resized` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView.commit_ui` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/dashboard_view.py:DashboardView._apply_on_ui` | BLE001 | 已記錄／回報 | 沒有 event loop（測試）就直接套用 |
| `app/views/extractor/extractor_dialog.py:_extractor_run_extraction` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/extractor/extractor_preview_dialog.py:_preview_do_scan` | BLE001 | 已記錄／回報 | 錯誤要回報到 UI |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._write_zh_file` | BLE001 | 已記錄／回報 | 錯誤由呼叫端顯示在 UI |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._load_entries` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._collect_jar_modids` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview/icon_cache.py:_follow_parent_chain` | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview/icon_cache.py:_extract_jar_icon` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview/icon_cache.py:_run_jar_workers` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview/icon_cache.py:_batch_extract_jar_icons` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview/icon_cache.py:_batch_extract_jar_icons._process_jar` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/icon_preview_row.py:_ensure_icon_size` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:IconPreviewView._load_async` | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/lm_view.py:LMView.refresh_db_info` | BLE001 | 已記錄／回報 | 資料庫問題只影響提示文字，不應讓頁面載入失敗 |
| `app/views/lm_view.py:LMView.refresh_key_stat` | BLE001 | UI／畫面保護 | 讀不到設定時只是不顯示 |
| `app/views/lookup_view.py:LookupView.single_lookup_worker` | BLE001 | 已記錄／回報 | 失敗也要恢復按鈕並顯示原因 |
| `app/views/lookup_view.py:LookupView.batch_lookup_worker` | BLE001 | 已記錄／回報 | 失敗也要恢復按鈕並顯示原因 |
| `app/views/merge_view.py:MergeView._broadcast_config_change_to_config_view` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._on_merge_field_changed` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView.start_merge._run_merge` | BLE001 | 已記錄／回報 | 背景執行緒邊界：失敗要寫進 session，否則輪詢永遠等不到結束 |
| `app/views/merge_view.py:MergeView._sync_ui_once` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._close_dialog_overlay` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/moddb/entries_panel.py:EntriesPanel._safe_update` | BLE001 | 已記錄／回報 | 頁面已卸載時不影響資料操作 |
| `app/views/moddb/overview_panel.py:OverviewPanel.refresh` | BLE001 | 已記錄／回報 | 頁面已卸載時不影響資料 |
| `app/views/moddb/scan_panel.py:ScanPanel._safe_update` | BLE001 | 已記錄／回報 | 頁面已卸載時不影響掃描本身 |
| `app/views/moddb_view.py:ModDbView._safe_update` | BLE001 | 已記錄／回報 | 頁面已卸載時不影響資料操作 |
| `app/views/pipeline/pipeline_bundle_dialog.py:_load_version_data` | BLE001 | UI／畫面保護 | 讀不到版本資料時使用空設定，UI 仍可開啟 |
| `app/views/pipeline/pipeline_extract_dialog.py:_extract_preview_worker` | BLE001 | 已記錄／回報 | 錯誤要顯示在對話框 |
| `app/views/pipeline/pipeline_one_click_dialog.py:_load_version_data` | BLE001 | UI／畫面保護 | 讀不到版本資料時使用空設定，UI 仍可開啟 |
| `app/views/pipeline/pipeline_session.py:PipelineRunner.start_sequence.worker` | BLE001 | 已記錄／回報 | 背景執行緒邊界，確保按鈕會恢復 |
| `app/views/pipeline/pipeline_session.py:PipelineRunner.run_step` | BLE001 | 已記錄／回報 | 背景步驟邊界：任何錯誤都轉成步驟失敗 |
| `app/views/qc_base.py:QCBase.task_worker.run` | BLE001 | 已記錄／回報 | 背景執行緒需把錯誤回報到 UI |
| `app/views/qc_view.py:QCView._async_pick_file_or_directory` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/qc_view.py:QCView._scroll_to_log` | BLE001 | 已記錄／回報 | 捲動失敗不影響任務 |
| `app/views/rules/rules_actions.py:perform_reload` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/rules/rules_actions.py:start_save_thread.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/rules_view.py:RulesView.on_test_change` | BLE001 | 已記錄／回報 | 規則有問題時顯示原因，不讓頁面出錯 |
| `app/views/rules_view.py:RulesView._initial_load.run` | BLE001 | 已記錄／回報 | 失敗要顯示在 UI |
| `app/views/rules_view.py:RulesView.save_rules_clicked._validate_then_save` | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/translation/translation_actions.py:_safe_add_log` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:_safe_page_update` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/checkers/english_residue_checker.py:check_english_residue_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/untranslated_checker.py:check_untranslated_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator.py:compare_variants_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator.py:compare_variants_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/checkers/variant_comparator_tsv.py:compare_variants_tsv_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/ftb_translator.py:_translate_single_file` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/ftb_translator.py:translate_directory_generator` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/ftb_translator.py:run_ftb_pipeline` | BLE001 | 已記錄／回報 | 預估批次數只是顯示資訊，失敗不影響翻譯 |
| `translation_tool/core/ftb_translator.py:run_ftb_pipeline` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor.py:extract_dual_files_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/jar_processor.py:extract_dual_files_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/jar_processor_extract.py:extract_from_jar_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_extract.py:run_extraction_process_impl._scan_in_background` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_extract.py:run_extraction_process_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_preview.py:_scan_single_jar_for_preview` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/jar_processor_preview.py:preview_extraction_generator_impl` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm._ProgressProxy.set_progress` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm._ProgressProxy.set_status` | BLE001/S110 | 盡力而為（靜默） | UI 狀態回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:run_kubejs_pipeline._count_pending_lang_keys` | BLE001 | 已記錄／回報 | 壞檔不計入總數，但要留下紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness` | BLE001 | 已記錄／回報 | 解析失敗的檔案不計入有效翻譯，已留 debug 紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness` | BLE001 | 已記錄／回報 | 單一檔案失敗不中斷整批統計，已留 debug 紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_cleanup_single_mod_extracted` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_safe_session_log` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_extracted_assets.py:_cleanup_extracted_dirs` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_io.py:quarantine_copy` | BLE001 | 已記錄／回報 | 隔離副本寫入失敗不可中斷合併，但要留下紀錄 |
| `translation_tool/core/lang_merge_pending.py:export_filtered_pending_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod._safe_read_lang_json` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_zip_io.py:quarantine_copy_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_folder` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_folder` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:merge_zhcn_to_zhtw_from_folder` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_api_client.py:call_gemini_requests` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_resume.py:check_resume_feasibility` | BLE001 | 已記錄／回報 | 檢查失敗要回報給使用者，不能讓啟動流程中斷 |
| `translation_tool/core/lm_translator.py:load_checkpoint` | BLE001 | 已記錄／回報 | 損毀的 checkpoint 視為沒有，但要留下紀錄 |
| `translation_tool/core/lm_translator.py:_scan_directory_files` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_handle_batch_error` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_handle_batch_error` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_attempt_batch` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_scan.py:scan_translatable_files` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lm_translator_scan.py:extract_items_parallel.process_file_task` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop.emit_progress` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_shared_loop.py:translate_items_with_cache_loop` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/md_translation_progress.py:_ProgressProxy.set_progress` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/md_translation_stats.py:count_md_pending_docs` | BLE001/S112 | 盡力而為（靜默） | 統計僅計入可解析的待翻譯檔，壞檔略過 |
| `translation_tool/core/md_translation_steps.py:step3_inject_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/plugin_resume.py:compute_source_fingerprint` | BLE001 | 已記錄／回報 | 失敗代表不提供續跑，流程本身會自己回報輸入問題 |
| `translation_tool/core/plugin_resume.py:read_marker` | BLE001 | 已記錄／回報 | 損毀的標記視為沒有，但要留下紀錄並隔離 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:_make_on_translated_item.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:_make_on_translated_item.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:_make_on_batch_flushed.on_batch_flushed` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw.set_prog` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw._count_one` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:translate_ftb_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/ftbquests/ftbquests_snbt_extractor.py:walk_snbt_file` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/plugins/ftbquests/ftbquests_snbt_inject.py:_read_snbt` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_extract.py:extract` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw._count_one` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw.on_batch_flushed` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:translate_kubejs_pending_to_zh_tw` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_batch_flushed` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/translation_db/identity.py:patchouli_dir_names` | BLE001 | 已記錄／回報 | 設定不可用時退回預設，不影響身分計算 |
| `translation_tool/translation_db/scanner.py:make_converter` | BLE001 | 已記錄／回報 | 轉換器是選配，缺少時仍可掃描 |
| `translation_tool/translation_db/settings.py:open_db` | BLE001 | 已記錄／回報 | 資料庫問題不應中斷翻譯 |
| `translation_tool/utils/cache_overview.py:build_cache_overview` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/utils/cache_overview.py:build_cache_overview` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/utils/cache_search.py:CacheSearchEngine.index_batch` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/cache_search.py:build_index_entries` | BLE001 | 已記錄／回報 | 單筆壞資料不影響整體索引 |
| `translation_tool/utils/cache_search_facade.py:CacheSearchFacade.is_search_index_current` | BLE001 | 已記錄／回報 | 判斷失敗就當作需要重建 |
| `translation_tool/utils/cache_shards.py:_commit_numbered_shard` | BLE001/S110 | 盡力而為（靜默） | （未寫原因；見分類） |
| `translation_tool/utils/cache_shards.py:_save_entries_to_active_shards` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/cancellation.py:is_cancelled` | BLE001 | 盡力而為（靜默） | 檢查函式出錯時視為未取消 |
| `translation_tool/utils/config_manager.py:save_config` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/exceptions.py:_resolve_error_log_dir` | BLE001 | 已記錄／回報 | 設定讀不到不可讓錯誤記錄本身失敗 |
| `translation_tool/utils/exceptions.py:_log_error_to_file` | BLE001 | 已記錄／回報 | 記錄失敗不可中斷主流程 |
| `translation_tool/utils/jar_browser.py:_get_default_workers` | BLE001/S110 | 盡力而為（靜默） | config 讀取失敗時不 blocking，直接用 fallback |
| `translation_tool/utils/jar_browser.py:_scan_single_jar` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/log_unit.py:_get_caller_logger_name` | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），退回預設 logger 名稱 |
| `translation_tool/utils/log_unit.py:_log` | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），也不可中斷呼叫端 |
| `translation_tool/utils/log_unit.py:log_exception` | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），也不可中斷呼叫端 |
| `translation_tool/utils/log_unit.py:progress` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷任務 |
| `translation_tool/utils/safe_json_loader.py:load_json_auto_encoding` | BLE001/S112 | 盡力而為（靜默） | 逐一嘗試編碼；全部失敗才回傳 None |
| `translation_tool/utils/species_cache.py:<module>` | BLE001 | 已記錄／回報 | 選用的第三方套件匯入可能出現各種失敗 |
| `translation_tool/utils/species_cache.py:query_wikipedia_and_update_cache` | BLE001 | 已記錄／回報 | wikipedia 套件可能拋出多種執行期錯誤 |
| `translation_tool/utils/text_processor.py:load_replace_rules` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:save_replace_rules` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:load_custom_translations` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:convert_snbt_file_inplace` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/ui_logging_handler.py:UISessionLogHandler.emit` | BLE001/S110 | 盡力而為（靜默） | 在 handler 內記錄錯誤會遞迴 |
