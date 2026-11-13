"""跨版本已審核譯文沿用與版本差異審核。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.cross_version_review import (
    ApplyItem,
    QueryCancelled,
)
from translation_tool.translation_db.schema import (
    KIND_FTB_QUEST_LANG,
    SRC_CROSS_VERSION,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
)


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "cv.db")
    yield d
    d.close()


def _item(key, en, tw="", mod="foo", source=None):
    return ScanItem(KIND_LANG, mod, key, en, tw, "", source)


def _entry(db, version, key, mod="foo"):
    return next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )


def _seed_reviewed(db, version="1.20.1", key="k", text="亮色"):
    db.ingest(version, [_item(key, "Bright", "")])
    eid = _entry(db, version, key).id
    db.save_manual(eid, text, propagate=False)
    db.review_manual(eid, expected_zh_tw=text, propagate=False)
    return eid


def _import(db, version, items):
    """匯入並回傳本次匯入交易實際新增或新增譯文的條目編號。"""
    ids: set[int] = set()
    db.ingest(version, items, entry_ids_out=ids)
    return ids


def _translation_rows(db, version, key):
    eid = _entry(db, version, key).id
    return {
        t.source: (t.zh_tw, t.review_status) for t in db.entry_detail(eid).translations
    }


def _apply_item(row, ref=None):
    return ApplyItem.from_row(row, ref or row.reference)


def test_user_scenario_import_reuses_reviewed_translation(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    assert _entry(db, "26.1", "k").zh_tw == "明亮"

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert (result.applied, result.conflicts, result.skipped_reviewed) == (1, 0, 0)
    row = _entry(db, "26.1", "k")
    assert row.zh_tw == "亮色"
    assert row.review_status == "reviewed"
    assert db.list_cross_version_differences("26.1") == []


def test_entry_without_any_translation_is_reused_too(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "")])  # 沒有 zh_tw 的新條目
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"


def test_repeat_import_is_idempotent(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 1
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 0


def test_conflicting_references_are_listed_not_applied(db):
    _seed_reviewed(db, "1.20.1", text="亮色")
    _seed_reviewed(db, "1.19.2", text="明亮")
    ids = _import(db, "26.1", [_item("k", "Bright", "光亮")])

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert (result.applied, result.conflicts) == (0, 1)
    assert _entry(db, "26.1", "k").zh_tw == "光亮"
    rows = db.list_cross_version_differences("26.1")
    assert len(rows) == 1 and rows[0].conflict
    assert {r.version for r in rows[0].refs} == {"1.20.1", "1.19.2"}


def test_target_reviewed_is_not_overwritten(db):
    _seed_reviewed(db, "1.20.1", text="亮色")
    ids = _import(db, "26.1", [_item("k", "Bright", "")])
    eid = _entry(db, "26.1", "k").id
    db.save_manual(eid, "光亮", propagate=False)
    db.review_manual(eid, expected_zh_tw="光亮", propagate=False)

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert (result.applied, result.skipped_reviewed) == (0, 1)
    assert _entry(db, "26.1", "k").zh_tw == "光亮"


def test_different_source_text_is_not_reused(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Brighter", "明亮")])
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 0
    # 不自動沿用，只在差異審核標為「原文不同」供逐筆判斷
    assert [r.source_changed for r in db.list_cross_version_differences("26.1")] == [
        True
    ]


def test_only_the_entry_ids_of_this_import_are_reused(db):
    """範圍是本次匯入回報的條目，不靠時間推測：連續快速匯入也不會互相影響。"""
    _seed_reviewed(db, key="a", text="甲")
    _seed_reviewed(db, key="b", text="乙")
    first = _import(db, "26.1", [_item("a", "Bright", "明亮")])
    second = _import(db, "26.1", [_item("b", "Bright", "明亮")])  # 同一秒內的第二批
    assert first.isdisjoint(second)

    assert db.reuse_reviewed_translations("26.1", entry_ids=second).applied == 1
    assert _entry(db, "26.1", "b").zh_tw == "乙"
    assert _entry(db, "26.1", "a").zh_tw == "明亮"  # 第一批的條目沒被碰


def test_untouched_entries_are_not_reported_by_a_noop_import(db):
    _seed_reviewed(db)
    _import(db, "26.1", [_item("k", "Bright", "明亮")])
    ids = _import(
        db, "26.1", [_item("k", "Bright", "明亮")]
    )  # 完全相同，沒有新增任何東西
    assert ids == set()
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 0


def test_reuse_is_undoable(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)
    assert db.revert(result.history_id) == 1
    row = _entry(db, "26.1", "k")
    assert row.zh_tw == "明亮" and row.source == SRC_JAR_TW


@pytest.mark.parametrize(
    "ref_source", [SRC_JAR_TW, SRC_SUBTITLE, SRC_MANUAL], ids=lambda s: f"ref{s}"
)
def test_reuse_keeps_the_targets_own_translation_even_with_the_same_source(
    db, ref_source
):
    """目標尚未審核時一律另寫 SRC_CROSS_VERSION 列，目標原本的譯文與來源不被覆寫。"""
    db.ingest("1.20.1", [_item("k", "Bright", "亮色", source=ref_source)])
    ref = _entry(db, "1.20.1", "k")
    db.review_manual(ref.id, expected_zh_tw="亮色", propagate=False)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮", source=ref_source)])

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert result.applied == 1
    rows = _translation_rows(db, "26.1", "k")
    assert rows[ref_source][0] == "明亮"  # 目標自己的譯文仍在原本的來源
    assert rows[SRC_CROSS_VERSION] == ("亮色", "reviewed")
    effective = _entry(db, "26.1", "k")
    assert (effective.zh_tw, effective.source) == ("亮色", SRC_CROSS_VERSION)
    assert db.revert(result.history_id) == 1
    assert SRC_CROSS_VERSION not in _translation_rows(db, "26.1", "k")
    assert _entry(db, "26.1", "k").zh_tw == "明亮"


def test_latest_reference_is_most_recently_reviewed_version(db):
    _seed_reviewed(db, "1.19.2", text="舊譯")
    _seed_reviewed(db, "1.20.1", text="新譯")
    db.ingest("26.1", [_item("k", "Bright", "")])
    fixed = db.list_cross_version_differences("26.1", ref_version="1.19.2")
    assert [r.reference.zh_tw for r in fixed] == ["舊譯"]
    both = db.list_cross_version_differences("26.1")
    assert both[0].conflict and both[0].reference.version == "1.20.1"


def test_review_time_comes_from_the_review_history_not_from_updates(db):
    """審核後又被其他操作更新譯文列，「最近審核」仍以審核事件為準。"""
    _seed_reviewed(db, "1.19.2", text="舊譯")
    eid = _seed_reviewed(db, "1.20.1", text="新譯")
    # 1.19.2 的列之後被更新（updated_at 變新），但沒有新的審核事件
    old = _entry(db, "1.19.2", "k").id
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET updated_at='2999-01-01 00:00:00' WHERE entry_id=?",
            (old,),
        )
    db.ingest("26.1", [_item("k", "Bright", "")])
    row = db.list_cross_version_differences("26.1")[0]
    by_version = {r.version: r for r in row.refs}
    assert by_version["1.20.1"].review_seq > by_version["1.19.2"].review_seq
    assert by_version["1.20.1"].review_seq > 0 and eid


def test_apply_reference_and_stale_guard(db):
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    stale = ApplyItem(
        row.entry_id,
        row.reference.version,
        row.reference.entry_id,
        "別人改過",
        row.reference.zh_tw,
        row.target_source,
        row.target_reviewed,
        row.target_revision,
        row.reference.revision,
    )
    assert db.apply_cross_version_references([stale]).stale == 1

    result = db.apply_cross_version_references([_apply_item(row)])
    assert result.applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"
    assert db.revert(result.history_id) == 1
    assert _entry(db, "26.1", "k").zh_tw == "明亮"


def test_apply_is_stale_when_the_target_became_reviewed_in_between(db):
    """畫面上目標是未審核；套用前同一段譯文被標為已審核 → 不可覆寫（文字沒變也一樣）。"""
    _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    assert not row.target_reviewed
    item = _apply_item(row)

    tgt = _entry(db, "26.1", "k")
    db.review_manual(tgt.id, expected_zh_tw="明亮", propagate=False)  # 另一個操作先審核

    result = db.apply_cross_version_references([item])

    assert (result.applied, result.stale) == (0, 1)
    after = _entry(db, "26.1", "k")
    assert (after.zh_tw, after.review_status) == ("明亮", "reviewed")


def test_apply_is_stale_when_the_target_revision_or_source_changed(db):
    _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    item = _apply_item(row)

    # 同樣的文字被重新儲存成人工譯文（來源與 revision 都變了，文字沒變）
    tgt = _entry(db, "26.1", "k")
    db.save_manual(tgt.id, "明亮", propagate=False)

    assert db.apply_cross_version_references([item]).stale == 1


def test_apply_is_stale_when_the_reference_changed(db):
    ref_id = _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    item = _apply_item(db.list_cross_version_differences("26.1")[0])

    # 參考被改成別的又改回原文（A→B→A）：文字相同，但 revision 已經不同
    db.save_manual(ref_id, "別的", propagate=False)
    db.save_manual(ref_id, "亮色", propagate=False)
    db.review_manual(ref_id, expected_zh_tw="亮色", propagate=False)

    assert db.apply_cross_version_references([item]).stale == 1


def test_explicit_overwrite_of_a_reviewed_target_still_works(db):
    """畫面上就是已審核的目標（使用者勾了「包含目標已審核」）仍可套用。"""
    _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "")])
    tgt = _entry(db, "26.1", "k")
    db.save_manual(tgt.id, "明亮", propagate=False)
    db.review_manual(tgt.id, expected_zh_tw="明亮", propagate=False)
    row = db.list_cross_version_differences("26.1")[0]
    assert row.target_reviewed

    assert db.apply_cross_version_references([_apply_item(row)]).applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"


def test_scan_service_helper_reuses_and_reports(db):
    from types import SimpleNamespace

    from app.services_impl.moddb_service import _reuse_reviewed_after_scan
    from translation_tool.translation_db.scanner import ScanOptions, ScanReport

    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    logs, summaries = [], []
    session = SimpleNamespace(
        set_summary=summaries.append,
        add_log=lambda *a, **k: logs.append(a),
        log=lambda *a, **k: logs.append(a),
    )
    report = ScanReport(version="26.1")
    report.touched_entry_ids.update(ids)
    _reuse_reviewed_after_scan(session, db, ScanOptions(version="26.1"), report)
    assert report.reuse_applied == 1
    assert summaries[-1]["reuse_applied"] == 1
    # 自動沿用的歷史編號帶進摘要，之後可從對話框直接復原
    assert summaries[-1]["reuse_history_id"] == report.reuse_history_id is not None
    assert db.revert(report.reuse_history_id) == 1
    assert _entry(db, "26.1", "k").zh_tw == "明亮"

    off = ScanReport(version="26.1")
    off.touched_entry_ids.update(ids)
    _reuse_reviewed_after_scan(
        session, db, ScanOptions(version="26.1", reuse_reviewed=False), off
    )
    assert off.reuse_applied == 0


@pytest.mark.parametrize(
    ("ref_source", "target_source"),
    [(SRC_JAR_TW, 3), (3, 6), (6, 3), (3, 3)],
)
def test_apply_replaces_a_reviewed_target_whatever_the_sources(
    db, ref_source, target_source
):
    """套用參考譯文後，目標目前顯示的譯文一定要變成參考譯文（不受來源排序影響），且可復原。"""
    db.ingest("1.21.1", [_item("k", "Bright", "亮色", source=ref_source)])
    ref = _entry(db, "1.21.1", "k")
    db.review_manual(ref.id, expected_zh_tw="亮色", propagate=False)
    db.ingest("1.20.1", [_item("k", "Bright", "明亮", source=target_source)])
    tgt = _entry(db, "1.20.1", "k")
    db.review_manual(tgt.id, expected_zh_tw="明亮", propagate=False)

    row = db.list_cross_version_differences("1.20.1", ref_version="1.21.1")[0]
    result = db.apply_cross_version_references([_apply_item(row)])

    after = _entry(db, "1.20.1", "k")
    assert result.applied == 1
    assert (after.zh_tw, after.review_status) == ("亮色", "reviewed")
    assert db.list_cross_version_differences("1.20.1", ref_version="1.21.1") == []
    assert db.revert(result.history_id) == 1
    assert _entry(db, "1.20.1", "k").zh_tw == "明亮"


def test_list_differences_can_be_cancelled_between_chunks(db):
    _seed_reviewed(db, key="a", text="甲")
    db.ingest("26.1", [_item("a", "Bright", "明亮")])
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1  # 第一段之後取消

    with pytest.raises(QueryCancelled):
        db.list_cross_version_differences("26.1", cancel=cancel)


def test_difference_queries_use_no_temporary_sort(db):
    """CLAUDE.md：translation_db 的查詢不得出現 ``USE TEMP B-TREE FOR ORDER BY``。"""
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    from translation_tool.translation_db import cross_version_review as cv

    captured: list[tuple[str, list]] = []
    conn = db._conn
    real = conn.execute

    class Spy:
        def __getattr__(self, name):
            return getattr(conn, name)

        def execute(self, sql, params=()):
            captured.append((sql, list(params)))
            return real(sql, params)

    spy = Spy()
    cv._fetch_rows(spy, "26.1", ref_version="", entry_ids=[1, 2])  # type: ignore[arg-type]
    db.list_cross_version_differences("26.1")
    for sql, params in captured:
        if not sql.lstrip().upper().startswith("SELECT"):
            continue
        plan = " ".join(
            str(r[3]) for r in real(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
        )
        # 規則禁的是 ORDER BY 的臨時排序；共用語系比對子查詢的 count(DISTINCT)
        # 只在單一條目的少數語系上建暫存結構，不在此限。
        assert "TEMP B-TREE FOR ORDER BY" not in plan, (sql[:80], plan)


def _make_legacy(db, version, key):
    """把該條目的人工譯文改成舊資料的狀態：畫面顯示「人工已審核」，但 review_status 不是 'reviewed'。"""
    eid = _entry(db, version, key).id
    with db._tx(keep_review=True) as conn:
        conn.execute(
            "UPDATE translation SET review_status='legacy_unknown' "
            "WHERE entry_id=? AND source=?",
            (eid, SRC_MANUAL),
        )
        db._refresh(conn, [eid])


def _manual(db, version, key, text, *, mod="foo"):
    db.ingest(version, [_item(key, "Bright", "", mod=mod)])
    eid = _entry(db, version, key, mod).id
    db.save_manual(eid, text, propagate=False)
    return eid


def test_legacy_manual_translations_count_as_reviewed_references(db):
    """舊資料的人工譯文（畫面顯示「人工已審核」）也是已審核參考。"""
    _manual(db, "1.21.1", "k", "需要 %s 級盔甲")
    _make_legacy(db, "1.21.1", "k")
    assert _entry(db, "1.21.1", "k").review_status == "legacy_unknown"
    db.ingest("26.1", [_item("k", "Bright", "適用於 §6§l%s§r級盔甲")])

    rows = db.list_cross_version_differences("26.1", ref_version="1.21.1")

    assert [r.reference.zh_tw for r in rows] == ["需要 %s 級盔甲"]


def test_legacy_manual_target_is_treated_as_a_reviewed_target(db):
    """目標是舊資料的人工譯文：畫面顯示已審核，批次套用預設不可覆蓋，明確套用時改寫並可復原。"""
    _seed_reviewed(db, text="亮色")
    _manual(db, "26.1", "k", "明亮")
    _make_legacy(db, "26.1", "k")

    row = db.list_cross_version_differences("26.1")[0]
    assert row.target_reviewed

    result = db.apply_cross_version_references([_apply_item(row)])
    after = _entry(db, "26.1", "k")
    assert (result.applied, after.zh_tw, after.review_status) == (1, "亮色", "reviewed")
    assert db.revert(result.history_id) == 1
    restored = _entry(db, "26.1", "k")
    assert (restored.zh_tw, restored.review_status) == ("明亮", "legacy_unknown")


def test_legacy_reference_is_reused_after_import(db):
    _manual(db, "1.20.1", "k", "亮色")
    _make_legacy(db, "1.20.1", "k")
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])

    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"


PACK_A = "actuallyadditions@All the Mods 10 8.3@40b3569cc156"
PACK_B = "actuallyadditions@All the Mons@bb60be2794c9"


def _pack_item(mod, key, en, tw="", *, locale=None):
    return ScanItem(
        KIND_KUBEJS_LANG,
        mod,
        key,
        en,
        tw,
        "",
        None,
        "assets/x/lang/pt_br.json" if locale else "",
        f"lang:{locale}:{key}" if locale else "",
        "h",
        "lang-json" if locale else "",
        "pack",
        locale,
    )


@pytest.mark.parametrize("locale", [None, "pt_br"])
def test_pack_content_matches_across_pack_identities(db, locale):
    """KubeJS／FTB 的模組識別帶整合包資料夾名稱與路徑雜湊；跨版本只看 @ 前面的命名空間。"""
    db.ingest(
        "1.21.1", [_pack_item(PACK_A, "k", "Sonho", "植物學家的夢想", locale=locale)]
    )
    ref = _entry(db, "1.21.1", "k", PACK_A)
    db.review_manual(ref.id, expected_zh_tw="植物學家的夢想", propagate=False)
    ids = _import(db, "26.1", [_pack_item(PACK_B, "k", "Sonho", "", locale=locale)])

    rows = db.list_cross_version_differences("26.1")
    assert [(r.mod_id, r.reference.mod_id) for r in rows] == [(PACK_B, PACK_A)]

    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 1
    assert _entry(db, "26.1", "k", PACK_B).zh_tw == "植物學家的夢想"


def test_pack_content_with_different_namespace_or_plain_lang_is_not_matched(db):
    db.ingest("1.21.1", [_pack_item(PACK_A, "k", "Sonho", "夢")])
    ref = _entry(db, "1.21.1", "k", PACK_A)
    db.review_manual(ref.id, expected_zh_tw="夢", propagate=False)
    other_ns = "otheradditions@All the Mons@bb60be2794c9"
    db.ingest("26.1", [_pack_item(other_ns, "k", "Sonho", "")])
    assert db.list_cross_version_differences("26.1") == []

    # 一般 lang 內容的模組識別不同，仍然不比對
    db.ingest("1.21.1", [_item("p", "Bright", "", mod="modA")])
    pid = _entry(db, "1.21.1", "p", "modA").id
    db.save_manual(pid, "亮", propagate=False)
    db.review_manual(pid, expected_zh_tw="亮", propagate=False)
    db.ingest("26.1", [_item("p", "Bright", "明亮", mod="modB")])
    assert db.list_cross_version_differences("26.1") == []


def test_two_pack_identities_in_one_version_are_told_apart_by_entry_id(db):
    """同一版本有兩個整合包身分、譯文不同 → 衝突；套用時以參考條目編號選定，不會套錯。"""
    pack_c = "actuallyadditions@Another Pack@aaaaaaaaaaaa"
    for pack, text in ((PACK_A, "甲"), (pack_c, "乙")):
        db.ingest("1.21.1", [_pack_item(pack, "k", "Sonho", text)])
        eid = _entry(db, "1.21.1", "k", pack).id
        db.review_manual(eid, expected_zh_tw=text, propagate=False)
    db.ingest("26.1", [_pack_item(PACK_B, "k", "Sonho", "")])

    row = db.list_cross_version_differences("26.1")[0]
    assert row.conflict and {r.mod_id for r in row.refs} == {PACK_A, pack_c}
    chosen = next(r for r in row.refs if r.mod_id == pack_c)

    assert db.apply_cross_version_references([_apply_item(row, chosen)]).applied == 1
    assert _entry(db, "26.1", "k", PACK_B).zh_tw == "乙"


def _ftb_item(mod, rel, lang_key, en, tw=""):
    return ScanItem(
        KIND_FTB_QUEST_LANG,
        mod,
        f"{rel}|lang:{lang_key}",
        en,
        tw,
    )


def test_ftb_lang_matches_across_lang_file_layouts(db):
    """FTB 語系鍵值含語系檔路徑：8.2 是單檔（__root__.snbt），8.5 拆成多檔；只看 |lang: 後的語系鍵。"""
    ftb_old = "ftbquests@All the Mods 10 8.2@14cb3797fa50"
    ftb_new = "ftbquests@All the Mods 10 8.5@39fa0c9d7170"
    lang_key = "quest.765CD913CC3896B2.quest_desc"
    db.ingest(
        "1.21.1",
        [_ftb_item(ftb_old, "__root__.snbt", lang_key, "Fire Dragons", "舊譯")],
    )
    old = db._one(
        "SELECT id FROM entry WHERE mod_id=? AND key LIKE ?", (ftb_old, "%quest_desc")
    )
    db.review_manual(old[0], expected_zh_tw="舊譯", propagate=False)
    ids = _import(
        db,
        "1.21.1",
        [_ftb_item(ftb_new, "chapters/ice_fire.snbt", lang_key, "Fire Dragons")],
    )

    rows = db.list_cross_version_differences("1.21.1")
    assert [(r.mod_id, r.reference.mod_id) for r in rows] == [(ftb_new, ftb_old)]
    assert db.reuse_reviewed_translations("1.21.1", entry_ids=ids).applied == 1
    new = db._one(
        "SELECT ef.zh_tw FROM entry e JOIN effective ef ON ef.entry_id=e.id "
        "WHERE e.mod_id=?",
        (ftb_new,),
    )
    assert new == ("舊譯",)


def test_ftb_lang_with_a_different_lang_key_is_not_matched(db):
    ftb_old = "ftbquests@All the Mods 10 8.2@14cb3797fa50"
    ftb_new = "ftbquests@All the Mods 10 8.5@39fa0c9d7170"
    db.ingest(
        "1.21.1", [_ftb_item(ftb_old, "__root__.snbt", "quest.A.title", "Hi", "好")]
    )
    old = db._one("SELECT id FROM entry WHERE mod_id=?", (ftb_old,))
    db.review_manual(old[0], expected_zh_tw="好", propagate=False)
    db.ingest("1.21.1", [_ftb_item(ftb_new, "chapter.snbt", "quest.B.title", "Hi")])
    assert db.list_cross_version_differences("1.21.1") == []


def test_same_game_version_other_pack_folder_counts_as_another_version(db):
    """整合包 8.2 與 8.5 都標成 1.21.1：同遊戲版本、不同整合包資料夾也要能沿用與列出差異。"""
    db.ingest("1.21.1", [_pack_item(PACK_A, "k", "Sonho", "舊譯")])
    old = _entry(db, "1.21.1", "k", PACK_A)
    db.review_manual(old.id, expected_zh_tw="舊譯", propagate=False)
    ids = _import(db, "1.21.1", [_pack_item(PACK_B, "k", "Sonho", "")])

    rows = db.list_cross_version_differences("1.21.1")
    assert [(r.mod_id, r.reference.mod_id) for r in rows] == [(PACK_B, PACK_A)]

    assert db.reuse_reviewed_translations("1.21.1", entry_ids=ids).applied == 1
    assert _entry(db, "1.21.1", "k", PACK_B).zh_tw == "舊譯"


def test_same_game_version_same_pack_or_plain_lang_is_not_another_version(db):
    """同一個整合包資料夾、或一般 lang 內容，同遊戲版本都不算其他版本。"""
    db.ingest("1.21.1", [_pack_item(PACK_A, "k", "Sonho", "舊譯")])
    db.review_manual(
        _entry(db, "1.21.1", "k", PACK_A).id, expected_zh_tw="舊譯", propagate=False
    )
    assert db.list_cross_version_differences("1.21.1") == []

    db.ingest("1.21.1", [_item("p", "Bright", "", mod="modA")])
    pid = _entry(db, "1.21.1", "p", "modA").id
    db.save_manual(pid, "亮", propagate=False)
    db.review_manual(pid, expected_zh_tw="亮", propagate=False)
    db.ingest("1.21.1", [_item("p", "Bright", "", mod="modB")])
    assert db.list_cross_version_differences("1.21.1") == []


def _origin(db, entry_id, locale, selector="lang:x:k", path="assets/x/lang/x.json"):
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO entry_origin (entry_id, pack_scope, source_kind, source_path, "
            "selector, source_hash, format_profile, source_locale) "
            "VALUES (?, 'pack', 'kubejs_lang', ?, ?, 'h', 'lang-json', ?)",
            (entry_id, path, selector, locale),
        )


def test_exclusions_explain_why_same_content_is_not_listed(db):
    """內容相同卻沒被列為差異：原因要能說出來（來源語系不相容、參考尚未審核）。"""
    # 參考已審核，但來源語系混有多個（不相容）
    db.ingest("1.21.1", [_pack_item(PACK_A, "amb", "Sonho", "夢")])
    ref = _entry(db, "1.21.1", "amb", PACK_A).id
    db.review_manual(ref, expected_zh_tw="夢", propagate=False)
    _origin(db, ref, "pt_br", "lang:pt_br:amb", "a/pt_br.json")
    _origin(db, ref, "es_es", "lang:es_es:amb", "a/es_es.json")
    db.ingest("26.1", [_pack_item(PACK_B, "amb", "Sonho", "")])
    _origin(
        db,
        _entry(db, "26.1", "amb", PACK_B).id,
        "pt_br",
        "lang:pt_br:amb",
        "b/pt_br.json",
    )
    # 參考只有尚未審核的譯文
    db.ingest("1.21.1", [_pack_item(PACK_A, "unrev", "Dois", "二")])
    db.ingest("26.1", [_pack_item(PACK_B, "unrev", "Dois", "")])
    # 正常可列出的
    db.ingest("1.21.1", [_pack_item(PACK_A, "ok", "Tres", "三")])
    ok = _entry(db, "1.21.1", "ok", PACK_A).id
    db.review_manual(ok, expected_zh_tw="三", propagate=False)
    db.ingest("26.1", [_pack_item(PACK_B, "ok", "Tres", "")])

    listed = {r.key for r in db.list_cross_version_differences("26.1")}
    summary = db.explain_cross_version_exclusions("26.1")

    assert listed == {"ok"}
    assert (summary.locale_incompatible, summary.ref_unreviewed) == (1, 1)
    assert summary.target_locale_unknown == 0  # 這筆是參考混有多個語系，目標語系已知
    assert any("amb" in s and "目標語系 pt_br" in s for s in summary.samples)


def test_exclusions_are_empty_when_everything_is_listed(db):
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    assert db.explain_cross_version_exclusions("26.1").total == 0


def test_rescan_repairs_a_target_whose_source_locale_was_never_recorded(db):
    """舊版匯入的條目來源紀錄沒有語系 → 不相容；重新掃描補上語系後就列得出來。"""
    db.ingest("1.21.1", [_pack_item(PACK_A, "k", "Sonho", "夢", locale="en_us")])
    ref = _entry(db, "1.21.1", "k", PACK_A)
    db.review_manual(ref.id, expected_zh_tw="夢", propagate=False)
    db.ingest("26.1", [_pack_item(PACK_B, "k", "Sonho", "", locale="en_us")])
    with db._tx() as conn:  # 模擬舊版匯入：來源紀錄沒有語系
        conn.execute(
            "UPDATE entry_origin SET source_locale=NULL WHERE entry_id=?",
            (_entry(db, "26.1", "k", PACK_B).id,),
        )
    assert db.list_cross_version_differences("26.1") == []
    summary = db.explain_cross_version_exclusions("26.1")
    assert (summary.locale_incompatible, summary.target_locale_unknown) == (1, 1)

    db.ingest(
        "26.1", [_pack_item(PACK_B, "k", "Sonho", "", locale="en_us")]
    )  # 重新掃描

    assert [r.key for r in db.list_cross_version_differences("26.1")] == ["k"]
    assert db.explain_cross_version_exclusions("26.1").total == 0


def test_exclusions_are_reported_even_when_another_reference_is_compatible(db):
    """目標「亮色」：參考 A 相容但譯文相同、參考 B 譯文不同卻語系不相容 → 沒有差異可列，
    但 B 擋掉的差異要被說出來（原本因為 A 相容而兩邊都不算）。"""
    db.ingest("1.21.1", [_pack_item(PACK_A, "k", "Sonho", "亮色")])
    a = _entry(db, "1.21.1", "k", PACK_A).id
    db.review_manual(a, expected_zh_tw="亮色", propagate=False)
    _origin(db, a, "pt_br", "lang:pt_br:k", "a/pt_br.json")

    pack_c = "actuallyadditions@Third@cccccccccccc"
    db.ingest("1.20.1", [_pack_item(pack_c, "k", "Sonho", "明亮")])
    b = _entry(db, "1.20.1", "k", pack_c).id
    db.review_manual(b, expected_zh_tw="明亮", propagate=False)
    _origin(db, b, "pt_br", "lang:pt_br:k", "c/pt_br.json")
    _origin(db, b, "es_es", "lang:es_es:k", "c/es_es.json")  # 混有多個語系：不相容

    db.ingest("26.1", [_pack_item(PACK_B, "k", "Sonho", "亮色")])
    _origin(
        db, _entry(db, "26.1", "k", PACK_B).id, "pt_br", "lang:pt_br:k", "b/pt_br.json"
    )

    assert db.list_cross_version_differences("26.1") == []
    summary = db.explain_cross_version_exclusions("26.1")
    assert (summary.locale_incompatible, summary.ref_unreviewed) == (1, 0)
    assert any("1.20.1" not in s and s.startswith("k（") for s in summary.samples)


def test_exclusions_count_each_target_once_with_the_locale_reason_first(db):
    """同一個目標既有語系不相容的已審核參考、又有未審核參考：只計一次，原因以語系為先。"""
    db.ingest("1.21.1", [_pack_item(PACK_A, "k", "Sonho", "明亮")])
    a = _entry(db, "1.21.1", "k", PACK_A).id
    db.review_manual(a, expected_zh_tw="明亮", propagate=False)
    _origin(db, a, "pt_br", "lang:pt_br:k", "a/pt_br.json")
    _origin(db, a, "es_es", "lang:es_es:k", "a/es_es.json")
    pack_c = "actuallyadditions@Third@cccccccccccc"
    db.ingest("1.20.1", [_pack_item(pack_c, "k", "Sonho", "光亮")])  # 未審核
    db.ingest("26.1", [_pack_item(PACK_B, "k", "Sonho", "")])
    _origin(
        db, _entry(db, "26.1", "k", PACK_B).id, "pt_br", "lang:pt_br:k", "b/pt_br.json"
    )

    summary = db.explain_cross_version_exclusions("26.1")

    assert (summary.locale_incompatible, summary.ref_unreviewed) == (1, 0)


def test_reference_can_be_pinned_to_one_pack_version(db):
    """同一個遊戲版本有多個整合包版本：指定整合包資料夾時，只拿那一個當參考。"""
    pack_c = "actuallyadditions@Another Pack@aaaaaaaaaaaa"
    for pack, text in ((PACK_A, "甲"), (pack_c, "乙")):
        db.ingest("1.21.1", [_pack_item(pack, "k", "Sonho", text)])
        eid = _entry(db, "1.21.1", "k", pack).id
        db.review_manual(eid, expected_zh_tw=text, propagate=False)
    db.ingest("26.1", [_pack_item(PACK_B, "k", "Sonho", "")])

    assert db.pack_versions("1.21.1") == ["All the Mods 10 8.3", "Another Pack"]

    rows = db.list_cross_version_differences(
        "26.1", ref_version="1.21.1", ref_pack="Another Pack"
    )
    assert [(r.conflict, r.reference.zh_tw) for r in rows] == [(False, "乙")]
    assert (
        db.list_cross_version_differences(
            "26.1", ref_version="1.21.1", ref_pack="不存在的整合包"
        )
        == []
    )
    # 沒指定整合包時維持原樣：兩個參考不同＝衝突
    assert db.list_cross_version_differences("26.1")[0].conflict

    summary = db.explain_cross_version_exclusions(
        "26.1", ref_version="1.21.1", ref_pack="不存在的整合包"
    )
    assert summary.total == 0


def test_target_can_be_pinned_to_one_pack_version(db):
    """目標選整合包版本：只列該整合包資料夾的條目，其他整合包的條目不列。"""
    pack_c = "actuallyadditions@Another Pack@aaaaaaaaaaaa"
    db.ingest("1.20.1", [_pack_item(PACK_A, "k", "Sonho", "甲")])
    eid = _entry(db, "1.20.1", "k", PACK_A).id
    db.review_manual(eid, expected_zh_tw="甲", propagate=False)
    db.ingest(
        "1.21.1",
        [
            _pack_item(PACK_B, "k", "Sonho", "乙"),
            _pack_item(pack_c, "k", "Sonho", "丙"),
        ],
    )

    both = db.list_cross_version_differences("1.21.1")
    assert {r.mod_id for r in both} == {PACK_B, pack_c}
    only = db.list_cross_version_differences("1.21.1", target_pack="Another Pack")
    assert [r.mod_id for r in only] == [pack_c]
    assert db.pack_mods("1.21.1", "Another Pack") == [pack_c]
    summary = db.explain_cross_version_exclusions("1.21.1", target_pack="不存在")
    assert summary.total == 0


def test_case_or_whitespace_only_source_change_counts_as_same_content(db):
    """原文只差大小寫或空白：視為相同內容，匯入後自動沿用其他版本已審核的譯文。"""
    db.ingest("1.20.1", [_pack_item(PACK_A, "k", "Mostre Sua Cara", "露出你的臉")])
    ref = _entry(db, "1.20.1", "k", PACK_A)
    db.review_manual(ref.id, expected_zh_tw="露出你的臉", propagate=False)
    ids = _import(db, "1.21.1", [_pack_item(PACK_B, "k", "Mostre  sua cara ")])

    rows = db.list_cross_version_differences("1.21.1")
    assert [(r.source_changed, r.reference.zh_tw) for r in rows] == [
        (False, "露出你的臉")
    ]
    assert db.reuse_reviewed_translations("1.21.1", entry_ids=ids).applied == 1
    assert _entry(db, "1.21.1", "k", PACK_B).zh_tw == "露出你的臉"


def test_real_source_change_is_listed_but_never_auto_reused(db):
    """原文真的不同：不自動沿用，但列在差異審核（標記原文不同、附參考原文）讓使用者逐筆套用。"""
    db.ingest("1.20.1", [_pack_item(PACK_A, "k", "Old text", "舊譯文")])
    ref = _entry(db, "1.20.1", "k", PACK_A)
    db.review_manual(ref.id, expected_zh_tw="舊譯文", propagate=False)
    ids = _import(db, "1.21.1", [_pack_item(PACK_B, "k", "New text")])

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.source_changed) == (0, 1)
    assert _entry(db, "1.21.1", "k", PACK_B).zh_tw == ""

    rows = db.list_cross_version_differences("1.21.1")
    assert [(r.source_changed, r.reference.en_us) for r in rows] == [(True, "Old text")]
    assert db.apply_cross_version_references([_apply_item(rows[0])]).applied == 1
    assert _entry(db, "1.21.1", "k", PACK_B).zh_tw == "舊譯文"


def test_same_source_reference_wins_over_changed_source_references(db):
    pack_c = "actuallyadditions@Another Pack@aaaaaaaaaaaa"
    for pack, en, text in ((PACK_A, "Same", "相同"), (pack_c, "Other words", "不同")):
        db.ingest("1.20.1", [_pack_item(pack, "k", en, text)])
        db.review_manual(
            _entry(db, "1.20.1", "k", pack).id, expected_zh_tw=text, propagate=False
        )
    db.ingest("1.21.1", [_pack_item(PACK_B, "k", "same")])
    row = db.list_cross_version_differences("1.21.1")[0]
    assert not row.source_changed and not row.conflict
    assert [r.zh_tw for r in row.refs] == ["相同"]


def test_unrelated_source_with_the_same_key_is_not_listed(db):
    db.ingest("1.20.1", [_pack_item(PACK_A, "k", "Condition Level 2", "條件")])
    db.review_manual(
        _entry(db, "1.20.1", "k", PACK_A).id, expected_zh_tw="條件", propagate=False
    )
    db.ingest("1.21.1", [_pack_item(PACK_B, "k", "This entry needs a key to open")])
    assert db.list_cross_version_differences("1.21.1") == []


def test_similar_source_matches_plain_ratio_threshold():
    """先用便宜的上界排除不相似原文，結果必須和直接算 ratio 相同。"""
    from difflib import SequenceMatcher

    from translation_tool.translation_db.content_identity import loose_text
    from translation_tool.translation_db.cross_version_review import (
        SOURCE_SIMILARITY,
        similar_source,
    )

    pairs = [
        ("Hello world", "hello  world!"),
        ("Hello world", "Completely different text"),
        ("Open the door", "Open the big door"),
        ("abc", "xyz"),
        ("", "abc"),
        ("a" * 50, "a" * 10),
    ]
    for a, b in pairs:
        left, right = loose_text(a), loose_text(b)
        expected = bool(left and right) and (
            SequenceMatcher(None, left, right, autojunk=False).ratio()
            >= SOURCE_SIMILARITY
        )
        assert similar_source(a, b) is expected


def test_scan_matches_fetch_rows_and_counts_exclusions(db):
    """一次比對（scan_differences）的差異列與逐段 ``_fetch_rows`` 相同，並同時統計未列出原因。"""
    from translation_tool.translation_db import cross_version_review as cv

    _seed_reviewed(db, "1.20.1", key="a", text="亮色")
    _seed_reviewed(db, "1.19.2", key="a", text="明亮")  # 與 1.20.1 衝突
    db.ingest("1.18.2", [_item("b", "Bright", "亮")])  # 其他版本只有未審核的譯文
    _import(
        db,
        "26.1",
        [
            _item("a", "Bright", "光亮"),
            _item("b", "Bright", "暗"),
            _item("c", "X", "y"),
        ],
    )

    rows, summary = cv.scan_differences(db, "26.1")

    ids = [r.id for r in db.list_entries("26.1", limit=None)[0]]
    expected = [
        r
        for r in cv._fetch_rows(db._conn, "26.1", ref_version="", entry_ids=ids)
        if r.differs
    ]
    assert rows and repr(rows) == repr(expected)
    assert summary.ref_unreviewed == 1
    assert db.list_cross_version_differences("26.1") == rows
    assert db.explain_cross_version_exclusions("26.1") == summary
