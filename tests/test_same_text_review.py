"""同文一致性審核：原文相同、譯文不一致的群組與統一譯文的套用。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.query_interrupt import QueryCancelled
from translation_tool.translation_db.same_text_review import (
    SameTextApplyItem,
)
from translation_tool.translation_db.schema import SRC_JAR_TW, SRC_MANUAL

VERSION = "1.20.1"
PACK_A = "actuallyadditions@Pack A@aaaaaaaaaaaa"
PACK_B = "actuallyadditions@Pack B@bbbbbbbbbbbb"


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "same_text.db")
    yield database
    database.close()


def _item(key, en, tw="", mod="foo", kind=KIND_LANG, source=None):
    return ScanItem(kind, mod, key, en, tw, "", source)


def _pack_item(mod, key, en, tw="", locale="en_us"):
    return ScanItem(
        KIND_KUBEJS_LANG,
        mod,
        key,
        en,
        tw,
        "",
        None,
        "assets/x/lang/en_us.json",
        f"lang:{locale}:{key}",
        "h",
        "lang-json",
        "pack",
        locale,
    )


def _entry(db, key, mod="foo", version=VERSION):
    return next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )


def _review(db, key, text, mod="foo", version=VERSION):
    """人工寫入並審核一筆譯文。"""
    eid = _entry(db, key, mod, version).id
    db.save_manual(eid, text, propagate=False)
    db.review_manual(eid, expected_zh_tw=text, propagate=False)
    return eid


def _groups(db, **kwargs):
    return db.list_same_text_groups(VERSION, **kwargs)


def _apply_items(group, text, *, include_reviewed=False, only=None):
    candidate = group.candidate(text)
    return [
        SameTextApplyItem.from_member(
            m, candidate, allow_overwrite_reviewed=include_reviewed
        )
        for m in group.targets(text)
        if only is None or m.entry_id in only
    ]


def _zh(db, key, mod="foo"):
    return _entry(db, key, mod).zh_tw


def test_groups_list_only_inconsistent_same_source_entries(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Cancel", "取消"),
            _item("b", "Cancel", "取消"),  # 一致的群組不列
            _item("c", "Close", "關閉"),
            _item("d", "Close", "閉合"),
            _item("e", "Open", "開啟"),
            _item("f", "Open", ""),  # 一種譯文＋尚未翻譯
            _item("g", "Alone", "單獨"),
            _item("h", "Empty", ""),
            _item("i", "Empty", ""),  # 全部未翻譯：沒有不一致可言
        ],
    )
    groups = _groups(db)
    assert sorted(g.en_us for g in groups) == ["Close", "Open"]
    close = next(g for g in groups if g.en_us == "Close")
    assert close.distinct_translations == 2 and close.untranslated == 0
    opened = next(g for g in groups if g.en_us == "Open")
    assert opened.distinct_translations == 1 and opened.untranslated == 1


def test_loose_source_match_merges_case_and_whitespace(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Iron  Ingot", "鐵錠"),
            _item("b", "iron ingot", "鐵條"),
            _item("c", "IRON INGOT", ""),
            _item("d", "Iron\nIngot", "鐵塊"),  # 換行位置有語義：不併入
        ],
    )
    (group,) = _groups(db)
    assert sorted(m.key for m in group.members) == ["a", "b", "c"]
    assert group.distinct_translations == 2


def test_lang_and_kubejs_lang_interoperate_other_kinds_group_by_themselves(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Sonho", "夢"),
            _pack_item(PACK_A, "k", "Sonho", "夢想"),
            _item("p1", "Sonho", "夢境", kind="patchouli"),
        ],
    )
    assert len(_groups(db)) == 1  # patchouli 只有一筆，自己不成群組
    (group,) = _groups(db, kinds=["lang", "kubejs_lang"])
    assert sorted(m.kind for m in group.members) == [KIND_KUBEJS_LANG, KIND_LANG]
    assert group.kinds == (KIND_LANG, KIND_KUBEJS_LANG)
    assert _groups(db, kinds=["patchouli"]) == []


def test_long_sources_are_skipped_and_every_kind_is_scanned(db):
    long_text = "x" * 81
    db.ingest(
        VERSION,
        [
            _item("a", long_text, "甲"),
            _item("b", long_text, "乙"),
            _item("c", "Same", "同", kind="guide"),
            _item("d", "Same", "相同", kind="guide"),
        ],
    )
    (group,) = _groups(db)  # 只剩 guide：沒有類型例外
    assert group.kinds == ("guide",)
    assert len(_groups(db, max_chars=200)) == 2
    assert len(_groups(db, kinds=["guide"])) == 1


def test_source_locale_isolates_groups_and_unknown_is_not_merged(db):
    db.ingest(
        VERSION,
        [
            _pack_item(PACK_A, "k1", "Sonho", "夢", locale="pt_br"),
            _pack_item(PACK_A, "k2", "Sonho", "夢想", locale="es_es"),
            _pack_item(PACK_A, "k3", "Sonho", "夢境", locale="pt_br"),
            _pack_item(PACK_A, "k4", "Sonho", "夢幻", locale="es_es"),
        ],
    )
    groups = _groups(db)
    assert sorted(g.locale for g in groups) == ["es_es", "pt_br"]
    for group in groups:
        assert len(group.members) == 2
    # 來源語系未記錄的條目不併入任何群組
    with db._tx() as conn:
        conn.execute("UPDATE entry_origin SET source_locale=NULL")
    assert _groups(db) == []


def test_pack_scope_limits_pack_content_but_not_jar_lang(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Sonho", "夢"),
            _pack_item(PACK_A, "k", "Sonho", "夢想"),
            _pack_item(PACK_B, "k", "Sonho", "夢境"),
        ],
    )
    (everything,) = _groups(db)
    assert len(everything.members) == 3
    (only_a,) = _groups(db, pack_scope="Pack A@aaaaaaaaaaaa")
    assert sorted(m.mod_id for m in only_a.members) == [PACK_A, "foo"]
    assert _groups(db, pack_scope="Pack Z@zzzz") == []


def test_mod_filter_and_source_text_filter(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Cancel", "取消", mod="foo"),
            _item("b", "Cancel", "撤銷", mod="foo"),
            _item("c", "Cancel", "取消", mod="bar"),
            _item("d", "Close", "關閉", mod="foo"),
            _item("e", "Close", "閉合", mod="foo"),
        ],
    )
    assert len(_groups(db)) == 2
    (scoped,) = _groups(db, mod_ids=["bar", "foo"], en_us="  cancel ")
    assert scoped.en_us == "Cancel"
    assert len(scoped.members) == 3
    (only_foo,) = _groups(db, mod_ids=["foo"], en_us="cancel")
    assert len(only_foo.members) == 2


def test_only_reviewed_translations_are_canonical_candidates(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "閉合"),
            _item("c", "Close", "關上"),
        ],
    )
    (group,) = _groups(db)
    assert not group.applicable and group.default_candidate is None

    _review(db, "b", "閉合")
    (group,) = _groups(db)
    assert [c.zh_tw for c in group.candidates] == ["閉合"]
    assert group.default_candidate.entry_id == _entry(db, "b").id


def test_default_candidate_prefers_most_members_then_manual(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "關閉"),
            _item("c", "Close", "閉合"),
            _item("d", "Close", "關上", source=SRC_JAR_TW),
        ],
    )
    _review(db, "a", "關閉")
    _review(db, "c", "閉合")
    (group,) = _groups(db)
    assert [c.zh_tw for c in group.candidates] == ["關閉", "閉合"]
    assert group.default_candidate.member_count == 2
    # 成員數相同時人工來源優先
    _review(db, "b", "關閉")
    db.ingest(VERSION, [_item("e", "Close", "閉合")])
    _review(db, "e", "閉合")
    (group,) = _groups(db)
    counts = {c.zh_tw: c.member_count for c in group.candidates}
    assert counts == {"關閉": 2, "閉合": 2}
    assert all(c.manual for c in group.candidates)


def test_apply_writes_reviewed_manual_rows_and_keeps_old_translations(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "閉合", source=SRC_JAR_TW),
            _item("c", "Close", ""),
        ],
    )
    _review(db, "a", "關閉")
    (group,) = _groups(db)
    result = db.apply_same_text_canonical(_apply_items(group, "關閉"), actor="測試")
    assert (result.applied, result.stale) == (2, 0)
    assert _zh(db, "b") == _zh(db, "c") == "關閉"
    # 已審核列贏過未審核列；b 原本的模組自帶譯文保留在自己的列
    rows = db._conn.execute(
        "SELECT source, zh_tw, review_status FROM translation WHERE entry_id=? "
        "ORDER BY source",
        (_entry(db, "b").id,),
    ).fetchall()
    assert rows == [(SRC_JAR_TW, "閉合", None), (SRC_MANUAL, "關閉", "reviewed")]
    note = db._conn.execute(
        "SELECT note, action FROM history WHERE id=?", (result.history_id,)
    ).fetchone()
    assert note == ("同文統一", "review")
    assert _groups(db) == []  # 統一後沒有不一致了


def test_reviewed_members_need_explicit_overwrite(db):
    db.ingest(VERSION, [_item("a", "Close", "關閉"), _item("b", "Close", "閉合")])
    _review(db, "a", "關閉")
    _review(db, "b", "閉合")
    (group,) = _groups(db)
    refused = db.apply_same_text_canonical(_apply_items(group, "關閉"))
    assert (refused.applied, refused.skipped_reviewed) == (0, 1)
    assert _zh(db, "b") == "閉合"

    allowed = db.apply_same_text_canonical(
        _apply_items(group, "關閉", include_reviewed=True)
    )
    assert allowed.applied == 1
    assert _zh(db, "b") == "關閉"
    row = db._conn.execute(
        "SELECT review_status FROM effective WHERE entry_id=?", (_entry(db, "b").id,)
    ).fetchone()
    assert row == ("reviewed",)


def test_stale_target_or_canonical_is_skipped(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "閉合"),
            _item("c", "Close", "關上"),
        ],
    )
    a = _review(db, "a", "關閉")
    (group,) = _groups(db)
    items = _apply_items(group, "關閉")
    assert len(items) == 2
    # 畫面載入後 b 被人改過：略過，不覆蓋較新的修改
    db.save_manual(_entry(db, "b").id, "闔上", propagate=False)
    result = db.apply_same_text_canonical(items)
    assert (result.applied, result.stale) == (1, 1)
    assert _zh(db, "b") == "闔上" and _zh(db, "c") == "關閉"

    # 統一譯文的來源條目被改了：整批都不套用
    (group,) = _groups(db)
    items = _apply_items(group, "關閉")
    db.save_manual(a, "關上了", propagate=False)
    result = db.apply_same_text_canonical(items)
    assert (result.applied, result.stale) == (0, len(items))


def test_apply_is_one_history_batch_and_revertible(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "閉合"),
            _item("c", "Close", ""),
        ],
    )
    _review(db, "a", "關閉")
    (group,) = _groups(db)
    result = db.apply_same_text_canonical(_apply_items(group, "關閉"))
    assert result.applied == 2
    batches = db._conn.execute(
        "SELECT DISTINCT batch FROM history WHERE note='同文統一'"
    ).fetchall()
    assert len(batches) == 1

    assert db.revert(result.history_id) == 2
    assert _zh(db, "b") == "閉合" and _zh(db, "c") == ""
    (group,) = _groups(db)
    assert group.distinct_translations == 2


def test_revert_restores_an_overwritten_reviewed_translation(db):
    db.ingest(VERSION, [_item("a", "Close", "關閉"), _item("b", "Close", "閉合")])
    _review(db, "a", "關閉")
    _review(db, "b", "閉合")
    (group,) = _groups(db)
    result = db.apply_same_text_canonical(
        _apply_items(group, "關閉", include_reviewed=True)
    )
    assert _zh(db, "b") == "關閉"
    assert db.revert(result.history_id) == 1
    assert _zh(db, "b") == "閉合"


def test_scan_is_cancellable_and_reports_progress(db):
    db.ingest(
        VERSION,
        [_item(f"k{i}", f"Text {i % 7}", f"譯{i % 3}") for i in range(1300)],
    )
    seen: list[float] = []
    groups = _groups(db, progress=seen.append)
    assert groups and seen[-1] == 1.0 and seen == sorted(seen)

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # 第一段之後取消

    with pytest.raises(QueryCancelled):
        _groups(db, cancel=cancel, max_chars=81)  # 換條件才不會取到快取


def test_source_text_filter_finds_common_case_and_space_variants(db):
    """指定原文時改走原文索引：原樣、全小寫、全大寫與首字大寫的寫法都找得到；多餘空白的寫法只有完整掃描找得到。"""
    db.ingest(
        VERSION,
        [
            _item("a", "Iron Ingot", "鐵錠"),
            _item("b", "iron ingot", "鐵條"),
            _item("c", "IRON INGOT", ""),
            _item("d", "Iron  Ingot ", "鐵塊"),
            _item("e", "Other", "別的"),
            _item("f", "Other", "其他"),
        ],
    )
    (group,) = _groups(db, en_us="Iron Ingot")
    assert sorted(m.key for m in group.members) == ["a", "b", "c"]
    assert [g.en_us for g in _groups(db, en_us=" iron  ingot")] == ["Iron Ingot"]
    assert _groups(db, en_us="Nothing") == []
    # 已知限制：原文索引查不到「多餘空白」的寫法（d），完整掃描才會併入
    (full,) = [g for g in _groups(db) if g.en_us == "Iron Ingot"]
    assert sorted(m.key for m in full.members) == ["a", "b", "c", "d"]


# ---------------------------------------------------------------- 審查後續修正


def _force_reviewed(db, key, source, text, checker="", mod="foo"):
    """直接寫入指定來源的已審核列並重整生效譯文（模擬非人工的已審核來源）。"""
    eid = _entry(db, key, mod).id
    with db._tx(keep_review=True) as conn:
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,?, 'reviewed') ON CONFLICT(entry_id, source) DO UPDATE SET "
            "zh_tw=excluded.zh_tw, checker=excluded.checker, review_status='reviewed'",
            (eid, source, text, checker),
        )
        db._refresh(conn, [eid])
    return eid


def _checker_of(db, key, source):
    return db._conn.execute(
        "SELECT checker FROM translation WHERE entry_id=? AND source=?",
        (_entry(db, key).id, source),
    ).fetchone()[0]


def test_written_rows_carry_a_recognisable_checker(db):
    from translation_tool.translation_db.same_text_review import SAME_TEXT_CHECKER
    from translation_tool.translation_db.schema import SRC_CROSS_VERSION

    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "閉合"),
            _item("c", "Close", "關上"),
            _item("d", "Close", "合上", source=SRC_JAR_TW),
        ],
    )
    _review(db, "a", "關閉")
    _force_reviewed(db, "c", SRC_CROSS_VERSION, "關上", checker="某人")
    _force_reviewed(db, "d", SRC_JAR_TW, "合上")
    (group,) = _groups(db)
    result = db.apply_same_text_canonical(
        _apply_items(group, "關閉", include_reviewed=True), actor="測試"
    )
    assert result.applied == 3
    assert _checker_of(db, "b", SRC_MANUAL) == SAME_TEXT_CHECKER  # 新寫的人工列
    assert _checker_of(db, "c", SRC_CROSS_VERSION) == SAME_TEXT_CHECKER  # 不再清空
    assert _checker_of(db, "d", SRC_JAR_TW) == ""  # 模組自帶來源維持空字串


def test_manual_candidates_rank_before_member_count(db):
    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉", source=SRC_JAR_TW),
            _item("b", "Close", "關閉", source=SRC_JAR_TW),
            _item("c", "Close", "關閉", source=SRC_JAR_TW),
            _item("d", "Close", "閉合"),
        ],
    )
    for key in "abc":
        _force_reviewed(db, key, SRC_JAR_TW, "關閉")
    _review(db, "d", "閉合")
    (group,) = _groups(db)
    assert [c.zh_tw for c in group.candidates] == ["閉合", "關閉"]
    assert group.default_candidate.source == SRC_MANUAL
    assert group.candidates[1].source == SRC_JAR_TW and not group.candidates[1].manual


def test_groups_split_into_inconsistent_and_fill_categories(db):
    from translation_tool.translation_db.same_text_review import (
        CATEGORY_FILL,
        CATEGORY_INCONSISTENT,
    )

    db.ingest(
        VERSION,
        [
            _item("a", "Close", "關閉"),
            _item("b", "Close", "閉合"),
            _item("c", "Open", "開啟"),
            _item("d", "Open", ""),
        ],
    )
    cats = {g.en_us: g.category for g in _groups(db)}
    assert cats == {"Close": CATEGORY_INCONSISTENT, "Open": CATEGORY_FILL}


def test_canonical_source_text_change_makes_the_batch_stale(db):
    db.ingest(VERSION, [_item("a", "Close", "關閉"), _item("b", "Close", "閉合")])
    a = _review(db, "a", "關閉")
    (group,) = _groups(db)
    items = _apply_items(group, "關閉")
    with db._tx() as conn:  # 統一譯文的來源條目換了原文（譯文與 revision 都沒變）
        conn.execute("UPDATE entry SET en_us='Shut' WHERE id=?", (a,))
    result = db.apply_same_text_canonical(items)
    assert (result.applied, result.stale) == (0, 1)
    # 只差大小寫與空白仍視為同一句
    with db._tx() as conn:
        conn.execute("UPDATE entry SET en_us=' CLOSE ' WHERE id=?", (a,))
    assert db.apply_same_text_canonical(items).applied == 1


def test_duplicate_entries_in_one_batch_are_applied_once(db):
    db.ingest(VERSION, [_item("a", "Close", "關閉"), _item("b", "Close", "閉合")])
    _review(db, "a", "關閉")
    (group,) = _groups(db)
    items = _apply_items(group, "關閉")
    result = db.apply_same_text_canonical(items + items)
    assert (result.applied, result.duplicates) == (1, 1)
    count = db._conn.execute(
        "SELECT COUNT(*) FROM history WHERE note='同文統一'"
    ).fetchone()[0]
    assert count == 1


def test_pack_scope_also_limits_other_kinds_with_a_pack_identity(db):
    db.ingest(
        VERSION,
        [
            _item("s1", "Hello", "你好", mod=PACK_A, kind="kubejs_script"),
            _item("s2", "Hello", "哈囉", mod=PACK_A, kind="kubejs_script"),
            _item("s3", "Hello", "嗨", mod=PACK_B, kind="kubejs_script"),
        ],
    )
    (everything,) = _groups(db)
    assert len(everything.members) == 3
    (only_a,) = _groups(db, pack_scope="Pack A@aaaaaaaaaaaa")
    assert sorted(m.key for m in only_a.members) == ["s1", "s2"]


def test_result_cache_follows_the_data_generation(db, monkeypatch):
    from translation_tool.translation_db import same_text_review as module

    db.ingest(VERSION, [_item("a", "Close", "關閉"), _item("b", "Close", "閉合")])
    calls = {"n": 0}
    real = module._scan_groups

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "_scan_groups", counting)
    first = _groups(db)
    assert _groups(db) == first and calls["n"] == 1  # 沒有資料變動：取快取
    _groups(db, max_chars=60)
    assert calls["n"] == 2  # 條件不同是另一組
    _review(db, "a", "關閉")  # 資料變動：世代前進，重新掃描
    after = _groups(db)
    assert calls["n"] == 3 and after[0].applicable
    for width in range(61, 61 + 8):  # 快取有上限
        _groups(db, max_chars=width)
    cache, _ = module._cache_of(db)
    assert len(cache) <= module._CACHE_SIZE
