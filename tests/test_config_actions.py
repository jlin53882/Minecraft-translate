"""Tests for app.views.config.config_actions (load_config_into_view, save_config_from_view)"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def make_full_view():
    """Create a mock view with all controls_map keys that load_config_into_view accesses."""

    class MinimalView:
        pass

    view = MinimalView()
    view.page = (
        MagicMock()
    )  # 2026-08-01 (PR #85 重構):show_snack(view.page, ...) 需要 view.page
    view.controls_map = {}
    view.DEFAULT_MODELS = {"gemini-2.5-flash": True}
    view.add_model_row = MagicMock()
    view.models_column = MagicMock()
    view.keys_column = MagicMock()
    view.key_fields = []
    keys = [
        "logging.log_level",
        "logging.log_dir",
        "logging.log_format",
        "translator.output_dir_name",
        "translator.replace_rules_path",
        "translator.cache_directory",
        "translator.enable_cache_saving",
        "translator.parallel_execution_workers",
        "translator.custom_translator_folder",
        "ftb_translator.output_dir_name",
        "species_cache.cache_directory",
        "species_cache.cache_filename",
        "species_cache.wikipedia_language",
        "species_cache.wikipedia_rate_limit_delay",
        "output_bundler.output_zip_name",
        "lang_merger.pending_folder_name",
        "lang_merger.pending_organized_folder_name",
        "lang_merger.filtered_pending_min_count",
        "lang_merger.quarantine_folder_name",
        "lang_merger.patchouli_skip_en_us_when_zh_cn_exists",
        "lang_merger.patchouli_effective_translation_threshold",
        "lang_merger.zh_en_letter_threshold",
        "lm_translator.temperature",
        "lm_translator.lm_translate_folder_name",
        "lm_translator.rate_limit.timeout",
        "lm_translator.rate_limit.sleep_seconds_between_batches",
        "lm_translator.patchouli_system_prompt",
        "lm_translator.lang_system_prompt",
        "lm_translator.initial_batch_size_patchouli",
        "lm_translator.initial_batch_size_lang",
        "lm_translator.initial_batch_size_ftb",
        "lm_translator.initial_batch_size_kubejs",
        "lm_translator.initial_batch_size_md",
        "lm_translator.min_batch_size",
        "lm_translator.batch_shrink_factor",
        "lm_translator.patchouli.dir_names",
        "lm_translator.translator.skip_terms",
        "lm_translator.translator.translatable_keywords",
        "extractor.output_folder_names.lang_extract",
        "extractor.output_folder_names.book_extract",
        "extractor.output_folder_names.lang_preview",
        "extractor.output_folder_names.book_preview",
        "extractor.output_folder_names.dual_extract",
        "extractor.output_folder_names.dual_preview",
        "extractor.skip_zh_cn_extract",
    ]
    for k in keys:
        view.controls_map[k] = MagicMock()
    view.controls_map["logging.log_format"].value = "%(message)s"
    # Saving tests that do not call load_config_into_view still model a real
    # settings form, whose six extractor suffix controls start at schema defaults.
    for key, value in {
        "lang_extract": "_提取lang_輸出",
        "book_extract": "_提取book_輸出",
        "dual_extract": "_提取both_輸出",
        "lang_preview": "_預覽lang_輸出",
        "book_preview": "_預覽book_輸出",
        "dual_preview": "_預覽both_輸出",
    }.items():
        view.controls_map[f"extractor.output_folder_names.{key}"].value = value
    return view


class TestLoadConfigIntoViewLangMergerLabels:
    """Tests that load_config_into_view sets dynamic labels for lang_merger fields."""

    def test_lang_merger_pending_folder_label_shows_current_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map["lang_merger.pending_folder_name"].label
            == "待翻譯資料夾名稱（目前：待翻譯）"
        )
        assert (
            view.controls_map["lang_merger.pending_organized_folder_name"].label
            == "整理資料夾名稱（目前：整理）"
        )
        assert (
            view.controls_map["lang_merger.filtered_pending_min_count"].label
            == "「整理」key最小出現次數（目前：3）"
        )

    def test_lang_merger_labels_use_custom_folder_names(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "MY_PENDING",
                "pending_organized_folder_name": "MY_ORGANIZED",
                "filtered_pending_min_count": 5,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map["lang_merger.pending_folder_name"].label
            == "待翻譯資料夾名稱（目前：MY_PENDING）"
        )
        assert (
            view.controls_map["lang_merger.pending_organized_folder_name"].label
            == "整理資料夾名稱（目前：MY_ORGANIZED）"
        )
        assert (
            view.controls_map["lang_merger.filtered_pending_min_count"].label
            == "「MY_ORGANIZED」key最小出現次數（目前：5）"
        )

    def test_lang_merger_labels_fall_back_to_defaults_when_missing(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "待翻譯整理需翻譯",
                "filtered_pending_min_count": 3,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map["lang_merger.pending_folder_name"].label
            == "待翻譯資料夾名稱（目前：待翻譯）"
        )
        assert (
            view.controls_map["lang_merger.pending_organized_folder_name"].label
            == "整理資料夾名稱（目前：待翻譯整理需翻譯）"
        )
        assert (
            view.controls_map["lang_merger.filtered_pending_min_count"].label
            == "「待翻譯整理需翻譯」key最小出現次數（目前：3）"
        )


class TestLoadConfigIntoViewPrompts:
    """Tests that load_config_into_view loads prompt values correctly."""

    def test_loads_patchouli_system_prompt_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        prompt_text = "Custom Patchouli Prompt"
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": prompt_text,
                "lang_system_prompt": "Lang Prompt",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map["lm_translator.patchouli_system_prompt"].value
            == prompt_text
        )

    def test_loads_lang_system_prompt_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        prompt_text = "Custom Lang Prompt"
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "Patchouli",
                "lang_system_prompt": prompt_text,
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map["lm_translator.lang_system_prompt"].value == prompt_text
        )


class TestLoadConfigIntoViewBatchSizes:
    """Tests that load_config_into_view loads batch size values correctly."""

    def test_loads_batch_shrink_factor_default_0_5(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "batch_shrink_factor": 0.5,
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map["lm_translator.batch_shrink_factor"].value == "0.5"

    def test_loads_batch_shrink_factor_custom_value(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "batch_shrink_factor": 0.3,
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map["lm_translator.batch_shrink_factor"].value == "0.3"


class TestLoadConfigIntoViewSkipTerms:
    """Tests skip_terms and translatable_keywords loading with full example.json defaults."""

    def test_skip_terms_has_22_items_from_example(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {
                    "skip_terms": [
                        "api documentation",
                        "api docs",
                        "documentation",
                        "discord",
                        "github",
                        "homepage",
                        "mod page",
                        "modpack",
                        "official website",
                        "patreon",
                        "Twitter",
                        "Modrinth",
                        "CurseForge",
                        "Crowdin",
                        "Twitch",
                        "Wiki",
                        "Minecraft",
                        "Forge",
                        "YouTube",
                        "Reddit",
                        "Ko-fi",
                        "Flattr",
                    ],
                    "translatable_keywords": [],
                },
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        expected = (
            "api documentation\napi docs\ndocumentation\ndiscord\ngithub\nhomepage\n"
            "mod page\nmodpack\nofficial website\npatreon\nTwitter\nModrinth\nCurseForge\n"
            "Crowdin\nTwitch\nWiki\nMinecraft\nForge\nYouTube\nReddit\nKo-fi\nFlattr"
        )
        assert (
            view.controls_map["lm_translator.translator.skip_terms"].value == expected
        )

    def test_translatable_keywords_has_18_items_from_example(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {
                    "skip_terms": [],
                    "translatable_keywords": [
                        "text",
                        "name",
                        "title",
                        "description",
                        "subtitle",
                        "hover",
                        "note",
                        "warning",
                        "quote",
                        "paragraph",
                        "body",
                        "header",
                        "footer",
                        "heading",
                        "effects",
                        "category",
                        "link_text",
                        "pages.title",
                    ],
                },
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        expected = (
            "text\nname\ntitle\ndescription\nsubtitle\nhover\nnote\nwarning\nquote\n"
            "paragraph\nbody\nheader\nfooter\nheading\neffects\ncategory\nlink_text\npages.title"
        )
        assert (
            view.controls_map["lm_translator.translator.translatable_keywords"].value
            == expected
        )


class TestLoadConfigIntoViewPatchouliSettings:
    """Tests that load_config_into_view loads the 3 new patchouli lang_merger fields."""

    def test_loads_patchouli_skip_en_us_switch_true(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
                "patchouli_skip_en_us_when_zh_cn_exists": True,
                "patchouli_effective_translation_threshold": 0.5,
                "zh_en_letter_threshold": 2,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map[
                "lang_merger.patchouli_skip_en_us_when_zh_cn_exists"
            ].value
            is True
        )

    def test_loads_patchouli_skip_en_us_switch_false(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
                "patchouli_skip_en_us_when_zh_cn_exists": False,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map[
                "lang_merger.patchouli_skip_en_us_when_zh_cn_exists"
            ].value
            is False
        )

    def test_loads_patchouli_effective_translation_threshold(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
                "patchouli_effective_translation_threshold": 0.7,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map[
                "lang_merger.patchouli_effective_translation_threshold"
            ].value
            == "0.7"
        )

    def test_loads_zh_en_letter_threshold(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
                "zh_en_letter_threshold": 5,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert view.controls_map["lang_merger.zh_en_letter_threshold"].value == "5"

    def test_loads_patchouli_settings_use_defaults(self):
        from app.views.config.config_actions import load_config_into_view

        view = make_full_view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "整理",
                "filtered_pending_min_count": 3,
                "quarantine_folder_name": "q",
                "patchouli_skip_en_us_when_zh_cn_exists": False,
                "patchouli_effective_translation_threshold": 0.5,
                "zh_en_letter_threshold": 2,
            },
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": ["patchouli_books"]},
            },
        }

        load_config_into_view(view, cfg)

        assert (
            view.controls_map[
                "lang_merger.patchouli_skip_en_us_when_zh_cn_exists"
            ].value
            is False
        )
        assert (
            view.controls_map[
                "lang_merger.patchouli_effective_translation_threshold"
            ].value
            == "0.5"
        )
        assert view.controls_map["lang_merger.zh_en_letter_threshold"].value == "2"


def _make_base_config():
    """Return a minimal config structure that save_config_from_view needs."""
    return {
        "logging": {
            "log_level": "INFO",
            "log_dir": "logs",
            "log_format": "%(message)s",
        },
        "translator": {},
        "ftb_translator": {},
        "species_cache": {},
        "output_bundler": {},
        "lang_merger": {"pending_folder_name": "待翻譯"},
        "lm_translator": {
            "temperature": 0.3,
            "rate_limit": {},
            "translator": {},
            "patchouli": {"dir_names": []},
        },
        "extractor": {"output_folder_names": {}},
    }


def _make_full_save_view():
    """Create a mock view for save_config_from_view tests with all required attributes."""
    view = make_full_view()
    view.load_config = MagicMock()
    view.models_column.controls = [
        SimpleNamespace(
            _model_name="test-model",
            _checkbox=SimpleNamespace(label="test-model", value=True),
            _max_output_tokens=SimpleNamespace(value=""),
        )
    ]
    # view 刪除 _show_snack_bar,SnackBar 顯示由 show_snack 函式處理
    view._success_color = MagicMock(return_value="green")
    return view


class TestSaveConfigFromViewPatchouliFields:
    """Tests that save_config_from_view saves the 3 new patchouli lang_merger fields."""

    def test_saves_patchouli_skip_en_us_when_zh_cn_exists_true(self):
        from app.views.config.config_actions import save_config_from_view

        view = _make_full_save_view()
        view.controls_map[
            "lang_merger.patchouli_skip_en_us_when_zh_cn_exists"
        ].value = True

        saved = {}

        def load_fn():
            return _make_base_config()

        def save_fn(cfg):
            saved.update(cfg)
            return True

        save_config_from_view(
            view,
            load_config_json_fn=load_fn,
            save_config_json_fn=save_fn,
            validate_api_keys_from_ui_fn=lambda keys: None,
        )

        assert saved["lang_merger"]["patchouli_skip_en_us_when_zh_cn_exists"] is True

    def test_saves_patchouli_effective_translation_threshold(self):
        from app.views.config.config_actions import save_config_from_view

        view = _make_full_save_view()
        view.controls_map[
            "lang_merger.patchouli_effective_translation_threshold"
        ].value = "0.7"

        saved = {}

        def load_fn():
            return _make_base_config()

        def save_fn(cfg):
            saved.update(cfg)
            return True

        save_config_from_view(
            view,
            load_config_json_fn=load_fn,
            save_config_json_fn=save_fn,
            validate_api_keys_from_ui_fn=lambda keys: None,
        )

        assert saved["lang_merger"]["patchouli_effective_translation_threshold"] == 0.7

    def test_saves_zh_en_letter_threshold(self):
        from app.views.config.config_actions import save_config_from_view

        view = _make_full_save_view()
        view.controls_map["lang_merger.zh_en_letter_threshold"].value = "5"

        saved = {}

        def load_fn():
            return _make_base_config()

        def save_fn(cfg):
            saved.update(cfg)
            return True

        save_config_from_view(
            view,
            load_config_json_fn=load_fn,
            save_config_json_fn=save_fn,
            validate_api_keys_from_ui_fn=lambda keys: None,
        )

        assert saved["lang_merger"]["zh_en_letter_threshold"] == 5


class TestRpmCooldownSetting:
    """B8：每批翻譯後等待秒數可在設定頁調整（預設 0 = 不等待）。"""

    def _view(self):
        view = _make_full_save_view()
        view.controls_map["lm_translator.rpm_cooldown_sec"] = MagicMock()
        return view

    def test_load_defaults_to_zero(self):
        from app.views.config.config_actions import load_config_into_view

        view = self._view()
        cfg = {
            "logging": {"log_level": "INFO", "log_dir": "logs"},
            "translator": {},
            "ftb_translator": {},
            "species_cache": {},
            "output_bundler": {},
            "lang_merger": {"pending_folder_name": "待翻譯"},
            "lm_translator": {
                "temperature": 0.3,
                "rate_limit": {},
                "patchouli_system_prompt": "p",
                "lang_system_prompt": "l",
                "translator": {"skip_terms": [], "translatable_keywords": []},
                "patchouli": {"dir_names": []},
                "models": {},
            },
        }
        load_config_into_view(view, cfg)
        assert view.controls_map["lm_translator.rpm_cooldown_sec"].value == "0"

        cfg["lm_translator"]["rpm_cooldown_sec"] = 12
        load_config_into_view(view, cfg)
        assert view.controls_map["lm_translator.rpm_cooldown_sec"].value == "12"

    @pytest.mark.parametrize("raw, expected", [("3.5", 3.5), ("-2", 0.0), ("", 0.0)])
    def test_save(self, raw, expected):
        from app.views.config.config_actions import save_config_from_view

        view = self._view()
        view.controls_map["lm_translator.rpm_cooldown_sec"].value = raw
        saved = {}
        save_config_from_view(
            view,
            load_config_json_fn=_make_base_config,
            save_config_json_fn=lambda cfg: (saved.update(cfg), True)[1],
            validate_api_keys_from_ui_fn=lambda keys: None,
        )
        assert saved["lm_translator"]["rpm_cooldown_sec"] == expected


class TestPerModelMaxOutputTokens:
    """Per-model blank/zero/numeric values keep the three-way contract."""

    @staticmethod
    def _view(raw_cap):
        view = _make_full_save_view()
        row = SimpleNamespace(
            _checkbox=SimpleNamespace(label="demo-model", value=True),
            _max_output_tokens=SimpleNamespace(value=raw_cap),
        )
        view.models_column.controls = [row]
        return view

    @staticmethod
    def _save(view):
        from app.views.config.config_actions import save_config_from_view

        saved = {}

        def load_fn():
            cfg = _make_base_config()
            cfg["lm_translator"]["models"] = {
                "demo-model": {"enabled": True, "max_output_tokens": 123}
            }
            return cfg

        save_config_from_view(
            view,
            load_config_json_fn=load_fn,
            save_config_json_fn=lambda cfg: (saved.update(cfg), True)[1],
            validate_api_keys_from_ui_fn=lambda keys: None,
        )
        return saved["lm_translator"]["models"]["demo-model"]

    def test_blank_removes_previous_override_and_falls_back_to_global(self):
        saved_model = self._save(self._view(""))
        assert "max_output_tokens" not in saved_model

    def test_zero_keeps_explicit_zero_override(self):
        saved_model = self._save(self._view("0"))
        assert saved_model["max_output_tokens"] == 0

    def test_numeric_value_keeps_explicit_override(self):
        saved_model = self._save(self._view("400"))
        assert saved_model["max_output_tokens"] == 400


class TestKeyFailureCooldownSetting:
    """The UI round-trip preserves the validator's numeric cooldown contract."""

    @staticmethod
    def _view():
        view = _make_full_save_view()
        view.controls_map["lm_translator.key_failure_cooldown_sec"] = MagicMock()
        return view

    @staticmethod
    def _round_trip(raw_config):
        from copy import deepcopy

        from app.views.config.config_actions import (
            load_config_into_view,
            save_config_from_view,
        )
        from translation_tool.utils.config_manager import DEFAULT_CONFIG

        view = TestKeyFailureCooldownSetting._view()
        config = deepcopy(DEFAULT_CONFIG)
        for section, values in raw_config.items():
            if isinstance(values, dict) and isinstance(config.get(section), dict):
                config[section].update(deepcopy(values))
            else:
                config[section] = deepcopy(values)
        config["lm_translator"]["models"] = {}
        config["lm_translator"]["keys"] = []
        load_config_into_view(view, config)
        view.models_column.controls = [
            SimpleNamespace(
                _model_name="enabled-test-model",
                _checkbox=SimpleNamespace(label="enabled-test-model", value=True),
                _max_output_tokens=SimpleNamespace(value=""),
            )
        ]
        saved = {}
        save_config_from_view(
            view,
            load_config_json_fn=lambda: deepcopy(config),
            save_config_json_fn=lambda cfg: (saved.update(cfg), True)[1],
            validate_api_keys_from_ui_fn=lambda keys: None,
        )
        return view, saved

    def test_fractional_cooldown_round_trips_through_ui(self):
        config = _make_base_config()
        config["lm_translator"]["key_failure_cooldown_sec"] = 0.5
        config["lm_translator"]["models"] = {}

        view, saved = self._round_trip(config)

        assert (
            view.controls_map["lm_translator.key_failure_cooldown_sec"].value == "0.5"
        )
        assert saved["lm_translator"]["key_failure_cooldown_sec"] == 0.5

    def test_unrelated_save_preserves_fractional_cooldown(self):
        config = _make_base_config()
        config["lm_translator"]["key_failure_cooldown_sec"] = 0.5
        config["lm_translator"]["models"] = {}
        view = self._view()

        from copy import deepcopy

        from app.views.config.config_actions import (
            load_config_into_view,
            save_config_from_view,
        )
        from translation_tool.utils.config_manager import DEFAULT_CONFIG

        merged_config = deepcopy(DEFAULT_CONFIG)
        for section, values in config.items():
            if isinstance(values, dict) and isinstance(
                merged_config.get(section), dict
            ):
                merged_config[section].update(deepcopy(values))
            else:
                merged_config[section] = deepcopy(values)
        merged_config["lm_translator"]["keys"] = []

        load_config_into_view(view, merged_config)
        view.models_column.controls = [
            SimpleNamespace(
                _model_name="enabled-test-model",
                _checkbox=SimpleNamespace(label="enabled-test-model", value=True),
                _max_output_tokens=SimpleNamespace(value=""),
            )
        ]
        view.controls_map["logging.log_dir"].value = "other-logs"
        saved = {}
        save_config_from_view(
            view,
            load_config_json_fn=lambda: deepcopy(merged_config),
            save_config_json_fn=lambda cfg: (saved.update(cfg), True)[1],
            validate_api_keys_from_ui_fn=lambda keys: None,
        )

        assert saved["logging"]["log_dir"] == "other-logs"
        assert saved["lm_translator"]["key_failure_cooldown_sec"] == 0.5

    def test_zero_cooldown_round_trips_as_disabled_value(self):
        config = _make_base_config()
        config["lm_translator"]["key_failure_cooldown_sec"] = 0.0
        config["lm_translator"]["models"] = {}

        _, saved = self._round_trip(config)

        assert saved["lm_translator"]["key_failure_cooldown_sec"] == 0.0


class TestConfigSaveFailureContracts:
    """The settings UI must distinguish validation, persistence, and refresh outcomes."""

    @staticmethod
    def _save(view, *, config=None, writer=None, monkeypatch=None, snacks=None):
        from app.views.config.config_actions import save_config_from_view

        if monkeypatch is not None:
            monkeypatch.setattr(
                "app.views.config.config_actions.show_snack",
                lambda _page, message, *_args: snacks.append(message),
            )
        return save_config_from_view(
            view,
            load_config_json_fn=lambda: config or _make_base_config(),
            save_config_json_fn=writer or (lambda _config: True),
            validate_api_keys_from_ui_fn=lambda _keys: None,
        )

    def test_writer_false_keeps_view_dirty_and_reports_unconfirmed_persistence(
        self, monkeypatch
    ):
        view = _make_full_save_view()
        snacks = []
        result = self._save(
            view,
            writer=lambda _config: False,
            monkeypatch=monkeypatch,
            snacks=snacks,
        )

        assert result is False
        view.load_config.assert_not_called()
        assert "無法確認設定檔是否已更新" in snacks[-1]

    def test_writer_oserror_reports_persistence_as_unknown(self, monkeypatch):
        view = _make_full_save_view()
        snacks = []

        def writer(_config):
            raise OSError("write/readback status unknown")

        result = self._save(view, writer=writer, monkeypatch=monkeypatch, snacks=snacks)

        assert result is False
        view.load_config.assert_not_called()
        assert "請先檢查 config.json" in snacks[-1]

    def test_invalid_temperature_is_rejected_before_writer(self, monkeypatch):
        view = _make_full_save_view()
        view.controls_map["lm_translator.temperature"].value = "3.0"
        writes = []
        snacks = []

        result = self._save(
            view,
            writer=lambda config: writes.append(config) or True,
            monkeypatch=monkeypatch,
            snacks=snacks,
        )

        assert result is False
        assert writes == []
        assert "尚未嘗試寫入" in snacks[-1]

    @pytest.mark.parametrize("model_rows", [[], [False, False]])
    def test_cannot_remove_or_disable_every_existing_model(
        self, model_rows, monkeypatch
    ):
        view = _make_full_save_view()
        view.models_column.controls = [
            SimpleNamespace(
                _model_name=f"model-{index}",
                _checkbox=SimpleNamespace(label=f"model-{index}", value=enabled),
                _max_output_tokens=SimpleNamespace(value=""),
            )
            for index, enabled in enumerate(model_rows)
        ]
        existing = _make_base_config()
        existing["lm_translator"]["models"] = {"old": {"enabled": True}}
        writes = []
        snacks = []

        result = self._save(
            view,
            config=existing,
            writer=lambda config: writes.append(config) or True,
            monkeypatch=monkeypatch,
            snacks=snacks,
        )

        assert result is False
        assert writes == []
        assert "至少需要保留一個啟用中的模型" in snacks[-1]

    def test_confirmed_write_survives_view_reload_failure_as_distinct_outcome(
        self, monkeypatch
    ):
        from app.views.config.config_actions import (
            SaveOutcome,
            save_config_from_view_with_outcome,
        )

        view = _make_full_save_view()
        view.load_config.side_effect = RuntimeError("reload failed")
        snacks = []

        monkeypatch.setattr(
            "app.views.config.config_actions.show_snack",
            lambda _page, message, *_args: snacks.append(message),
        )
        result = save_config_from_view_with_outcome(
            view,
            load_config_json_fn=_make_base_config,
            save_config_json_fn=lambda _config: True,
            validate_api_keys_from_ui_fn=lambda _keys: None,
        )

        assert result is SaveOutcome.SAVED_RELOAD_FAILED
        assert "設定已寫入，但畫面重新載入失敗" in snacks[-1]

    def test_existing_zero_enabled_models_allow_unrelated_settings_save(
        self, monkeypatch
    ):
        view = _make_full_save_view()
        view.models_column.controls[0]._checkbox.value = False
        existing = _make_base_config()
        existing["lm_translator"]["models"] = {"test-model": {"enabled": False}}
        writes = []
        snacks = []

        result = self._save(
            view,
            config=existing,
            writer=lambda config: writes.append(config) or True,
            monkeypatch=monkeypatch,
            snacks=snacks,
        )

        assert result is True
        assert len(writes) == 1
        assert not any("至少需要保留" in message for message in snacks)

    def test_config_load_failure_is_reported_without_attempting_write(
        self, monkeypatch
    ):
        from app.views.config.config_actions import save_config_from_view

        view = _make_full_save_view()
        writes = []
        snacks = []
        monkeypatch.setattr(
            "app.views.config.config_actions.show_snack",
            lambda _page, message, *_args: snacks.append(message),
        )

        result = save_config_from_view(
            view,
            load_config_json_fn=lambda: (_ for _ in ()).throw(OSError("read failed")),
            save_config_json_fn=lambda config: writes.append(config) or True,
            validate_api_keys_from_ui_fn=lambda _keys: None,
        )

        assert result is False
        assert writes == []
        assert "設定驗證失敗" in snacks[-1]


def test_chatgpt_settings_save_without_gemini_key_validation():
    from app.views.config.config_actions import save_config_from_view

    view = _make_full_save_view()
    view.controls_map["lm_translator.provider"] = SimpleNamespace(value="chatgpt")
    view.controls_map["lm_translator.chatgpt_model"] = SimpleNamespace(
        value="gpt-5-codex"
    )
    view.collect_chatgpt_model_settings = MagicMock(
        return_value={"gpt-5-codex": {"max_input_token_budget": 18000}}
    )
    saved = {}

    result = save_config_from_view(
        view,
        load_config_json_fn=_make_base_config,
        save_config_json_fn=lambda config: (saved.update(config), True)[1],
        validate_api_keys_from_ui_fn=lambda _keys: pytest.fail(
            "ChatGPT OAuth must not validate Gemini API keys"
        ),
    )

    assert result is True
    assert saved["lm_translator"]["provider"] == "chatgpt"
    assert saved["lm_translator"]["chatgpt_model"] == "gpt-5-codex"
    assert saved["lm_translator"]["chatgpt_model_settings"] == {
        "gpt-5-codex": {"max_input_token_budget": 18000}
    }


def test_chatgpt_settings_save_does_not_require_an_enabled_gemini_model():
    from app.views.config.config_actions import save_config_from_view

    view = _make_full_save_view()
    view.controls_map["lm_translator.provider"] = SimpleNamespace(value="chatgpt")
    view.controls_map["lm_translator.chatgpt_model"] = SimpleNamespace(
        value="gpt-5-codex"
    )
    view.models_column.controls[0]._checkbox.value = False
    view.collect_chatgpt_model_settings = MagicMock(return_value={})
    saved = []

    result = save_config_from_view(
        view,
        load_config_json_fn=_make_base_config,
        save_config_json_fn=lambda config: (saved.append(config), True)[1],
        validate_api_keys_from_ui_fn=lambda _keys: pytest.fail(
            "ChatGPT mode must not validate Gemini API keys"
        ),
    )

    assert result is True
    assert len(saved) == 1
    assert saved[0]["lm_translator"]["models"]["test-model"]["enabled"] is False


def test_chatgpt_settings_save_requires_a_selected_chatgpt_model(monkeypatch):
    from app.views.config.config_actions import save_config_from_view

    view = _make_full_save_view()
    view.controls_map["lm_translator.provider"] = SimpleNamespace(value="chatgpt")
    view.controls_map["lm_translator.chatgpt_model"] = SimpleNamespace(value=" ")
    writes = []
    snacks = []
    monkeypatch.setattr(
        "app.views.config.config_actions.show_snack",
        lambda _page, message, *_args: snacks.append(message),
    )

    result = save_config_from_view(
        view,
        load_config_json_fn=_make_base_config,
        save_config_json_fn=lambda config: (writes.append(config), True)[1],
        validate_api_keys_from_ui_fn=lambda _keys: None,
    )

    assert result is False
    assert writes == []
    assert "選擇模型" in snacks[-1]
