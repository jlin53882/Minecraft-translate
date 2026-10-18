"""進階篩選的排序方向：每種排序依據都能切換遞增／遞減。"""

from __future__ import annotations

from datetime import UTC
from types import SimpleNamespace
from unittest import mock

from app.views.moddb import advanced_filters


def _filters():
    changes = []
    filters = advanced_filters.AdvancedFilters(lambda: changes.append("changed"))
    filters.panel = SimpleNamespace(page=object(), visible=False)
    filters.sort_direction.update = lambda: None
    return filters, changes


def test_default_sort_toggles_between_ascending_and_descending():
    filters, changes = _filters()
    assert filters.sort_by == "default"
    with mock.patch.object(advanced_filters, "show_snack") as snack:
        filters._toggle_sort_direction()
    assert filters.sort_by == "default_desc"
    assert changes == ["changed"]
    assert "Z→A" in snack.call_args.args[1]
    with mock.patch.object(advanced_filters, "show_snack"):
        filters._toggle_sort_direction()
    assert filters.sort_by == "default"


def test_date_sorts_start_newest_first_and_toggle_to_oldest():
    filters, _ = _filters()
    filters.sort.value = "effective_updated_newest"
    filters._selection_changed()
    assert filters.sort_by == "effective_updated_newest"
    with mock.patch.object(advanced_filters, "show_snack"):
        filters._toggle_sort_direction()
    assert filters.sort_by == "effective_updated_oldest"
    assert "遞增" in filters.sort_direction.text


def test_changing_sort_basis_resets_to_its_natural_direction_and_reset_clears():
    filters, _ = _filters()
    with mock.patch.object(advanced_filters, "show_snack"):
        filters._toggle_sort_direction()  # default_desc
    filters.sort.value = "entry_newest"
    filters._selection_changed()
    assert filters.sort_by == "entry_newest"
    filters.reset()
    assert filters.sort_by == "default"
    assert "A→Z" in filters.sort_direction.tooltip


def test_programmatic_sort_value_uses_that_basis_natural_direction():
    filters, _ = _filters()
    filters.sort.value = "entry_newest"  # 沒有走 _selection_changed
    assert filters.sort_by == "entry_newest"


def test_picked_date_without_time_basis_selects_one_and_applies_filter():
    from datetime import date, datetime

    filters, changes = _filters()
    shown = {}

    class Page:
        locale_configuration = None

        def update(self):
            pass

        def show_dialog(self, dialog):
            shown["dialog"] = dialog

    filters.panel = SimpleNamespace(page=Page(), visible=False)
    assert filters.time_kind.value == "none"
    filters._pick_date(filters.start_date, "起始日")
    picker = shown["dialog"]
    assert str(picker.locale) == "zh_TW"
    event = SimpleNamespace(
        control=SimpleNamespace(value=datetime(2026, 10, 10, 16, tzinfo=UTC))
    )
    with mock.patch.object(advanced_filters, "show_snack") as snack:
        picker.on_change(event)
    assert filters.start_date.value == "2026-10-11"
    assert changes == []  # 只選了起始日：先提醒還要選結束日，不顯示錯誤
    assert not filters.error_text.visible
    assert "結束日" in snack.call_args.args[1]
    assert "條目匯入時間" in snack.call_args.args[1]

    filters._pick_date(filters.end_date, "結束日")
    end_event = SimpleNamespace(
        control=SimpleNamespace(value=datetime(2026, 10, 12, 16, tzinfo=UTC))
    )
    with mock.patch.object(advanced_filters, "show_snack") as snack:
        shown["dialog"].on_change(end_event)
    assert filters.end_date.value == "2026-10-13"
    assert filters.time_kind.value == "entry_created"
    assert filters.time_preset.value == "custom"
    assert changes == ["changed"]
    assert "已套用結束日" in snack.call_args.args[1]
    assert filters.time_filter() is not None
    assert date.fromisoformat(filters.start_date.value) == date(2026, 10, 11)
