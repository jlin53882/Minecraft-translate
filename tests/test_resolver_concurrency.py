"""TranslationResolver：載入某個模組時，不擋住查詢已載入模組的其他執行緒。"""

from __future__ import annotations

import threading

from translation_tool.translation_db.resolver import TranslationResolver
from translation_tool.translation_db.schema import KIND_LANG


class _SlowDb:
    """載入 ``slow`` 模組時卡住，直到測試放行；其他模組立刻回傳。"""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.loads: list[str] = []

    def load_mod(self, mod_id):
        self.loads.append(mod_id)
        if mod_id == "slow":
            self.entered.set()
            assert self.release.wait(timeout=10)
        return [(KIND_LANG, "k", "Hello", "1.21.1", "你好", 1)]


def test_lookup_of_loaded_mod_does_not_wait_for_another_mod_load():
    db = _SlowDb()
    resolver = TranslationResolver(db, "1.21.1")
    assert resolver.lookup(KIND_LANG, "fast", "k", "Hello").zh_tw == "你好"

    loader = threading.Thread(
        target=lambda: resolver.lookup(KIND_LANG, "slow", "k", "Hello")
    )
    loader.start()
    try:
        assert db.entered.wait(timeout=10)
        result: list = []
        reader = threading.Thread(
            target=lambda: result.append(
                resolver.lookup(KIND_LANG, "fast", "k", "Hello")
            )
        )
        reader.start()
        reader.join(timeout=5)

        assert not reader.is_alive(), "已載入模組的查詢被另一個模組的載入擋住"
        assert result[0].zh_tw == "你好"
    finally:
        db.release.set()
        loader.join(timeout=10)

    assert db.loads == ["fast", "slow"]


def test_concurrent_first_lookups_load_each_mod_once():
    db = _SlowDb()
    db.release.set()
    resolver = TranslationResolver(db, "1.21.1")
    threads = [
        threading.Thread(
            target=lambda: resolver.lookup(KIND_LANG, "slow", "k", "Hello")
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert db.loads == ["slow"]
