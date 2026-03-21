#!/usr/bin/env python3
"""更精確的分析 - 只列出真正需要測試的功能模組"""

# 現有測試覆蓋的模組
covered = {
    # cache 系列
    "translation_tool.utils.cache_manager",
    "translation_tool.utils.cache_shards",
    "translation_tool.utils.cache_store",
    "translation_tool.utils.cache_search",
    "translation_tool.utils.cache_search_facade",
    # jar processor
    "translation_tool.core.jar_processor",
    "translation_tool.core.jar_processor_extract",
    "translation_tool.core.jar_processor_discovery",
    "translation_tool.core.jar_processor_preview",
    # kubejs
    "translation_tool.core.kubejs_translator",
    "translation_tool.core.kubejs_translator_clean",
    "translation_tool.core.kubejs_translator_io",
    "translation_tool.core.kubejs_translator_paths",
    # lang merge
    "translation_tool.core.lang_merge_content",
    "translation_tool.core.lang_merge_content_patchers",
    "translation_tool.core.lang_merge_pending",
    "translation_tool.core.lang_merge_pipeline",
    "translation_tool.core.lang_merge_zip_io",
    "translation_tool.core.lang_merger",
    # lm translator
    "translation_tool.core.lm_translator",
    "translation_tool.core.lm_translator_main",
    "translation_tool.core.lm_translator_scan",
    "translation_tool.core.lm_translator_shared",
    "translation_tool.core.lm_translator_shared_cache",
    "translation_tool.core.lm_translator_shared_loop",
    "translation_tool.core.lm_translator_shared_preview",
    "translation_tool.core.lm_translator_shared_recording",
    # md translation
    "translation_tool.core.md_translation_assembly",
    "translation_tool.core.md_translation_progress",
    "translation_tool.core.md_translation_stats",
    "translation_tool.core.md_translation_steps",
    # ftb
    "translation_tool.core.ftb_translator",
    "translation_tool.core.ftb_translator_clean",
    "translation_tool.core.ftb_translator_export",
    # plugins
    "translation_tool.plugins.shared",
    # checkers
    "translation_tool.checkers.untranslated_checker",
    # utils
    "translation_tool.utils.text_processor",
    "translation_tool.utils.config_manager",
    # app
    "app.startup_tasks",
    "app.view_registry",
    # icon
    "translation_tool.core.icon_classifier",
    "translation_tool.core.icon_preview_cache",
    "translation_tool.core.icon_reason",
    "translation_tool.core.icon_resolver",
    # bundler
    "translation_tool.core.output_bundler",
}

# 真正需要測試的功能模組（排除命名空間包）
needed_modules = {
    # app - 這三個是功能模組
    "app.services": "F/S facade interface - 已deprecated，需確認是否移除或重構",
    "app.services_impl": "實際服務實現 - 需檢查是否已有足夠覆蓋",
    "app.task_session": "任務會話管理 - 需要測試",
    "app.ui": "UI 元件 - 已有 test_ui_components.py 和 test_pr1_to_pr6.py 覆蓋",
    
    # translation_tool.checkers
    "translation_tool.checkers.english_residue_checker": "英文殘留檢查器 - 需要測試",
    "translation_tool.checkers.variant_comparator": "變體比較器 - 需要測試",
    "translation_tool.checkers.variant_comparator_tsv": "TSV 變體比較器 - 需要測試",
    
    # translation_tool.core - 這些是核心翻譯功能
    "translation_tool.core.lang_codec": "語言編碼處理 - 需要測試",
    "translation_tool.core.lang_item_row": "語言項目行處理 - 需要測試",
    "translation_tool.core.lang_processing_format": "處理格式定義 - 可能是 dataclass，需要測試",
    "translation_tool.core.lm_api_client": "LM API 客戶端 - 需要測試",
    "translation_tool.core.lm_config_rules": "LM 配置規則 - 需要測試",
    "translation_tool.core.lm_response_parser": "LM 回應解析器 - 需要測試",
    "translation_tool.core.translatable_extractor": "可翻譯項目提取器 - 需要測試",
    "translation_tool.core.translation_path_writer": "翻譯路徑寫入器 - 需要測試",
    
    # translation_tool.plugins
    "translation_tool.plugins.ftbquests": "FTB Quests 插件 - 需要測試",
    "translation_tool.plugins.kubejs": "KubeJS 插件 - 需要測試",
    "translation_tool.plugins.md": "MD 插件 - 需要測試",
    
    # translation_tool.utils
    "translation_tool.utils.cache_loader": "快取載入器 - 需要測試",
    "translation_tool.utils.cache_overview": "快取總覽 - 需要測試",
    "translation_tool.utils.config_access": "配置訪問 - 需要測試",
    "translation_tool.utils.exceptions": "異常定義 - 已有 test_qc_services_facade.py 間接覆蓋",
    "translation_tool.utils.log_unit": "日誌單元 - 需要測試",
    "translation_tool.utils.safe_json_loader": "安全 JSON 載入器 - 需要測試",
    "translation_tool.utils.species_cache": "學名快取 - 需要測試",
    "translation_tool.utils.ui_logging_handler": "UI 日誌處理器 - 需要測試",
}

# 計算缺口
missing = {}
for mod, desc in needed_modules.items():
    if mod not in covered:
        missing[mod] = desc

print("=" * 70)
print("缺少測試的功能模組清單")
print("=" * 70)

# 分類輸出
categories = {
    "app": [],
    "translation_tool.checkers": [],
    "translation_tool.core": [],
    "translation_tool.plugins": [],
    "translation_tool.utils": [],
}

for mod, desc in missing.items():
    for cat in categories:
        if mod.startswith(cat):
            categories[cat].append((mod, desc))
            break

for cat, items in categories.items():
    if items:
        print(f"\n【{cat}】")
        for mod, desc in items:
            print(f"  ❌ {mod}")
            print(f"     → {desc}")

print("\n" + "=" * 70)
print(f"總計：{len(missing)} 個功能模組缺少測試")
print("=" * 70)
