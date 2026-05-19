from __future__ import annotations

import json
import traceback
from pathlib import Path
from translation_tool.utils.config_manager import load_config as load_config_default


EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.example.json"

# ============================================================================
# Default values (mirrored from config.example.json)
# ============================================================================

DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_DIR = "logs"
DEFAULT_OUTPUT_DIR_NAME = "zh_tw_generated"
DEFAULT_FTB_OUTPUT_DIR = "FTB任務翻譯輸出"
DEFAULT_REPLACE_RULES_PATH = "replace_rules.json"
DEFAULT_CACHE_DIRECTORY = "快取資料"
DEFAULT_PARALLEL_WORKERS = 4
DEFAULT_SPECIES_CACHE_DIR = "學名資料庫"
DEFAULT_SPECIES_CACHE_FILENAME = "species_cache.tsv"
DEFAULT_WIKIPEDIA_LANGUAGE = "zh"
DEFAULT_WIKIPEDIA_RATE_LIMIT_DELAY = 0.5
DEFAULT_LM_TEMPERATURE = 0.3
DEFAULT_LM_TRANSLATE_FOLDER = "LM翻譯後"
DEFAULT_BATCH_PATCHOULI = 100
DEFAULT_BATCH_LANG = 300
DEFAULT_BATCH_FTB = 200
DEFAULT_BATCH_KUBEJS = 200
DEFAULT_BATCH_MD = 100
DEFAULT_MIN_BATCH_SIZE = 50
DEFAULT_BATCH_SHRINK_FACTOR = 0.5
DEFAULT_RATE_LIMIT_TIMEOUT = 600
DEFAULT_RATE_LIMIT_SLEEP = 0.0
DEFAULT_OUTPUT_ZIP_NAME = "可使用翻譯.zip"
DEFAULT_PENDING_FOLDER = "待翻譯"
DEFAULT_ORGANIZED_FOLDER = "待翻譯整理需翻譯"
DEFAULT_FILTERED_MIN_COUNT = 3
DEFAULT_QUARANTINE_FOLDER = "問題檔案skipped_json"

DEFAULT_PATCHOULI_SYSTEM_PROMPT = (
    "你是專業的 Minecraft Patchouli 手冊翻譯員。\r\n\r\n"
    "你正在翻譯一個「ID → Value 對照表」。\r\n\r\n"
    "⚠️【極重要規則 — ID 不可變】⚠️\r\n"
    "- items[].id 是不可變的識別符號\r\n"
    "- id 不具有任何語意，也不對應任何 JSON 結構\r\n"
    "- id 只能被視為純文字索引\r\n"
    "- 絕對禁止：\r\n"
    "  - 修改、重寫、補零、轉型、排序、重編任何 id\r\n"
    "  - 新增或刪除任何 id\r\n"
    "  - 嘗試推測 id 與內容的關聯\r\n\r\n"
    "📌 任務規則：\r\n"
    "1. 只允許修改 items[].value 的字串內容\r\n"
    "2. items[].id 必須與輸入完全一字不差\r\n"
    "3. items 的數量與順序必須與輸入完全一致\r\n"
    "4. 如果你不確定如何翻譯，請原樣回傳 value\r\n"
    "5. 回傳必須是合法 JSON，且格式與輸入完全一致\r\n"
    "6. 僅翻譯為繁體中文（台灣用語）\r\n"
    "7. 保留 §, %, {}, $(...) 等所有符號與格式\r\n"
    "8. 單位（mb、tick 等）請保留原文\r\n"
    "9. Minecraft 請保持原文，不要翻譯成「當個創世神」\r\n"
    "10. 每一筆 value 必須只根據該筆原文自身內容翻譯\r\n"
    "11. 只要 value 包含人類語言就必須翻譯\r\n"
    "12. 學名請翻譯為台灣常用語（如 Creeper → 苦力怕）,(Spawn Egg-> 生怪蛋),(cobblestone->鵝卵石）"
)

DEFAULT_LANG_SYSTEM_PROMPT = (
    "你正在翻譯 Minecraft 語言檔案（JSON 格式）。\r\n\r\n"
    "你收到的是一個「ID → value 對照表」。\r\n\r\n"
    "⚠️【極重要規則 — ID 不可變】⚠️\r\n"
    "- items[].id 是唯一識別符號\r\n"
    "- id 不具有任何語意\r\n"
    "- 絕對禁止：\r\n"
    "  - 修改、轉型、補零、重排、推測或重寫任何 id\r\n"
    "  - 新增或刪除任何 item\r\n\r\n"
    "📌 任務規則：\r\n"
    "1. 只允許修改 items[].value 的字串內容\r\n"
    "2. items[].id 必須與輸入完全一字不差\r\n"
    "3. items 的數量與順序必須與輸入完全一致\r\n"
    "4. 如果你不確定如何翻譯，請原樣回傳 value\r\n"
    "5. 回傳必須是合法 JSON，格式必須為 {\"items\":[{\"id\":...,\"value\":...}, ...]}\r\n"
    "6. 僅翻譯為繁體中文（台灣用語）\r\n"
    "7. 保留 §, %, {}, $(...) 等所有符號與格式\r\n"
    "8. 單位（mb、tick 等）請保留原文\r\n"
    "9. Minecraft 請保持原文\r\n"
    "10. 每一筆 value 只依該筆原文翻譯\r\n"
    "11. 只要 value 包含人類語言就必須翻譯\r\n"
    "12. 學名請翻譯為台灣常用語（如 Creeper → 苦力怕）,(Spawn Egg-> 生怪蛋),(cobblestone->鵝卵石）"
)

DEFAULT_SKIP_TERMS = [
    "api documentation", "api docs", "documentation", "discord", "github",
    "homepage", "mod page", "modpack", "official website", "patreon",
    "Twitter", "Modrinth", "CurseForge", "Crowdin", "Twitch", "Wiki",
    "Minecraft", "Forge", "YouTube", "Reddit", "Ko-fi", "Flattr",
]

DEFAULT_TRANSLATABLE_KEYWORDS = [
    "text", "name", "title", "description", "subtitle", "hover", "note",
    "warning", "quote", "paragraph", "body", "header", "footer", "heading",
    "effects", "category", "link_text", "pages.title",
]

DEFAULT_PATHOULI_DIR_NAMES = ["patchouli_books", "book", "manual", "guidebook"]

DEFAULT_LANG_EXTRACT = "_提取lang_輸出"
DEFAULT_BOOK_EXTRACT = "_提取book_輸出"
DEFAULT_LANG_PREVIEW = "_預覽lang_輸出"
DEFAULT_BOOK_PREVIEW = "_預覽book_輸出"
DEFAULT_DUAL_EXTRACT = "_提取both_輸出"
DEFAULT_DUAL_PREVIEW = "_預覽both_輸出"


def _load_example_config() -> dict:
    """載入 config.example.json 當作 fallback 預設值。"""
    if EXAMPLE_CONFIG_PATH.exists():
        with EXAMPLE_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_config_into_view(view, config: dict):
    """將 config 字典中的值填入 view 的各個 UI 控制項。"""
    ex = _load_example_config()

    view.controls_map['logging.log_level'].value = config.get('logging', {}).get('log_level') or ex.get('logging', {}).get('log_level') or DEFAULT_LOG_LEVEL
    view.controls_map['logging.log_dir'].value = config.get('logging', {}).get('log_dir') or ex.get('logging', {}).get('log_dir') or DEFAULT_LOG_DIR
    view.controls_map['translator.output_dir_name'].value = config.get('translator', {}).get('output_dir_name') or ex.get('translator', {}).get('output_dir_name') or DEFAULT_OUTPUT_DIR_NAME
    view.controls_map['ftb_translator.output_dir_name'].value = config.get('ftb_translator', {}).get('output_dir_name') or ex.get('ftb_translator', {}).get('output_dir_name') or DEFAULT_FTB_OUTPUT_DIR
    view.controls_map['translator.replace_rules_path'].value = config.get('translator', {}).get('replace_rules_path') or ex.get('translator', {}).get('replace_rules_path') or DEFAULT_REPLACE_RULES_PATH
    view.controls_map['translator.cache_directory'].value = config.get('translator', {}).get('cache_directory') or ex.get('translator', {}).get('cache_directory') or DEFAULT_CACHE_DIRECTORY
    view.controls_map['translator.enable_cache_saving'].value = config.get('translator', {}).get('enable_cache_saving')
    view.controls_map['translator.parallel_execution_workers'].value = str(config.get('translator', {}).get('parallel_execution_workers') or ex.get('translator', {}).get('parallel_execution_workers') or DEFAULT_PARALLEL_WORKERS)
    view.controls_map['species_cache.cache_directory'].value = config.get('species_cache', {}).get('cache_directory') or ex.get('species_cache', {}).get('cache_directory') or DEFAULT_SPECIES_CACHE_DIR
    view.controls_map['species_cache.cache_filename'].value = config.get('species_cache', {}).get('cache_filename') or ex.get('species_cache', {}).get('cache_filename') or DEFAULT_SPECIES_CACHE_FILENAME
    view.controls_map['species_cache.wikipedia_language'].value = config.get('species_cache', {}).get('wikipedia_language') or ex.get('species_cache', {}).get('wikipedia_language') or DEFAULT_WIKIPEDIA_LANGUAGE
    view.controls_map['species_cache.wikipedia_rate_limit_delay'].value = str(config.get('species_cache', {}).get('wikipedia_rate_limit_delay') or ex.get('species_cache', {}).get('wikipedia_rate_limit_delay') or DEFAULT_WIKIPEDIA_RATE_LIMIT_DELAY)
    view.controls_map['lm_translator.temperature'].value = str(config.get('lm_translator', {}).get('temperature') or ex.get('lm_translator', {}).get('temperature') or DEFAULT_LM_TEMPERATURE)
    view.controls_map['lm_translator.rate_limit.timeout'].value = str(config.get('lm_translator', {}).get('rate_limit', {}).get('timeout') or ex.get('lm_translator', {}).get('rate_limit', {}).get('timeout') or DEFAULT_RATE_LIMIT_TIMEOUT)
    view.controls_map['lm_translator.rate_limit.sleep_seconds_between_batches'].value = str(config.get('lm_translator', {}).get('rate_limit', {}).get('sleep_seconds_between_batches') or ex.get('lm_translator', {}).get('rate_limit', {}).get('sleep_seconds_between_batches') or DEFAULT_RATE_LIMIT_SLEEP)
    view.controls_map['output_bundler.output_zip_name'].value = config.get('output_bundler', {}).get('output_zip_name') or ex.get('output_bundler', {}).get('output_zip_name') or DEFAULT_OUTPUT_ZIP_NAME
    view.controls_map['lang_merger.pending_folder_name'].value = config.get('lang_merger', {}).get('pending_folder_name') or ex.get('lang_merger', {}).get('pending_folder_name') or DEFAULT_PENDING_FOLDER
    view.controls_map['lang_merger.pending_organized_folder_name'].value = config.get('lang_merger', {}).get('pending_organized_folder_name') or ex.get('lang_merger', {}).get('pending_organized_folder_name') or DEFAULT_ORGANIZED_FOLDER
    view.controls_map['lang_merger.filtered_pending_min_count'].value = str(config.get('lang_merger', {}).get('filtered_pending_min_count') or ex.get('lang_merger', {}).get('filtered_pending_min_count') or DEFAULT_FILTERED_MIN_COUNT)
    pending_name = config.get('lang_merger', {}).get('pending_folder_name') or ex.get('lang_merger', {}).get('pending_folder_name') or DEFAULT_PENDING_FOLDER
    organized_name = config.get('lang_merger', {}).get('pending_organized_folder_name') or ex.get('lang_merger', {}).get('pending_organized_folder_name') or DEFAULT_ORGANIZED_FOLDER
    min_count = config.get('lang_merger', {}).get('filtered_pending_min_count') or ex.get('lang_merger', {}).get('filtered_pending_min_count') or DEFAULT_FILTERED_MIN_COUNT
    view.controls_map['lang_merger.pending_folder_name'].label = f"待翻譯資料夾名稱（目前：{pending_name}）"
    view.controls_map['lang_merger.pending_organized_folder_name'].label = f"整理資料夾名稱（目前：{organized_name}）"
    view.controls_map['lang_merger.filtered_pending_min_count'].label = f"「{organized_name}」key最小出現次數（目前：{min_count}）"
    view.controls_map['lang_merger.quarantine_folder_name'].value = config.get('lang_merger', {}).get('quarantine_folder_name') or ex.get('lang_merger', {}).get('quarantine_folder_name') or DEFAULT_QUARANTINE_FOLDER
    view.controls_map['lm_translator.initial_batch_size_patchouli'].value = int(config.get('lm_translator', {}).get('initial_batch_size_patchouli') or ex.get('lm_translator', {}).get('initial_batch_size_patchouli') or DEFAULT_BATCH_PATCHOULI)
    view.controls_map['lm_translator.initial_batch_size_lang'].value = int(config.get('lm_translator', {}).get('initial_batch_size_lang') or ex.get('lm_translator', {}).get('initial_batch_size_lang') or DEFAULT_BATCH_LANG)
    view.controls_map['lm_translator.initial_batch_size_ftb'].value = int(config.get('lm_translator', {}).get('initial_batch_size_ftb') or ex.get('lm_translator', {}).get('initial_batch_size_ftb') or DEFAULT_BATCH_FTB)
    view.controls_map['lm_translator.initial_batch_size_kubejs'].value = int(config.get('lm_translator', {}).get('initial_batch_size_kubejs') or ex.get('lm_translator', {}).get('initial_batch_size_kubejs') or DEFAULT_BATCH_KUBEJS)
    view.controls_map['lm_translator.initial_batch_size_md'].value = int(config.get('lm_translator', {}).get('initial_batch_size_md') or ex.get('lm_translator', {}).get('initial_batch_size_md') or DEFAULT_BATCH_MD)
    view.controls_map['lm_translator.min_batch_size'].value = int(config.get('lm_translator', {}).get('min_batch_size') or ex.get('lm_translator', {}).get('min_batch_size') or DEFAULT_MIN_BATCH_SIZE)
    view.controls_map['lm_translator.batch_shrink_factor'].value = float(config.get('lm_translator', {}).get('batch_shrink_factor') or ex.get('lm_translator', {}).get('batch_shrink_factor') or DEFAULT_BATCH_SHRINK_FACTOR)
    view.controls_map['lm_translator.patchouli_system_prompt'].value = str(config.get('lm_translator', {}).get('patchouli_system_prompt') or ex.get('lm_translator', {}).get('patchouli_system_prompt') or DEFAULT_PATCHOULI_SYSTEM_PROMPT)
    view.controls_map['lm_translator.lang_system_prompt'].value = str(config.get('lm_translator', {}).get('lang_system_prompt') or ex.get('lm_translator', {}).get('lang_system_prompt') or DEFAULT_LANG_SYSTEM_PROMPT)
    view.controls_map['lm_translator.patchouli.dir_names'].value = '\n'.join(config.get('lm_translator', {}).get('patchouli', {}).get('dir_names') or ex.get('lm_translator', {}).get('patchouli', {}).get('dir_names') or DEFAULT_PATHOULI_DIR_NAMES)
    view.controls_map['lm_translator.translator.skip_terms'].value = '\n'.join(config.get('lm_translator', {}).get('translator', {}).get('skip_terms') or ex.get('lm_translator', {}).get('translator', {}).get('skip_terms') or DEFAULT_SKIP_TERMS)
    view.controls_map['lm_translator.translator.translatable_keywords'].value = '\n'.join(config.get('lm_translator', {}).get('translator', {}).get('translatable_keywords') or ex.get('lm_translator', {}).get('translator', {}).get('translatable_keywords') or DEFAULT_TRANSLATABLE_KEYWORDS)

    view.controls_map['extractor.output_folder_names.lang_extract'].value = config.get('extractor', {}).get('output_folder_names', {}).get('lang_extract') or ex.get('extractor', {}).get('output_folder_names', {}).get('lang_extract') or DEFAULT_LANG_EXTRACT
    view.controls_map['extractor.output_folder_names.book_extract'].value = config.get('extractor', {}).get('output_folder_names', {}).get('book_extract') or ex.get('extractor', {}).get('output_folder_names', {}).get('book_extract') or DEFAULT_BOOK_EXTRACT
    view.controls_map['extractor.output_folder_names.lang_preview'].value = config.get('extractor', {}).get('output_folder_names', {}).get('lang_preview') or ex.get('extractor', {}).get('output_folder_names', {}).get('lang_preview') or DEFAULT_LANG_PREVIEW
    view.controls_map['extractor.output_folder_names.book_preview'].value = config.get('extractor', {}).get('output_folder_names', {}).get('book_preview') or ex.get('extractor', {}).get('output_folder_names', {}).get('book_preview') or DEFAULT_BOOK_PREVIEW
    view.controls_map['extractor.output_folder_names.dual_extract'].value = config.get('extractor', {}).get('output_folder_names', {}).get('dual_extract') or ex.get('extractor', {}).get('output_folder_names', {}).get('dual_extract') or DEFAULT_DUAL_EXTRACT
    view.controls_map['extractor.output_folder_names.dual_preview'].value = config.get('extractor', {}).get('output_folder_names', {}).get('dual_preview') or ex.get('extractor', {}).get('output_folder_names', {}).get('dual_preview') or DEFAULT_DUAL_PREVIEW

    lm_cfg = config.get('lm_translator', {})
    view.models_column.controls.clear()
    raw_models = lm_cfg.get('models')
    if raw_models:
        models_cfg = raw_models
    elif hasattr(view, 'DEFAULT_MODELS'):
        models_cfg = {name: {'enabled': enabled} for name, enabled in view.DEFAULT_MODELS.items()}
    else:
        models_cfg = {}
    for name, cfg in models_cfg.items():
        view.add_model_row(name)
        view.models_column.controls[-1]._checkbox.value = bool(cfg.get('enabled', False))

    view.key_fields.clear()
    view.keys_column.controls.clear()
    raw_keys = lm_cfg.get('keys')
    if isinstance(raw_keys, list) and hasattr(view, '_build_key_field'):
        for key in raw_keys:
            tf = view._build_key_field(value=key)
            row = view._build_key_row(tf)
            view.key_fields.append(tf)
            view.keys_column.controls.append(row)


def save_config_from_view(view, *, load_config_json_fn, save_config_json_fn, validate_api_keys_from_ui_fn, registry=None):
    """從 view UI 控制項收集使用者輸入並儲存至 config.json。"""
    new_config = load_config_json_fn()
    if 'ftb_translator' not in new_config:
        new_config['ftb_translator'] = {}
    try:
        new_config['logging']['log_level'] = view.controls_map['logging.log_level'].value
        new_config['logging']['log_dir'] = view.controls_map['logging.log_dir'].value
        new_config['translator']['output_dir_name'] = view.controls_map['translator.output_dir_name'].value
        new_config['ftb_translator']['output_dir_name'] = view.controls_map['ftb_translator.output_dir_name'].value
        new_config['translator']['replace_rules_path'] = view.controls_map['translator.replace_rules_path'].value
        new_config['translator']['cache_directory'] = view.controls_map['translator.cache_directory'].value
        new_config['translator']['enable_cache_saving'] = view.controls_map['translator.enable_cache_saving'].value
        new_config['translator']['parallel_execution_workers'] = int(view.controls_map['translator.parallel_execution_workers'].value)
        new_config['species_cache']['cache_directory'] = view.controls_map['species_cache.cache_directory'].value
        new_config['species_cache']['cache_filename'] = view.controls_map['species_cache.cache_filename'].value
        new_config['species_cache']['wikipedia_language'] = view.controls_map['species_cache.wikipedia_language'].value
        new_config['species_cache']['wikipedia_rate_limit_delay'] = float(view.controls_map['species_cache.wikipedia_rate_limit_delay'].value)
        new_config['lm_translator']['temperature'] = float(view.controls_map['lm_translator.temperature'].value)
        new_config['lm_translator']['rate_limit']['timeout'] = int(view.controls_map['lm_translator.rate_limit.timeout'].value)
        new_config['lm_translator']['rate_limit']['sleep_seconds_between_batches'] = float(view.controls_map['lm_translator.rate_limit.sleep_seconds_between_batches'].value)
        new_config['output_bundler']['output_zip_name'] = view.controls_map['output_bundler.output_zip_name'].value
        new_config['lang_merger']['pending_folder_name'] = view.controls_map['lang_merger.pending_folder_name'].value
        new_config['lang_merger']['pending_organized_folder_name'] = view.controls_map['lang_merger.pending_organized_folder_name'].value
        new_config['lang_merger']['filtered_pending_min_count'] = int(view.controls_map['lang_merger.filtered_pending_min_count'].value)
        new_config['lm_translator']['lm_translate_folder_name'] = str(view.controls_map['lm_translator.lm_translate_folder_name'].value)
        new_config['lang_merger']['quarantine_folder_name'] = view.controls_map['lang_merger.quarantine_folder_name'].value
        new_config['lm_translator']['patchouli_system_prompt'] = view.controls_map['lm_translator.patchouli_system_prompt'].value
        new_config['lm_translator']['lang_system_prompt'] = view.controls_map['lm_translator.lang_system_prompt'].value
        new_config['lm_translator']['initial_batch_size_patchouli'] = int(view.controls_map['lm_translator.initial_batch_size_patchouli'].value)
        new_config['lm_translator']['initial_batch_size_lang'] = int(view.controls_map['lm_translator.initial_batch_size_lang'].value)
        new_config['lm_translator']['initial_batch_size_ftb'] = int(view.controls_map['lm_translator.initial_batch_size_ftb'].value)
        new_config['lm_translator']['initial_batch_size_kubejs'] = int(view.controls_map['lm_translator.initial_batch_size_kubejs'].value)
        new_config['lm_translator']['initial_batch_size_md'] = int(view.controls_map['lm_translator.initial_batch_size_md'].value)
        new_config['lm_translator']['min_batch_size'] = int(view.controls_map['lm_translator.min_batch_size'].value)
        new_config['lm_translator']['batch_shrink_factor'] = float(view.controls_map['lm_translator.batch_shrink_factor'].value)
        new_config['lm_translator']['patchouli']['dir_names'] = [line.strip() for line in view.controls_map['lm_translator.patchouli.dir_names'].value.splitlines() if line.strip()]
        new_config['lm_translator']['translator']['skip_terms'] = [line.strip() for line in view.controls_map['lm_translator.translator.skip_terms'].value.splitlines() if line.strip()]
        new_config['lm_translator']['translator']['translatable_keywords'] = [line.strip() for line in view.controls_map['lm_translator.translator.translatable_keywords'].value.splitlines() if line.strip()]
        new_config['extractor']['output_folder_names'] = {
            'lang_extract': view.controls_map['extractor.output_folder_names.lang_extract'].value,
            'book_extract': view.controls_map['extractor.output_folder_names.book_extract'].value,
            'lang_preview': view.controls_map['extractor.output_folder_names.lang_preview'].value,
            'book_preview': view.controls_map['extractor.output_folder_names.book_preview'].value,
            'dual_extract': view.controls_map['extractor.output_folder_names.dual_extract'].value,
            'dual_preview': view.controls_map['extractor.output_folder_names.dual_preview'].value,
        }
        api_keys = [key_field.value.strip() for key_field in view.key_fields if key_field.value and key_field.value.strip()]
        validate_api_keys_from_ui_fn(api_keys)
        new_config['lm_translator']['keys'] = api_keys
        models = {}
        for row in view.models_column.controls:
            cb = row._checkbox
            models[cb.label] = {'enabled': bool(cb.value)}
        new_config['lm_translator']['models'] = models
    except (ValueError, TypeError, RuntimeError) as err:
        traceback.print_exc()
        view._show_snack_bar(f'❌ 發生錯誤：{type(err).__name__}: {err}')
        return False
    save_config_json_fn(new_config)
    view.load_config()

    if registry is not None:
        for item in registry:
            if item['key'] == 'extractor' and hasattr(item['view'].content, 'refresh_output_dir_helper'):
                item['view'].content.refresh_output_dir_helper()

    view._show_snack_bar('✅ 設定已成功儲存！', view._success_color())
    return True