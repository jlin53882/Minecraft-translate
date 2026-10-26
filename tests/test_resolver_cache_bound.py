"""TranslationResolver：模組表快取有上限，依最近使用淘汰，且淘汰後仍能正確重新載入。"""

from __future__ import annotations

from translation_tool.translation_db.resolver import (
    MIN_CACHED_MODS,
    TranslationResolver,
)
from translation_tool.translation_db.schema import KIND_LANG


class _CountingDb:
    def __init__(self, rows_per_mod: int) -> None:
        self.rows_per_mod = rows_per_mod
        self.loads: list[str] = []

    def load_mod(self, mod_id):
        self.loads.append(mod_id)
        return [
            (KIND_LANG, f"k{i}", f"en{i}", "1.21.1", f"譯{mod_id}{i}", 1)
            for i in range(self.rows_per_mod)
        ]


def test_sequential_mods_are_loaded_once_and_cache_stays_bounded():
    db = _CountingDb(rows_per_mod=100)
    resolver = TranslationResolver(db, "1.21.1", max_cached_rows=1000)
    for n in range(200):
        mod = f"mod{n}"
        for i in range(100):
            assert resolver.lookup(KIND_LANG, mod, f"k{i}", f"en{i}").zh_tw == (
                f"譯{mod}{i}"
            )
    assert db.loads == [f"mod{n}" for n in range(200)]  # 每個模組只查詢一次
    assert len(resolver._mods) <= max(1000 // 100, MIN_CACHED_MODS) + 1
    assert sum(resolver._rows.values()) <= 1000 + 100


def test_recently_used_mod_survives_eviction_and_evicted_mod_reloads():
    db = _CountingDb(rows_per_mod=50)
    resolver = TranslationResolver(db, "1.21.1", max_cached_rows=400)
    resolver.lookup(KIND_LANG, "hot", "k0", "en0")
    for n in range(40):
        resolver.lookup(KIND_LANG, f"cold{n}", "k0", "en0")
        resolver.lookup(KIND_LANG, "hot", "k0", "en0")  # 持續使用，不該被淘汰
    assert db.loads.count("hot") == 1
    assert "cold0" not in resolver._mods
    hit = resolver.lookup(
        KIND_LANG, "cold0", "k1", "en1"
    )  # 被淘汰的模組重新載入後結果相同
    assert hit is not None and hit.zh_tw == "譯cold01"
    assert db.loads.count("cold0") == 2


def test_single_mod_larger_than_budget_is_still_usable():
    db = _CountingDb(rows_per_mod=500)
    resolver = TranslationResolver(db, "1.21.1", max_cached_rows=10)
    for _ in range(3):
        assert resolver.lookup(KIND_LANG, "big", "k499", "en499").zh_tw == "譯big499"
    assert db.loads == ["big"]
