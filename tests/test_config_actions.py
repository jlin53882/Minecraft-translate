"""Tests for app.views.config.config_actions (load_config_into_view, save_config_from_view)"""

import pytest
from unittest.mock import MagicMock


def make_full_view():
    """Create a mock view with all controls_map keys that load_config_into_view accesses."""
    class MinimalView:
        pass
    view = MinimalView()
    view.controls_map = {}
    view.DEFAULT_MODELS = {'gemini-2.5-flash': True}
    view.add_model_row = MagicMock()
    view.models_column = MagicMock()
    view.keys_column = MagicMock()
    view.key_fields = []
    keys = [
        'logging.log_level', 'logging.log_dir',
        'translator.output_dir_name', 'translator.replace_rules_path',
        'translator.cache_directory', 'translator.enable_cache_saving',
        'translator.parallel_execution_workers',
        'ftb_translator.output_dir_name',
        'species_cache.cache_directory', 'species_cache.cache_filename',
        'species_cache.wikipedia_language', 'species_cache.wikipedia_rate_limit_delay',
        'output_bundler.output_zip_name',
        'lang_merger.pending_folder_name', 'lang_merger.pending_organized_folder_name',
        'lang_merger.filtered_pending_min_count', 'lang_merger.quarantine_folder_name',
        'lm_translator.temperature', 'lm_translator.lm_translate_folder_name',
        'lm_translator.rate_limit.timeout', 'lm_translator.rate_limit.sleep_seconds_between_batches',
        'lm_translator.patchouli_system_prompt', 'lm_translator.lang_system_prompt',
        'lm_translator.initial_batch_size_patchouli', 'lm_translator.initial_batch_size_lang',
        'lm_translator.initial_batch_size_ftb', 'lm_translator.initial_batch_size_kubejs',
        'lm_translator.initial_batch_size_md', 'lm_translator.min_batch_size',
        'lm_translator.batch_shrink_factor',
        'lm_translator.patchouli.dir_names',
        'lm_translator.translator.skip_terms', 'lm_translator.translator.translatable_keywords',
    ]
    for k in keys:
        view.controls_map[k] = MagicMock()
    return view


class TestLoadConfigIntoViewLangMergerLabels:
    """Tests that load_config_into_view sets dynamic labels for lang_merger fields."""

    def test_lang_merger_pending_folder_label_shows_current_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯',
                'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3,
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'patchouli_system_prompt': 'p',
                'lang_system_prompt': 'l', 'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lang_merger.pending_folder_name'].label == "待翻譯資料夾名稱（目前：待翻譯）"
        assert view.controls_map['lang_merger.pending_organized_folder_name'].label == "整理資料夾名稱（目前：整理）"
        assert view.controls_map['lang_merger.filtered_pending_min_count'].label == "「整理」key最小出現次數（目前：3）"

    def test_lang_merger_labels_use_custom_folder_names(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': 'MY_PENDING',
                'pending_organized_folder_name': 'MY_ORGANIZED',
                'filtered_pending_min_count': 5,
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'patchouli_system_prompt': 'p',
                'lang_system_prompt': 'l', 'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lang_merger.pending_folder_name'].label == "待翻譯資料夾名稱（目前：MY_PENDING）"
        assert view.controls_map['lang_merger.pending_organized_folder_name'].label == "整理資料夾名稱（目前：MY_ORGANIZED）"
        assert view.controls_map['lang_merger.filtered_pending_min_count'].label == "「MY_ORGANIZED」key最小出現次數（目前：5）"

    def test_lang_merger_labels_fall_back_to_defaults_when_missing(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {},
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'patchouli_system_prompt': 'p',
                'lang_system_prompt': 'l', 'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lang_merger.pending_folder_name'].label == "待翻譯資料夾名稱（目前：待翻譯）"
        assert view.controls_map['lang_merger.pending_organized_folder_name'].label == "整理資料夾名稱（目前：待翻譯整理需翻譯）"
        assert view.controls_map['lang_merger.filtered_pending_min_count'].label == "「待翻譯整理需翻譯」key最小出現次數（目前：3）"


class TestLoadConfigIntoViewPrompts:
    """Tests that load_config_into_view loads prompt values correctly."""

    def test_loads_patchouli_system_prompt_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        prompt_text = "Custom Patchouli Prompt"
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3, 'quarantine_folder_name': 'q',
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'patchouli_system_prompt': prompt_text,
                'lang_system_prompt': 'Lang Prompt',
                'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lm_translator.patchouli_system_prompt'].value == prompt_text

    def test_loads_lang_system_prompt_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        prompt_text = "Custom Lang Prompt"
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3, 'quarantine_folder_name': 'q',
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'patchouli_system_prompt': 'Patchouli',
                'lang_system_prompt': prompt_text,
                'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lm_translator.lang_system_prompt'].value == prompt_text


class TestLoadConfigIntoViewBatchSizes:
    """Tests that load_config_into_view loads batch size values correctly."""

    def test_loads_batch_shrink_factor_default_0_5(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3, 'quarantine_folder_name': 'q',
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'batch_shrink_factor': 0.5,
                'patchouli_system_prompt': 'p', 'lang_system_prompt': 'l',
                'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lm_translator.batch_shrink_factor'].value == 0.5

    def test_loads_batch_shrink_factor_custom_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3, 'quarantine_folder_name': 'q',
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {}, 'batch_shrink_factor': 0.3,
                'patchouli_system_prompt': 'p', 'lang_system_prompt': 'l',
                'translator': {'skip_terms': [], 'translatable_keywords': []},
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map['lm_translator.batch_shrink_factor'].value == 0.3


class TestLoadConfigIntoViewSkipTerms:
    """Tests skip_terms and translatable_keywords loading with full example.json defaults."""

    def test_skip_terms_has_22_items_from_example(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3, 'quarantine_folder_name': 'q',
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {},
                'patchouli_system_prompt': 'p', 'lang_system_prompt': 'l',
                'translator': {
                    'skip_terms': [
                        'api documentation', 'api docs', 'documentation', 'discord', 'github',
                        'homepage', 'mod page', 'modpack', 'official website', 'patreon',
                        'Twitter', 'Modrinth', 'CurseForge', 'Crowdin', 'Twitch', 'Wiki',
                        'Minecraft', 'Forge', 'YouTube', 'Reddit', 'Ko-fi', 'Flattr',
                    ],
                    'translatable_keywords': [],
                },
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        expected = (
            'api documentation\napi docs\ndocumentation\ndiscord\ngithub\nhomepage\n'
            'mod page\nmodpack\nofficial website\npatreon\nTwitter\nModrinth\nCurseForge\n'
            'Crowdin\nTwitch\nWiki\nMinecraft\nForge\nYouTube\nReddit\nKo-fi\nFlattr'
        )
        assert view.controls_map['lm_translator.translator.skip_terms'].value == expected

    def test_translatable_keywords_has_18_items_from_example(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            'logging': {'log_level': 'INFO', 'log_dir': 'logs'},
            'translator': {}, 'ftb_translator': {}, 'species_cache': {},
            'output_bundler': {}, 'lang_merger': {
                'pending_folder_name': '待翻譯', 'pending_organized_folder_name': '整理',
                'filtered_pending_min_count': 3, 'quarantine_folder_name': 'q',
            },
            'lm_translator': {
                'temperature': 0.3, 'rate_limit': {},
                'patchouli_system_prompt': 'p', 'lang_system_prompt': 'l',
                'translator': {
                    'skip_terms': [],
                    'translatable_keywords': [
                        'text', 'name', 'title', 'description', 'subtitle', 'hover', 'note',
                        'warning', 'quote', 'paragraph', 'body', 'header', 'footer',
                        'heading', 'effects', 'category', 'link_text', 'pages.title',
                    ],
                },
                'patchouli': {'dir_names': ['patchouli_books']},
            },
        }

        load_config_into_view(view, cfg)

        expected = (
            'text\nname\ntitle\ndescription\nsubtitle\nhover\nnote\nwarning\nquote\n'
            'paragraph\nbody\nheader\nfooter\nheading\neffects\ncategory\nlink_text\npages.title'
        )
        assert view.controls_map['lm_translator.translator.translatable_keywords'].value == expected