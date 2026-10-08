"""#134：設定頁由 schema 產生；新增設定只需 DEFAULT_CONFIG + 一個 Setting。

這些測試強制 schema 與 ``DEFAULT_CONFIG``、實際設定頁、版面、套用時機一致，
並證明新增一個 Setting 會自動出現在設定頁、可載入、可儲存。
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config_apply import ALL_TIMINGS, DEFAULT_TIMING, get_apply_rule
from app.views.config import settings_schema as schema
from app.views.config.config_actions import (
    load_config_into_view,
    save_config_from_view,
)
from tests.conftest import mock_page
from translation_tool.utils.config_manager import DEFAULT_CONFIG

_OPAQUE = {"lm_translator.models"}  # 使用者自訂 mapping：整體當成一個設定


def _leaves(data: dict, prefix: str = ""):
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value and path not in _OPAQUE:
            yield from _leaves(value, path)
        else:
            yield path


def _default(path: str):
    node = DEFAULT_CONFIG
    for key in path.split("."):
        node = node[key]
    return node


# --- schema 與 DEFAULT_CONFIG 一致 -----------------------------------------


def test_every_default_setting_has_a_schema_entry():
    declared = set(schema.SETTINGS_BY_PATH)
    missing = [p for p in _leaves(DEFAULT_CONFIG) if p not in declared]
    assert not missing, (
        f"DEFAULT_CONFIG 有設定缺少 schema（請在 settings_schema.SETTINGS 新增）：{missing}"
    )


def test_schema_has_no_stale_entries():
    leaves = set(_leaves(DEFAULT_CONFIG))
    stale = [p for p in schema.SETTINGS_BY_PATH if p not in leaves]
    assert not stale, f"schema 內有 DEFAULT_CONFIG 已不存在的設定：{stale}"


def test_paths_are_unique():
    paths = [s.path for s in schema.SETTINGS]
    assert len(paths) == len(set(paths))


_KIND_TYPES = {
    "str": (str,),
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
    "text": (str,),
    "lines": (list,),
    "choice": (str,),
}


@pytest.mark.parametrize("setting", schema.editable_settings(), ids=lambda s: s.path)
def test_kind_matches_default_value_type(setting):
    value = _default(setting.path)
    assert isinstance(value, _KIND_TYPES[setting.kind]), (
        f"{setting.path}：kind={setting.kind} 與預設值型別 {type(value).__name__} 不符"
    )
    if setting.kind == "int":
        assert not isinstance(value, bool)


@pytest.mark.parametrize("setting", schema.editable_settings(), ids=lambda s: s.path)
def test_setting_definition_is_complete(setting):
    assert setting.label, "UI 設定必須有標籤"
    assert setting.page in {p["id"] for p in schema.NAV_PAGES}
    assert setting.card
    if setting.kind == "choice":
        assert setting.choices and _default(setting.path) in setting.choices
    if setting.validator:
        assert setting.validator in schema.VALIDATOR_NAMES
    assert setting.blank in {"default", "zero", "error"}


def test_excluded_settings_explain_why():
    for s in schema.SETTINGS:
        if s.kind == "none":
            assert s.reason, f"{s.path} 不在設定頁，必須說明原因"


def test_every_setting_has_an_apply_timing_answer():
    for path in _leaves(DEFAULT_CONFIG):
        rule = get_apply_rule(path)
        timing = rule["timing"] if rule else DEFAULT_TIMING
        assert timing in ALL_TIMINGS, path


# --- 版面 -----------------------------------------------------------------


def test_layout_references_only_editable_settings_and_places_each_once():
    editable = {s.path for s in schema.editable_settings()}
    # 手寫 LAYOUT 不得引用不存在或重複的設定
    explicit = schema.layout_paths(schema.LAYOUT)
    assert set(explicit) <= editable, set(explicit) - editable
    assert len(explicit) == len(set(explicit)), "同一設定在 LAYOUT 出現多次"
    # 合併自動版面後，每個一般設定剛好出現一次
    resolved = schema.layout_paths(schema.resolved_layout())
    assert sorted(resolved) == sorted(editable)


def test_every_custom_block_has_a_builder():
    custom = {
        b.name
        for cards in schema.resolved_layout().values()
        for card in cards
        for b in card.blocks
        if isinstance(b, schema.Custom)
    }
    assert custom == {"keys", "models"}


# --- 真正的設定頁 -----------------------------------------------------------


def _config_view_paths() -> set[str]:
    from app.views.config_view import ConfigView

    return set(ConfigView(mock_page()).controls_map)


def test_settings_page_has_a_control_for_every_schema_setting():
    ui = _config_view_paths()
    expected = {s.path for s in schema.ui_settings()}
    assert expected <= ui
    # 設定頁不應有 schema 沒登記的控制項
    assert ui <= expected, ui - expected


def test_same_as_source_retry_setting_is_editable_on_translation_behavior_page():
    path = "lm_translator.retry_same_as_source"
    setting = schema.SETTINGS_BY_PATH[path]
    assert setting.kind == "bool"
    assert setting.default is True
    assert setting.page == "translation_behavior"
    assert setting.card == "基本設定"
    assert path in _config_view_paths()
    basic_card = next(
        card
        for card in schema.resolved_layout()["translation_behavior"]
        if card.title == "基本設定"
    )
    assert path in schema.layout_paths({"translation_behavior": (basic_card,)})


def test_settings_page_renders_all_cards_and_pages():
    from app.views.config_view import NAV_ITEMS, ConfigView

    view = ConfigView(mock_page())
    assert [i["id"] for i in NAV_ITEMS] == [p["id"] for p in schema.NAV_PAGES]
    assert set(view._content_containers) == {p["id"] for p in schema.NAV_PAGES}
    titles = [
        card.title for cards in schema.resolved_layout().values() for card in cards
    ]
    assert len(titles) == len(set(titles)), "卡片標題重複"
    assert titles  # 有內容


# --- 載入／儲存（由 schema 驅動）-----------------------------------------------


def _fake_view(extra_settings=()):
    from tests.test_config_actions import make_full_view

    view = make_full_view()
    view.load_config = MagicMock()
    view._success_color = MagicMock(return_value="green")
    for s in [*schema.editable_settings(), *extra_settings]:
        view.controls_map[s.path] = MagicMock(value=None, label="")
    return view


def _config():
    config = deepcopy(DEFAULT_CONFIG)
    config["lm_translator"]["models"] = {}
    config["lm_translator"]["keys"] = []
    return config


def _save(view, config):
    saved: dict = {}
    view.models_column.controls = [
        SimpleNamespace(
            _model_name="enabled-test-model",
            _checkbox=SimpleNamespace(label="enabled-test-model", value=True),
            _max_output_tokens=SimpleNamespace(value=""),
        )
    ]
    ok = save_config_from_view(
        view,
        load_config_json_fn=lambda: deepcopy(config),
        save_config_json_fn=lambda cfg: (saved.update(cfg), True)[1],
        validate_api_keys_from_ui_fn=lambda keys: None,
    )
    return ok, saved


def test_default_config_round_trips_through_the_settings_page():
    """載入 DEFAULT_CONFIG → 不改動 → 儲存：所有一般設定的值維持不變。"""
    config = _config()
    view = _fake_view()
    load_config_into_view(view, config)
    ok, saved = _save(view, config)
    assert ok
    for s in schema.editable_settings():
        assert schema.get_path(saved, s.path) == schema.get_path(config, s.path), s.path


def test_loaded_values_follow_kind():
    config = _config()
    view = _fake_view()
    load_config_into_view(view, config)
    cm = view.controls_map
    assert cm["lm_translator.token_budget_enabled"].value is True
    assert cm["lm_translator.temperature"].value == str(
        DEFAULT_CONFIG["lm_translator"]["temperature"]
    )
    skip = cm["lm_translator.translator.skip_terms"].value
    assert skip == "\n".join(
        DEFAULT_CONFIG["lm_translator"]["translator"]["skip_terms"]
    )
    assert cm["logging.log_level"].value == DEFAULT_CONFIG["logging"]["log_level"]


def test_label_templates_show_current_values():
    config = _config()
    config["lang_merger"]["pending_folder_name"] = "待翻"
    config["lang_merger"]["pending_organized_folder_name"] = "整理X"
    config["lang_merger"]["filtered_pending_min_count"] = 7
    view = _fake_view()
    load_config_into_view(view, config)
    cm = view.controls_map
    assert (
        cm["lang_merger.pending_folder_name"].label == "待翻譯資料夾名稱（目前：待翻）"
    )
    assert (
        cm["lang_merger.pending_organized_folder_name"].label
        == "整理資料夾名稱（目前：整理X）"
    )
    assert (
        cm["lang_merger.filtered_pending_min_count"].label
        == "「整理X」key最小出現次數（目前：7）"
    )


def test_numeric_blank_minimum_and_invalid_handling():
    config = _config()
    view = _fake_view()
    load_config_into_view(view, config)
    cm = view.controls_map
    cm["lm_translator.budget_recover_after"].value = ""  # blank=default
    cm["lm_translator.max_output_tokens"].value = ""  # blank=zero
    cm["lm_translator.rpm_cooldown_sec"].value = "-5"  # minimum=0
    cm["lm_translator.translator.short_text_skip_len"].value = "-2"  # minimum=0
    cm["lm_translator.output_token_factor"].value = "2.25"
    ok, saved = _save(view, config)
    assert ok
    lm = saved["lm_translator"]
    assert (
        lm["budget_recover_after"]
        == DEFAULT_CONFIG["lm_translator"]["budget_recover_after"]
    )
    assert lm["max_output_tokens"] == 0
    assert lm["rpm_cooldown_sec"] == 0.0
    assert lm["translator"]["short_text_skip_len"] == 0
    assert lm["output_token_factor"] == 2.25

    cm["lm_translator.temperature"].value = ""  # blank=error
    ok, saved = _save(view, config)
    assert ok is False and saved == {}
    cm["lm_translator.temperature"].value = "abc"
    ok, saved = _save(view, config)
    assert ok is False and saved == {}


def test_lines_setting_strips_blank_lines():
    config = _config()
    view = _fake_view()
    load_config_into_view(view, config)
    view.controls_map["lm_translator.patchouli.dir_names"].value = " a \n\n  b\n"
    ok, saved = _save(view, config)
    assert ok and saved["lm_translator"]["patchouli"]["dir_names"] == ["a", "b"]


def test_invalid_log_format_is_rejected_by_validator():
    config = _config()
    view = _fake_view()
    load_config_into_view(view, config)
    view.controls_map["logging.log_format"].value = "%(nope)s"
    ok, saved = _save(view, config)
    assert ok is False and saved == {}


# --- 新增設定只需要一個 Setting（default／timing／sensitive 都在 schema）----------------


@pytest.fixture
def with_new_setting(monkeypatch):
    from translation_tool.utils import config_schema

    new = schema.Setting(
        "translator.brand_new_option",
        "int",
        "全新設定",
        "general",
        schema.C_TRANSLATOR,
        "示範：新增設定只需 schema 一列",
        default=5,
        minimum=1,
        timing="next_batch",
        timing_note="示範：下一批次讀取。",
    )
    monkeypatch.setattr(config_schema, "SETTINGS", (*config_schema.SETTINGS, new))
    monkeypatch.setitem(config_schema.SETTINGS_BY_PATH, new.path, new)
    return new


def test_new_setting_default_comes_from_the_schema(with_new_setting):
    from translation_tool.utils.config_schema import build_default_config

    assert build_default_config()["translator"]["brand_new_option"] == 5


def test_new_setting_appears_in_its_card_automatically(with_new_setting):
    layout = schema.resolved_layout()
    card = next(c for c in layout["general"] if c.title == schema.C_TRANSLATOR)
    assert schema.F(with_new_setting.path) in card.blocks


def test_new_setting_gets_a_control_loads_and_saves(with_new_setting):
    from app.views.config.settings_form import build_controls

    controls: dict = {}
    build_controls(controls)
    assert with_new_setting.path in controls
    assert controls[with_new_setting.path].label == "全新設定"

    config = _config()
    config["translator"]["brand_new_option"] = 9
    view = _fake_view(extra_settings=[with_new_setting])
    load_config_into_view(view, config)
    assert view.controls_map[with_new_setting.path].value == "9"
    view.controls_map[with_new_setting.path].value = "0"  # 低於 minimum
    ok, saved = _save(view, config)
    assert ok and saved["translator"]["brand_new_option"] == 1


def test_sensitive_setting_values_are_registered_as_secrets(monkeypatch):
    """schema 標示 sensitive 的設定，載入時登錄為已知機密（不再硬編碼金鑰路徑）。"""
    from translation_tool.utils import config_manager, config_schema, redaction

    redaction._known_secrets.clear()
    extra = config_schema.Setting(
        "translator.api_secret_demo", "str", default="", sensitive=True
    )
    monkeypatch.setattr(config_schema, "SETTINGS", (*config_schema.SETTINGS, extra))
    values = list(
        config_manager._sensitive_values(
            {"translator": {"api_secret_demo": "demo-secret-value-123"}}
        )
    )
    assert values == ["demo-secret-value-123"]
    redaction._known_secrets.clear()


# --- schema 是預設值、套用時機、敏感資訊的唯一來源 --------------------------------


def test_default_config_is_built_from_the_schema():
    from translation_tool.utils.config_schema import build_default_config

    assert DEFAULT_CONFIG == build_default_config()


def test_apply_rules_are_derived_from_the_schema():
    from app.config_apply import CONFIG_APPLY_RULES, timing_of

    for setting in schema.SETTINGS:
        assert timing_of(setting.path) == setting.timing, setting.path
        if setting.timing != DEFAULT_TIMING or setting.timing_note:
            assert CONFIG_APPLY_RULES[setting.path]["timing"] == setting.timing


def test_every_schema_timing_is_known_and_non_default_timing_is_explained():
    from translation_tool.utils.config_schema import TIMINGS

    for setting in schema.SETTINGS:
        assert setting.timing in TIMINGS, setting.path
        if setting.timing != DEFAULT_TIMING:
            assert setting.timing_note, f"{setting.path} 的套用時機需要說明文字"


def test_secret_like_settings_are_marked_sensitive():
    """名稱看起來像機密的設定必須標示 sensitive，避免新增後漏登錄遮蔽。"""
    import re

    from translation_tool.utils.config_schema import sensitive_paths

    secret_like = re.compile(
        r"(api[_-]?key|secret|password|credential)|\.keys$", re.IGNORECASE
    )
    for setting in schema.SETTINGS:
        if secret_like.search(setting.path):
            assert setting.path in sensitive_paths(), setting.path
    assert sensitive_paths() == {"lm_translator.keys"}
