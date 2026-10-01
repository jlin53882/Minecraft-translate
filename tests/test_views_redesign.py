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
