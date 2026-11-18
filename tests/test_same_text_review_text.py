"""同文一致性審核：成員分組、勾選狀態與對話框卡片的摺疊行為（純邏輯）。"""

from __future__ import annotations

from types import SimpleNamespace

from app.views.moddb.same_text_review_text import (
    bucket_members,
    default_selection,
    selection_state,
)
from translation_tool.translation_db.same_text_review import SameTextMember


def _member(eid, text):
    return SameTextMember(eid, "lang", "foo", f"k{eid}", "Close", text, 6, False, 1)


def test_bucket_members_group_by_translation_and_put_untranslated_last():
    members = [
        _member(1, "關閉"),
        _member(2, "已停用"),
        _member(3, ""),
        _member(4, "已停用"),
        _member(5, ""),
        _member(6, ""),
        _member(7, "閉合"),
    ]
    buckets, more = bucket_members(members, "關閉", limit=100)
    assert [(b.text, b.total) for b in buckets] == [
        ("已停用", 2),
        ("閉合", 1),
        ("", 3),
    ]
    assert more == 0
    assert 1 not in {m.entry_id for b in buckets for m in b.members}  # 已等於統一譯文


def test_bucket_members_cap_keeps_totals_and_reports_hidden_count():
    members = [_member(i, "甲") for i in range(10)] + [_member(20, "乙")]
    buckets, more = bucket_members(members, "丙", limit=4)
    assert [(b.text, b.total, len(b.members)) for b in buckets] == [
        ("甲", 10, 4),
        ("乙", 1, 0),
    ]
    assert more == 7


def test_bucket_members_without_canonical_lists_everyone():
    members = [_member(1, "甲"), _member(2, "乙")]
    buckets, more = bucket_members(members, None, limit=30)
    assert sum(b.total for b in buckets) == 2 and more == 0


def test_selection_state_is_tristate():
    assert selection_state(set(), {1}) == (False, 0)
    assert selection_state({1, 2, 3}, set()) == (False, 0)
    assert selection_state({1, 2, 3}, {1, 9}) == (None, 1)
    assert selection_state({1, 2, 3}, {1, 2, 3, 9}) == (True, 3)


def test_nothing_is_selected_by_default():
    assert default_selection() == set()


def _bare_dialog():
    """不經 ``__init__``（不需要資料庫與頁面）建立只含卡片所需狀態的對話框。"""
    import flet as ft

    from app.views.moddb.same_text_review_dialog import SameTextReviewDialog
    from translation_tool.translation_db.same_text_review import (
        CanonicalCandidate,
        SameTextGroup,
    )

    members = [
        _member(1, "關閉"),
        _member(2, "閉合"),
        _member(3, ""),
        _member(4, ""),
    ]
    group = SameTextGroup(
        "g",
        "Close",
        ("lang",),
        "en_us",
        tuple(members),
        (CanonicalCandidate("關閉", 1, 1, 1, 1, True),),
    )
    dialog = SameTextReviewDialog.__new__(SameTextReviewDialog)
    dialog.groups = [group]
    dialog.selected = default_selection()
    dialog.expanded = set()
    dialog._cards = {}
    dialog.choice = {}
    dialog._catalog = {}
    dialog._confirm_overwrite = False
    dialog.include_reviewed = ft.Checkbox(value=False)
    dialog._render = lambda: None
    dialog._render_apply_button = lambda: None
    dialog._safe_update = lambda: None
    return dialog, group


def test_select_page_only_selects_groups_on_current_page():
    from app.views.moddb.same_text_review_dialog import PAGE_SIZE
    from translation_tool.translation_db.same_text_review import (
        CanonicalCandidate,
        SameTextGroup,
    )

    dialog, _ = _bare_dialog()
    groups = [
        SameTextGroup(
            f"g{i}",
            "Close",
            ("lang",),
            "en_us",
            (_member(i * 10 + 1, "關閉"), _member(i * 10 + 2, "閉合")),
            (CanonicalCandidate("關閉", 1, 1, 1, 1, True),),
        )
        for i in range(PAGE_SIZE + 2)
    ]
    dialog.groups = groups
    dialog._shown = lambda: groups
    dialog.current_page = 2
    dialog.selected = {12}  # 第一頁已勾的保留

    dialog._select_page()

    # 第二頁只有最後兩組；每組只有不等於統一譯文的成員可勾
    assert dialog.selected == {12, PAGE_SIZE * 10 + 2, (PAGE_SIZE + 1) * 10 + 2}

    dialog.current_page = 1
    dialog.selected = set()
    dialog._select_page()
    assert dialog.selected == {i * 10 + 2 for i in range(PAGE_SIZE)}
    dialog._select_all()
    assert dialog.selected == {i * 10 + 2 for i in range(PAGE_SIZE + 2)}


def test_member_list_is_built_only_when_expanded_and_header_follows_selection():
    dialog, group = _bare_dialog()
    dialog._group_card(group)
    refs = dialog._cards["g"]
    assert refs["holder"].content is None and not refs["holder"].visible  # 預設收合
    assert refs["check"].value is False and "已勾選 0" in refs["title"].value

    dialog._toggle_expand(group)
    assert refs["holder"].content is not None and refs["holder"].visible
    dialog._toggle_expand(group)
    assert refs["holder"].content is None and "g" not in dialog.expanded

    dialog._toggle_group(group)  # 勾選整組可統一的成員（2、3、4）
    assert dialog.selected == {2, 3, 4}
    dialog._cards = {}
    dialog._group_card(group)
    assert dialog._cards["g"]["check"].value is True
    dialog.selected.discard(3)
    dialog._refresh_group_header("g")
    assert dialog._cards["g"]["check"].value is None  # 部分勾選
    dialog._toggle_group(group)  # 部分勾選時再按會補齊
    assert dialog.selected == {2, 3, 4}
    dialog._toggle_group(group)  # 全部已勾時再按取消
    assert dialog.selected == set()


def _two_candidate_group():
    from translation_tool.translation_db.same_text_review import (
        CanonicalCandidate,
        SameTextGroup,
    )

    members = (_member(1, "飛向星辰"), _member(2, "飛向星辰"), _member(3, "Ad Astra"))
    return SameTextGroup(
        "g2",
        "Ad Astra",
        ("lang",),
        "en_us",
        members,
        (
            CanonicalCandidate("飛向星辰", 1, 2, 2, 2, True),
            CanonicalCandidate("Ad Astra", 3, 1, 1, 1, True),
        ),
    )


def test_changing_canonical_keeps_a_fully_selected_group_fully_selected():
    dialog, _ = _bare_dialog()
    group = _two_candidate_group()
    dialog.groups = [group]
    dialog._select_group(group)
    assert dialog.selected == {3}

    dialog._choose(group, "Ad Astra")  # 原本整組已勾：改勾新譯文下可統一的成員
    assert dialog.selected == {1, 2}
    dialog._choose(group, "飛向星辰")
    assert dialog.selected == {3}


def test_changing_canonical_does_not_select_an_unselected_group():
    dialog, _ = _bare_dialog()
    group = _two_candidate_group()
    dialog.groups = [group]
    dialog._choose(group, "Ad Astra")
    assert dialog.selected == set()

    dialog.selected = {1}  # 部分勾選：只移除已等於新譯文的成員，不自動補勾
    dialog._choose(group, "飛向星辰")
    assert dialog.selected == set()


def test_same_text_mod_options_drop_mods_without_groups(monkeypatch):
    from app.views.moddb import same_text_review_dialog as stdlg
    from app.views.moddb.result_mod_options import ResultModOptions

    dialog, group = _bare_dialog()
    dialog._closed = False
    dialog._generation = 1
    dialog._set_busy = lambda *a, **k: None
    dialog._mod_options = ResultModOptions()
    dialog.kind_dd = SimpleNamespace(value=stdlg.ALL_KINDS)
    shown = {}
    monkeypatch.setattr(
        stdlg.kit,
        "set_dropdown_options",
        lambda dd, pairs: shown.__setitem__(id(dd), [k for k, _ in pairs]),
    )
    dialog.mod_dd = SimpleNamespace(value=stdlg.ALL_MODS)
    key = ("1.21.1", "", None)

    dialog._loaded(1, [group], (key, None, ["foo"]), None)
    assert shown[id(dialog.mod_dd)] == [stdlg.ALL_MODS, "foo"]

    dialog.mod_dd.value = "foo"
    dialog._loaded(1, [], (key, "foo", None), None)  # 統一後這個模組已沒有群組
    assert shown[id(dialog.mod_dd)] == [stdlg.ALL_MODS, "foo"]  # 選著時保留
    assert dialog._mod_options.options(None) == []
