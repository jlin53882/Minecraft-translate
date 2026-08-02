from __future__ import annotations

import traceback

from app.ui.snack import show_snack

from translation_tool.utils.config_manager import get_default


def load_config_into_view(view, config: dict):
    """
    將 config 字典中的值填入 view 的各個 UI 控制項。

    注意：傳入的 `config` 已經是 load_config() 三層合併後的結果。
    三層 priority：config.json（用戶）> config.example.json > DEFAULT_CONFIG。
    因此這裡直接用 config.get() 取值，不需要額外的 fallback。

    對於 list 欄位（dir_names、skip_terms、translatable_keywords），
    空清單 [] 是用戶的有效設定，會直接保留，不會被 DEFAULT 值置換。
    """
    log_cfg = config.get('logging', {})
    trans_cfg = config.get('translator', {})
    ftb_cfg = config.get('ftb_translator', {})
    species_cfg = config.get('species_cache', {})
    lm_cfg = config.get('lm_translator', {})
    bundle_cfg = config.get('output_bundler', {})

    view.controls_map['logging.log_level'].value = log_cfg.get('log_level')
    view.controls_map['logging.log_dir'].value = log_cfg.get('log_dir')
    view.controls_map['translator.output_dir_name'].value = trans_cfg.get('output_dir_name')
    view.controls_map['ftb_translator.output_dir_name'].value = ftb_cfg.get('output_dir_name')
    view.controls_map['translator.replace_rules_path'].value = trans_cfg.get('replace_rules_path')
    view.controls_map['translator.cache_directory'].value = trans_cfg.get('cache_directory')
    view.controls_map['translator.enable_cache_saving'].value = trans_cfg.get('enable_cache_saving')
    view.controls_map['translator.parallel_execution_workers'].value = str(trans_cfg.get('parallel_execution_workers'))
    view.controls_map['species_cache.cache_directory'].value = species_cfg.get('cache_directory')
    view.controls_map['species_cache.cache_filename'].value = species_cfg.get('cache_filename')
    view.controls_map['species_cache.wikipedia_language'].value = species_cfg.get('wikipedia_language')
    view.controls_map['species_cache.wikipedia_rate_limit_delay'].value = str(species_cfg.get('wikipedia_rate_limit_delay'))
    view.controls_map['lm_translator.temperature'].value = str(lm_cfg.get('temperature'))
    view.controls_map['lm_translator.rate_limit.timeout'].value = str((lm_cfg.get('rate_limit') or {}).get('timeout', 600))
    view.controls_map['lm_translator.rate_limit.sleep_seconds_between_batches'].value = str((lm_cfg.get('rate_limit') or {}).get('sleep_seconds_between_batches', 0.0))
    view.controls_map['output_bundler.output_zip_name'].value = bundle_cfg.get('output_zip_name')

    lang_merger_cfg = config.get('lang_merger', {})
    view.controls_map['lang_merger.pending_folder_name'].value = lang_merger_cfg.get('pending_folder_name')
    view.controls_map['lang_merger.pending_organized_folder_name'].value = lang_merger_cfg.get('pending_organized_folder_name')
    view.controls_map['lang_merger.filtered_pending_min_count'].value = str(lang_merger_cfg.get('filtered_pending_min_count'))
    pending_name = lang_merger_cfg.get('pending_folder_name')
    organized_name = lang_merger_cfg.get('pending_organized_folder_name')
    min_count = lang_merger_cfg.get('filtered_pending_min_count')
    view.controls_map['lang_merger.pending_folder_name'].label = f"待翻譯資料夾名稱（目前：{pending_name}）"
    view.controls_map['lang_merger.pending_organized_folder_name'].label = f"整理資料夾名稱（目前：{organized_name}）"
    view.controls_map['lang_merger.filtered_pending_min_count'].label = f"「{organized_name}」key最小出現次數（目前：{min_count}）"
    view.controls_map['lang_merger.quarantine_folder_name'].value = lang_merger_cfg.get('quarantine_folder_name')
    if 'lang_merger.patchouli_skip_en_us_when_zh_cn_exists' in view.controls_map:
        view.controls_map['lang_merger.patchouli_skip_en_us_when_zh_cn_exists'].value = lang_merger_cfg.get('patchouli_skip_en_us_when_zh_cn_exists')
    _v = lang_merger_cfg.get('patchouli_effective_translation_threshold')
    view.controls_map['lang_merger.patchouli_effective_translation_threshold'].value = \
        str(_v if _v is not None else get_default('lang_merger.patchouli_effective_translation_threshold'))
    _v = lang_merger_cfg.get('zh_en_letter_threshold')
    view.controls_map['lang_merger.zh_en_letter_threshold'].value = \
        str(_v if _v is not None else get_default('lang_merger.zh_en_letter_threshold'))
    # 2026-08-02 (PR-XX merge-asset-integration):階段 2 開關載入
    if 'lang_merger.enable_extracted_to_assets_merge' in view.controls_map:
        view.controls_map['lang_merger.enable_extracted_to_assets_merge'].value = \
            lang_merger_cfg.get('enable_extracted_to_assets_merge', True)

    view.controls_map['lm_translator.lm_translate_folder_name'].value = str(lm_cfg.get('lm_translate_folder_name'))
    view.controls_map['lm_translator.patchouli_system_prompt'].value = str(lm_cfg.get('patchouli_system_prompt'))
    view.controls_map['lm_translator.lang_system_prompt'].value = str(lm_cfg.get('lang_system_prompt'))
    _v = lm_cfg.get('initial_batch_size_patchouli')
    view.controls_map['lm_translator.initial_batch_size_patchouli'].value = _v if _v is not None else get_default('lm_translator.initial_batch_size_patchouli')
    _v = lm_cfg.get('initial_batch_size_lang')
    view.controls_map['lm_translator.initial_batch_size_lang'].value = _v if _v is not None else get_default('lm_translator.initial_batch_size_lang')
    _v = lm_cfg.get('initial_batch_size_ftb')
    view.controls_map['lm_translator.initial_batch_size_ftb'].value = _v if _v is not None else get_default('lm_translator.initial_batch_size_ftb')
    _v = lm_cfg.get('initial_batch_size_kubejs')
    view.controls_map['lm_translator.initial_batch_size_kubejs'].value = _v if _v is not None else get_default('lm_translator.initial_batch_size_kubejs')
    _v = lm_cfg.get('initial_batch_size_md')
    view.controls_map['lm_translator.initial_batch_size_md'].value = _v if _v is not None else get_default('lm_translator.initial_batch_size_md')
    _v = lm_cfg.get('min_batch_size')
    view.controls_map['lm_translator.min_batch_size'].value = _v if _v is not None else get_default('lm_translator.min_batch_size')
    _v = lm_cfg.get('batch_shrink_factor')
    view.controls_map['lm_translator.batch_shrink_factor'].value = _v if _v is not None else get_default('lm_translator.batch_shrink_factor')
    view.controls_map['lm_translator.patchouli.dir_names'].value = '\n'.join(lm_cfg.get('patchouli', {}).get('dir_names', []))
    view.controls_map['lm_translator.translator.skip_terms'].value = '\n'.join(lm_cfg.get('translator', {}).get('skip_terms', []))
    view.controls_map['lm_translator.translator.translatable_keywords'].value = '\n'.join(lm_cfg.get('translator', {}).get('translatable_keywords', []))

    extractor_cfg = config.get('extractor', {})
    folder_names = extractor_cfg.get('output_folder_names', {})
    view.controls_map['extractor.output_folder_names.lang_extract'].value = folder_names.get('lang_extract')
    view.controls_map['extractor.output_folder_names.book_extract'].value = folder_names.get('book_extract')
    view.controls_map['extractor.output_folder_names.lang_preview'].value = folder_names.get('lang_preview')
    view.controls_map['extractor.output_folder_names.book_preview'].value = folder_names.get('book_preview')
    view.controls_map['extractor.output_folder_names.dual_extract'].value = folder_names.get('dual_extract')
    view.controls_map['extractor.output_folder_names.dual_preview'].value = folder_names.get('dual_preview')

    view.models_column.controls.clear()
    models_cfg = lm_cfg.get('models')
    if 'models' not in lm_cfg:
        models_cfg = {name: {'enabled': enabled} for name, enabled in view.DEFAULT_MODELS.items()}
    else:
        models_cfg = models_cfg or {}
    for name, cfg in models_cfg.items():
        view.add_model_row(name)
        view.models_column.controls[-1]._checkbox.value = bool(cfg.get('enabled', False))

    view.key_fields.clear()
    view.keys_column.controls.clear()
    for key in lm_cfg.get('keys', []):
        tf = view._build_key_field(value=key)
        row = view._build_key_row(tf)
        view.key_fields.append(tf)
        view.keys_column.controls.append(row)


def save_config_from_view(view, *, load_config_json_fn, save_config_json_fn, validate_api_keys_from_ui_fn, registry=None):
    """從 view UI 控制項收集使用者輸入並寫入 config.json。

    寫入流程：
      1. load_config_json_fn() → 取得三層合併後的設定（作為基底）
      2. 從 view 控制項讀取新值，更新到基底 dict
      3. save_config_json_fn(new_config) → 寫入 config.json（觸發 normalization）
      4. view.load_config() → 重新讀取並刷新 UI（顯示寫入後的實際值）

    注意：基底來自 load_config_json_fn()，代表：
      - 如果 config.json 存在，會讀取使用者的實際設定（含自訂值）
      - 如果 config.json 不存在，會拿到 DEFAULT_CONFIG 的值
      → 按儲存後，使用者的「預設值」就會固化進 config.json（Layer 1 覆蓋 Layer 2/3）
    """
    new_config = load_config_json_fn()
    if 'ftb_translator' not in new_config:
        new_config['ftb_translator'] = {}
    if 'lm_translator' not in new_config:
        new_config['lm_translator'] = {}
    if 'rate_limit' not in new_config['lm_translator']:
        new_config['lm_translator']['rate_limit'] = {}
    if 'patchouli' not in new_config['lm_translator']:
        new_config['lm_translator']['patchouli'] = {}
    if 'translator' not in new_config['lm_translator']:
        new_config['lm_translator']['translator'] = {}
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
        new_config['lang_merger']['patchouli_skip_en_us_when_zh_cn_exists'] = view.controls_map['lang_merger.patchouli_skip_en_us_when_zh_cn_exists'].value
        new_config['lang_merger']['patchouli_effective_translation_threshold'] = float(view.controls_map['lang_merger.patchouli_effective_translation_threshold'].value)
        new_config['lang_merger']['zh_en_letter_threshold'] = int(view.controls_map['lang_merger.zh_en_letter_threshold'].value)
        # 2026-08-02 (PR-XX merge-asset-integration):階段 2 開關儲存
        if 'lang_merger.enable_extracted_to_assets_merge' in view.controls_map:
            new_config['lang_merger']['enable_extracted_to_assets_merge'] = bool(
                view.controls_map['lang_merger.enable_extracted_to_assets_merge'].value
            )
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
        show_snack(view.page, f'❌ 發生錯誤：{type(err).__name__}: {err}')
        return False
    save_config_json_fn(new_config)
    view.load_config()

    if registry is not None:
        for item in registry:
            if item['key'] == 'extractor' and hasattr(item['view'].content, 'refresh_output_dir_helper'):
                item['view'].content.refresh_output_dir_helper()

    show_snack(view.page, '✅ 設定已成功儲存！', view._success_color())
    return True