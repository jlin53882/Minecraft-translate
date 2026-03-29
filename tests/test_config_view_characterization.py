import flet as ft

from app.views.config_view import ConfigView


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.loop = None
    def update(self):
        self.updated += 1


def test_config_view_loads_models_and_keys_from_config(monkeypatch):
    cfg = {
        'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
        'translator': {'output_dir_name': 'out', 'replace_rules_path': 'replace_rules.json', 'cache_directory': 'cache', 'enable_cache_saving': True, 'parallel_execution_workers': 4},
        'species_cache': {'cache_directory': 'sp', 'cache_filename': 'sp.tsv', 'wikipedia_language': 'zh', 'wikipedia_rate_limit_delay': 0.5},
        'output_bundler': {'output_zip_name': 'bundle.zip'},
        'lang_merger': {'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理', 'filtered_pending_min_count': 2, 'quarantine_folder_name': 'skip'},
        'lm_translator': {
            'temperature': 0.2,
            'rate_limit': {'timeout': 600},
            'lm_translate_folder_name': 'LM翻譯後',
            'patchouli_system_prompt': 'p',
            'lang_system_prompt': 'l',
            'iniital_batch_size_patchouli': 1,
            'iniital_batch_size_lang': 2,
            'initial_batch_size_ftb': 3,
            'initial_batch_size_kubejs': 4,
            'initial_batch_size_md': 5,
            'min_batch_size': 6,
            'batch_shrink_factor': 0.7,
            'patchouli': {'dir_names': ['patchouli_books']},
            'translator': {'skip_terms': ['api'], 'translatable_keywords': ['text']},
            'models': {'gemini-2.5-flash': {'enabled': True}, 'gemini-3-flash-preview': {'enabled': False}},
            'keys': ['k1', 'k2'],
        },
    }
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: cfg)

    view = ConfigView(_Page())

    assert len(view.models_column.controls) == 2
    assert [tf.value for tf in view.key_fields] == ['k1', 'k2']


def test_config_view_add_and_remove_model_row(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(_Page())
    start = len(view.models_column.controls)

    view.add_model_row('demo-model')
    cb = view.models_column.controls[-1]._checkbox
    view.remove_model_by_checkbox(cb)

    assert len(view.models_column.controls) == start


def test_config_view_save_click_maps_rows_back_to_config(monkeypatch):
    saved = {}
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {'rate_limit': {}, 'patchouli': {}, 'translator': {}}, 'output_bundler': {}, 'lang_merger': {}})
    monkeypatch.setattr('app.views.config_view.save_config_json', lambda cfg: saved.update(cfg))
    monkeypatch.setattr('app.views.config_view.validate_api_keys_from_ui', lambda keys: None)

    view = ConfigView(_Page())
    view.controls_map['logging.log_level'].value = 'INFO'
    view.controls_map['logging.log_dir'].value = 'logs'
    view.controls_map['translator.output_dir_name'].value = 'out'
    view.controls_map['translator.replace_rules_path'].value = 'replace_rules.json'
    view.controls_map['translator.cache_directory'].value = 'cache'
    view.controls_map['translator.enable_cache_saving'].value = True
    view.controls_map['translator.parallel_execution_workers'].value = '4'
    view.controls_map['species_cache.cache_directory'].value = 'sp'
    view.controls_map['species_cache.cache_filename'].value = 'sp.tsv'
    view.controls_map['species_cache.wikipedia_language'].value = 'zh'
    view.controls_map['species_cache.wikipedia_rate_limit_delay'].value = '0.5'
    view.controls_map['lm_translator.temperature'].value = '0.2'
    view.controls_map['lm_translator.rate_limit.timeout'].value = '600'
    view.controls_map['output_bundler.output_zip_name'].value = 'bundle.zip'
    view.controls_map['lang_merger.pending_folder_name'].value = '待翻譯'
    view.controls_map['lang_merger.pending_organized_folder_name'].value = '整理'
    view.controls_map['lang_merger.filtered_pending_min_count'].value = '2'
    view.controls_map['lm_translator.lm_translate_folder_name'].value = 'LM翻譯後'
    view.controls_map['lang_merger.quarantine_folder_name'].value = 'skip'
    view.controls_map['lm_translator.patchouli_system_prompt'].value = 'p'
    view.controls_map['lm_translator.lang_system_prompt'].value = 'l'
    view.controls_map['lm_translator.iniital_batch_size_patchouli'].value = '1'
    view.controls_map['lm_translator.iniital_batch_size_lang'].value = '2'
    view.controls_map['lm_translator.initial_batch_size_ftb'].value = '3'
    view.controls_map['lm_translator.initial_batch_size_kubejs'].value = '4'
    view.controls_map['lm_translator.initial_batch_size_md'].value = '5'
    view.controls_map['lm_translator.min_batch_size'].value = '6'
    view.controls_map['lm_translator.batch_shrink_factor'].value = '0.7'
    view.controls_map['lm_translator.patchouli.dir_names'].value = 'patchouli_books'
    view.controls_map['lm_translator.translator.skip_terms'].value = 'api'
    view.controls_map['lm_translator.translator.translatable_keywords'].value = 'text'
    view.key_fields = [ft.TextField(value='k1')]
    view.models_column.controls.clear()
    view.add_model_row('demo-model')

    view.save_config_clicked(None)

    assert saved['lm_translator']['keys'] == ['k1']
    assert saved['lm_translator']['models']['demo-model']['enabled'] is True
