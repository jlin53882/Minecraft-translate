#!/usr/bin/env python3
"""精確分析測試覆蓋"""

import os

# 現有測試檔案（從 tests/ 目錄）
test_files = [
    "test_bundler_view_characterization.py",
    "test_cache_controller.py",
    "test_cache_history_store.py",
    "test_cache_manager_api_surface.py",
    "test_cache_presenter.py",
    "test_cache_search_orchestration.py",
    "test_cache_shards.py",
    "test_cache_state.py",
    "test_cache_store.py",
    "test_cache_view_features.py",
    "test_cache_view_monkeypatch_integration.py",
    "test_cache_view_state_gate.py",
    "test_config_proxy_compat.py",
    "test_config_view_characterization.py",
    "test_extractor_view_characterization.py",
    "test_ftb_pipeline_smoke.py",
    "test_ftb_translator_clean.py",
    "test_ftb_translator_export.py",
    "test_icon_preview_view_characterization.py",
    "test_jar_preview_report.py",
    "test_jar_processor_extract.py",
    "test_jar_processor_find.py",
    "test_kubejs_cleaning.py",
    "test_kubejs_path_resolution.py",
    "test_kubejs_pipeline_steps.py",
    "test_lang_merger_guards.py",
    "test_lang_merger_zip_baseline.py",
    "test_lang_merge_content_patchers.py",
    "test_lang_merge_pending_export.py",
    "test_lm_translator_cache_split.py",
    "test_lm_translator_dry_run.py",
    "test_lm_translator_main_guards.py",
    "test_lm_translator_output_writeback.py",
    "test_lm_translator_shared_preview.py",
    "test_lm_translator_shared_recording.py",
    "test_lm_view_characterization.py",
    "test_lookup_view_characterization.py",
    "test_main_imports.py",
    "test_md_pipeline_steps.py",
    "test_md_progress_proxy.py",
    "test_merge_view_characterization.py",
    "test_path_resolution.py",
    "test_pipeline_logging_bootstrap.py",
    "test_pipeline_services_error_handling.py",
    "test_pipeline_services_session_lifecycle.py",
    "test_plugins_shared_helpers.py",
    "test_plugins_shared_json_io.py",
    "test_plugins_shared_lang_rules.py",
    "test_pr1_to_pr6.py",
    "test_qc_services_facade.py",
    "test_qc_view_characterization.py",
    "test_rules_view_characterization.py",
    "test_startup_tasks.py",
    "test_text_processor_config_resolution.py",
    "test_translation_view_characterization.py",
    "test_ui_components.py",
    "test_ui_refactor_guard.py",
    "test_view_registry.py",
    "test_view_wrapper.py",
]

# 映射：測試關鍵字 -> 實際覆蓋的模組
test_coverage = {
    "cache_": [
        "translation_tool.utils.cache_manager",
        "translation_tool.utils.cache_shards",
        "translation_tool.utils.cache_store",
        "translation_tool.utils.cache_search",
        "translation_tool.utils.cache_search_facade",
    ],
    "jar_processor": [
        "translation_tool.core.jar_processor",
        "translation_tool.core.jar_processor_extract",
        "translation_tool.core.jar_processor_discovery",
        "translation_tool.core.jar_processor_preview",
    ],
    "kubejs": [
        "translation_tool.core.kubejs_translator",
        "translation_tool.core.kubejs_translator_clean",
        "translation_tool.core.kubejs_translator_io",
        "translation_tool.core.kubejs_translator_paths",
    ],
    "lang_merge": [
        "translation_tool.core.lang_merge_content",
        "translation_tool.core.lang_merge_content_copy",
        "translation_tool.core.lang_merge_content_patchers",
        "translation_tool.core.lang_merge_pending",
        "translation_tool.core.lang_merge_pipeline",
        "translation_tool.core.lang_merge_zip_io",
        "translation_tool.core.lang_merger",
    ],
    "lm_translator": [
        "translation_tool.core.lm_translator",
        "translation_tool.core.lm_translator_main",
        "translation_tool.core.lm_translator_scan",
        "translation_tool.core.lm_translator_shared",
        "translation_tool.core.lm_translator_shared_cache",
        "translation_tool.core.lm_translator_shared_loop",
        "translation_tool.core.lm_translator_shared_preview",
        "translation_tool.core.lm_translator_shared_recording",
    ],
    "md_": [
        "translation_tool.core.md_translation_assembly",
        "translation_tool.core.md_translation_progress",
        "translation_tool.core.md_translation_stats",
        "translation_tool.core.md_translation_steps",
    ],
    "plugins_shared": [
        "translation_tool.plugins.shared",
    ],
    "qc_": [
        "translation_tool.checkers.untranslated_checker",
    ],
    "text_processor": [
        "translation_tool.utils.text_processor",
    ],
    "startup": [
        "app.startup_tasks",
    ],
    "view_registry": [
        "app.view_registry",
    ],
    "config": [
        "translation_tool.utils.config_manager",
    ],
    "ftb_translator": [
        "translation_tool.core.ftb_translator",
        "translation_tool.core.ftb_translator_clean",
        "translation_tool.core.ftb_translator_export",
        "translation_tool.core.ftb_translator_template",
    ],
    "output_bundler": [
        "translation_tool.core.output_bundler",
    ],
    "icon_": [
        "translation_tool.core.icon_classifier",
        "translation_tool.core.icon_preview_cache",
        "translation_tool.core.icon_reason",
        "translation_tool.core.icon_resolver",
    ],
}

# 所有功能模組（排除 views）
all_modules = {
    "app": [
        "app.services",
        "app.services_impl",
        "app.startup_tasks",
        "app.task_session",
        "app.ui",
        "app.view_registry",
    ],
    "translation_tool.checkers": [
        "translation_tool.checkers",
        "translation_tool.checkers.english_residue_checker",
        "translation_tool.checkers.untranslated_checker",
        "translation_tool.checkers.variant_comparator",
        "translation_tool.checkers.variant_comparator_tsv",
    ],
    "translation_tool.core": [
        "translation_tool.core",
        "translation_tool.core.ftb_translator",
        "translation_tool.core.ftb_translator_clean",
        "translation_tool.core.ftb_translator_export",
        "translation_tool.core.ftb_translator_template",
        "translation_tool.core.icon_classifier",
        "translation_tool.core.icon_preview_cache",
        "translation_tool.core.icon_reason",
        "translation_tool.core.icon_resolver",
        "translation_tool.core.jar_processor",
        "translation_tool.core.jar_processor_discovery",
        "translation_tool.core.jar_processor_extract",
        "translation_tool.core.jar_processor_preview",
        "translation_tool.core.kubejs_translator",
        "translation_tool.core.kubejs_translator_clean",
        "translation_tool.core.kubejs_translator_io",
        "translation_tool.core.kubejs_translator_paths",
        "translation_tool.core.lang_codec",
        "translation_tool.core.lang_item_row",
        "translation_tool.core.lang_merge_content",
        "translation_tool.core.lang_merge_content_copy",
        "translation_tool.core.lang_merge_content_patchers",
        "translation_tool.core.lang_merge_pending",
        "translation_tool.core.lang_merge_pipeline",
        "translation_tool.core.lang_merge_zip_io",
        "translation_tool.core.lang_merger",
        "translation_tool.core.lang_processing_format",
        "translation_tool.core.lm_api_client",
        "translation_tool.core.lm_config_rules",
        "translation_tool.core.lm_response_parser",
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
        "translation_tool.core.output_bundler",
        "translation_tool.core.translatable_extractor",
        "translation_tool.core.translation_path_writer",
    ],
    "translation_tool.plugins": [
        "translation_tool.plugins",
        "translation_tool.plugins.ftbquests",
        "translation_tool.plugins.kubejs",
        "translation_tool.plugins.md",
        "translation_tool.plugins.shared",
    ],
    "translation_tool.utils": [
        "translation_tool.utils",
        "translation_tool.utils.cache_loader",
        "translation_tool.utils.cache_manager",
        "translation_tool.utils.cache_overview",
        "translation_tool.utils.cache_search",
        "translation_tool.utils.cache_search_facade",
        "translation_tool.utils.cache_shards",
        "translation_tool.utils.cache_store",
        "translation_tool.utils.config_access",
        "translation_tool.utils.config_manager",
        "translation_tool.utils.exceptions",
        "translation_tool.utils.log_unit",
        "translation_tool.utils.safe_json_loader",
        "translation_tool.utils.species_cache",
        "translation_tool.utils.text_processor",
        "translation_tool.utils.ui_logging_handler",
    ],
}

# 計算覆蓋
covered_modules = set()
for test_file in test_files:
    base = test_file.replace("test_", "").replace(".py", "")
    for key, mods in test_coverage.items():
        if key in base:
            covered_modules.update(mods)

# 列出所有模組
all_mods = []
for category, mods in all_modules.items():
    all_mods.extend(mods)

# 找出未覆蓋的
missing = sorted(set(all_mods) - covered_modules)

print("=" * 60)
print("已覆蓋的模組")
print("=" * 60)
for m in sorted(covered_modules):
    print(f"  ✅ {m}")

print("\n" + "=" * 60)
print("未覆蓋的模組（缺少測試）")
print("=" * 60)
for m in missing:
    print(f"  ❌ {m}")
