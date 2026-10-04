"""#134：新增設定不能只改一處卻漏掉設定頁／套用時機——用測試強制。

DEFAULT_CONFIG 是預設值的單一來源。每個設定葉節點必須：
1. 出現在設定頁（ConfigView.controls_map），或登記在下方 UI_EXCLUDED 並說明原因；
2. 有套用時機說明（app.config_apply），或落在「預設：下次任務才生效」的明確預設。
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock

from app.config_apply import DEFAULT_TIMING, get_apply_rule
from app.views.config.config_actions import (
    LM_EXTRA_FIELDS,
    load_config_into_view,
    save_config_from_view,
)
from tests.conftest import mock_page
from translation_tool.utils.config_manager import (
    DEFAULT_CONFIG,
    DEPRECATED_CONFIG_KEYS,
)

# 不在設定頁編輯的設定，以及原因。新增項目前請先考慮直接加到設定頁。
UI_EXCLUDED = {
    "ui.theme_mode": "由側欄深淺色切換，不在設定頁",
    "jar_extractor.lang_codes": "提取流程固定語系清單，由提取頁處理",
    "lang_merger.process_zh_cn_files": "由合併頁的開關覆寫（merge_view）",
    "lang_merger.skip_zh_cn_when_only_process_lang": "由合併流程內部使用，尚無設定頁需求",
    **{
        path: "歷史相容欄位，不宣稱可調整（見 PR-A）" for path in DEPRECATED_CONFIG_KEYS
    },
}
# 使用者自訂的 mapping：整體當成一個設定，不逐 key 檢查
_OPAQUE = {"lm_translator.models"}


def _leaves(data: dict, prefix: str = ""):
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value and path not in _OPAQUE:
            yield from _leaves(value, path)
        else:
            yield path


def _config_view_paths() -> set[str]:
    from app.views.config_view import ConfigView

    view = ConfigView(mock_page())
    return set(view.controls_map)


def test_every_default_setting_is_on_the_settings_page_or_explicitly_excluded():
    ui = _config_view_paths()
    # 這些由專用控制項處理（金鑰欄位、模型列），不在 controls_map
    handled = {"lm_translator.keys", "lm_translator.models"}
    missing = [
        p
        for p in _leaves(DEFAULT_CONFIG)
        if p not in ui and p not in handled and p not in UI_EXCLUDED
    ]
    assert not missing, (
        "以下設定沒有出現在設定頁，也沒有登記在 UI_EXCLUDED（請加到設定頁，"
        f"或在此檔案說明原因）：{missing}"
    )


def test_ui_excluded_entries_are_real_settings():
    leaves = set(_leaves(DEFAULT_CONFIG))
    stale = [p for p in UI_EXCLUDED if p not in leaves]
    assert not stale, f"UI_EXCLUDED 內有已不存在的設定：{stale}"


def test_every_setting_has_an_apply_timing_answer():
    for path in _leaves(DEFAULT_CONFIG):
        rule = get_apply_rule(path)
        timing = rule["timing"] if rule else DEFAULT_TIMING
        assert timing in {
            "immediate",
            "next_task",
            "next_batch",
            "next_request",
            "next_reload",
            "restart",
        }, path


def test_extra_fields_round_trip_through_the_settings_page():
    from tests.test_config_actions import make_full_view

    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["models"] = {}
    config["lm_translator"]["keys"] = []
    view = make_full_view()
    view.load_config = MagicMock()
    view._success_color = MagicMock(return_value="green")
    for key, kind, *_ in LM_EXTRA_FIELDS:
        view.controls_map[f"lm_translator.{key}"] = MagicMock(
            value=False if kind == "bool" else ""
        )

    load_config_into_view(view, config)
    for key, kind, *_ in LM_EXTRA_FIELDS:
        default = DEFAULT_CONFIG["lm_translator"][key]
        shown = view.controls_map[f"lm_translator.{key}"].value
        assert shown == (bool(default) if kind == "bool" else str(default)), key

    view.controls_map["lm_translator.token_budget_enabled"].value = False
    view.controls_map["lm_translator.output_token_factor"].value = "2.25"
    view.controls_map["lm_translator.budget_recover_after"].value = "5"
    view.controls_map["lm_translator.batch_write_interval"].value = ""  # 留空 = 預設
    saved: dict = {}
    save_config_from_view(
        view,
        load_config_json_fn=lambda: deepcopy(config),
        save_config_json_fn=saved.update,
        validate_api_keys_from_ui_fn=lambda keys: None,
    )
    lm = saved["lm_translator"]
    assert lm["token_budget_enabled"] is False
    assert lm["output_token_factor"] == 2.25
    assert lm["budget_recover_after"] == 5 and isinstance(
        lm["budget_recover_after"], int
    )
    assert (
        lm["batch_write_interval"]
        == DEFAULT_CONFIG["lm_translator"]["batch_write_interval"]
    )


def test_invalid_extra_field_is_rejected_without_saving():
    from tests.test_config_actions import make_full_view

    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["models"] = {}
    config["lm_translator"]["keys"] = []
    view = make_full_view()
    view.load_config = MagicMock()
    view._success_color = MagicMock(return_value="green")
    for key, kind, *_ in LM_EXTRA_FIELDS:
        view.controls_map[f"lm_translator.{key}"] = MagicMock(
            value=False if kind == "bool" else ""
        )
    load_config_into_view(view, config)
    view.controls_map["lm_translator.budget_recover_after"].value = "abc"
    saved: dict = {}
    ok = save_config_from_view(
        view,
        load_config_json_fn=lambda: deepcopy(config),
        save_config_json_fn=saved.update,
        validate_api_keys_from_ui_fn=lambda keys: None,
    )
    assert ok is False and saved == {}
