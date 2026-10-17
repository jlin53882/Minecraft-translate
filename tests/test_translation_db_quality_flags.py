"""條目品質旗標（entry.qflags）：與逐列判斷完全相同、待重算時退回，以及安裝與觸發器。

各寫入流程（掃描、手動儲存、審核、批次取代、清理與還原、優先序重建、寫回、修復同步）
後的比對在 ``test_translation_db_stat_summary.py`` 的共用檢查中一併執行。
"""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TranslationDB,
    quality_flags,
    repository,
)
from translation_tool.translation_db.models import QualityFilter
from translation_tool.translation_db.read_cache import ReadCache
from translation_tool.translation_db.schema import (
    KIND_GUIDE,
    KIND_PATCHOULI,
    SRC_AI,
    SRC_JAR_CN,
    SRC_MANUAL,
)

VERSIONS = ("1.20.1", "1.21.1", "1.22", "26.1")
STATUSES = tuple(quality_flags.BITS)
SORTS = (
    "default",
    "entry_newest",
    "effective_updated_newest",
    "manual_activity_newest",
)
SOURCES = ["", "Plain", "Use %s", "§aGreen", "line1\nline2", "$(item)Hammer$()"]
TRANSLATIONS = ["", "純文字", "使用 %s", " 前置空白", "§a綠", "換行\n", "$(item)槌$()"]
SPECIES = [
    ("plant.latin", "Quercus robur"),
    ("plant.name", "Quercus robur"),
    ("plant.name2", "Mentha × piperita"),
    ("plant.var", "Fagus sylvatica var. purpurea"),
    ("tip", "Energy usage"),
    ("tip2", " Spaced  name "),
]
# 指南條目：連結目標一致、連結目標被改壞（不含格式符號，只有結構標記）、純文字
GUIDE = [
    ("[Crafting](../items/crafting.md)", "[合成](../items/crafting.md)"),
    ("[Crafting](../items/crafting.md)", "[合成](/../items/crafting.md)"),
    ("Plain text", "純文字"),
]


def _seed(db: TranslationDB) -> None:
    """四個版本：原文與譯文各種組合、學名候選，以及人工、審核與不同來源。"""
    for index, version in enumerate(VERSIONS):
        items = []
        for i, (en, tw) in enumerate(itertools.product(SOURCES, TRANSLATIONS)):
            if (i + index) % 5 == 0:
                continue  # 每個版本少幾筆，讓各版本的結果不同
            source = (None, SRC_AI, SRC_JAR_CN)[i % 3]
            items.append(
                ScanItem(KIND_LANG, f"mod{i % 3}", f"k{i:03d}", en, tw, source=source)
            )
        for key, en in SPECIES:
            items.append(ScanItem(KIND_LANG, "flora", key, en, "譯" if index else ""))
        for gi, (en, tw) in enumerate(GUIDE):
            items.append(ScanItem(KIND_GUIDE, "guidemod", f"g{gi}", en, tw))
        db.ingest(version, items)
    rows = db.list_entries("1.21.1", limit=500)[0]
    for row in rows[::7]:
        db.save_manual(row.id, f"人工 %s {row.id}", actor="editor", propagate=False)
    db.review_manual(rows[0].id, expected_zh_tw=f"人工 %s {rows[0].id}", actor="ck")
    with db._tx() as conn:
        conn.execute(
            "UPDATE entry SET created_at='2030-01-0' || (id % 3 + 1) WHERE id % 4 = 0"
        )
        conn.execute(
            "UPDATE translation SET updated_at='2030-02-0' || (id % 3 + 1) "
            "WHERE id % 5 = 0"
        )


def test_patchouli_structure_only_mismatch_reaches_flags_and_repair_candidates(db):
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_PATCHOULI,
                "patchmod",
                "entry",
                '<ItemLink id="example:item" />\nText',
                "錯誤格式",
                source=SRC_AI,
            )
        ],
    )

    rows = db.list_entries("1.21.1", limit=100)[0]
    assert rows[0].quality_state == "mismatch"
    assert rows[0].quality_issues
    assert db.mismatched_translation_entries("1.21.1")[0] == 1
    repairable = list(db.iter_repairable_translation_entries("1.21.1"))
    assert len(repairable) == 1


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "quality.db")
    yield d
    d.close()


def _page(db, criteria, offset, limit, *, flags=True):
    """一頁的（條目編號、總數）；``flags=False`` 時強制走原本的逐列判斷。

    每次都換掉共用快取，兩條路徑不會讀到對方的結果。
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(repository, "READ_CACHE", ReadCache())
        patch.setattr(TranslationDB, "_quality_page_cache", None)
        if not flags:
            patch.setattr(db, "_quality_flags_ready", lambda conn=None: False)
        rows, total = db.list_entries(criteria=criteria, limit=limit, offset=offset)
    return [row.id for row in rows], total


def test_new_database_installs_ready_flags(db):
    meta = dict(db._q("SELECT key, value FROM meta WHERE key LIKE 'quality%'"))
    assert meta == {
        "quality_schema_version": quality_flags.SCHEMA_VERSION,
        "quality_valid": "1",
    }
    assert quality_flags.is_installed(db._conn)
    _seed(db)
    assert db.verify_quality_flags() == []
    assert db._quality_flags_ready()
    for status, bit in quality_flags.BITS.items():
        assert db._one(f"SELECT COUNT(*) FROM entry WHERE qflags & {bit}")[0], status


@pytest.mark.parametrize(
    ("version", "status"), list(itertools.product(VERSIONS, STATUSES))
)
def test_flag_pages_match_row_by_row_scan(db, version, status):
    _seed(db)
    extras = (
        {},
        {"mod_id": "mod1"},
        {"source": SRC_AI},
        {"review_status": "reviewed"},
        {"review_status": "unreviewed"},
        {"state": "ok"},
        {"query": "s"},
    )
    for extra, sort_by in itertools.product(extras, SORTS):
        criteria = EntryFilter(
            version=version,
            quality=QualityFilter(status=status),
            sort_by=sort_by,
            **extra,
        )
        full = _page(db, criteria, 0, 10_000, flags=False)
        assert _page(db, criteria, 0, 10_000) == full, (extra, sort_by)
        total = full[1]
        for offset in sorted({0, 3, total // 2, max(0, total - 2), total + 1}):
            assert _page(db, criteria, offset, 4) == _page(
                db, criteria, offset, 4, flags=False
            ), (extra, sort_by, offset)


@pytest.mark.parametrize(
    ("direction", "category"),
    [("missing", "all"), ("extra", "all"), ("all", "placeholder"), ("all", "newline")],
)
def test_narrowed_mismatch_scans_only_flagged_rows(db, direction, category):
    _seed(db)
    for status in ("all", "mismatch"):
        criteria = EntryFilter(
            version="1.21.1",
            quality=QualityFilter(
                status=status, direction=direction, token_category=category
            ),
        )
        assert _page(db, criteria, 0, 10_000) == _page(
            db, criteria, 0, 10_000, flags=False
        )

    prefilter = db._quality_prefilter_sql(criteria.quality, flags_ready=True)
    assert prefilter == "e.qflags & 4"


def test_pending_rows_from_external_writer_fall_back(db):
    _seed(db)
    criteria = EntryFilter(version="1.21.1", quality=QualityFilter(status="mismatch"))
    before = _page(db, criteria, 0, 10_000)
    target = db._one(
        "SELECT e.id FROM entry e JOIN effective f ON f.entry_id=e.id "
        "WHERE e.mc_version='1.21.1' AND e.qflags & 4 LIMIT 1"
    )[0]
    # 舊版程式等外部寫入者：觸發器標記待重算，但沒有人重算
    with sqlite3.connect(db.path) as other:
        other.execute(
            "UPDATE effective SET zh_tw=(SELECT en_us FROM entry WHERE id=?) "
            "WHERE entry_id=?",
            (target, target),
        )
    assert not db._quality_flags_ready()
    after = _page(db, criteria, 0, 10_000)
    assert after == _page(db, criteria, 0, 10_000, flags=False)
    assert after[1] == before[1] - 1 and target not in after[0]
    # 下一個寫入交易提交前補算
    with db._tx():
        pass
    assert db._quality_flags_ready() and db.verify_quality_flags() == []
    assert _page(db, criteria, 0, 10_000) == after


def test_reads_inside_write_transaction_see_pending_rows(db):
    _seed(db)
    criteria = EntryFilter(
        version="1.21.1", quality=QualityFilter(status="missing_translation")
    )
    before = _page(db, criteria, 0, 10_000)
    target = before[0][0]
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?, ?, '補上')",
            (target, SRC_MANUAL),
        )
        db._refresh(conn, [target])
        assert not db._quality_flags_ready()
        rows, total = db.list_entries(criteria=criteria, limit=10_000)
        assert total == before[1] - 1 and target not in [r.id for r in rows]
    assert db.verify_quality_flags() == []


def _exercise_triggers(db) -> list[list[tuple]]:
    """逐步做會觸發每個觸發器的直接寫入，回傳每一步之後的驗證結果。"""
    rows = db._q(
        "SELECT e.id FROM entry e JOIN effective f ON f.entry_id=e.id "
        "WHERE e.mc_version='1.20.1' AND e.en_us='Use %s' ORDER BY e.id LIMIT 3"
    )
    a, b, c = (row[0] for row in rows)
    blank = db._one(
        "SELECT e.id FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
        "WHERE e.mc_version='1.20.1' AND e.en_us<>'' AND f.entry_id IS NULL"
    )[0]
    latin = db._one("SELECT id FROM entry WHERE key='plant.name' LIMIT 1")[0]
    removed = db._q("SELECT * FROM entry WHERE id=?", (c,))[0]
    columns = [row[1] for row in db._q("PRAGMA table_info(entry)")]
    steps = [
        ("UPDATE effective SET zh_tw='使用' WHERE entry_id=?", (a,)),
        ("DELETE FROM effective WHERE entry_id=?", (b,)),
        (
            "INSERT INTO effective (entry_id, zh_tw, source) VALUES (?, '有了', ?)",
            (blank, SRC_AI),
        ),
        ("UPDATE entry SET en_us='Use' WHERE id=?", (a,)),
        ("UPDATE entry SET key='plant.latin.alt' WHERE id=?", (latin,)),
        ("DELETE FROM entry WHERE id=?", (c,)),
        # 還原清理快照：連同舊旗標一起新增，原文已不同
        (
            (
                f"INSERT INTO entry ({','.join(columns)}) "
                f"VALUES ({','.join('?' * len(columns))})"
            ),
            tuple(
                "Changed %s" if name == "en_us" else value
                for name, value in zip(columns, removed, strict=True)
            ),
        ),
    ]
    results = []
    for sql, params in steps:
        with db._tx() as conn:
            conn.execute(sql, params)
        results.append(db.verify_quality_flags())
    return results


def test_every_write_trigger_keeps_flags_exact(db):
    _seed(db)
    assert all(result == [] for result in _exercise_triggers(db))


@pytest.mark.parametrize("trigger", sorted(quality_flags._TRIGGERS))
def test_each_trigger_is_needed(db, trigger):
    _seed(db)
    with db._tx() as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
    assert any(result != [] for result in _exercise_triggers(db)), trigger


def test_flag_queries_read_indexes_without_sorting(db):
    _seed(db)
    for status, extra in itertools.product(
        STATUSES, ({}, {"mod_id": "mod1"}, {"source": SRC_AI}, {"state": "ok"})
    ):
        criteria = EntryFilter(
            version="1.21.1", quality=QualityFilter(status=status), **extra
        )
        cond, params = db._filter_sql(criteria)
        cond += f" AND ({quality_flags.filter_sql(criteria.quality)})"
        order = db._sort_sql(criteria.sort_by)
        source = db._candidate_source(f"{cond} {order}")
        for sql, values in (
            (f"SELECT COUNT(*) {source} WHERE {cond}", params),
            (
                f"SELECT e.id {source} WHERE {cond} ORDER BY {order} LIMIT ? OFFSET ?",
                (*params, 10, 0),
            ),
        ):
            plan = " ".join(r[3] for r in db._q(f"EXPLAIN QUERY PLAN {sql}", values))
            assert "TEMP B-TREE" not in plan, (status, extra, plan)
    plan = " ".join(
        r[3]
        for r in db._q("EXPLAIN QUERY PLAN SELECT 1 FROM entry WHERE qflags IS NULL")
    )
    assert "idx_qflags_pending" in plan


def _drop_flags(path):
    with sqlite3.connect(path) as conn:
        for name in quality_flags._TRIGGERS:
            conn.execute(f"DROP TRIGGER {name}")
        for name in quality_flags._INDEXES:
            conn.execute(f"DROP INDEX {name}")
        conn.execute("ALTER TABLE entry DROP COLUMN qflags")
        conn.execute("DELETE FROM meta WHERE key LIKE 'quality%'")


def _all_pages(db):
    return {
        (version, status): _page(
            db,
            EntryFilter(version=version, quality=QualityFilter(status=status)),
            0,
            10_000,
        )
        for version, status in itertools.product(VERSIONS, STATUSES)
    }


def test_readonly_old_database_falls_back_and_writable_open_installs(
    tmp_path,
):
    path = tmp_path / "old.db"
    writer = TranslationDB(path)
    _seed(writer)
    expected = _all_pages(writer)
    writer.close()
    _drop_flags(path)
    reader = TranslationDB(path, readonly=True)
    try:
        assert not reader._quality_flags_ready()
        assert _all_pages(reader) == expected
    finally:
        reader.close()
    migrated = TranslationDB(path)
    try:
        assert migrated._quality_flags_ready()
        assert migrated.verify_quality_flags() == []
        assert _all_pages(migrated) == expected
    finally:
        migrated.close()


def test_interrupted_backfill_resumes_on_next_open(tmp_path, monkeypatch):
    path = tmp_path / "resume.db"
    first = TranslationDB(path)
    _seed(first)
    first.close()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE entry SET qflags=NULL WHERE id % 2 = 0")
    monkeypatch.setattr(quality_flags, "SETTLE_CHUNK", 7)
    statements: list[str] = []
    reopened = TranslationDB(path)
    try:
        assert reopened._quality_flags_ready()
        assert reopened.verify_quality_flags() == []
    finally:
        reopened.close()
    # 已安裝、有效且沒有待重算時，重新開啟不做任何寫入
    again = TranslationDB(path)
    again._conn.set_trace_callback(statements.append)
    try:
        with again._lock:
            quality_flags.install(again._conn)
    finally:
        again._conn.set_trace_callback(None)
        again.close()
    assert not any(sql.lstrip().upper().startswith("BEGIN") for sql in statements)


def test_invalid_flag_falls_back_until_rebuilt(db):
    _seed(db)
    expected = _all_pages(db)
    with db._tx() as conn:
        conn.execute("UPDATE entry SET qflags = 63 - qflags")
        conn.execute("UPDATE meta SET value='0' WHERE key='quality_valid'")
    assert not db._quality_flags_ready()
    assert _all_pages(db) == expected
    assert db.verify_quality_flags()
    generation = db._data_gen()
    db.rebuild_quality_flags()
    assert db._data_gen() != generation
    assert db._quality_flags_ready() and db.verify_quality_flags() == []
    assert _all_pages(db) == expected


def test_definition_version_change_recomputes_every_entry(tmp_path):
    path = tmp_path / "outdated.db"
    first = TranslationDB(path)
    _seed(first)
    with first._tx() as conn:
        conn.execute("UPDATE entry SET qflags = 63 - qflags")
        conn.execute("UPDATE meta SET value='0' WHERE key='quality_schema_version'")
        conn.execute("DROP TRIGGER qflags_effective_update")
    assert not quality_flags.is_installed(first._conn)
    first.close()
    reopened = TranslationDB(path)
    try:
        assert quality_flags.is_installed(reopened._conn)
        assert reopened.verify_quality_flags() == []
        with reopened._tx() as conn:
            conn.execute("UPDATE effective SET zh_tw='改' WHERE entry_id % 2 = 0")
        assert reopened.verify_quality_flags() == []
    finally:
        reopened.close()


def test_guide_link_breakage_is_mismatch_in_flags_pages_and_rows(db):
    """指南連結目標被改壞時，條目清單的「格式不一致」要列出它，不能顯示為「一致」。"""
    _seed(db)
    rows = {
        row.key: row
        for row in db.list_entries("1.21.1", limit=10_000)[0]
        if row.kind == KIND_GUIDE
    }
    assert rows["g1"].quality_state == "mismatch"
    assert any("連結目標" in issue for issue in rows["g1"].quality_issues)
    assert rows["g0"].quality_state == "consistent"
    assert rows["g2"].quality_state == "consistent"

    criteria = EntryFilter(
        version="1.21.1", quality=QualityFilter(status="mismatch"), sort_by="default"
    )
    mismatch_ids, _ = _page(db, criteria, 0, 10_000)
    assert rows["g1"].id in mismatch_ids
    assert rows["g0"].id not in mismatch_ids
    assert db.verify_quality_flags() == []


def test_guide_structure_only_mismatch_is_discovered_and_repaired(db):
    """純 Markdown 結構差異也要能進入候選、混合換行差異不能被跳過。"""
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_GUIDE,
                "guidemod",
                "broken_link",
                "[Crafting](../items/crafting.md)",
                "[合成](/../items/crafting.md)",
                source=SRC_AI,
            ),
            ScanItem(
                KIND_GUIDE,
                "guidemod",
                "broken_link_with_newline",
                "[Crafting](../items/crafting.md)\nNext",
                "[合成](/../items/crafting.md)",
                source=SRC_AI,
            ),
        ],
    )

    count, candidates = db.mismatched_translation_entries("1.21.1")
    assert count == 2
    assert {candidate.key for candidate in candidates} == {
        "broken_link",
        "broken_link_with_newline",
    }

    repairable = {
        entry.key: (entry, newline_mismatch, mixed)
        for entry, newline_mismatch, mixed in db.iter_repairable_translation_entries(
            "1.21.1"
        )
    }
    assert repairable["broken_link"][1:] == (False, False)
    assert repairable["broken_link_with_newline"][1:] == (True, True)

    entry = repairable["broken_link"][0]
    result = db.replace_translation_quality_mismatch(
        entry.entry_id,
        int(entry.source_id),
        entry.current_ai_translation,
        "[合成](../items/crafting.md)",
        expected_version=entry.mc_version,
        expected_kind=entry.kind,
        expected_mod_id=entry.mod_id,
        expected_key=entry.key,
        expected_en_us=entry.en_us,
        expected_revision=entry.revision,
    )
    assert result.status == "updated"


def test_reviewed_lang_glossary_excludes_quality_mismatches(db):
    """已審核但格式錯誤的 lang 譯文不能成為 LLM 術語提示。"""
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "good", "Cobblestone", "鵝卵石", source=SRC_AI),
            ScanItem(KIND_LANG, "foo", "bad", "Use %s", "使用", source=SRC_AI),
        ],
    )
    rows = {row.key: row for row in db.list_entries("1.21.1", limit=10)[0]}
    for row in rows.values():
        db.review_manual(row.id, expected_zh_tw=row.zh_tw, actor="tester")

    assert db.reviewed_lang_glossary("foo") == [
        {"key": "good", "en_us": "Cobblestone", "zh_tw": "鵝卵石"}
    ]


def test_reviewed_lang_glossary_is_version_scoped_and_omits_conflicts(db):
    db.ingest(
        "1.20.1",
        [
            ScanItem(KIND_LANG, "foo", "pressure.old", "Pressure Chamber", "壓力室"),
        ],
    )
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "pressure.a", "Pressure Chamber", "壓力室"),
            ScanItem(KIND_LANG, "foo", "pressure.b", "Pressure Chamber", "氣壓室"),
            ScanItem(KIND_LANG, "foo", "cobble", "Cobblestone", "鵝卵石"),
        ],
    )
    for row in (
        db.list_entries("1.20.1", limit=10)[0] + db.list_entries("1.21.1", limit=10)[0]
    ):
        db.review_manual(row.id, expected_zh_tw=row.zh_tw, actor="tester")

    assert db.reviewed_lang_glossary(
        "foo", "1.21.1", ("Pressure Chamber", "Cobblestone"), limit=None
    ) == [{"key": "cobble", "en_us": "Cobblestone", "zh_tw": "鵝卵石"}]
    assert db.reviewed_lang_glossary("foo", "1.20.1") == [
        {"key": "pressure.old", "en_us": "Pressure Chamber", "zh_tw": "壓力室"}
    ]
