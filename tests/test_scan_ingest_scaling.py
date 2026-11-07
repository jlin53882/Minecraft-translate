"""模組包匯入寫入路徑的規模特性：成本隨項目數線性成長，且舊來源淘汰／引用解析語意不變。

FTB Quests 的任務文字全部屬於同一個 (kind, mod_id) 群組，數千筆項目若在寫入交易內做
O(N²) 工作，會長時間持有資料庫鎖，整個畫面（任何要讀資料庫的頁面）都會卡住。
"""

from __future__ import annotations

from collections import Counter

import pytest

from translation_tool.translation_db import (
    KIND_FTB_QUEST_LANG,
    KIND_LANG,
    ScanItem,
    identity,
    scan_ingest,
)
from translation_tool.translation_db.models import EntryOrigin, SourceReference
from translation_tool.translation_db.repository import TranslationDB


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    yield database
    database.close()


def _quest_items(count: int) -> list[ScanItem]:
    return [
        ScanItem(
            KIND_FTB_QUEST_LANG,
            "ftbquests@pack",
            f"chapter|lang:quest.{i}.title",
            f"Quest {i}",
            source_path="lang/en_us/chapter.snbt",
            source_selector=f"lang:en_us:quest.{i}.title",
            source_hash="h",
            pack_scope="pack@1",
        )
        for i in range(count)
    ]


def test_same_group_ingest_does_not_recompute_canonical_per_known_entry(
    db, monkeypatch
):
    calls = Counter()
    real = identity.canonical_entry_key

    def counting(kind, key):
        calls["n"] += 1
        return real(kind, key)

    monkeypatch.setattr(scan_ingest, "canonical_entry_key", counting)
    count = 400
    db.ingest_many("26.1", [_quest_items(count)], None)
    # 線性：每個項目與每個既有鍵各算一次；舊的逐筆掃描是 count² / 2 = 80,000 次。
    assert calls["n"] <= 4 * count


def test_stale_origins_removed_by_exact_selector_only(db):
    items = _quest_items(3)
    db.ingest_many("26.1", [items], None)
    stale = [
        EntryOrigin("pack@1", KIND_FTB_QUEST_LANG, "lang/en_us/chapter.snbt", sel)
        for sel in ("lang:en_us:quest.0.title", "lang:no_such_selector")
    ]
    db.ingest_many("26.1", [[]], None, stale_origins=stale)
    with db._tx() as conn:
        remaining = {
            row[0]
            for row in conn.execute(
                "SELECT selector FROM entry_origin WHERE pack_scope='pack@1'"
            )
        }
    assert remaining == {"lang:en_us:quest.1.title", "lang:en_us:quest.2.title"}


def test_stale_origins_only_touch_requested_version(db):
    db.ingest_many("26.1", [_quest_items(1)], None)
    db.ingest_many("1.21.1", [_quest_items(1)], None)
    stale = [
        EntryOrigin(
            "pack@1",
            KIND_FTB_QUEST_LANG,
            "lang/en_us/chapter.snbt",
            "lang:en_us:quest.0.title",
        )
    ]
    db.ingest_many("26.1", [[]], None, stale_origins=stale)
    with db._tx() as conn:
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT e.mc_version FROM entry_origin eo "
                "JOIN entry e ON e.id=eo.entry_id"
            )
        ]
    assert versions == ["1.21.1"]


def _reference(key: str, *, mod_id: str = "") -> SourceReference:
    return SourceReference(
        pack_scope="pack@1",
        source_kind="kubejs",
        source_path="a.js",
        selector=f"sel:{key}:{mod_id}",
        target_kind=KIND_LANG,
        target_mod_id=mod_id,
        target_key=key,
        target_locale="en_us",
        status="",
        detail="",
        source_hash="h",
    )


def test_reference_resolution_statuses(db):
    def lang(mod: str, key: str) -> ScanItem:
        return ScanItem(
            KIND_LANG,
            mod,
            key,
            "text",
            source_path="p.json",
            source_selector=f"{mod}:{key}",
            source_hash="h",
            pack_scope="pack@1",
        )

    db.ingest_many(
        "26.1",
        [[lang("a", "only.here"), lang("a", "dup.key"), lang("b", "dup.key")]],
        None,
    )
    refs = [
        _reference("only.here"),
        _reference("dup.key"),
        _reference("dup.key", mod_id="b"),
        _reference("nowhere"),
    ]
    assert db.record_source_references("26.1", refs, pack_scope="pack@1") == 4
    status = {
        (r.target_key, r.target_mod_id): r.status
        for r in db.source_references("26.1", pack_scope="pack@1")
    }
    assert status == {
        ("only.here", ""): "resolved",
        ("dup.key", ""): "ambiguous",
        ("dup.key", "b"): "resolved",
        ("nowhere", ""): "missing",
    }


def _write_ftb_lang(root, text="Hello", zh="你好"):
    for locale, value in (("en_us", text), ("zh_tw", zh)):
        path = root / "config" / "ftbquests" / "quests" / "lang" / locale
        path.mkdir(parents=True, exist_ok=True)
        (path / "chapter.snbt").write_text(
            f'{{\n\tchapter.AAAA.title: "{value}"\n}}\n', encoding="utf-8"
        )


def _run_scan(db, folder, **options):
    from translation_tool.translation_db.scanner import (
        ScanOptions,
        scan_folder_generator,
    )

    return list(scan_folder_generator(db, str(folder), ScanOptions(**options)))


def test_translated_mode_imports_pack_content_from_folder_with_chosen_source(
    db, tmp_path
):
    from translation_tool.translation_db.schema import SRC_SUBTITLE

    _write_ftb_lang(tmp_path)
    _run_scan(
        db,
        tmp_path,
        version="26.1",
        translated=True,
        translation_source=SRC_SUBTITLE,
    )
    rows = db.list_entries("26.1")[0]
    assert [(r.en_us, r.zh_tw, r.source) for r in rows] == [
        ("Hello", "你好", SRC_SUBTITLE)
    ]


def test_translated_mode_pack_option_off_skips_pack_content(db, tmp_path):
    _write_ftb_lang(tmp_path)
    _run_scan(db, tmp_path, version="26.1", translated=True, include_pack_content=False)
    assert db.list_entries("26.1")[0] == []
