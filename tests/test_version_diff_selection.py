"""版本差異審核：批次套用的勾選規則（衝突不可批次、目標已審核需明確納入）。"""

from __future__ import annotations

from app.views.moddb.version_diff_dialog import selectable, selected_items
from translation_tool.translation_db.cross_version_review import (
    CrossVersionRow,
    VersionRef,
)


def _ref(version: str, text: str) -> VersionRef:
    return VersionRef(1, version, text, 6, "人工", "2026-11-04 16:29:00")


def _row(entry_id: int, *, reviewed: bool, refs=("亮色",)) -> CrossVersionRow:
    return CrossVersionRow(
        entry_id,
        "lang",
        "foo",
        f"k{entry_id}",
        "Bright",
        "明亮",
        1,
        reviewed,
        tuple(_ref(f"1.2{i}", text) for i, text in enumerate(refs)),
    )


def test_reviewed_targets_are_selectable_only_when_included():
    row = _row(1, reviewed=True)
    assert not selectable(row, include_reviewed=False)
    assert selectable(row, include_reviewed=True)
    assert selectable(_row(2, reviewed=False), include_reviewed=False)


def test_conflicts_are_never_batch_selectable():
    conflict = _row(3, reviewed=False, refs=("亮色", "明亮色"))
    assert conflict.conflict
    assert not selectable(conflict, include_reviewed=True)


def test_selected_items_follow_selection_and_include_flag():
    rows = [_row(1, reviewed=True), _row(2, reviewed=False)]
    assert [i.entry_id for i in selected_items(rows, {1, 2}, False)] == [2]
    assert [i.entry_id for i in selected_items(rows, {1, 2}, True)] == [1, 2]
    assert selected_items(rows, set(), True) == []
    item = selected_items(rows, {2}, False)[0]
    assert (item.expected_target_zh_tw, item.expected_ref_zh_tw) == ("明亮", "亮色")
