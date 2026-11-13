"""同文一致性審核：勾選、統一譯文選擇與篩選規則（不需要建立畫面）。"""

from __future__ import annotations

from app.views.moddb.same_text_review_dialog import (
    ALL_KINDS,
    VIEW_APPLICABLE,
    VIEW_BLOCKED,
    chosen_text,
    filter_groups,
    kind_options,
    overwrite_count,
    selectable,
    selectable_ids,
    selected_items,
)
from translation_tool.translation_db.same_text_review import (
    CanonicalCandidate,
    SameTextGroup,
    SameTextMember,
)


def _member(eid, text, *, reviewed=False, kind="lang"):
    return SameTextMember(
        eid, kind, "foo", f"k{eid}", "Close", text, 6 if text else None, reviewed, 1
    )


def _group(members, candidates, kinds=("lang", "kubejs_lang"), gid="g"):
    return SameTextGroup(
        gid, "Close", kinds, "en_us", tuple(members), tuple(candidates)
    )


def _candidate(text, eid, count=1):
    return CanonicalCandidate(text, eid, 1, count, 1, True)


def _sample():
    members = [
        _member(1, "關閉", reviewed=True),
        _member(2, "閉合"),
        _member(3, "闔上", reviewed=True),
        _member(4, ""),
    ]
    return _group(members, [_candidate("關閉", 1, 1), _candidate("闔上", 3, 1)])


def test_selectable_requires_difference_and_explicit_reviewed_opt_in():
    group = _sample()
    by_id = {m.entry_id: m for m in group.members}
    assert not selectable(by_id[1], "關閉", True)  # 已經是統一譯文
    assert selectable(by_id[2], "關閉", False)
    assert selectable(by_id[4], "關閉", False)  # 未翻譯可以接收
    assert not selectable(by_id[3], "關閉", False)
    assert selectable(by_id[3], "關閉", True)
    assert not selectable(by_id[2], None, True)  # 沒有已審核候選


def test_chosen_text_defaults_to_first_candidate_and_follows_choice():
    group = _sample()
    assert chosen_text(group, {}) == "關閉"
    assert chosen_text(group, {"g": "闔上"}) == "闔上"
    assert chosen_text(group, {"g": "不存在"}) == "關閉"
    blocked = _group([_member(1, "甲"), _member(2, "乙")], [])
    assert chosen_text(blocked, {}) is None
    assert selectable_ids(blocked, {}, True) == set()


def test_selected_items_follow_selection_choice_and_include_flag():
    group = _sample()
    everything = {1, 2, 3, 4}
    items = selected_items([group], everything, {}, False)
    assert sorted(i.entry_id for i in items) == [2, 4]
    assert {i.zh_tw for i in items} == {"關閉"}
    assert {i.canonical_entry_id for i in items} == {1}

    items = selected_items([group], everything, {"g": "闔上"}, True)
    assert sorted(i.entry_id for i in items) == [1, 2, 4]
    assert all(i.zh_tw == "闔上" and i.allow_overwrite_reviewed for i in items)
    assert overwrite_count(items) == 1  # 只有成員 1 是已審核
    assert selected_items([group], set(), {}, True) == []


def test_filters_by_kind_group_and_applicability():
    lang = _sample()
    guide = _group([_member(5, "甲", kind="guide")], [], kinds=("guide",), gid="h")
    groups = [lang, guide]
    keys = [key for key, _ in kind_options(groups)]
    assert keys[0] == ALL_KINDS and set(keys[1:]) == {"lang|kubejs_lang", "guide"}
    assert filter_groups(groups, "guide", "all") == [guide]
    assert filter_groups(groups, ALL_KINDS, VIEW_APPLICABLE) == [lang]
    assert filter_groups(groups, ALL_KINDS, VIEW_BLOCKED) == [guide]
