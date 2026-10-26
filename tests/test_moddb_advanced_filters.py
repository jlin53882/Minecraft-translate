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


def test_clear_button_resets_active_conditions_and_notifies_once():
    filters, changes = _filters()
    filters.quality_status.value = "mismatch"
    filters.set_summary()

    assert filters.clear_button.visible is True

    filters._clear()

    assert filters.quality_status.value == "all"
    assert filters.active_text.value == ""
    assert filters.clear_button.visible is False
    assert changes == ["changed"]


def test_toggle_label_follows_panel_state_and_active_count():
    filters, _ = _filters()
    filters.quality_status.value = "mismatch"
    filters.set_summary()
    assert filters.toggle_button.content == "進階篩選（1）"

    filters._toggle()
    assert filters.panel.visible is True
    assert filters.toggle_button.content == "收合進階篩選"

    filters._toggle()
    assert filters.toggle_button.content == "進階篩選（1）"
    filters._clear()
    assert filters.toggle_button.content == "進階篩選"


def test_programmatic_sort_value_uses_that_basis_natural_direction():
    filters, _ = _filters()
    filters.sort.value = "entry_newest"  # 沒有走 _selection_changed
    assert filters.sort_by == "entry_newest"


def _calendar_value(year, month, day):
    """日曆選到某天時回傳的值：該日本地零點換算成 UTC（與執行環境的時區無關）。"""
    from datetime import datetime

    return datetime(year, month, day).astimezone(UTC)


def test_picked_date_without_time_basis_selects_one_and_applies_filter():
    from datetime import date

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
        control=SimpleNamespace(value=_calendar_value(2026, 10, 11))
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
        control=SimpleNamespace(value=_calendar_value(2026, 10, 13))
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


def test_no_data_hint_names_the_basis_and_where_the_data_actually_is():
    filters, _ = _filters()
    filters.time_kind.value = "entry_created"
    hint = filters._range_message("2026-10-08 16:00:00", "2026-10-16 03:00:00")
    assert "條目匯入時間" in hint
    assert "2026-10-09 ～ 2026-10-16" in hint
    assert "沒有資料" in hint
    none_hint = filters._range_message(None, None)
    assert "沒有任何時間資料" in none_hint


def test_no_data_hint_only_for_date_bounded_filters():
    from translation_tool.translation_db.models import EntryFilter, TimeFilter

    filters, _ = _filters()
    filters.time_kind.value = "entry_created"
    db = SimpleNamespace(time_data_range=lambda criteria: ("2026-10-01", "2026-10-02"))
    plain = EntryFilter(version="1.21.1")
    unbounded = EntryFilter(
        version="1.21.1", time=TimeFilter(kind="entry_created", unknown_policy="only")
    )
    bounded = EntryFilter(
        version="1.21.1",
        time=TimeFilter(kind="entry_created", start_utc="2026-10-16 16:00:00"),
    )
    assert filters.no_data_hint(db, plain) is None
    assert filters.no_data_hint(db, unbounded) is None
    assert "條目匯入時間" in filters.no_data_hint(db, bounded)


def test_announce_no_data_shows_snack_and_ignores_invalid_criteria():
    from translation_tool.translation_db.models import EntryFilter, TimeFilter

    filters, _ = _filters()
    filters.time_kind.value = "entry_created"
    db = SimpleNamespace(time_data_range=lambda criteria: ("2026-10-01", "2026-10-02"))
    bounded = EntryFilter(
        version="1.21.1",
        time=TimeFilter(kind="entry_created", start_utc="2026-10-16 16:00:00"),
    )
    with mock.patch.object(advanced_filters, "show_snack") as snack:
        hint = filters.announce_no_data(lambda: db, lambda: bounded)
        assert hint and snack.call_args.args[1] == hint
        snack.reset_mock()

        def invalid():
            raise ValueError("日期格式請使用 YYYY-MM-DD")

        assert filters.announce_no_data(lambda: db, invalid) is None
        assert filters.announce_no_data(lambda: None, lambda: bounded) is None
        snack.assert_not_called()
