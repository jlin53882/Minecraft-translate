"""PR-A regression tests for settings with previously missing consumers."""

from copy import deepcopy
from unittest.mock import MagicMock, patch


def test_config_ui_round_trips_pr_a_settings():
    from app.views.config.config_actions import (
        load_config_into_view,
        save_config_from_view,
    )
    from tests.test_config_actions import make_full_view
    from translation_tool.utils.config_manager import DEFAULT_CONFIG

    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["models"] = {}
    config["lm_translator"]["keys"] = []
    view = make_full_view()
    view.load_config = MagicMock()
    view._success_color = MagicMock(return_value="green")

    load_config_into_view(view, config)
    assert (
        view.controls_map["logging.log_format"].value
        == DEFAULT_CONFIG["logging"]["log_format"]
    )
    assert (
        view.controls_map["translator.custom_translator_folder"].value
        == "custom_translators"
    )
    assert view.controls_map["extractor.skip_zh_cn_extract"].value is False

    view.controls_map["logging.log_format"].value = "%(levelname)s :: %(message)s"
    view.controls_map["translator.custom_translator_folder"].value = "my_translations"
    view.controls_map["extractor.skip_zh_cn_extract"].value = True
    saved = {}
    save_config_from_view(
        view,
        load_config_json_fn=lambda: deepcopy(config),
        save_config_json_fn=saved.update,
        validate_api_keys_from_ui_fn=lambda keys: None,
    )

    assert saved["logging"]["log_format"] == "%(levelname)s :: %(message)s"
    assert saved["translator"]["custom_translator_folder"] == "my_translations"
    assert saved["extractor"]["skip_zh_cn_extract"] is True


def test_skip_zh_cn_config_controls_actual_lang_code_selection():
    from app.services_impl.pipelines.extract_service import get_lang_codes

    with patch(
        "app.services_impl.pipelines.extract_service.load_config",
        return_value={"extractor": {"skip_zh_cn_extract": True}},
    ):
        assert "zh_cn" not in get_lang_codes()
        assert "zh_cn" in get_lang_codes(skip_zh_cn=False)


def test_saved_log_format_is_used_by_ui_handler():
    from app.services_impl.logging_service import UI_LOG_HANDLER, update_logger_config

    update_logger_config(
        lambda: {"logging": {"log_level": "INFO", "log_format": "CUSTOM %(message)s"}},
        logger_name="pr_a_test_logger",
    )

    assert UI_LOG_HANDLER.formatter._fmt == "CUSTOM %(message)s"


def test_ftb_consumer_reads_saved_custom_translator_folder(tmp_path):
    from translation_tool.core import ftb_translator

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    loaded_paths = []
    config = {
        "ftb_translator": {"output_dir_name": "out"},
        "translator": {
            "custom_translator_folder": "configured_translations",
            "replace_rules_path": "replace_rules.json",
            "parallel_execution_workers": 1,
        },
    }

    with (
        patch.object(ftb_translator, "load_config", return_value=config),
        patch.object(ftb_translator, "load_replace_rules", return_value={}),
        patch.object(
            ftb_translator,
            "load_custom_translations",
            side_effect=lambda path: loaded_paths.append(path) or {},
        ),
    ):
        list(ftb_translator.translate_directory_generator(str(input_dir)))

    assert loaded_paths == ["configured_translations"]


def test_pr_a_legacy_settings_are_explicitly_compatibility_only():
    from translation_tool.utils.config_manager import DEPRECATED_CONFIG_KEYS

    assert DEPRECATED_CONFIG_KEYS == {
        "extractor.target_language",
        "translator.cjk_ratio_threshold",
    }
