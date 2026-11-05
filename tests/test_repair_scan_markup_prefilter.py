"""修復掃描的 SQL 預先過濾不得排除只含 ``<tag>`` 或 ``&`` 色碼的條目。

``<Desligado>`` 這類尖括號標記、FTB 的 ``&a`` 色碼沒有 § % $ { \\ 或換行，
SQL 過濾若不認得它們，條目詳情會顯示「特殊字元不一致」，修復掃描卻找不到。
"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import KIND_LANG, TranslationDB, WriteBackItem
from translation_tool.translation_db.models import EntryFilter, QualityFilter

CASES = {
    "tag": ("<Desligado>", "<已關閉>"),
    "color": ("&aHello", "你好"),
}


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "scan.db")
    database.write_back(
        "1.21.1",
        [
            WriteBackItem(KIND_LANG, "m", f"k.{name}", en, zh)
            for name, (en, zh) in CASES.items()
        ]
        + [WriteBackItem(KIND_LANG, "m", "k.plain", "Off", "關閉")],
        fill_other_versions=False,
    )
    yield database
    database.close()


def test_iter_repairable_entries_finds_tag_and_color_mismatches(db):
    keys = {
        entry.key
        for entry, _nl, _mx in db.iter_repairable_translation_entries("1.21.1")
    }
    assert keys == {"k.tag", "k.color"}


def test_mismatched_translation_entries_finds_them(db):
    _total, entries = db.mismatched_translation_entries("1.21.1")
    assert {entry.key for entry in entries} == {"k.tag", "k.color"}


def test_entries_mismatch_filter_finds_them(db):
    criteria = EntryFilter(version="1.21.1", quality=QualityFilter(status="mismatch"))
    where = db._quality_prefilter_sql(criteria.quality, flags_ready=False)
    rows = db._q(
        "SELECT e.key FROM entry e JOIN effective f ON f.entry_id=e.id "
        f"WHERE e.mc_version='1.21.1' AND {where}"
    )
    assert {row[0] for row in rows} >= {"k.tag", "k.color"}
