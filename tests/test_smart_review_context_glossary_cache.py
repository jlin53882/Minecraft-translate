"""智慧校對的詞組 glossary 查詢（KubeJS／FTB 模組包範圍）。

模組包範圍的條件是 ``EXISTS (… entry_origin.pack_scope …)``，沒有索引提示時會掃整個
版本，且每筆候選都要查一次。這裡驗證三條路徑的結果逐列一致，並鎖住它們的成本：

- 已審核詞庫完整（未被 5,000 列上限截斷）：直接取快取，不查資料庫；
- 詞庫被截斷：改用詞組導向的索引查詢，而不是掃整個版本；
- 其他情況（舊資料庫缺少索引）：退回原本的 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from translation_tool.translation_db import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_LANG,
    KIND_KUBEJS_SCRIPT,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db import smart_review_context as context_module
from translation_tool.translation_db.schema import SRC_AI

V = "1.21.1"
OTHER_V = "1.20.1"
PACK = "pack-a"


@dataclass(frozen=True)
class PackCase:
    """一種模組包來源：語系 kind、詞庫所在的模組，以及會取用詞庫的候選。"""

    name: str
    lang_kind: str
    term_mods: tuple[str, str]  # 同包的兩個模組（可相同）
    other_pack_mod: str
    candidates: tuple[tuple[str, str, str, str], ...]  # (kind, mod, key, en_us)


KUBEJS = PackCase(
    "kubejs",
    KIND_KUBEJS_LANG,
    ("demo", "sibling"),
    "other",
    (
        (KIND_KUBEJS_LANG, "demo", "tip.install", "Install the Quantum Valve"),
        (KIND_KUBEJS_LANG, "demo", "tip.both", "Plasma Coil and Quantum Valve"),
        (KIND_KUBEJS_LANG, "demo", "tip.coil", "Plasma Coil"),
        (KIND_KUBEJS_LANG, "demo", "tip.none", "Nothing to match here"),
        (KIND_KUBEJS_SCRIPT, "kubejs", "client_scripts/a.js|tip", "Quantum Valve"),
    ),
)
FTB = PackCase(
    "ftb",
    KIND_FTB_QUEST_LANG,
    ("ftbquests", "ftbquests"),
    "ftbquests",
    (
        (KIND_FTB_QUEST_LANG, "ftbquests", "tip.install", "Install the Quantum Valve"),
        (KIND_FTB_QUEST_LANG, "ftbquests", "tip.both", "Plasma Coil and Quantum Valve"),
        (KIND_FTB_QUEST_LANG, "ftbquests", "tip.coil", "Plasma Coil"),
        (KIND_FTB_QUEST_LANG, "ftbquests", "tip.none", "Nothing to match here"),
        (KIND_FTB_QUESTS, "ftbquests", "chapters/a.snbt|task", "Quantum Valve"),
    ),
)
CASES = [pytest.param(KUBEJS, id="kubejs"), pytest.param(FTB, id="ftb")]


def _item(kind: str, mod: str, key: str, en: str, zh: str = "", scope: str = PACK):
    return ScanItem(
        kind,
        f"{mod}@{scope}",
        key,
        en,
        zh,
        source_path=f"pack/{mod}/{key}",
        source_selector=f"lang:en_us:{key}",
        source_hash=f"{mod}-{key}",
        source_format="lang-json",
        pack_scope=scope,
    )


def _review_translated(database: TranslationDB) -> None:
    for row in database.list_entries(V, limit=None)[0]:
        if row.zh_tw:
            database.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)


@pytest.fixture
def pack(request, tmp_path):
    case: PackCase = request.param
    first, second = case.term_mods
    database = TranslationDB(tmp_path / f"{case.name}.db")
    database.ingest(
        V,
        [
            _item(case.lang_kind, first, "term.valve", "Quantum Valve", "量子閥"),
            _item(case.lang_kind, second, "term.coil", "Plasma Coil", "電漿線圈"),
            _item(
                case.lang_kind,
                case.other_pack_mod,
                "term.valve",
                "Quantum Valve",
                "錯誤閥",
                scope="pack-b",
            ),
            *(_item(*candidate) for candidate in case.candidates),
        ],
    )
    _review_translated(database)
    yield case, database
    database.close()


def _builder(database, kind, mod_id, *, key="k", en_us="x", entry_id=1):
    database._smart_review_context_cache = {}
    return context_module._LocalContextBuilder(
        database,
        database._smart_review_context_cache,
        entry_id=entry_id,
        kind=kind,
        mc_version=V,
        mod_id=mod_id,
        key=key,
        en_us=en_us,
    )


def _candidate_rows(database, case: PackCase):
    keys = {(kind, f"{mod}@{PACK}", key) for kind, mod, key, _en in case.candidates}
    return [
        row
        for row in database.list_entries(V, limit=None)[0]
        if (row.kind, row.mod_id, row.key) in keys
    ]


def _contexts(database, case, monkeypatch, *, truncated: bool) -> dict:
    """逐筆建立候選 context；``truncated`` 時把 bulk 詞庫視為被上限截斷。"""
    database._smart_review_context_cache = {}
    builder = context_module._LocalContextBuilder
    if truncated:
        original = builder._bulk_glossary_rows

        def forced(self):
            rows = original(self)
            scope = (*self._glossary_cache_scope(), self.mc_version)
            self.cache.setdefault("glossary_complete", {})[scope] = False
            return rows

        monkeypatch.setattr(builder, "_bulk_glossary_rows", forced)
    return {
        (row.kind, row.key): database._smart_review_context(
            row.id, row.kind, V, row.mod_id, row.key, row.en_us
        )
        for row in _candidate_rows(database, case)
    }


def _pairs(context: list) -> set[tuple[str, str]]:
    return {
        (item["en_us"], item["zh_tw"])
        for item in context
        if item["reference_type"] == "reviewed_lang_glossary"
    }


@pytest.mark.parametrize("pack", CASES, indirect=True)
def test_cached_and_truncated_paths_build_the_same_context(pack, monkeypatch):
    case, database = pack
    cached = _contexts(database, case, monkeypatch, truncated=False)
    queried = _contexts(database, case, monkeypatch, truncated=True)

    assert cached == queried
    first_key = (case.lang_kind, "tip.install")
    assert ("Quantum Valve", "量子閥") in _pairs(cached[first_key])
    assert ("Plasma Coil", "電漿線圈") in _pairs(cached[(case.lang_kind, "tip.both")])
    # 腳本／任務候選取用同一包的語系詞庫
    other_kind = case.candidates[-1][0]
    assert ("Quantum Valve", "量子閥") in _pairs(
        cached[(other_kind, case.candidates[-1][2])]
    )
    # 另一個包的衝突譯文不會被帶入，沒有命中的候選也不會有詞庫
    assert all(("Quantum Valve", "錯誤閥") not in _pairs(c) for c in cached.values())
    assert _pairs(cached[(case.lang_kind, "tip.none")]) == set()


def _statements(database, case, monkeypatch, *, truncated: bool) -> list[str]:
    statements: list[str] = []
    database._conn.set_trace_callback(statements.append)
    try:
        _contexts(database, case, monkeypatch, truncated=truncated)
    finally:
        database._conn.set_trace_callback(None)
    return [sql for sql in statements if "en_us IN (" in sql]


@pytest.mark.parametrize("pack", CASES, indirect=True)
def test_complete_bulk_glossary_does_not_query_per_candidate(pack, monkeypatch):
    case, database = pack
    assert _statements(database, case, monkeypatch, truncated=False) == []


@pytest.mark.parametrize("pack", CASES, indirect=True)
def test_truncated_bulk_glossary_uses_the_phrase_index(pack, monkeypatch):
    case, database = pack
    phrase_queries = _statements(database, case, monkeypatch, truncated=True)

    # 確實有逐筆詞組查詢（對照組），而且都走 en_us 索引、不含會被掃整個版本的 LIMIT 查詢
    assert phrase_queries
    assert all("INDEXED BY idx_entry_en" in sql for sql in phrase_queries)
    assert not any("ORDER BY e.id LIMIT 96" in sql for sql in phrase_queries)


@pytest.mark.parametrize("pack", CASES, indirect=True)
def test_scoped_phrase_query_plan_uses_the_phrase_index(pack):
    case, database = pack
    builder = _builder(database, case.lang_kind, f"{case.term_mods[0]}@{PACK}")
    scope_sql, scope_params = builder._glossary_scope_sql()
    sql = context_module._scoped_phrase_sql(3, scope_sql)
    plan = " ".join(
        str(row[3])
        for row in database._q(
            "EXPLAIN QUERY PLAN " + sql, ("a", "b", "c", case.lang_kind, *scope_params)
        )
    )

    assert "idx_entry_en" in plan
    assert "idx_entry_version" not in plan  # 沒有索引提示時會走這個，掃整個版本
    assert "TEMP B-TREE" not in plan  # 專案規定：不得臨時排序


def _bulk_target_rows(builder, phrases):
    builder._bulk_glossary_rows()
    scope = (*builder._glossary_cache_scope(), builder.mc_version)
    assert builder.cache["glossary_complete"][scope]
    return builder._target_rows_from_bulk(scope, phrases)


@pytest.mark.parametrize("pack", CASES, indirect=True)
def test_target_rows_cap_and_order_match_the_database_query(pack):
    case, database = pack
    first = case.term_mods[0]
    # 同一詞組在目前版本與其他版本各有超過上限的已審核列
    database.ingest(
        V,
        [
            _item(case.lang_kind, first, f"bulk.{n:03d}", "Bulk Phrase", "批量")
            for n in range(110)
        ],
    )
    database.ingest(
        OTHER_V,
        [
            _item(case.lang_kind, first, f"old.{n:03d}", "Bulk Phrase", "舊批量")
            for n in range(110)
        ],
    )
    for version in (V, OTHER_V):
        for row in database.list_entries(version, limit=None)[0]:
            if row.zh_tw:
                database.review_manual(
                    row.id, expected_zh_tw=row.zh_tw, propagate=False
                )
    phrases = ["Bulk Phrase", "Quantum Valve"]
    mod_id = f"{first}@{PACK}"

    expected = _builder(
        database, case.lang_kind, mod_id
    )._glossary_target_rows_from_sql(phrases)
    scoped = _builder(database, case.lang_kind, mod_id)._scoped_target_rows(phrases)
    bulk = _bulk_target_rows(_builder(database, case.lang_kind, mod_id), phrases)

    assert len(expected) > 2 * 96 - 1  # 上限確實被碰到
    assert scoped == expected
    assert bulk == expected


@pytest.mark.parametrize("pack", CASES, indirect=True)
def test_entries_with_two_reviewed_translations_keep_every_row(pack):
    case, database = pack
    first = case.term_mods[0]
    entry_id = next(
        row.id
        for row in database.list_entries(V, limit=None)[0]
        if row.kind == case.lang_kind
        and row.mod_id == f"{first}@{PACK}"
        and row.key == "term.valve"
    )
    with database._tx() as conn:
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, review_status) "
            "VALUES (?,?,?,'reviewed')",
            (entry_id, SRC_AI, "量子閥（另一份）"),
        )
    mod_id = f"{first}@{PACK}"
    phrases = ["Quantum Valve"]

    expected = _builder(
        database, case.lang_kind, mod_id
    )._glossary_target_rows_from_sql(phrases)
    scoped = _builder(database, case.lang_kind, mod_id)._scoped_target_rows(phrases)
    bulk = _bulk_target_rows(_builder(database, case.lang_kind, mod_id), phrases)

    assert sum(1 for row in expected if row[0] == entry_id) == 2
    assert scoped == expected
    assert bulk == expected
