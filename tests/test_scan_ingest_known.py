"""``scan_ingest._find_existing`` 的等價性、索引同步與擴展性。

舊實作（逐一比對同群組所有已知鍵值）的複本當作參考：新實作對任何已知條目與掃描項目
都必須回傳相同的結果，也必須讓 ``known`` 留下相同的內容。
"""

from __future__ import annotations

import random
import unicodedata

import pytest

from translation_tool.translation_db import scan_ingest
from translation_tool.translation_db.identity import canonical_entry_key
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.scan_ingest import (
    _find_existing,
    _same_text,
    ingest_groups,
)

KINDS = ("kubejs_lang", "lang", "guide", "patchouli", "ftb_quest_lang")


def _reference_find_existing(known: dict, kind: str, item: ScanItem):
    """舊實作的複本：逐一呼叫 canonical_entry_key 比對。"""
    exact = known.get(item.key)
    if exact is not None:
        return exact
    canonical = canonical_entry_key(kind, item.key)
    matches_by_id = {
        value[0]: value
        for key, value in known.items()
        if key != item.key
        and canonical_entry_key(kind, key) == canonical
        and (not item.en_us or not value[1] or _same_text(value[1], item.en_us))
    }
    if len(matches_by_id) != 1:
        return None
    match = next(iter(matches_by_id.values()))
    known[item.key] = match
    return match


def make_known(kind: str, pairs=()):
    """建立 ``known``：新實作用 ``KnownEntries``，沒有時退回一般 dict。"""
    cls = getattr(scan_ingest, "KnownEntries", None)
    if cls is None:
        return dict(pairs)
    return cls(kind, pairs)


def _item(kind: str, key: str, en: str = "x") -> ScanItem:
    return ScanItem(kind, "ns@Pack@0123456789ab", key, en)


# 鍵值別名：反斜線、NFC／NFD、指南的語系目錄
_BASES = ("item", "block", "quest", "é", "a b")
_ALIAS = {
    "kubejs_lang": lambda base, n: [f"{base}/{n}", f"{base}\\{n}"],
    "lang": lambda base, n: [f"{base}/{n}", f"{base}\\{n}"],
    "ftb_quest_lang": lambda base, n: [f"{base}/{n}", f"{base}\\{n}"],
    "guide": lambda base, n: [
        f"{base}/_en_us/a_{n}.txt#0",
        f"{base}/a_{n}.txt#0",
        f"{base}/_zh_tw/a_{n}.txt#0",
    ],
    "patchouli": lambda base, n: [f"{base}/en_us/a_{n}.json#x", f"{base}/a_{n}.json#x"],
}
_TEXTS = ("", "A\nB", "A\r\nB", "A\rB", "other", "x")


def _random_key(rng: random.Random, kind: str) -> str:
    base = rng.choice(_BASES)
    key = rng.choice(_ALIAS[kind](base, rng.randrange(6)))
    form = rng.choice(("NFC", "NFD"))
    return unicodedata.normalize(form, key)


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("kind", KINDS)
def test_find_existing_matches_the_reference_implementation(seed, kind):
    rng = random.Random(f"{kind}-{seed}")
    ref: dict = {}
    new = make_known(kind)
    next_id = 1
    for _ in range(300):
        item = _item(kind, _random_key(rng, kind), rng.choice(_TEXTS))
        expected = _reference_find_existing(ref, kind, item)
        actual = _find_existing(new, kind, item)
        assert actual == expected
        assert dict(new) == ref
        action = rng.random()
        if expected is None and action < 0.7:
            # 新增條目（呼叫端 add_entry 之後寫入 known）
            ref[item.key] = (next_id, item.en_us)
            new[item.key] = (next_id, item.en_us)
            next_id += 1
        elif expected is not None and action < 0.3 and item.en_us:
            # 補上原文（adopt_en 之後更新 known）
            ref[item.key] = (expected[0], item.en_us)
            new[item.key] = (expected[0], item.en_us)
    assert dict(new) == ref


def test_same_key_in_another_kind_is_not_an_alias():
    known = make_known("kubejs_lang", {"item/0": (1, "x")}.items())
    assert _find_existing(known, "kubejs_lang", _item("kubejs_lang", "item\\0")) == (
        1,
        "x",
    )
    other = make_known("lang", {"a#0": (1, "x")}.items())
    # 指南專用的別名規則不套用到 lang：這裡只有逐字相同的鍵值才算同一筆
    assert _find_existing(other, "lang", _item("lang", "b#0")) is None


def test_ambiguous_aliases_are_not_guessed():
    known = make_known(
        "guide",
        {"b/_en_us/a.txt#0": (1, "x"), "b/_zh_tw/a.txt#0": (2, "x")}.items(),
    )
    assert _find_existing(known, "guide", _item("guide", "b/a.txt#0")) is None
    assert "b/a.txt#0" not in known


def test_index_follows_add_entry_adopt_en_and_alias_reuse():
    kind = "kubejs_lang"
    known = make_known(kind)
    # add_entry 之後：新鍵值立刻可被別名找到
    known["item/0"] = (1, "")
    assert _find_existing(known, kind, _item(kind, "item\\0", "Hello")) == (1, "")
    # 別名重用之後：known 多了別名鍵，原本的與別名的 id 相同，再用第三種寫法仍是同一個條目
    assert known["item\\0"] == (1, "")
    assert _find_existing(known, kind, _item(kind, "item/0", "Hello")) == (1, "")
    # adopt_en 之後：原文以 known 的最新內容判斷，不用過期的快取
    known["item/0"] = (1, "Hello")
    known["item\\0"] = (1, "Hello")
    assert _find_existing(known, kind, _item(kind, "item\u0000", "Other")) is None
    known["c/9"] = (2, "He\nllo")
    assert _find_existing(known, kind, _item(kind, "c\\9", "He\r\nllo")) == (
        2,
        "He\nllo",
    )


class _MemoryTarget:
    """只在記憶體記錄的寫入目標，用來單獨量測比對本身的成本。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.next_id = 1

    def existing(self, kind, mod_id):
        return make_known(kind), set()

    def add_entry(self, kind, mod_id, key, en_us):
        self.next_id += 1
        return self.next_id

    def record_src_change(self, *args):
        return None

    def adopt_en(self, entry_id, en_us):
        return None

    def add_translation(self, *args):
        return None

    def record_origin(self, *args):
        return None


def _ingest_new_keys(n: int, monkeypatch) -> int:
    """匯入 ``n`` 個新鍵值，回傳 canonical_entry_key 的呼叫次數。"""
    calls = 0

    def counting(kind, key):
        nonlocal calls
        calls += 1
        return canonical_entry_key(kind, key)

    monkeypatch.setattr(scan_ingest, "canonical_entry_key", counting)
    items = [_item("kubejs_lang", f"item.n{i}", f"text {i}") for i in range(n)]
    groups = scan_ingest.group_scan_items(items)
    stats = ingest_groups(_MemoryTarget("kubejs_lang"), groups, None)
    assert stats.new_entries == n
    return calls


@pytest.mark.parametrize("n", [2000, 4000])
def test_new_key_import_calls_canonical_key_linearly(n, monkeypatch):
    """每個新鍵值只計算自己的 canonical key（含索引），不隨已知鍵值數量成長。"""
    assert _ingest_new_keys(n, monkeypatch) <= 3 * n
