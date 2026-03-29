#!/usr/bin/env python3
"""更新後的覆蓋分析"""

# 已覆蓋的模組（加上新增的測試）
covered = {
    # 現有測試
    "translation_tool.utils.cache_manager",
    "translation_tool.utils.cache_shards",
    "translation_tool.utils.cache_store",
    "translation_tool.utils.cache_search",
    "translation_tool.utils.cache_search_facade",
    "translation_tool.core.jar_processor",
    "translation_tool.core.jar_processor_extract",
    "translation_tool.core.jar_processor_discovery",
    "translation_tool.core.jar_processor_preview",
    "translation_tool.core.kubejs_translator",
    "translation_tool.core.kubejs_translator_clean",
    "translation_tool.core.kubejs_translator_io",
    "translation_tool.core.kubejs_translator_paths",
    "translation_tool.core.lang_merge_content",
    "translation_tool.core.lang_merge_content_patchers",
    "translation_tool.core.lang_merge_pending",
    "translation_tool.core.lang_merge_pipeline",
    "translation_tool.core.lang_merge_zip_io",
    "translation_tool.core.lang_merger",
    "translation_tool.core.lm_translator",
    "translation_tool.core.lm_translator_main",
    "translation_tool.core.lm_translator_scan",
    "translation_tool.core.lm_translator_shared",
    "translation_tool.core.lm_translator_shared_cache",
    "translation_tool.core.lm_translator_shared_loop",
    "translation_tool.core.lm_translator_shared_preview",
    "translation_tool.core.lm_translator_shared_recording",
    "translation_tool.core.md_translation_assembly",
    "translation_tool.core.md_translation_progress",
    "translation_tool.core.md_translation_stats",
    "translation_tool.core.md_translation_steps",
    "translation_tool.core.ftb_translator",
    "translation_tool.core.ftb_translator_clean",
    "translation_tool.core.ftb_translator_export",
    "translation_tool.plugins.shared",
    "translation_tool.checkers.untranslated_checker",
    "translation_tool.utils.text_processor",
    "translation_tool.utils.config_manager",
    "app.startup_tasks",
    "app.view_registry",
    "translation_tool.core.icon_classifier",
    "translation_tool.core.icon_preview_cache",
    "translation_tool.core.icon_reason",
    "translation_tool.core.icon_resolver",
    "translation_tool.core.output_bundler",
    # 新增：high priority
    "translation_tool.core.lm_api_client",
    "translation_tool.core.lm_response_parser",
    "translation_tool.core.lang_codec",
    "translation_tool.core.lang_item_row",
    "translation_tool.core.lang_processing_format",
    "translation_tool.core.translatable_extractor",
    "translation_tool.core.translation_path_writer",
    # 新增：medium priority
    "translation_tool.checkers.english_residue_checker",
    "translation_tool.checkers.variant_comparator",
    "translation_tool.checkers.variant_comparator_tsv",
    "translation_tool.plugins.ftbquests",
    "translation_tool.plugins.kubejs",
    "translation_tool.plugins.md",
    "translation_tool.utils.cache_loader",
    "translation_tool.utils.cache_overview",
    "translation_tool.utils.config_access",
}

# 仍然缺少的模組
still_missing = {
    "app.services": "⚠️ Deprecated，需移除或重構",
    "app.services_impl": "實際服務實現",
    "app.task_session": "任務會話管理",
    "app.ui": "✅ 已有 test_ui_components.py",
    "translation_tool.utils.exceptions": "✅ 已有間接覆蓋",
    "translation_tool.utils.log_unit": "日誌單元",
    "translation_tool.utils.safe_json_loader": "安全 JSON 載入器",
    "translation_tool.utils.species_cache": "學名快取",
    "translation_tool.utils.ui_logging_handler": "UI 日誌處理器",
}

print("=" * 60)
print("✅ 已覆蓋模組數量：", len(covered))
print("❌ 仍缺少測試的模組數量：", len(still_missing))
print("=" * 60)

print("\n❌ 仍缺少測試的模組：")
for mod, desc in sorted(still_missing.items()):
    print(f"  • {mod}")
    print(f"    {desc}")
