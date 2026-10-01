"""重新設計後各頁面的新行為（結構性測試；舊行為由各 *_characterization 測試負責）。"""

from __future__ import annotations

import flet as ft
import pytest

from app.ui import kit
from tests.conftest import mock_filepicker, mock_page


def _texts(control) -> list[str]:
    out = []
    stack = [control]
    while stack:
        node = stack.pop()
        if isinstance(node, ft.Text) and node.value:
            out.append(node.value)
        for attr in ("controls", "content"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                stack.extend(child)
            elif child is not None and not isinstance(child, str):
                stack.append(child)
    return out


# -- ChoiceCard -------------------------------------------------------------


def test_choice_card_selection_and_callback():
    seen: list[str] = []
    card = kit.ChoiceCard(
        "a", "標題", "說明", icon=ft.Icons.INFO, on_select=seen.append
    )
    assert card.selected is False and card._check.visible is False
    card._click()
    assert seen == ["a"]
    card.set_selected(True)
    assert (
        card.selected
        and card._check.visible
        and card.bgcolor != kit.ChoiceCard("b", "x").bgcolor
    )


# -- QC ---------------------------------------------------------------------


def test_qc_view_shows_only_the_selected_mode_panel():
    from app.views.qc_view import QCView

    view = QCView(mock_page(), mock_filepicker())
    visible = {k for k, p in view.mode_panels.items() if p.visible}
    assert visible == {"untranslated"}
    view.select_mode("compare_json")
    assert {k for k, p in view.mode_panels.items() if p.visible} == {"compare_json"}
    assert view.mode_cards["compare_json"].selected
    assert not view.mode_cards["untranslated"].selected
    view.select_mode("compare_tsv")
    assert {k for k, p in view.mode_panels.items() if p.visible} == {"compare_tsv"}


def test_qc_view_rejects_unknown_mode():
    from app.views.qc_view import QCView

    view = QCView(mock_page(), mock_filepicker())
    with pytest.raises(KeyError):
        view.select_mode("nope")


def test_qc_view_has_header_and_log_card():
    from app.views.qc_view import QCView

    texts = _texts(QCView(mock_page(), mock_filepicker()))
    assert "QC 品質檢驗" in texts and "處理日誌" in texts


# -- 查詢 -------------------------------------------------------------------


def test_lookup_recent_queries_are_deduplicated_newest_first_and_capped():
    from app.views.lookup_view import RECENT_LIMIT, LookupView

    view = LookupView(mock_page())
    assert view.recent_row.visible is False
    for name in ["a b", "c d", "a b"] + [f"x{i} y" for i in range(10)]:
        view._remember(name)
    assert view.recent_row.visible is True
    assert len(view._recent) == RECENT_LIMIT
    assert view._recent[0] == "x9 y"
    assert len(set(view._recent)) == len(view._recent)
    assert len(view.recent_row.controls) == RECENT_LIMIT


def test_lookup_recent_chip_triggers_lookup(monkeypatch):
    from app.views.lookup_view import LookupView

    view = LookupView(mock_page())
    calls = []
    monkeypatch.setattr(
        view, "single_lookup_clicked", lambda e: calls.append(view.single_input.value)
    )
    view._remember("Felis catus")
    view.recent_row.controls[0].on_click(None)
    assert calls == ["Felis catus"]
    view.single_input.disabled = True  # 查詢進行中不重複觸發
    view.recent_row.controls[0].on_click(None)
    assert calls == ["Felis catus"]


# -- § 格式碼 -----------------------------------------------------------------


def test_mc_text_parses_colors_and_formats():
    from app.ui.mc_text import MC_COLORS, parse_mc_text

    segs = parse_mc_text("§a繁體§l粗§r白")
    assert [s["text"] for s in segs] == ["繁體", "粗", "白"]
    assert segs[0]["color"] == MC_COLORS["a"] and not segs[0]["bold"]
    assert segs[1]["bold"] and segs[1]["color"] == MC_COLORS["a"]  # 粗體保留顏色
    assert segs[2]["color"] is None and not segs[2]["bold"]  # §r 重設


def test_mc_text_color_code_resets_formatting():
    from app.ui.mc_text import parse_mc_text

    segs = parse_mc_text("§l粗§c紅")
    assert segs[1]["color"] and not segs[1]["bold"]


def test_mc_text_keeps_unknown_codes_and_trailing_sign():
    from app.ui.mc_text import parse_mc_text

    assert "".join(s["text"] for s in parse_mc_text("a§zb§")) == "a§zb§"
    assert parse_mc_text("") == []


def test_mc_text_spans_match_segments():
    from app.ui.mc_text import mc_text_spans

    spans = mc_text_spans("§6金§nX")
    assert [s.text for s in spans] == ["金", "X"]
    assert spans[1].style.decoration == ft.TextDecoration.UNDERLINE


# -- 打包 ---------------------------------------------------------------------


def test_bundler_mcmeta_preview_matches_what_the_bundler_writes():
    import json

    from app.views.bundler_view import BundlerView

    view = BundlerView(mock_page(), mock_filepicker())
    version = next(iter(view.version_data))
    view._select_version(version)
    view.description_field.value = "§a繁體中文"
    view._update_preview()
    data = json.loads(view.mcmeta_text())
    info = view.version_data[version]
    assert data == {
        "pack": {
            "description": "§a繁體中文",
            "min_format": str(info["min_format"]),
            "max_format": str(info["max_format"]),
        }
    }
    assert view.mcmeta_view.value == view.mcmeta_text()
    assert view.preview_title.spans[0].text == "繁體中文"


def test_bundler_format_range_labels():
    from app.views.bundler_view import BundlerView

    assert (
        BundlerView._format_range({"min_format": 9, "max_format": 15}) == "format 9–15"
    )
    assert BundlerView._format_range({"min_format": 3, "max_format": 3}) == "format 3"
    assert BundlerView._format_range({}) == ""


def test_bundler_version_toggle_updates_icon_and_dropdown():
    from app.views.bundler_view import BundlerView

    view = BundlerView(mock_page(), mock_filepicker())
    view._toggle_version_expand(None)
    assert view.version_dropdown_container_ref.visible is True
    assert view._version_toggle_icon.name == ft.Icons.EXPAND_LESS
    version = next(iter(view.version_data))
    view._select_version(version)
    assert view.version_dropdown_container_ref.visible is False
    assert view._version_toggle_label.value == version
    assert view._version_toggle_format.value.startswith("format")


def test_bundler_missing_image_shows_placeholder():
    from app.views.bundler_view import BundlerView

    view = BundlerView(mock_page(), mock_filepicker())
    view.pack_image_field.value = "/no/such/pack.png"
    view._update_preview()
    assert isinstance(view.preview_image.content, ft.Icon)


# -- 規則 ---------------------------------------------------------------------


def _rules_view(monkeypatch, rules):
    from app.views.rules_view import RulesView

    monkeypatch.setattr(RulesView, "_initial_load", lambda self: None)
    view = RulesView(mock_page())
    view.all_rules_data = [dict(r) for r in rules]
    return view


def test_rules_live_test_applies_unsaved_rules(monkeypatch):
    view = _rules_view(
        monkeypatch, [{"from": "软件", "to": "軟體"}, {"from": "内存", "to": "記憶體"}]
    )
    view.test_input.value = "升级软件与内存"
    view.on_test_change()
    assert "軟體" in view.test_result.value and "記憶體" in view.test_result.value
    assert "文字有變更" in view.test_info.value


def test_rules_live_test_reports_no_match_and_clears(monkeypatch):
    view = _rules_view(monkeypatch, [{"from": "软件", "to": "軟體"}])
    view.test_input.value = "nothing here"
    view.on_test_change()
    assert view.test_result.value == "nothing here"
    assert "沒有符合" in view.test_info.value
    view.test_input.value = ""
    view.on_test_change()
    assert view.test_result.value == "" and view.test_info.value == ""


def test_rules_live_test_skips_invalid_regex_and_blank_rules(monkeypatch):
    view = _rules_view(
        monkeypatch,
        [{"from": "(", "to": "x"}, {"from": "", "to": "y"}, {"from": "甲", "to": "乙"}],
    )
    view.test_input.value = "甲甲"
    view.on_test_change()  # 壞掉的正則不可讓頁面出錯
    assert view.test_result.value == "乙乙"
    assert "1 條規則" in view.test_info.value


def test_rules_layout_has_table_footer_and_test_panel(monkeypatch):
    view = _rules_view(monkeypatch, [])
    assert view.prev_button in list(_walk_controls(view))
    assert view.test_input in list(_walk_controls(view))
    assert view.rules_table in list(_walk_controls(view))


def _walk_controls(control):
    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for c in child:
                yield from _walk_controls(c)
        elif child is not None and not isinstance(child, str):
            yield from _walk_controls(child)
