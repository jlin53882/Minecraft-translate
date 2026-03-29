#!/usr/bin/env python3
"""全面分析單元測試覆蓋"""

import os

# translation_tool/core 所有模組
core_modules = [
    "ftb_translator",
    "ftb_translator_clean", 
    "ftb_translator_export",
    "ftb_translator_template",
    "icon_classifier",
    "icon_preview_cache",
    "icon_reason",
    "icon_resolver",
    "jar_processor",
    "jar_processor_discovery",
    "jar_processor_extract",
    "jar_processor_preview",
    "kubejs_translator",
    "kubejs_translator_clean",
    "kubejs_translator_io",
    "kubejs_translator_paths",
    "lang_codec",
    "lang_item_row",
    "lang_merger",
    "lang_merge_content",
    "lang_merge_content_copy",
    "lang_merge_content_patchers",
    "lang_merge_pending",
    "lang_merge_pipeline",
    "lang_merge_zip_io",
    "lang_processing_format",
    "lm_api_client",
    "lm_config_rules",
    "lm_response_parser",
    "lm_translator",
    "lm_translator_main",
    "lm_translator_scan",
    "lm_translator_shared",
    "lm_translator_shared_cache",
    "lm_translator_shared_loop",
    "lm_translator_shared_preview",
    "lm_translator_shared_recording",
    "md_translation_assembly",
    "md_translation_progress",
    "md_translation_stats",
    "md_translation_steps",
    "output_bundler",
    "translatable_extractor",
    "translation_path_writer",
]

# 翻譯工具 utils
utils_modules = [
    "cache_loader",
    "cache_manager",
    "cache_overview",
    "cache_search",
    "cache_search_facade",
    "cache_shards",
    "cache_store",
    "config_access",
    "config_manager",
    "exceptions",
    "log_unit",
    "safe_json_loader",
    "species_cache",
    "text_processor",
    "ui_logging_handler",
]

# plugins
plugins_modules = [
    "ftbquests",
    "kubejs",
    "md",
    "shared",
]

# checkers
checkers_modules = [
    "english_residue_checker",
    "untranslated_checker",
    "variant_comparator",
    "variant_comparator_tsv",
]

# app
app_modules = [
    "services",
    "services_impl",
    "startup_tasks",
    "task_session",
    "ui",
    "view_registry",
]

# 讀取測試檔案名稱
test_dir = "tests"
test_files = []
if os.path.exists(test_dir):
    test_files = [f.replace("test_", "").replace(".py", "") for f in os.listdir(test_dir) if f.startswith("test_")]

# 建立覆蓋表
covered = set()
for t in test_files:
    # 簡單比對
    for m in core_modules + utils_modules + plugins_modules + checkers_modules + app_modules:
        if t.replace("_", "").replace(".", "").startswith(m.replace("_", "").replace(".", "")) or m.replace("_", "").startswith(t.replace("_", "").replace(".", "")):
            covered.add(m)

print("=" * 70)
print("翻譯工具核心模組測試覆蓋情況")
print("=" * 70)

all_modules = core_modules + utils_modules + plugins_modules + checkers_modules + app_modules

covered_modules = sorted([m for m in all_modules if m in covered])
missing_modules = sorted([m for m in all_modules if m not in covered])

print(f"\n✅ 已覆蓋 ({len(covered_modules)}):")
for m in covered_modules:
    print(f"  • {m}")

print(f"\n❌ 缺少測試 ({len(missing_modules)}):")
for m in missing_modules:
    print(f"  • {m}")
