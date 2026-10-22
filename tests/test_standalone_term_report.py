"""單獨成句術語報告工具：候選清單、唯讀 Mod DB 統計、前後效果（誤匹配必須為 0）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.report_standalone_term_candidates import (
    candidate_rows,
    effect_report,
    moddb_standalone_stats,
)
from translation_tool.core.official_locale import load_official_locale_index
from translation_tool.translation_db.schema import connect, init_schema

_FIXTURE = Path(__file__).parent / "data" / "standalone_term_fixture.json"


@pytest.fixture(scope="module")
def index():
    return load_official_locale_index()


def test_effect_report_has_no_new_false_matches(index) -> None:
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]

    report = effect_report(index, cases)

    assert report["new_false_match_count"] == 0
    assert report["new_hit_count"] == sum(1 for c in cases if c["expected"])


def test_effect_report_flags_unexpected_standalone_hit(index) -> None:
    report = effect_report(index, [{"source": "Netherite", "expected": None}])

    assert report["new_false_match_count"] == 1


def test_candidate_rows_mark_allowlist_and_dangerous_terms(index) -> None:
    rows = {row["source"]: row for row in candidate_rows(index)}

    assert rows["Netherite"]["allowlisted"] is True
    assert rows["Netherite"]["key"] == "block.minecraft.netherite_block"
    assert rows["Max"]["allowlisted"] is False
    assert rows["Wood"]["allowlisted"] is False
    assert rows["Planks"]["flags"] == ["single_char_target"]


def test_moddb_stats_is_read_only_and_counts_standalone_entries(
    tmp_path, index
) -> None:
    db = tmp_path / "fixture.db"
    conn = connect(db)
    init_schema(conn)
    rows = [
        ("modA", "k1", "Netherite", "獄髓", "reviewed"),
        ("modA", "k2", "Netherite", "下界合金", "unreviewed"),
        ("modB", "k3", "Iron", "鐵", None),
        ("modB", "k4", "Max", "最大", None),
        ("modB", "k5", "Netherite Sword", "獄髓劍", None),
        ("modB", "k6", "Iron", "", None),
    ]
    for mod, key, en, zh, status in rows:
        cur = conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES ('lang', '1.20.1', ?, ?, ?)",
            (mod, key, en),
        )
        if zh:
            conn.execute(
                "INSERT INTO effective (entry_id, zh_tw, source, review_status) "
                "VALUES (?, ?, 0, ?)",
                (cur.lastrowid, zh, status),
            )
    conn.commit()
    conn.close()
    before = db.read_bytes()

    stats = moddb_standalone_stats(db, index)

    assert db.read_bytes() == before
    assert stats["standalone_entries"] == 5  # 4 個 allowlist + 1 個 Max
    assert stats["allowlist_hits"] == 4
    assert stats["allowlist_hits_valid_unreviewed"] == 2
    assert stats["candidate_hits_not_allowlisted"] == 1
    assert stats["allowlist_by_term"] == {"iron": 2, "netherite": 2}


def test_moddb_stats_missing_db_does_not_create_file(tmp_path, index) -> None:
    missing = tmp_path / "none.db"

    with pytest.raises(FileNotFoundError):
        moddb_standalone_stats(missing, index)
    assert not missing.exists()
