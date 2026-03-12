from __future__ import annotations

import traceback


def load_config_into_view(view, config: dict):
    log_cfg = config.get('logging', {})
    trans_cfg = config.get('translator', {})
    species_cfg = config.get('species_cache', {})
    lm_cfg = config.get('lm_translator', {})
    bundle_cfg = config.get('output_bundler', {})

    view.controls_map['logging.log_level'].value = log_cfg.get('log_level')
    view.controls_map['logging.log_dir'].value = log_cfg.get('log_dir')
    view.controls_map['translator.output_dir_name'].value = trans_cfg.get('output_dir_name', 'zh_tw_generated')
    view.controls_map['translator.replace_rules_path'].value = trans_cfg.get('replace_rules_path', 'replace_rules.json')
    view.controls_map['translator.cache_directory'].value = trans_cfg.get('cache_directory', '快取資料夾')
    view.controls_map['translator.enable_cache_saving'].value = trans_cfg.get('enable_cache_saving')
    view.controls_map['translator.parallel_execution_workers'].value = str(trans_cfg.get('parallel_execution_workers', '4'))
    view.controls_map['species_cache.cache_directory'].value = species_cfg.get('cache_directory', '學名資料庫')
    view.controls_map['species_cache.cache_filename'].value = species_cfg.get('cache_filename', 'species_cache.tsv')
    view.controls_map['species_cache.wikipedia_language'].value = species_cfg.get('wikipedia_language')
    view.controls_map['species_cache.wikipedia_rate_limit_delay'].value = str(species_cfg.get('wikipedia_rate_limit_delay'))
    view.controls_map['lm_translator.temperature'].value = str(lm_cfg.get('temperature'))
    view.controls_map['lm_translator.rate_limit.timeout'].value = str(lm_cfg.get('rate_limit', {}).get('timeout', '600'))
    view.controls_map['output_bundler.output_zip_name'].value = bundle_cfg.get('output_zip_name')
    view.controls_map['lang_merger.pending_folder_name'].value = config.get('lang_merger', {}).get('pending_folder_name', '待翻譯')
    view.controls_map['lang_merger.pending_organized_folder_name'].value = config.get('lang_merger', {}).get('pending_organized_folder_name', '待翻譯整理需翻譯')
    view.controls_map['lang_merger.filtered_pending_min_count'].value = str(config.get('lang_merger', {}).get('filtered_pending_min_count', 2))
    view.controls_map['lm_translator.lm_translate_folder_name'].value = str(config.get('lm_translator', {}).get('lm_translate_folder_name', 'LM翻譯後'))
    view.controls_map['lm_translator.patchouli_system_prompt'].value = str(config.get('lm_translator', {}).get('patchouli_system_prompt', '你是專業的 Minecraft patchouli 手冊翻譯員，專精於《當個創世神》繁體中文（台灣）官方譯名或台灣用語的翻譯。'))
    view.controls_map['lm_translator.lang_system_prompt'].value = str(config.get('lm_translator', {}).get('lang_system_prompt', '你是專業的 Minecraft Lang翻譯員，你正在翻譯 Minecraft 語言檔案（JSON格式）。'))
    view.controls_map['lang_merger.quarantine_folder_name'].value = config.get('lang_merger', {}).get('quarantine_folder_name', 'skipped_json')
    view.controls_map['lm_translator.iniital_batch_size_patchouli'].value = int(config.get('lm_translator', {}).get('iniital_batch_size_patchouli', 100))
    view.controls_map['lm_translator.iniital_batch_size_lang'].value = int(config.get('lm_translator', {}).get('iniital_batch_size_lang', 300))
    view.controls_map['lm_translator.initial_batch_size_ftb'].value = int(config.get('lm_translator', {}).get('initial_batch_size_ftb', 100))
    view.controls_map['lm_translator.initial_batch_size_kubejs'].value = int(config.get('lm_translator', {}).get('initial_batch_size_kubejs', 200))
    view.controls_map['lm_translator.initial_batch_size_md'].value = int(config.get('lm_translator', {}).get('initial_batch_size_md', 100))
    view.controls_map['lm_translator.min_batch_size'].value = int(config.get('lm_translator', {}).get('min_batch_size', 50))
    view.controls_map['lm_translator.batch_shrink_factor'].value = float(config.get('lm_translator', {}).get('batch_shrink_factor', 0.75))
    view.controls_map['lm_translator.patchouli.dir_names'].value = '\n'.join(config.get('lm_translator', {}).get('patchouli', {}).get('dir_names', 'patchouli_books'))
    view.controls_map['lm_translator.translator.skip_terms'].value = '\n'.join(config.get('lm_translator', {}).get('translator', {}).get('skip_terms', ['api documentation']))
    view.controls_map['lm_translator.translator.translatable_keywords'].value = '\n'.join(config.get('lm_translator', {}).get('translator', {}).get('translatable_keywords', 'text'))

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


def save_config_from_view(view, *, load_config_json_fn, save_config_json_fn, validate_api_keys_from_ui_fn):
    new_config = load_config_json_fn()
    try:
        new_config['logging']['log_level'] = view.controls_map['logging.log_level'].value
        new_config['logging']['log_dir'] = view.controls_map['logging.log_dir'].value
        new_config['translator']['output_dir_name'] = view.controls_map['translator.output_dir_name'].value
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
        new_config['output_bundler']['output_zip_name'] = view.controls_map['output_bundler.output_zip_name'].value
        new_config['lang_merger']['pending_folder_name'] = view.controls_map['lang_merger.pending_folder_name'].value
        new_config['lang_merger']['pending_organized_folder_name'] = view.controls_map['lang_merger.pending_organized_folder_name'].value
        new_config['lang_merger']['filtered_pending_min_count'] = int(view.controls_map['lang_merger.filtered_pending_min_count'].value)
        new_config['lm_translator']['lm_translate_folder_name'] = str(view.controls_map['lm_translator.lm_translate_folder_name'].value)
        new_config['lang_merger']['quarantine_folder_name'] = view.controls_map['lang_merger.quarantine_folder_name'].value
        new_config['lm_translator']['patchouli_system_prompt'] = view.controls_map['lm_translator.patchouli_system_prompt'].value
        new_config['lm_translator']['lang_system_prompt'] = view.controls_map['lm_translator.lang_system_prompt'].value
        new_config['lm_translator']['iniital_batch_size_patchouli'] = int(view.controls_map['lm_translator.iniital_batch_size_patchouli'].value)
        new_config['lm_translator']['iniital_batch_size_lang'] = int(view.controls_map['lm_translator.iniital_batch_size_lang'].value)
        new_config['lm_translator']['initial_batch_size_ftb'] = int(view.controls_map['lm_translator.initial_batch_size_ftb'].value)
        new_config['lm_translator']['initial_batch_size_kubejs'] = int(view.controls_map['lm_translator.initial_batch_size_kubejs'].value)
        new_config['lm_translator']['initial_batch_size_md'] = int(view.controls_map['lm_translator.initial_batch_size_md'].value)
        new_config['lm_translator']['min_batch_size'] = int(view.controls_map['lm_translator.min_batch_size'].value)
        new_config['lm_translator']['batch_shrink_factor'] = float(view.controls_map['lm_translator.batch_shrink_factor'].value)
        new_config['lm_translator']['patchouli']['dir_names'] = [line.strip() for line in view.controls_map['lm_translator.patchouli.dir_names'].value.splitlines() if line.strip()]
        new_config['lm_translator']['translator']['skip_terms'] = [line.strip() for line in view.controls_map['lm_translator.translator.skip_terms'].value.splitlines() if line.strip()]
        new_config['lm_translator']['translator']['translatable_keywords'] = [line.strip() for line in view.controls_map['lm_translator.translator.translatable_keywords'].value.splitlines() if line.strip()]
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
    view._show_snack_bar('✅ 設定已成功儲存！', view._success_color())
    return True
