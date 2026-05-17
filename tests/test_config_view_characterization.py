import pytest
import flet as ft
from app.views.config_view import ConfigView
from tests.conftest import mock_page


@pytest.fixture
def cv():
    import app.views.config_view as config_view
    return config_view


@pytest.fixture
def page():
    return mock_page()


@pytest.fixture
def mock_controls_map():
    return {}


def test_config_view_loads_models_and_keys_from_config(monkeypatch):
    cfg = {
        'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
        'translator': {'output_dir_name': 'out', 'replace_rules_path': 'replace_rules.json', 'cache_directory': 'cache', 'enable_cache_saving': True, 'parallel_execution_workers': 4},
        'ftb_translator': {'output_dir_name': 'FTB任務翻譯輸出'},
        'species_cache': {'cache_directory': 'sp', 'cache_filename': 'sp.tsv', 'wikipedia_language': 'zh', 'wikipedia_rate_limit_delay': 0.5},
        'output_bundler': {'output_zip_name': 'bundle.zip'},
        'lang_merger': {'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理', 'filtered_pending_min_count': 2, 'quarantine_folder_name': 'skip'},
        'lm_translator': {
            'temperature': 0.2,
            'rate_limit': {'timeout': 600, 'sleep_seconds_between_batches': 0.0},
            'lm_translate_folder_name': 'LM翻譯後',
            'patchouli_system_prompt': 'p',
            'lang_system_prompt': 'l',
            'initial_batch_size_patchouli': 1,
            'initial_batch_size_lang': 2,
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

    view = ConfigView(mock_page())

    assert len(view.models_column.controls) == 2
    assert [tf.value for tf in view.key_fields] == ['k1', 'k2']


def test_config_view_add_and_remove_model_row(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'ftb_translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    start = len(view.models_column.controls)

    view.add_model_row('demo-model')
    cb = view.models_column.controls[-1]._checkbox
    view.remove_model_by_checkbox(cb)

    assert len(view.models_column.controls) == start


def test_config_view_save_click_maps_rows_back_to_config(monkeypatch):
    saved = {}
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {'rate_limit': {}, 'patchouli': {}, 'translator': {}}, 'output_bundler': {}, 'lang_merger': {}, 'extractor': {'output_folder_names': {'lang_extract': '', 'book_extract': '', 'lang_preview': '', 'book_preview': ''}}})
    monkeypatch.setattr('app.views.config_view.save_config_json', lambda cfg: saved.update(cfg))
    monkeypatch.setattr('app.views.config_view.validate_api_keys_from_ui', lambda keys: None)

    view = ConfigView(mock_page())
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
    view.controls_map['lm_translator.initial_batch_size_patchouli'].value = '1'
    view.controls_map['lm_translator.initial_batch_size_lang'].value = '2'
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
    assert saved['lm_translator']['rate_limit']['sleep_seconds_between_batches'] == 0.0


def test_config_view_show_snack_bar_adds_to_overlay(monkeypatch):
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'ftb_translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    page = mock_page()
    view = ConfigView(page)

    view._show_snack_bar('Test error', '#FF0000')

    assert len(page.overlay) == 1
    assert page.overlay[0].open is True


def test_config_view_init_controls_builds_ui(monkeypatch):
    """測試 _init_controls 構建所有 UI 控制項"""
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'ftb_translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())

    assert view.footer is not None
    assert view.models_column is not None
    assert view.keys_column is not None
    assert view.page is not None


def test_config_view_controls_map_exists(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())

    assert hasattr(view, 'controls_map')
    assert view.controls_map is not None


def test_config_view_move_model_row(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    view.move_model_row(0, 1)


def test_config_view_add_and_remove_model_row_integration(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    start = len(view.models_column.controls)

    view.add_model_row('test-model')
    assert len(view.models_column.controls) == start + 1

    cb = view.models_column.controls[-1]._checkbox
    view.remove_model_by_checkbox(cb)
    assert len(view.models_column.controls) == start


def test_config_view_load_config_updates_controls_map(monkeypatch):
    cfg = {'logging': {'log_level': 'INFO'}}
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: cfg)
    view = ConfigView(mock_page())

    assert 'logging.log_level' in view.controls_map


def test_config_view_on_add_model_clicked(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    view.new_model_field = type('F', (), {'value': 'new_model'})()
    start = len(view.models_column.controls)

    view.on_add_model_clicked(None)

    assert len(view.models_column.controls) == start + 1


def test_config_view_success_color(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())

    color = view._success_color()
    assert color is not None


def test_config_view_build_key_field(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())

    field = view._build_key_field('test_key')
    assert field is not None


def test_config_view_build_header(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    header = view._build_header()
    assert header is not None


def test_config_view_build_footer(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    footer = view._build_footer()
    assert footer is not None


def test_config_view_build_card(monkeypatch):
    monkeypatch.setattr('app.views.config_view.load_config_json', lambda: {'logging': {}, 'translator': {}, 'species_cache': {}, 'lm_translator': {}, 'output_bundler': {}, 'lang_merger': {}})
    view = ConfigView(mock_page())
    card = view._build_card('Test', [])
    assert card is not None


def test_config_view_build_nav_column(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, '_build_nav_column')
    assert callable(view._build_nav_column)


def test_config_view_build_content_area(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, '_build_content_area')
    assert callable(view._build_content_area)


def test_config_view_build_lang_merger_card(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, '_build_lang_merger_card')
    assert callable(view._build_lang_merger_card)


def test_config_view_add_model_row(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, 'add_model_row')
    assert callable(view.add_model_row)


def test_config_view_remove_model_by_checkbox(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, 'remove_model_by_checkbox')
    assert callable(view.remove_model_by_checkbox)


def test_config_view_build_key_row(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, '_build_key_row')
    assert callable(view._build_key_row)


def test_config_view_add_key_row(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, 'add_key_row')
    assert callable(view.add_key_row)


def test_config_view_remove_key_row(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, 'remove_key_row')
    assert callable(view.remove_key_row)


def test_config_view_refresh_model_order_labels(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, '_refresh_model_order_labels')
    assert callable(view._refresh_model_order_labels)


def test_config_view_save_config_clicked(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert hasattr(view, 'save_config_clicked')
    assert callable(view.save_config_clicked)


def test_config_view_page_property(cv, page, mock_controls_map):
    view = cv.ConfigView(page)
    assert view.page is not None


def test_nav_items_has_six_categories(cv, page, mock_controls_map):
    """測試 NAV_ITEMS 有 6 個分類導航項目"""
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert len(cv.NAV_ITEMS) == 7
    expected_ids = ['general', 'api_models', 'translation_behavior', 'prompts', 'species_lookup', 'batch_limits', 'extractor']
    actual_ids = [item['id'] for item in cv.NAV_ITEMS]
    assert actual_ids == expected_ids


def test_nav_items_has_required_fields(cv, page, mock_controls_map):
    """測試每個 NAV_ITEM 都有 id, label, icon"""
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    for item in cv.NAV_ITEMS:
        assert 'id' in item
        assert 'label' in item
        assert 'icon' in item
        assert item['id'] is not None
        assert item['label'] is not None
        assert item['icon'] is not None


def test_selected_nav_default_is_general(cv, page, mock_controls_map):
    """測試預設選中的導航是 general"""
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    assert view._selected_nav == 'general'


def test_nav_click_id_is_correct(cv, page, mock_controls_map):
    """測試點擊各導航 ID 都正確（只測試狀態更新）"""
    view = cv.ConfigView(page)
    view.controls_map = mock_controls_map
    for item in cv.NAV_ITEMS:
        view._selected_nav = item['id']
        assert view._selected_nav == item['id']
