from __future__ import annotations

import json
import traceback
from pathlib import Path
from translation_tool.utils.config_manager import load_config as load_config_default


EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.example.json"


def _load_example_config() -> dict:
    """載入 config.example.json 當作 fallback 預設值。"""
    if EXAMPLE_CONFIG_PATH.exists():
        with EXAMPLE_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get(config: dict, *keys, default=None):
    """取得 config 值，若不存在則 fallback 到 config.example.json。"""
    val = config
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            val = None
        if val is None:
            break
    if val is not None:
        return val
    val = _load_example_config()
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            val = None
        if val is None:
            break
    return val if val is not None else default


def load_config_into_view(view, config: dict):
    """將 config 字典中的值填入 view 的各個 UI 控制項。"""
    ex = _load_example_config()
    view.controls_map['logging.log_level'].value = _get(config, 'logging', 'log_level') or ex.get('logging', {}).get('log_level')
    view.controls_map['logging.log_dir'].value = _get(config, 'translator', 'log_dir') or ex.get('logging', {}).get('log_dir')
    view.controls_map['translator.output_dir_name'].value = _get(config, 'translator', 'output_dir_name') or ex.get('translator', {}).get('output_dir_name', 'zh_tw_generated')
    view.controls_map['ftb_translator.output_dir_name'].value = _get(config, 'ftb_translator', 'output_dir_name') or ex.get('ftb_translator', {}).get('output_dir_name', 'FTB任務翻譯輸出')
    view.controls_map['translator.replace_rules_path'].value = _get(config, 'translator', 'replace_rules_path') or ex.get('translator', {}).get('replace_rules_path', 'replace_rules.json')
    view.controls_map['translator.cache_directory'].value = _get(config, 'translator', 'cache_directory') or ex.get('translator', {}).get('cache_directory', '快取資料')
    view.controls_map['translator.enable_cache_saving'].value = _get(config, 'translator', 'enable_cache_saving')
    view.controls_map['translator.parallel_execution_workers'].value = str(_get(config, 'translator', 'parallel_execution_workers') or ex.get('translator', {}).get('parallel_execution_workers', '4'))
    view.controls_map['species_cache.cache_directory'].value = _get(config, 'species_cache', 'cache_directory') or ex.get('species_cache', {}).get('cache_directory', '學名資料庫')
    view.controls_map['species_cache.cache_filename'].value = _get(config, 'species_cache', 'cache_filename') or ex.get('species_cache', {}).get('cache_filename', 'species_cache.tsv')
    view.controls_map['species_cache.wikipedia_language'].value = _get(config, 'species_cache', 'wikipedia_language')
    view.controls_map['species_cache.wikipedia_rate_limit_delay'].value = str(_get(config, 'species_cache', 'wikipedia_rate_limit_delay') or ex.get('species_cache', {}).get('wikipedia_rate_limit_delay'))
    view.controls_map['lm_translator.temperature'].value = str(_get(config, 'lm_translator', 'temperature') or ex.get('lm_translator', {}).get('temperature'))
    view.controls_map['lm_translator.rate_limit.timeout'].value = str(_get(config, 'lm_translator', 'rate_limit', 'timeout') or ex.get('lm_translator', {}).get('rate_limit', {}).get('timeout', '600'))
    view.controls_map['lm_translator.rate_limit.sleep_seconds_between_batches'].value = str(_get(config, 'lm_translator', 'rate_limit', 'sleep_seconds_between_batches') or ex.get('lm_translator', {}).get('rate_limit', {}).get('sleep_seconds_between_batches', '0.0'))
    view.controls_map['output_bundler.output_zip_name'].value = _get(config, 'output_bundler', 'output_zip_name') or ex.get('output_bundler', {}).get('output_zip_name')
    view.controls_map['lang_merger.pending_folder_name'].value = _get(config, 'lang_merger', 'pending_folder_name') or ex.get('lang_merger', {}).get('pending_folder_name', '待翻譯')
    view.controls_map['lang_merger.pending_organized_folder_name'].value = _get(config, 'lang_merger', 'pending_organized_folder_name') or ex.get('lang_merger', {}).get('pending_organized_folder_name', '待翻譯整理需翻譯')
    view.controls_map['lang_merger.filtered_pending_min_count'].value = str(_get(config, 'lang_merger', 'filtered_pending_min_count') or ex.get('lang_merger', {}).get('filtered_pending_min_count', 3))
    pending_name = _get(config, 'lang_merger', 'pending_folder_name') or ex.get('lang_merger', {}).get('pending_folder_name', '待翻譯')
    organized_name = _get(config, 'lang_merger', 'pending_organized_folder_name') or ex.get('lang_merger', {}).get('pending_organized_folder_name', '待翻譯整理需翻譯')
    min_count = _get(config, 'lang_merger', 'filtered_pending_min_count') or ex.get('lang_merger', {}).get('filtered_pending_min_count', 3)
    view.controls_map['lang_merger.pending_folder_name'].label = f"待翻譯資料夾名稱（目前：{pending_name}）"
    view.controls_map['lang_merger.pending_organized_folder_name'].label = f"整理資料夾名稱（目前：{organized_name}）"
    view.controls_map['lang_merger.filtered_pending_min_count'].label = f"「{organized_name}」key最小出現次數（目前：{min_count}）"
    view.controls_map['lang_merger.quarantine_folder_name'].value = _get(config, 'lang_merger', 'quarantine_folder_name') or ex.get('lang_merger', {}).get('quarantine_folder_name', '問題檔案skipped_json')
    view.controls_map['lm_translator.initial_batch_size_patchouli'].value = int(_get(config, 'lm_translator', 'initial_batch_size_patchouli') or ex.get('lm_translator', {}).get('initial_batch_size_patchouli', 100))
    view.controls_map['lm_translator.initial_batch_size_lang'].value = int(_get(config, 'lm_translator', 'initial_batch_size_lang') or ex.get('lm_translator', {}).get('initial_batch_size_lang', 300))
    view.controls_map['lm_translator.initial_batch_size_ftb'].value = int(_get(config, 'lm_translator', 'initial_batch_size_ftb') or ex.get('lm_translator', {}).get('initial_batch_size_ftb', 100))
    view.controls_map['lm_translator.initial_batch_size_kubejs'].value = int(_get(config, 'lm_translator', 'initial_batch_size_kubejs') or ex.get('lm_translator', {}).get('initial_batch_size_kubejs', 200))
    view.controls_map['lm_translator.initial_batch_size_md'].value = int(_get(config, 'lm_translator', 'initial_batch_size_md') or ex.get('lm_translator', {}).get('initial_batch_size_md', 100))
    view.controls_map['lm_translator.min_batch_size'].value = int(_get(config, 'lm_translator', 'min_batch_size') or ex.get('lm_translator', {}).get('min_batch_size', 50))
    view.controls_map['lm_translator.batch_shrink_factor'].value = float(_get(config, 'lm_translator', 'batch_shrink_factor') or ex.get('lm_translator', {}).get('batch_shrink_factor', 0.5))
    view.controls_map['lm_translator.patchouli_system_prompt'].value = str(_get(config, 'lm_translator', 'patchouli_system_prompt') or ex.get('lm_translator', {}).get('patchouli_system_prompt', ''))
    view.controls_map['lm_translator.lang_system_prompt'].value = str(_get(config, 'lm_translator', 'lang_system_prompt') or ex.get('lm_translator', {}).get('lang_system_prompt', ''))
    view.controls_map['lm_translator.patchouli.dir_names'].value = '\n'.join(_get(config, 'lm_translator', 'patchouli', 'dir_names') or ex.get('lm_translator', {}).get('patchouli', {}).get('dir_names', ['patchouli_books', 'book', 'manual', 'guidebook']))
    view.controls_map['lm_translator.translator.skip_terms'].value = '\n'.join(_get(config, 'lm_translator', 'translator', 'skip_terms') or ex.get('lm_translator', {}).get('translator', {}).get('skip_terms', ['api documentation', 'api docs', 'documentation', 'discord', 'github', 'homepage', 'mod page', 'modpack', 'official website', 'patreon', 'Twitter', 'Modrinth', 'CurseForge', 'Crowdin', 'Twitch', 'Wiki', 'Minecraft', 'Forge', 'YouTube', 'Reddit', 'Ko-fi', 'Flattr']))
    view.controls_map['lm_translator.translator.translatable_keywords'].value = '\n'.join(_get(config, 'lm_translator', 'translator', 'translatable_keywords') or ex.get('lm_translator', {}).get('translator', {}).get('translatable_keywords', ['text', 'name', 'title', 'description', 'subtitle', 'hover', 'note', 'warning', 'quote', 'paragraph', 'body', 'header', 'footer', 'heading', 'effects', 'category', 'link_text', 'pages.title']))

    view.controls_map['extractor.output_folder_names.lang_extract'].value = _get(config, 'extractor', 'output_folder_names', 'lang_extract') or ex.get('extractor', {}).get('output_folder_names', {}).get('lang_extract', '_提取lang_輸出')
    view.controls_map['extractor.output_folder_names.book_extract'].value = _get(config, 'extractor', 'output_folder_names', 'book_extract') or ex.get('extractor', {}).get('output_folder_names', {}).get('book_extract', '_提取book_輸出')
    view.controls_map['extractor.output_folder_names.lang_preview'].value = _get(config, 'extractor', 'output_folder_names', 'lang_preview') or ex.get('extractor', {}).get('output_folder_names', {}).get('lang_preview', '_預覽lang_輸出')
    view.controls_map['extractor.output_folder_names.book_preview'].value = _get(config, 'extractor', 'output_folder_names', 'book_preview') or ex.get('extractor', {}).get('output_folder_names', {}).get('book_preview', '_預覽book_輸出')
    view.controls_map['extractor.output_folder_names.dual_extract'].value = _get(config, 'extractor', 'output_folder_names', 'dual_extract') or ex.get('extractor', {}).get('output_folder_names', {}).get('dual_extract', '_提取both_輸出')
    view.controls_map['extractor.output_folder_names.dual_preview'].value = _get(config, 'extractor', 'output_folder_names', 'dual_preview') or ex.get('extractor', {}).get('output_folder_names', {}).get('dual_preview', '_預覽both_輸出')

    lm_cfg = _get(config, 'lm_translator') or {}
    view.models_column.controls.clear()
    raw_models = _get(config, 'lm_translator', 'models')
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
    raw_keys = _get(config, 'lm_translator', 'keys')
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
