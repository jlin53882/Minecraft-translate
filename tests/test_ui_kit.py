"""app/ui/kit 共用元件測試：結構、純邏輯與互動行為（不需要 Page）。"""

from __future__ import annotations

import flet as ft
import pytest

from app.ui import kit
from app.ui.kit.basics import expand_kwargs
from app.ui.kit.gallery import build_gallery


def _walk(control: ft.Control):
    """走訪控制項樹（content / controls）。"""
    yield control
    children = list(getattr(control, "controls", None) or [])
    content = getattr(control, "content", None)
    if content is not None:
        children.append(content)
    for child in children:
        yield from _walk(child)


# -- expand_kwargs：Flet 1.0 對 expand=False 在 Row(wrap=True) 內會爆版 ----------


def test_expand_kwargs_only_passes_truthy_values():
    assert expand_kwargs(False) == {}
    assert expand_kwargs(0) == {}
    assert expand_kwargs(None) == {}
    assert expand_kwargs(True) == {"expand": True}
    assert expand_kwargs(2) == {"expand": 2}


def test_button_does_not_force_expand_by_default():
    assert kit.button("x").expand in (None, 0)  # 絕不是明確的 False
    assert kit.button("x").expand is not False
    assert kit.button("x", expand=True).expand is True


# -- button -----------------------------------------------------------------


@pytest.mark.parametrize("kind", kit.inputs.BUTTON_KINDS)
def test_button_kinds_build(kind):
    b = kit.button("文字", kind, icon=ft.Icons.PLAY_ARROW)
    assert isinstance(b, ft.Button)
    assert b.content == "文字"


def test_button_rejects_unknown_kind():
    with pytest.raises(ValueError):
        kit.button("x", "nope")


def test_button_sizes_map_to_heights():
    assert kit.button("x", size="sm").height == 30
    assert kit.button("x").height == 36
    assert kit.button("x", size="lg").height == 44


def test_button_click_handler_is_forwarded():
    called = []
    b = kit.button("x", on_click=lambda e: called.append(e))
    b.on_click("event")
    assert called == ["event"]


# -- chip / badge -----------------------------------------------------------


def test_chip_has_text_and_optional_parts():
    c = kit.chip("完成", "em", icon=ft.Icons.CHECK, dot=True)
    row = c.content
    assert isinstance(row, ft.Row)
    kinds = [type(x) for x in row.controls]
    assert kinds == [ft.Container, ft.Icon, ft.Text]
    assert row.controls[-1].value == "完成"


def test_count_badge_does_not_stretch():
    # 帶 alignment 的 Container 在 Row(wrap=True) 內會被撐滿整列
    badge = kit.count_badge(28, "red")
    assert badge.alignment is None
    assert any(isinstance(c, ft.Text) and c.value == "28" for c in _walk(badge))


# -- page_window（純函式）---------------------------------------------------


@pytest.mark.parametrize(
    ("current", "total", "expected"),
    [
        (1, 0, []),
        (1, 1, [1]),
        (1, 7, [1, 2, 3, 4, 5, 6, 7]),
        (1, 26, [1, 2, 3, 4, 5, "…", 26]),
        (13, 26, [1, "…", 11, 12, 13, 14, 15, "…", 26]),
        (26, 26, [1, "…", 22, 23, 24, 25, 26]),
    ],
)
def test_page_window(current, total, expected):
    assert kit.page_window(current, total) == expected


def test_page_window_clamps_out_of_range_current():
    assert kit.page_window(99, 26) == kit.page_window(26, 26)
    assert kit.page_window(-3, 26) == kit.page_window(1, 26)


def test_page_window_always_contains_first_last_and_current():
    for total in (8, 9, 20, 100):
        for current in range(1, total + 1):
            window = kit.page_window(current, total)
            assert window[0] == 1
            assert window[-1] == total
            assert current in window


# -- Pager ------------------------------------------------------------------


def test_pager_summary_and_pages():
    p = kit.Pager(1842, page=3, page_size=8)
    assert p.total_pages == 231
    assert p.current_page == 3
    assert "17" in p.summary.value and "24" in p.summary.value
    assert "1,842" in p.summary.value


def test_pager_empty():
    p = kit.Pager(0)
    assert p.total_pages == 1
    assert p.summary.value == "沒有資料"


def test_pager_goto_notifies_once_and_clamps():
    seen: list[int] = []
    p = kit.Pager(100, page_size=10, on_change=seen.append)
    p.goto(4)
    p.goto(4)  # 同一頁不重複通知
    p.goto(999)  # 超出範圍 → 夾到最後一頁
    p.goto(0)
    assert seen == [4, 10, 1]
    assert p.current_page == 1


def test_pager_set_state_does_not_notify_and_clamps_page():
    seen: list[int] = []
    p = kit.Pager(100, page=10, page_size=10, on_change=seen.append)
    p.set_state(30)  # 只剩 3 頁 → 目前頁被夾到 3
    assert p.current_page == 3
    assert seen == []


def test_pager_disables_edges():
    p = kit.Pager(100, page=1, page_size=10)
    prev_cell, *_, next_cell = p.buttons.controls
    assert prev_cell.on_click is None
    assert next_cell.on_click is not None


# -- Segmented --------------------------------------------------------------


def test_segmented_selects_and_notifies():
    seen: list[str] = []
    s = kit.Segmented([("a", "甲"), ("b", "乙")], "a", seen.append)
    assert s.value == "a"
    s.select("b", notify=True)
    assert s.value == "b"
    assert seen == ["b"]


def test_segmented_defaults_to_first_and_rejects_unknown():
    s = kit.Segmented([("a", "甲"), ("b", "乙")])
    assert s.value == "a"
    with pytest.raises(KeyError):
        s.select("zzz")
    with pytest.raises(ValueError):
        kit.Segmented([])


def test_segmented_click_on_current_does_not_notify():
    seen: list[str] = []
    s = kit.Segmented([("a", "甲"), ("b", "乙")], "a", seen.append)
    s._click("a")
    assert seen == []
    s._click("b")
    assert seen == ["b"]


# -- SwitchRow --------------------------------------------------------------


def test_switch_row_value_roundtrip():
    row = kit.SwitchRow("標題", "說明", True)
    assert row.value is True
    row.value = False
    assert row.switch.value is False
    row.set_disabled(True)
    assert row.disabled_state is True


def test_switch_row_without_sub_has_single_text():
    row = kit.SwitchRow("標題", None, False, divider=False)
    texts = [c for c in _walk(row) if isinstance(c, ft.Text)]
    assert [t.value for t in texts] == ["標題"]
    assert row.border is None


# -- SectionCard ------------------------------------------------------------


def test_section_card_header_and_body():
    body = ft.Text("內容")
    card = kit.section_card("標題", body, icon=ft.Icons.INFO, badge=kit.chip("3"))
    assert card.body.content is body
    texts = [c.value for c in _walk(card) if isinstance(c, ft.Text)]
    assert "標題" in texts


def test_section_card_without_title_has_no_header():
    card = kit.section_card(None, ft.Text("x"))
    assert card.content.controls == [card.body]


def test_section_card_flush_removes_padding():
    assert kit.section_card("t", ft.Text("x"), flush=True).body.padding == 0


def test_section_card_collapse_toggle():
    card = kit.section_card("t", ft.Text("x"), collapsible=True)
    assert card.collapsed is False and card.body.visible is True
    card._toggle()
    assert card.collapsed is True and card.body.visible is False
    assert card.collapse_button.icon == ft.Icons.EXPAND_MORE
    card.set_collapsed(False)
    assert card.body.visible is True
    assert card.collapse_button.icon == ft.Icons.EXPAND_LESS


def test_section_card_starts_collapsed():
    card = kit.section_card("t", ft.Text("x"), collapsible=True, collapsed=True)
    assert card.body.visible is False


def test_section_card_expand_only_when_requested():
    assert kit.section_card("t", ft.Text("x")).expand in (None, 0)
    assert kit.section_card("t", ft.Text("x"), expand=True).expand is True


# -- stat_card / page_header ------------------------------------------------


def test_stat_card_shows_label_value_and_delta():
    card = kit.stat_card("模組總數", "412", icon=ft.Icons.INBOX, delta="+18")
    texts = [c.value for c in _walk(card) if isinstance(c, ft.Text)]
    assert {"模組總數", "412", "+18"} <= set(texts)


def test_page_header_shows_title_subtitle_and_actions():
    action = kit.button("動作")
    header = kit.page_header("標題", "副標", icon=ft.Icons.HOME, actions=[action])
    nodes = list(_walk(header))
    texts = [c.value for c in nodes if isinstance(c, ft.Text)]
    assert "標題" in texts and "副標" in texts
    assert action in nodes


# -- 進度 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), (-1, 0.0), (0.4, 0.4), (3, 1.0)],
)
def test_clamp01(raw, expected):
    assert kit.clamp01(raw) == expected


def test_progress_bar_has_no_stop_dot():
    bar = kit.progress_bar(0.5, "gold")
    assert bar.value == 0.5
    assert bar.stop_indicator_radius == 0
    assert kit.progress_bar(None).value is None  # 不確定進度


def test_progress_ring_label_and_updates():
    ring = kit.ProgressRing(0.87)
    assert ring.label.value == "87%"
    ring.set_value(0.5, sub="一半")
    assert ring.label.value == "50%"
    assert ring.sub.value == "一半" and ring.sub.visible is True
    ring.set_value(None)
    assert ring.label.value == "…"
    ring.set_value(0.1, label="自訂", sub="")
    assert ring.label.value == "自訂" and ring.sub.visible is False


# -- 狀態 -------------------------------------------------------------------


def test_empty_state_action_button_only_when_requested():
    plain = kit.empty_state("沒有資料")
    assert not any(isinstance(c, ft.Button) for c in _walk(plain))
    with_action = kit.empty_state("沒有資料", action_text="清除", on_action=lambda e: 0)
    assert any(isinstance(c, ft.Button) for c in _walk(with_action))


def test_error_state_retry():
    state = kit.error_state("失敗", "細節", on_retry=lambda e: 0)
    assert any(isinstance(c, ft.Button) for c in _walk(state))
    assert any(isinstance(c, ft.Text) and c.value == "細節" for c in _walk(state))


def test_loading_state_text_appears_once():
    texts = [
        c.value for c in _walk(kit.loading_state("讀取中…")) if isinstance(c, ft.Text)
    ]
    assert texts == ["讀取中…"]


# -- gallery ----------------------------------------------------------------


def test_gallery_builds_and_has_no_stray_expand_false():
    root = build_gallery()
    assert isinstance(root, ft.Control)
    # 回歸：expand=False 的控制項放進 wrap 的 Row 會讓 Flutter 爆版
    for node in _walk(root):
        assert getattr(node, "expand", None) is not False, type(node).__name__


# -- text_field -------------------------------------------------------------


def test_text_field_applies_design_tokens_and_options():
    from app.ui.design import C

    f = kit.text_field(
        "標籤", hint="提示", mono=True, multiline=True, min_lines=3, expand=True
    )
    assert f.label == "標籤" and f.hint_text == "提示"
    assert f.bgcolor == C.PANEL2 and f.focused_border_color == C.EM
    assert f.multiline is True and f.min_lines == 3 and f.expand is True
    assert f.text_style.font_family == "JetBrains Mono"


def test_text_field_does_not_force_expand():
    assert kit.text_field("x").expand in (None, 0)
    assert kit.text_field("x", read_only=True).read_only is True
