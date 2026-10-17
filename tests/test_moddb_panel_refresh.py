"""Fast Mod DB refresh contracts."""

from __future__ import annotations

from app.views.moddb.panel_refresh import ALL_MODS, _load_translate_snapshot


class _CountOnlyDatabase:
    def versions(self):
        return ["1.21.1"]

    def mods(self, version):
        assert version == "1.21.1"
        return ["example"]

    def count_untranslated(self, version, mod_ids):
        assert version == "1.21.1"
        assert mod_ids == ()
        return 120

    def count_reusable(self, version, mod_ids):
        assert version == "1.21.1"
        assert mod_ids == ()
        return 20

    def untranslated_entries(self, *_args, **_kwargs):
        raise AssertionError("範圍刷新不應建立完整待翻譯 items")


def test_translate_snapshot_only_reads_fast_counts():
    snapshot = _load_translate_snapshot(
        _CountOnlyDatabase(),
        {"version": "1.21.1", "mod": ALL_MODS, "reuse": True, "limit": 100_000},
        {},
    )

    assert snapshot["missing"] == 120
    assert snapshot["reusable"] == 20
    assert snapshot["planned_items"] is None
    assert snapshot["planned_batches"] is None
