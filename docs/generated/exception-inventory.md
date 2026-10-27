# 寬鬆例外逐項盤點

> 由 `tools/gen_exception_inventory.py` 產生；`tests/test_exception_inventory.py` 檢查整份文件與程式碼逐字一致（不含行號，避免普通修改造成漂移）。
> 範圍：`app/`、`translation_tool/`、`main.py` 內所有帶 `noqa: BLE001／S110／S112` 的位置。
> 命令列 QA 工具（`md_extract_qa.py`、`md_inject_qa.py`）的 `print` 為刻意保留，不在此表。

共 **368** 項；其中 **102** 項尚未在程式碼內寫明原因（以「分類」說明處理方式）。

| 分類 | 數量 | 意義 |
|---|---|---|
| 已記錄／回報 | 330 | 例外處理本身有 log、提示、回報錯誤事件或重新丟出；寬鬆捕捉是為了不中斷整批流程 |
| UI／畫面保護 | 19 | UI 層的畫面更新、icon 快取等；失敗只影響顯示，不影響資料 |
| 盡力而為（靜默） | 19 | 引擎層、只有 `pass`／`continue`／回傳常數；失敗不影響結果（例如進度回報、還原失敗時以原始例外為準） |

| 位置 | 規則 | 分類 | 原因／處理 |
|---|---|---|---|
| `app/config_store.py:notify_saved` | BLE001 | 已記錄／回報 | 設定訂閱者錯誤不得中斷寫入 |
| `app/config_store.py:notify_saved` | BLE001 | 已記錄／回報 | 設定訂閱者錯誤不得中斷寫入 |
| `app/icon_index.py:_iter_entries_from_lang_files` | BLE001 | 已記錄／回報 | 單一 lang 檔讀不出來就略過 |
| `app/icon_index.py:_process_single_jar` | BLE001 | 已記錄／回報 | 單一 JAR 索引失敗不中止整體，但要留下堆疊 |
| `app/icon_index.py:build_icon_index` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/icon_index.py:load_icon_index` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/icon_jar_scan.py:scan_jar` | BLE001 | 已記錄／回報 | 單一 JAR 失敗不中止整批，但要留下是哪個 JAR |
| `app/icon_preview_image.py:render_icon_uri` | BLE001 | 已記錄／回報 | 單張壞圖不應中斷整頁，改顯示佔位圖 |
| `app/icon_reader.py:_ZipCache.get` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_reader.py:_ZipCache.close_all` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_reader.py:read_icon_bytes` | BLE001 | UI／畫面保護 | （未寫原因；見分類） |
| `app/icon_scan_pool.py:icon_scan_executor` | BLE001 | 已記錄／回報 | 行程池無法啟動（打包、權限等）時退回執行緒 |
| `app/icon_scan_pool.py:_stream_once` | BLE001 | 已記錄／回報 | worker 回報異常：該 JAR 視為失敗，其他照常 |
| `app/services.py:run_untranslated_check_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_variant_compare_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_english_residue_check_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services.py:run_variant_compare_tsv_service` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `app/services_impl/cache/cache_services.py:cache_search_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/cache/cache_services.py:cache_rebuild_index_service` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/services_impl/moddb_batch_operation.py:_run_batch_job` | BLE001 | 已記錄／回報 | worker boundary must publish unexpected failures |
| `app/services_impl/moddb_retranslate_service.py:_cache_finalized_translation` | BLE001 | 已記錄／回報 | cache is best-effort after DB commit |
| `app/services_impl/moddb_retranslate_service.py:_flush_repair_cache` | BLE001 | 已記錄／回報 | DB results survive cache failure |
| `app/services_impl/moddb_retranslate_service.py:run_moddb_retranslate_service` | BLE001 | 已記錄／回報 | service boundary reports failure in TaskSession |
| `app/services_impl/moddb_service.py:warm_stats_quietly` | BLE001 | 已記錄／回報 | 統計預熱失敗不影響任務結果 |
| `app/services_impl/moddb_service.py:run_moddb_scan_service` | BLE001 | 已記錄／回報 | 背景任務：任何失敗都要回報到 session，不可讓執行緒默默結束 |
| `app/services_impl/moddb_smart_review_service.py:_apply_moddb_smart_review_legacy_batch` | BLE001 | 已記錄／回報 | one bad candidate must not stop the batch |
| `app/services_impl/moddb_translate_service.py:_flush_buffer` | BLE001 | 已記錄／回報 | 持久化失敗需重試並回報 |
| `app/services_impl/moddb_translate_service.py:run_moddb_translate_service` | BLE001 | 已記錄／回報 | 背景任務：任何失敗都要回報到 session |
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
| `app/ui/page_post.py:post_to_ui` | BLE001 | 已記錄／回報 | a disposed page may reject the callback |
| `app/ui/page_post.py:post_to_ui` | BLE001 | 已記錄／回報 | recovery must not raise into the worker |
| `app/ui/page_resize.py:subscribe_page_resize` | BLE001 | 已記錄／回報 | 無法掛在頁面上時退化成空操作訂閱 |
| `app/ui/page_resize.py:notify_page_resize` | BLE001 | 已記錄／回報 | 一個 view 的 resize 失敗不可拖垮其他 view |
| `app/ui/safe_file_picker.py:SafeFilePicker._notify_unsupported` | BLE001 | 已記錄／回報 | 控制項未掛載時只能記錄 |
| `app/ui/safe_file_picker.py:SafeFilePicker._is_web` | BLE001 | UI／畫面保護 | 控制項尚未掛載：視為非 Web，交給原本的行為 |
| `app/ui/snack.py:_clear_existing_snacks` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/ui/snack.py:_show_snack_dialog` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/snack.py:_show_snack_dialog` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/snack.py:show_snack` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/ui/sync_text_field.py:_clean_path_input` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響輸入 |
| `app/ui/update_scope.py:update_regions` | BLE001 | 已記錄／回報 | 頁面已卸載時不影響資料操作 |
| `app/ui/update_scope.py:suppress_auto_update` | BLE001 | 已記錄／回報 | 不在 Flet 事件脈絡內（單元測試）時沒有影響 |
| `app/views/bundler_view.py:BundlerView._bundling_worker` | BLE001 | 已記錄／回報 | 背景執行緒邊界，錯誤顯示於日誌 |
| `app/views/cache_manager/cache_actions.py:_execute_cache_work` | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
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
| `app/views/cache_manager/cache_view_query.py:CacheQueryMixin._on_query_search._search_standalone` | BLE001 | 已記錄／回報 | event loop displays the error |
| `app/views/cache_manager/cache_view_query.py:CacheQueryMixin._on_query_search.search` | BLE001 | 已記錄／回報 | event loop displays the error |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._dynamic_type_shard_panel_height` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._dynamic_shard_key_panel_width` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._set_shard_workspace_visible` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard.py:CacheShardMixin._on_shard_dst_copy` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_manager/cache_view_shard_detail.py:CacheShardDetailMixin._on_shard_dst_apply` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._batch_refresh` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView.did_mount` | BLE001/S110 | UI／畫面保護 | 尚未完成掛載時略過 |
| `app/views/cache_view.py:CacheView._fetch_overview` | BLE001 | UI／畫面保護 | 錯誤顯示在 UI |
| `app/views/cache_view.py:CacheView._finish_mount` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._finish_mount` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/cache_view.py:CacheView._on_page_resized` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/config/chatgpt_oauth_panel.py:ChatGPTOAuthPanel._start_login` | BLE001 | 已記錄／回報 | interactive OAuth boundary |
| `app/views/config/chatgpt_oauth_panel.py:ChatGPTOAuthPanel._on_account_selected` | BLE001 | 已記錄／回報 | account switching UI boundary |
| `app/views/config/chatgpt_oauth_panel.py:ChatGPTOAuthPanel.ensure_models_loaded` | BLE001 | 已記錄／回報 | page lifecycle may already be closing |
| `app/views/config/chatgpt_oauth_panel.py:ChatGPTOAuthPanel._load_models` | BLE001 | 已記錄／回報 | provider request UI boundary |
| `app/views/config/chatgpt_oauth_panel.py:ChatGPTOAuthPanel._on_disconnect` | BLE001 | 已記錄／回報 | credential revocation UI boundary |
| `app/views/config/config_actions.py:_collect_validated_config` | BLE001 | 已記錄／回報 | config collection is a UI save boundary |
| `app/views/config/config_actions.py:_write_config_with_feedback` | BLE001 | 已記錄／回報 | failure may occur before or after atomic replace |
| `app/views/config/config_actions.py:_reload_after_confirmed_write` | BLE001 | 已記錄／回報 | reload is a UI boundary after confirmed persistence |
| `app/views/config/config_actions.py:load_config_transactionally` | BLE001 | 已記錄／回報 | hint refresh must not mask the reload error |
| `app/views/config/db_location.py:DbLocationBanner._on_prune` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響設定頁 |
| `app/views/config/db_location.py:DbLocationBanner.safe_refresh` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響設定頁 |
| `app/views/config/db_location.py:attach_path_hooks.chain.handler` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響輸入 |
| `app/views/config/db_location.py:attach_priority_hooks.handler` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響輸入 |
| `app/views/config/db_location.py:attach_prune_cleanup.drop` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響設定頁 |
| `app/views/config_view.py:ConfigView._on_unsaved_dialog_discard` | BLE001 | 已記錄／回報 | keep the dialog recoverable on reload failure |
| `app/views/config_view.py:ConfigView._retry_config_reload` | BLE001 | 已記錄／回報 | keep recovery state until a full reload succeeds |
| `app/views/dashboard_view.py:DashboardView._apply_on_ui` | BLE001 | 已記錄／回報 | 沒有 event loop（測試）就直接套用 |
| `app/views/extractor/extractor_dialog.py:_extractor_run_extraction` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/extractor/extractor_preview_dialog.py:_preview_do_scan` | BLE001 | 已記錄／回報 | 錯誤要回報到 UI |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._open_mod_detail.find_file` | BLE001 | 已記錄／回報 | UI coroutine 顯示錯誤 |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._open_mod_detail` | BLE001 | 已記錄／回報 | 排程失敗時需結束 owner 並回報 UI |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._write_zh_file` | BLE001 | 已記錄／回報 | 錯誤由呼叫端顯示在 UI |
| `app/views/icon_preview/detail_mixin.py:IconPreviewDetailMixin._load_entries` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview/load_operation.py:observe_async_owner.on_done` | BLE001 | 已記錄／回報 | Future cancellation must release reservation |
| `app/views/icon_preview/load_operation.py:_run_blocking` | BLE001 | UI／畫面保護 | drain even when blocking work fails |
| `app/views/icon_preview/load_operation.py:load_icon_preview` | BLE001 | 已記錄／回報 | 錯誤顯示在 UI |
| `app/views/icon_preview/render_operation.py:render_current_page.prepare_icons` | BLE001 | 已記錄／回報 | event loop displays failure |
| `app/views/icon_preview/render_operation.py:render_current_page` | BLE001 | 已記錄／回報 | scheduling failure must be reported |
| `app/views/icon_preview/scan_operation.py:_load_prebuilt_index` | BLE001 | 已記錄／回報 | 預建索引載入失敗時改為逐 JAR 解析，但要留下紀錄 |
| `app/views/icon_preview_row.py:_ensure_icon_size` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/icon_preview_view.py:IconPreviewView._on_load_clicked` | BLE001 | 已記錄／回報 | 排程失敗時需結束 owner 並回報 UI |
| `app/views/icon_preview_view.py:IconPreviewView._handle_cached_source_error` | BLE001 | 已記錄／回報 | 頁面關閉等排程失敗只記錄並重設旗標，下次載入會再重建 |
| `app/views/lm_view.py:LMView.refresh_key_stat` | BLE001 | UI／畫面保護 | 讀不到設定時只是不顯示 |
| `app/views/lookup_view.py:LookupView.single_lookup_worker` | BLE001 | 已記錄／回報 | 失敗也要恢復按鈕並顯示原因 |
| `app/views/lookup_view.py:LookupView.batch_lookup_worker` | BLE001 | 已記錄／回報 | 失敗也要恢復按鈕並顯示原因 |
| `app/views/merge/merge_db_options.py:MergeDbOptions._on_version_focus` | BLE001 | 已記錄／回報 | 頁面尚未掛載時略過即時更新 |
| `app/views/merge/merge_db_options.py:MergeDbOptions._on_changed` | BLE001 | 已記錄／回報 | 頁面尚未掛載時只是不即時更新提示 |
| `app/views/merge/merge_db_options.py:MergeDbOptions._on_version_changed` | BLE001 | 已記錄／回報 | 頁面尚未掛載時只是不即時更新提示 |
| `app/views/merge/merge_db_options.py:MergeDbOptions._refresh_database_state` | BLE001 | 已記錄／回報 | picker 本身也採 fail-closed |
| `app/views/merge/merge_db_options.py:MergeDbOptions._refresh_database_state` | BLE001 | 已記錄／回報 | 只影響提示文字，不應讓頁面載入失敗 |
| `app/views/merge_view.py:MergeView._broadcast_config_change_to_config_view` | BLE001 | 已記錄／回報 | 通知失敗不影響合併頁，但要留下紀錄 |
| `app/views/merge_view.py:MergeView._on_merge_field_changed` | BLE001 | 已記錄／回報 | 欄位寫入失敗不可中斷 UI，但設定沒存成功必須留下紀錄 |
| `app/views/merge_view.py:MergeView._run_merge_worker` | BLE001 | 已記錄／回報 | 背景執行緒邊界：失敗要寫進 session，否則輪詢永遠等不到結束 |
| `app/views/merge_view.py:MergeView._sync_ui_once` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/merge_view.py:MergeView._close_dialog_overlay` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/moddb/background_read.py:run_read.call` | BLE001 | UI／畫面保護 | worker 邊界：交給 apply 顯示 |
| `app/views/moddb/background_read.py:run_read` | BLE001 | 已記錄／回報 | 無法啟動：讓呼叫端恢復畫面 |
| `app/views/moddb/bulk_review_dialog.py:BulkReviewDialog._start_scope_options_load.work` | BLE001 | 已記錄／回報 | worker boundary |
| `app/views/moddb/bulk_review_dialog.py:BulkReviewDialog._preview.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/bulk_review_dialog.py:BulkReviewDialog._confirm.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/bulk_review_dialog.py:BulkReviewDialog._launch` | BLE001 | 已記錄／回報 | restore the dialog if the worker cannot launch |
| `app/views/moddb/cleanup_dialog.py:CleanupDialog._refresh_overview.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/cleanup_dialog.py:CleanupDialog._export.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/cleanup_dialog.py:CleanupDialog._preview.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/cleanup_dialog.py:CleanupDialog._apply.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/cleanup_dialog.py:CleanupDialog._revert.work` | BLE001 | 已記錄／回報 | worker boundary reports unexpected failures |
| `app/views/moddb/cleanup_dialog.py:CleanupDialog._launch` | BLE001 | 已記錄／回報 | restore the dialog if the worker cannot launch |
| `app/views/moddb/duplicate_cleanup_dialog.py:DuplicateCleanupDialog._preview.work` | BLE001 | 已記錄／回報 | worker boundary reports UI error |
| `app/views/moddb/duplicate_cleanup_dialog.py:DuplicateCleanupDialog._apply.work` | BLE001 | 已記錄／回報 | worker boundary reports UI error |
| `app/views/moddb/duplicate_cleanup_dialog.py:DuplicateCleanupDialog._revert.work` | BLE001 | 已記錄／回報 | worker boundary reports UI error |
| `app/views/moddb/duplicate_cleanup_dialog.py:DuplicateCleanupDialog._launch` | BLE001 | 已記錄／回報 | restore dialog when worker cannot launch |
| `app/views/moddb/entries_panel.py:EntriesPanel._scroll_list_to.scroll` | BLE001 | 已記錄／回報 | 尚未掛上頁面時不影響清單 |
| `app/views/moddb/entries_panel.py:EntriesPanel._scroll_list_to` | BLE001 | 已記錄／回報 | 排程失敗不影響清單 |
| `app/views/moddb/entry_selection.py:select_entry.load` | BLE001 | 已記錄／回報 | worker 邊界：回報錯誤並恢復編輯區 |
| `app/views/moddb/entry_selection.py:select_entry` | BLE001 | 已記錄／回報 | 無法啟動時恢復編輯區 |
| `app/views/moddb/entry_writes.py:run_write.call` | BLE001 | UI／畫面保護 | worker 邊界：交給 apply 顯示 |
| `app/views/moddb/entry_writes.py:run_write` | BLE001 | 已記錄／回報 | 無法啟動：讓呼叫端恢復畫面 |
| `app/views/moddb/lm_db_options.py:LmDbOptions._update` | BLE001 | 已記錄／回報 | control may be temporarily detached |
| `app/views/moddb/lm_db_options.py:LmDbOptions._refresh_database_state` | BLE001 | 已記錄／回報 | LM still permits a manual new target |
| `app/views/moddb/lm_db_options.py:LmDbOptions._refresh_database_state` | BLE001 | 已記錄／回報 | keep the page usable and expose warning |
| `app/views/moddb/repair_reviewer.py:RepairReviewer._apply_async` | BLE001 | 已記錄／回報 | keep the draft and show the worker failure |
| `app/views/moddb/repair_reviewer.py:RepairReviewer._reload_async` | BLE001 | 已記錄／回報 | keep the review as it was |
| `app/views/moddb/retranslation_controller.py:_launch_preview_query` | BLE001 | 已記錄／回報 | restore controls when the operation cannot launch |
| `app/views/moddb/retranslation_controller.py:_schedule_result` | BLE001 | 已記錄／回報 | a disposed page may reject UI work |
| `app/views/moddb/retranslation_controller.py:apply_preview_result` | BLE001 | 已記錄／回報 | unavailable database means stale result |
| `app/views/moddb/review_scope_dialog.py:ReviewScopeController.request_impact.load` | BLE001 | 已記錄／回報 | 影響預覽失敗不影響編輯 |
| `app/views/moddb/review_scope_dialog.py:ReviewScopeController.request_impact` | BLE001 | 已記錄／回報 | 無法啟動時保留上一份預覽 |
| `app/views/moddb/scan_panel.py:ScanPanel._poll` | BLE001 | 已記錄／回報 | 輪詢失敗不能讓畫面永遠卡在「執行中」 |
| `app/views/moddb/smart_review_context.py:SmartReviewContext.run_background.target` | BLE001 | 已記錄／回報 | UI reports bounded failure |
| `app/views/moddb/smart_review_context.py:SmartReviewContext.run_inline` | BLE001 | 已記錄／回報 | mirror the background boundary |
| `app/views/moddb/smart_review_job.py:SmartReviewJob._work` | BLE001 | 已記錄／回報 | job reports bounded failure |
| `app/views/moddb/smart_review_job.py:SmartReviewJob._load_breakdown` | BLE001 | 已記錄／回報 | statistics are optional |
| `app/views/moddb/smart_review_job.py:SmartReviewJob._notify_finished.deliver` | BLE001 | 已記錄／回報 | one listener must not block others |
| `app/views/moddb/species_bulk_lookup.py:safe_lookup` | BLE001 | 已記錄／回報 | worker boundary |
| `app/views/moddb/species_bulk_lookup.py:SpeciesBulkLookup._notify` | BLE001 | 已記錄／回報 | one closed view must not stop others |
| `app/views/moddb/species_bulk_lookup.py:SpeciesBulkLookup.start` | BLE001 | 已記錄／回報 | restore state after launch failure |
| `app/views/moddb/species_bulk_lookup.py:SpeciesBulkLookup._run` | BLE001 | 已記錄／回報 | admission closed while app shuts down |
| `app/views/moddb/species_bulk_lookup.py:SpeciesBulkLookup._run` | BLE001 | 已記錄／回報 | worker boundary |
| `app/views/moddb/species_dialog.py:SpeciesCandidateDialog._start_scope_options_load.work` | BLE001 | 已記錄／回報 | worker boundary |
| `app/views/moddb/species_dialog.py:SpeciesCandidateDialog._scan.work` | BLE001 | 已記錄／回報 | worker boundary |
| `app/views/moddb/species_dialog.py:SpeciesCandidateDialog._launch` | BLE001 | 已記錄／回報 | restore UI after launch failure |
| `app/views/moddb/species_suggestion.py:SpeciesSuggestion._launch` | BLE001 | 已記錄／回報 | restore UI after launch failure |
| `app/views/moddb/translate_panel.py:TranslatePanel._launch_retranslation_worker` | BLE001 | 已記錄／回報 | caller restores the UI state |
| `app/views/moddb/translate_panel.py:TranslatePanel._poll` | BLE001 | 已記錄／回報 | 輪詢失敗不能讓畫面永遠卡在「執行中」 |
| `app/views/moddb_view.py:_load_overview_snapshot` | BLE001 | 已記錄／回報 | 顯示載入失敗並恢復頁面 |
| `app/views/moddb_view.py:ModDbView._request_overview_refresh.load` | BLE001 | 已記錄／回報 | page may detach during load |
| `app/views/moddb_view.py:ModDbView._request_overview_refresh` | BLE001 | 已記錄／回報 | restore state on launch failure |
| `app/views/moddb_view.py:ModDbView._request_panel_refresh` | BLE001 | 已記錄／回報 | restore state if admission fails |
| `app/views/moddb_view.py:ModDbView._launch_panel_refresh.load` | BLE001 | 已記錄／回報 | 顯示載入錯誤並恢復頁籤 |
| `app/views/moddb_view.py:ModDbView._launch_panel_refresh.load` | BLE001 | 已記錄／回報 | page may detach during load |
| `app/views/moddb_view.py:ModDbView._mark_snapshot_generation` | BLE001 | 已記錄／回報 | 連線可能剛被關閉：視為過期，讓 UI 重新載入 |
| `app/views/pipeline/pipeline_extract_dialog.py:_extract_preview_worker` | BLE001 | 已記錄／回報 | 錯誤要顯示在對話框 |
| `app/views/pipeline/pipeline_session.py:PipelineRunner.start_sequence.worker` | BLE001 | 已記錄／回報 | 背景執行緒邊界，確保按鈕會恢復 |
| `app/views/pipeline/pipeline_session.py:PipelineRunner.run_step` | BLE001 | 已記錄／回報 | 背景步驟邊界：任何錯誤都轉成步驟失敗 |
| `app/views/qc_base.py:QCBase.task_worker.run` | BLE001 | 已記錄／回報 | 背景執行緒需把錯誤回報到 UI |
| `app/views/qc_view.py:QCView._async_pick_file_or_directory` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/qc_view.py:QCView._scroll_to_log` | BLE001 | 已記錄／回報 | 捲動失敗不影響任務 |
| `app/views/rules/rules_actions.py:perform_reload` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/rules_view.py:RulesView.on_test_change` | BLE001 | 已記錄／回報 | 規則有問題時顯示原因，不讓頁面出錯 |
| `app/views/rules_view.py:RulesView._initial_load.run` | BLE001 | 已記錄／回報 | 失敗要顯示在 UI |
| `app/views/rules_view.py:RulesView._scroll_table_to_top` | BLE001 | 已記錄／回報 | 捲動只是輔助，失敗不影響編輯 |
| `app/views/rules_view.py:RulesView._focus_new_rule` | BLE001 | 已記錄／回報 | 取得焦點只是輔助 |
| `app/views/translation/translation_actions.py:_safe_add_log` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:_safe_page_update` | BLE001/S110 | UI／畫面保護 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_ftb.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_kjs.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation/translation_actions.py:run_md.worker` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | 服務載入失敗時整頁仍可開啟，但原因必須留在 log |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | 服務載入失敗時整頁仍可開啟，但原因必須留在 log |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | 服務載入失敗時整頁仍可開啟，但原因必須留在 log |
| `app/views/translation_view.py:<module>` | BLE001 | 已記錄／回報 | 載入失敗時整頁仍可開啟，但原因必須留在 log |
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
| `translation_tool/core/jar_processor.py:_sniff_book_phase` | BLE001 | 已記錄／回報 | 補充階段失敗不影響已提取的檔案 |
| `translation_tool/core/jar_processor.py:extract_dual_files_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/jar_processor.py:extract_dual_files_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/jar_processor_extract.py:extract_from_jar_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_extract.py:run_extraction_process_impl._scan_in_background` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_extract.py:run_extraction_process_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/jar_processor_preview.py:preview_extraction_generator_impl` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm._ProgressProxy.set_progress` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm._ProgressProxy.set_status` | BLE001/S110 | 盡力而為（靜默） | UI 狀態回報失敗不可中斷翻譯 |
| `translation_tool/core/kubejs_translator.py:step2_translate_lm` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷翻譯 |
| `translation_tool/core/lang_merge_content_copy.py:_patchouli_compare` | BLE001 | 盡力而為（靜默） | 讀不出來就無從比對，維持原本流程 |
| `translation_tool/core/lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness` | BLE001 | 已記錄／回報 | 解析失敗的檔案不計入有效翻譯，已留 debug 紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:_compute_patchouli_lang_effectiveness` | BLE001 | 已記錄／回報 | 單一檔案失敗不中斷整批統計，已留 debug 紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | invalid source is quarantined, never rewritten |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | 既有 zh_tw 讀不出來時視為空白，但要留下紀錄 |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_copy.py:process_content_or_copy_file_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_content_patchers.py:patch_localized_content_json_impl` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/lang_merge_db.py:open_merge_db_fill` | BLE001 | 已記錄／回報 | DB 補譯不可讓正常合併失敗 |
| `translation_tool/core/lang_merge_extracted_assets.py:_safe_session_log` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_extracted_assets.py:_cleanup_extracted_dirs` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_extracted_assets.py:_merge_extracted_to_assets` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷批次流程 |
| `translation_tool/core/lang_merge_io.py:quarantine_copy` | BLE001 | 已記錄／回報 | 隔離副本寫入失敗不可中斷合併，但要留下紀錄 |
| `translation_tool/core/lang_merge_pending.py:export_filtered_pending_impl` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod._safe_read_lang_json` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod` | BLE001 | 已記錄／回報 | 既有輸出讀不出來時從空白重建，但要留下是哪個檔 |
| `translation_tool/core/lang_merge_pipeline.py:_process_single_mod` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merge_zip_io.py:quarantine_copy_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:_merge_zhcn_to_zhtw_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:_merge_zhcn_to_zhtw_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:_merge_zhcn_to_zhtw_from_zip` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:_merge_zhcn_to_zhtw_from_folder` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:_merge_zhcn_to_zhtw_from_folder` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lang_merger.py:_merge_zhcn_to_zhtw_from_folder` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_api_client.py:_emit_network_event` | BLE001 | 已記錄／回報 | telemetry must never break an API call |
| `translation_tool/core/lm_api_client.py:call_gemini_requests` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_batch_budget.py:_get_tiktoken_encoding` | BLE001 | 盡力而為（靜默） | optional fallback must never block translation |
| `translation_tool/core/lm_batch_budget.py:_tiktoken_count` | BLE001 | 盡力而為（靜默） | malformed provider text uses local fallback |
| `translation_tool/core/lm_resume.py:check_resume_feasibility` | BLE001 | 已記錄／回報 | 檢查失敗要回報給使用者，不能讓啟動流程中斷 |
| `translation_tool/core/lm_translator.py:load_checkpoint` | BLE001 | 已記錄／回報 | 損毀的 checkpoint 視為沒有，但要留下紀錄 |
| `translation_tool/core/lm_translator.py:_scan_directory_files` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_emit_runtime_event` | BLE001 | 已記錄／回報 | telemetry must never break translation |
| `translation_tool/core/lm_translator_main.py:_payload_error_allows_shrink` | BLE001 | 已記錄／回報 | 判斷本身不能遮蔽原始錯誤 |
| `translation_tool/core/lm_translator_main.py:_remote_error_detail` | BLE001 | 已記錄／回報 | 回應不是 JSON 時改用原始文字 |
| `translation_tool/core/lm_translator_main.py:_handle_service_unavailable_error` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_handle_batch_error` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/core/lm_translator_main.py:_retry_same_source_translations` | BLE001 | 已記錄／回報 | optional quality retry must not fail the valid batch |
| `translation_tool/core/lm_translator_main.py:_retry_quality_translations` | BLE001 | 已記錄／回報 | optional quality retry must not fail a valid batch |
| `translation_tool/core/lm_translator_main.py:_retry_quality_translations` | BLE001 | 已記錄／回報 | health is best effort |
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
| `translation_tool/core/md_translation_stats.py:count_md_pending_docs` | BLE001 | 已記錄／回報 | 統計僅計入可解析的待翻譯檔，壞檔略過但要記錄是哪個檔 |
| `translation_tool/core/md_translation_steps.py:step3_inject_impl` | BLE001 | 已記錄／回報 | 單一待注入檔讀取失敗：計入錯誤並繼續，但要留下是哪個檔 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/output_bundler.py:bundle_outputs_generator` | BLE001 | 已記錄／回報 | 錯誤已記錄或回報給呼叫端，不中斷整批流程 |
| `translation_tool/core/patchouli_semantics.py:_configured_book_dirs` | BLE001 | 已記錄／回報 | 上下文解析失敗時仍採用預設目錄 |
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
| `translation_tool/plugins/md/md_inject_qa.py:main` | BLE001 | 已記錄／回報 | 單一 JSON 損壞時略過該檔，繼續處理其餘檔案 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_translated_item` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending.on_batch_flushed` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/plugins/md/md_lmtranslator.py:translate_md_pending` | BLE001 | 已記錄／回報 | 失敗已記錄，不中斷翻譯批次 |
| `translation_tool/translation_db/identity.py:patchouli_dir_names` | BLE001 | 已記錄／回報 | 設定不可用時退回預設，不影響身分計算 |
| `translation_tool/translation_db/scan_pool.py:scan_executor` | BLE001 | 已記錄／回報 | 行程池無法啟動（打包、權限等）時退回執行緒 |
| `translation_tool/translation_db/scanner.py:load_rules` | BLE001 | 已記錄／回報 | 規則檔問題不應讓掃描失敗，只是略過替換 |
| `translation_tool/translation_db/settings.py:remember_db_path` | BLE001 | 已記錄／回報 | 寫設定失敗不應中斷建立資料庫 |
| `translation_tool/translation_db/settings.py:open_db` | BLE001 | 已記錄／回報 | 資料庫問題不應中斷翻譯 |
| `translation_tool/translation_db/smart_review_apply.py:SmartReviewApplyMixin.apply_smart_review_candidate_batch` | BLE001 | 已記錄／回報 | preserve partial batch success |
| `translation_tool/translation_db/smart_review_apply.py:SmartReviewApplyMixin._apply_smart_review_candidate_chunk` | BLE001 | 已記錄／回報 | report a failed transaction chunk |
| `translation_tool/translation_db/smart_review_apply.py:SmartReviewApplyMixin._apply_smart_review_candidate_savepoint` | BLE001 | 已記錄／回報 | isolate one candidate with SAVEPOINT |
| `translation_tool/translation_db/smart_review_repair.py:_ValidationRepair.call_provider` | BLE001 | 已記錄／回報 | safe repair event |
| `translation_tool/translation_db/smart_review_repair.py:_ValidationRepair.parse` | BLE001 | 已記錄／回報 | strict parser |
| `translation_tool/translation_db/smart_review_request.py:_BatchRequester._attempt` | BLE001 | 已記錄／回報 | credential-free class |
| `translation_tool/translation_db/smart_review_run.py:_SmartReviewBatchRunner._parse_response` | BLE001 | 已記錄／回報 | response contract class |
| `translation_tool/utils/cache_overview.py:build_cache_overview` | BLE001 | 已記錄／回報 | 讀取失敗不中斷總覽，但要留下紀錄 |
| `translation_tool/utils/cache_overview.py:build_cache_overview` | BLE001 | 已記錄／回報 | 讀取失敗不中斷總覽，但要留下紀錄 |
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
| `translation_tool/utils/jar_browser.py:_scan_single_jar_detailed` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/log_unit.py:_get_caller_logger_name` | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），退回預設 logger 名稱 |
| `translation_tool/utils/log_unit.py:_log` | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），也不可中斷呼叫端 |
| `translation_tool/utils/log_unit.py:log_exception` | BLE001/S110 | 盡力而為（靜默） | logging 自身失敗不可再記錄（會遞迴），也不可中斷呼叫端 |
| `translation_tool/utils/log_unit.py:progress` | BLE001/S110 | 盡力而為（靜默） | UI 進度回報失敗不可中斷任務 |
| `translation_tool/utils/safe_json_loader.py:load_json_auto_encoding` | BLE001/S112 | 盡力而為（靜默） | 逐一嘗試編碼；全部失敗才回傳 None |
| `translation_tool/utils/species_cache.py:_convert_wikipedia_title` | BLE001 | 已記錄／回報 | 轉換失敗時仍可使用原始條目標題 |
| `translation_tool/utils/species_cache.py:_fetch_and_cache` | BLE001 | 已記錄／回報 | 外部 API 與回應解析錯誤需分類回報 |
| `translation_tool/utils/text_processor.py:_load_rules_uncached` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:ReplaceRulesStore._notify` | BLE001 | 已記錄／回報 | 訂閱者失敗不可阻斷規則寫入 |
| `translation_tool/utils/text_processor.py:load_custom_translations` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/text_processor.py:convert_snbt_file_inplace` | BLE001 | 已記錄／回報 | （未寫原因；見分類） |
| `translation_tool/utils/ui_logging_handler.py:UISessionLogHandler.emit` | BLE001/S110 | 盡力而為（靜默） | 在 handler 內記錄錯誤會遞迴 |
| `translation_tool/utils/ui_mirror.py:_BackendSeenTracker.emit` | BLE001 | 盡力而為（靜默） | handler 內不可再丟例外 |
| `translation_tool/utils/ui_mirror.py:mirror_to_backend` | BLE001 | 盡力而為（靜默） | 鏡像失敗不可影響 UI 或任務本身 |
