"""
Unit tests for extractor configuration load/save.
Tests the extractor.output_folder_names config fields.
"""

import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import mock_page, mock_filepicker


class _MockSession:
    def __init__(self, max_logs=2000):
        self._status = 'IDLE'
        self._progress = 0
        self._logs = []
        self._error = False

    def start(self):
        self._status = 'RUNNING'

    def snapshot(self):
        return {'status': self._status, 'progress': self._progress, 'logs': self._logs, 'error': self._error}


def test_config_load_extractor_folder_names():
    """Test load_config_into_view loads extractor output folder names."""
    with patch('app.task_session.TaskSession', _MockSession):
        from app.views.config_view import ConfigView
        view = ConfigView(mock_page())

        config = {
            'extractor': {
                'output_folder_names': {
                    'lang_extract': '_自訂lang_輸出',
                    'book_extract': '_自訂book_輸出',
                    'lang_preview': '_自訂lang_預覽',
                    'book_preview': '_自訂book_預覽',
                }
            }
        }

        from app.views.config.config_actions import load_config_into_view
        load_config_into_view(view, config)

        assert view.controls_map['extractor.output_folder_names.lang_extract'].value == '_自訂lang_輸出'
        assert view.controls_map['extractor.output_folder_names.book_extract'].value == '_自訂book_輸出'
        assert view.controls_map['extractor.output_folder_names.lang_preview'].value == '_自訂lang_預覽'
        assert view.controls_map['extractor.output_folder_names.book_preview'].value == '_自訂book_預覽'


def test_config_load_extractor_defaults_when_missing():
    """Test load_config_into_view uses default values when extractor config is missing.

    Uses load_config() to produce a real three-layer merged config — same as production.
    """
    with patch('app.task_session.TaskSession', _MockSession):
        from app.views.config_view import ConfigView
        view = ConfigView(mock_page())

        # Use load_config() to get the real three-layer merged config
        from translation_tool.utils.config_manager import load_config
        config = load_config()

        from app.views.config.config_actions import load_config_into_view
        load_config_into_view(view, config)

        assert view.controls_map['extractor.output_folder_names.lang_extract'].value == '_提取lang_輸出'
        assert view.controls_map['extractor.output_folder_names.book_extract'].value == '_提取book_輸出'
        assert view.controls_map['extractor.output_folder_names.lang_preview'].value == '_預覽lang_輸出'
        assert view.controls_map['extractor.output_folder_names.book_preview'].value == '_預覽book_輸出'


def test_config_save_extractor_folder_names():
    """Test save_config_from_view saves extractor output folder names."""
    with patch('app.task_session.TaskSession', _MockSession):
        from app.views.config_view import ConfigView
        page = mock_page()
        view = ConfigView(page)

        view.controls_map['extractor.output_folder_names.lang_extract'].value = '_測試lang_輸出'
        view.controls_map['extractor.output_folder_names.book_extract'].value = '_測試book_輸出'
        view.controls_map['extractor.output_folder_names.lang_preview'].value = '_測試lang_預覽'
        view.controls_map['extractor.output_folder_names.book_preview'].value = '_測試book_預覽'

        saved_config = {}

        def mock_load():
            return {
                'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
                'translator': {'output_dir_name': 'zh_tw_generated', 'replace_rules_path': 'replace_rules.json', 'cache_directory': '快取資料', 'enable_cache_saving': True, 'parallel_execution_workers': 4},
                'species_cache': {'cache_directory': '學名資料庫', 'cache_filename': 'species_cache.tsv', 'wikipedia_language': 'zh', 'wikipedia_rate_limit_delay': 0.5},
                'lm_translator': {'temperature': 0.2, 'rate_limit': {'timeout': 600}, 'lm_translate_folder_name': 'LM翻譯後', 'patchouli_system_prompt': '', 'lang_system_prompt': '', 'initial_batch_size_patchouli': 100, 'initial_batch_size_lang': 300, 'initial_batch_size_ftb': 100, 'initial_batch_size_kubejs': 200, 'initial_batch_size_md': 100, 'min_batch_size': 50, 'batch_shrink_factor': 0.75, 'patchouli': {'dir_names': ['patchouli_books']}, 'translator': {'skip_terms': [], 'translatable_keywords': ['text']}, 'keys': [], 'models': {}},
                'output_bundler': {'output_zip_name': '可使用翻譯.zip'},
                'lang_merger': {'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '待翻譯整理需翻譯', 'filtered_pending_min_count': 2, 'quarantine_folder_name': 'skipped_json'},
                'extractor': {'output_folder_names': {'lang_extract': '_提取lang_輸出', 'book_extract': '_提取book_輸出', 'lang_preview': '_預覽lang_輸出', 'book_preview': '_預覽book_輸出'}},
            }

        def mock_save(cfg):
            saved_config.update(cfg)

        from app.views.config.config_actions import save_config_from_view

        save_config_from_view(
            view,
            load_config_json_fn=mock_load,
            save_config_json_fn=mock_save,
            validate_api_keys_from_ui_fn=lambda keys: None,
        )

        assert saved_config['extractor']['output_folder_names']['lang_extract'] == '_測試lang_輸出'
        assert saved_config['extractor']['output_folder_names']['book_extract'] == '_測試book_輸出'
        assert saved_config['extractor']['output_folder_names']['lang_preview'] == '_測試lang_預覽'
        assert saved_config['extractor']['output_folder_names']['book_preview'] == '_測試book_預覽'


def test_extractor_view_output_dir_helper_text():
    """Test output_dir_textfield has correct helper text."""
    with patch('app.task_session.TaskSession', _MockSession):
        from app.views.extractor_view import ExtractorView
        view = ExtractorView(mock_page(), mock_filepicker())

        helper_text = view.output_dir_textfield.helper
        assert '未指定時自動產生' in helper_text
        assert '路徑 + 設定名稱' in helper_text