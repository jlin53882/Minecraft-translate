"""Boundary tests for the Related Lang probe (synthetic fixtures; read-only access)."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import prototype as p

from translation_tool.translation_db import (
    KIND_LANG,
    ScanItem,
    TranslationDB,
)

V = "1.20.1"


def _db(tmp_path, items, version=V, reviewed=()):
    path = tmp_path / "probe.db"
    db = TranslationDB(path)
    db.ingest(
        version, [ScanItem(KIND_LANG, m, k, en, tw, "") for m, k, en, tw in items]
    )
    for m, k, en, tw in items:
        if k in reviewed:
            row = next(r for r in db.list_entries(version, limit=None)[0] if r.key == k)
            db.review_manual(row.id, expected_zh_tw=tw, propagate=False)
    db.close()
    return path


def _id(conn, key, version=V, mod=None):
    sql = "SELECT id FROM entry WHERE key = ? AND mc_version = ?"
    params = [key, version]
    if mod:
        sql += " AND mod_id = ?"
        params.append(mod)
    return conn.execute(sql, params).fetchone()[0]


def test_desc_and_title_pair_both_directions(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "item.demo.aerbunny", "Aerbunny", "空中兔"),
            (
                "demo",
                "item.demo.aerbunny.desc",
                "Flies around the player.",
                "會繞著玩家飛",
            ),
        ],
        reviewed={"item.demo.aerbunny", "item.demo.aerbunny.desc"},
    )
    conn = p.open_readonly(str(path))
    forward = p.retrieve(
        conn, entry_id=_id(conn, "item.demo.aerbunny.desc"), kind=KIND_LANG
    )
    backward = p.retrieve(
        conn, entry_id=_id(conn, "item.demo.aerbunny"), kind=KIND_LANG
    )
    assert [e.key for e in forward if e.relation_type == "structural_suffix"] == [
        "item.demo.aerbunny"
    ]
    assert [e.key for e in backward if e.relation_type == "structural_suffix"] == [
        "item.demo.aerbunny.desc"
    ]
    assert all(
        e.confidence == "high"
        for e in forward + backward
        if e.relation_type == "structural_suffix"
    )


def test_structural_match_is_capped_below_high_without_review(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "item.demo.dart", "Dart", ""),
            ("demo", "item.demo.dart.desc", "Shoots darts.", ""),
        ],
    )
    conn = p.open_readonly(str(path))
    ev = p.retrieve(conn, entry_id=_id(conn, "item.demo.dart"), kind=KIND_LANG)
    struct = [e for e in ev if e.relation_type == "structural_suffix"]
    assert struct and all(e.confidence == "medium" for e in struct)
    assert any("not authoritative" in lim for lim in struct[0].limitations)


def test_generic_words_and_short_words_do_not_create_lexical_links(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "item.demo.alpha.tooltip", "Alpha tooltip", ""),
            ("demo", "item.demo.beta.tooltip", "Beta tooltip", ""),
            ("demo", "item.demo.it.note", "It note", ""),
        ],
    )
    conn = p.open_readonly(str(path))
    ev = p.retrieve(conn, entry_id=_id(conn, "item.demo.alpha.tooltip"), kind=KIND_LANG)
    assert "item.demo.beta.tooltip" not in [
        e.key for e in ev
    ]  # only generic 'tooltip' shared
    assert all(
        e.relation_type != "lexical" or "tooltip" not in e.relevance_reason for e in ev
    )
    ev_it = p.retrieve(conn, entry_id=_id(conn, "item.demo.it.note"), kind=KIND_LANG)
    assert all("'it'" not in e.relevance_reason for e in ev_it)


def test_different_mc_version_and_mod_are_never_returned(tmp_path):
    path = tmp_path / "multi.db"
    db = TranslationDB(path)
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "demo", "item.demo.aerbunny", "Aerbunny", "", "")],
    )
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "demo", "item.demo.aerbunny.desc", "Desc", "", "")],
    )
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "other", "item.other.aerbunny.desc", "Other mod", "", "")],
    )
    db.close()
    conn = p.open_readonly(str(path))
    ev = p.retrieve(
        conn, entry_id=_id(conn, "item.demo.aerbunny", "1.20.1", "demo"), kind=KIND_LANG
    )
    assert ev == []  # the 1.21.1 companion and the other mod's companion must not link


def test_sibling_variants_are_not_reported_as_the_same_entity(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "block.demo.red_sand_5x", "Red Sand 5x", ""),
            ("demo", "block.demo.red_sand_1x", "Red Sand 1x", ""),
        ],
    )
    conn = p.open_readonly(str(path))
    ev = p.retrieve(conn, entry_id=_id(conn, "block.demo.red_sand_5x"), kind=KIND_LANG)
    assert [e.relation_type for e in ev] == ["sibling_variant"]
    assert ev[0].confidence == "low"


def test_format_mismatch_is_downgraded_and_not_used_as_same_text(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "item.demo.power", "Uses %s power", ""),
            ("demo", "item.demo.power.desc", "Uses power", ""),
        ],
        reviewed=set(),
    )
    conn = p.open_readonly(str(path))
    ev = [
        e
        for e in p.retrieve(conn, entry_id=_id(conn, "item.demo.power"), kind=KIND_LANG)
        if e.relation_type == "structural_suffix"
    ]
    assert ev and ev[0].confidence == "low"
    assert any("format tokens differ" in lim for lim in ev[0].limitations)


def test_five_item_cap_and_exclusion_of_existing_evidence(tmp_path):
    items = [("demo", "item.demo.gear.main", "Gear", "")]
    items += [
        ("demo", f"item.demo.gear.part{i}", f"Gear part {i}", "") for i in range(12)
    ]
    path = _db(tmp_path, items)
    conn = p.open_readonly(str(path))
    target = _id(conn, "item.demo.gear.main")
    ev = p.retrieve(conn, entry_id=target, kind=KIND_LANG)
    assert len(ev) == p.MAX_NEW
    excluded = frozenset(_id(conn, f"item.demo.gear.part{i}") for i in range(12))
    assert p.retrieve(conn, entry_id=target, kind=KIND_LANG, exclude_ids=excluded) == []


def test_retrieval_is_deterministic_and_leaves_database_untouched(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "item.demo.gear", "Gear", "齒輪"),
            ("demo", "item.demo.gear.desc", "A gear.", "一個齒輪"),
            ("demo", "item.demo.gear_box", "Gear box", ""),
        ],
        reviewed={"item.demo.gear", "item.demo.gear.desc"},
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    conn = p.open_readonly(str(path))
    target = _id(conn, "item.demo.gear")
    first = p.retrieve(conn, entry_id=target, kind=KIND_LANG)
    second = p.retrieve(conn, entry_id=target, kind=KIND_LANG)
    assert first == second
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE entry SET en_us = 'x'")
    conn.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_odd_unicode_and_long_keys_do_not_crash(tmp_path):
    long_key = "item.demo." + "a_" * 300 + "x"
    path = _db(
        tmp_path,
        [
            ("demo", long_key, "Very long", ""),
            ("demo", "item.demo.emoji_🔥", "Fire ★ %s §c", ""),
            ("demo", "item.demo.fire", "Fire", ""),
        ],
    )
    conn = p.open_readonly(str(path))
    for key in (long_key, "item.demo.emoji_🔥"):
        p.retrieve(conn, entry_id=_id(conn, key), kind=KIND_LANG)


def test_retrieval_returns_nothing_for_unknown_entry(tmp_path):
    path = _db(tmp_path, [("demo", "item.demo.a", "A", "")])
    conn = p.open_readonly(str(path))
    assert p.retrieve(conn, entry_id=999999, kind=KIND_LANG) == []


def test_untranslated_zh_is_flagged_as_not_a_reference(tmp_path):
    path = _db(
        tmp_path,
        [
            ("demo", "item.demo.synchronizer", "Synchronizer", ""),
            ("demo", "item.demo.synchronizer.tooltip", "Emitted during explosions.", "Emitted during explosions."),
            ("demo", "item.demo.gear", "Gear", ""),
            ("demo", "item.demo.gear.tooltip", "A gear.", "一個齒輪。"),
        ],
    )
    conn = p.open_readonly(str(path))
    ev = p.retrieve(conn, entry_id=_id(conn, "item.demo.synchronizer"), kind=KIND_LANG)
    tip = next(e for e in ev if e.key.endswith(".tooltip"))
    assert any("untranslated_reference" in lim for lim in tip.limitations)
    ev2 = p.retrieve(conn, entry_id=_id(conn, "item.demo.gear"), kind=KIND_LANG)
    tip2 = next(e for e in ev2 if e.key.endswith(".tooltip"))
    assert not any("untranslated_reference" in lim for lim in tip2.limitations)
