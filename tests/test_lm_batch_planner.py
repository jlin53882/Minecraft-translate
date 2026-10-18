"""Canonical planner regression tests."""

from __future__ import annotations

from translation_tool.core.lm_batch_planner import next_batch


class _IndexTrackingItems(list):
    def __init__(self, values):
        super().__init__(values)
        self.integer_indexes: list[int] = []

    def __getitem__(self, index):
        if isinstance(index, int):
            self.integer_indexes.append(index)
        return super().__getitem__(index)


def test_next_batch_does_not_scan_past_preferred_window():
    items = _IndexTrackingItems(
        [{"cache_type": "lang", "_batch_group": ("mod", "lang")}] * 100_000
    )

    batch, cache_type = next_batch(
        items,
        lm_cfg=None,
        batch_size_for_type=lambda _cache_type: 100,
        select_size=lambda *_args: 100,
    )

    assert cache_type == "lang"
    assert len(batch) == 100
    assert items.integer_indexes == [0, 0, *range(1, 100)]
