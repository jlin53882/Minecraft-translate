"""進階篩選的排序方向：每種排序依據都能切換遞增／遞減。"""

from __future__ import annotations

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
