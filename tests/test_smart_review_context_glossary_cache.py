"""智慧校對的詞組 glossary 查詢：完整 bulk 詞庫直接取快取，不逐筆候選查資料庫。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db import smart_review_context as context_module

V = "1.21.1"
PACK = "pack-a"


def _item(mod: str, key: str, en: str, zh: str = "", scope: str = PACK) -> ScanItem:
    return ScanItem(
        KIND_KUBEJS_LANG,
        f"{mod}@{scope}",
        key,
        en,
        zh,
        source_path=f"kubejs/assets/{mod}/lang/en_us.json",
        source_selector=f"lang:en_us:{key}",
        source_hash=f"{mod}-{key}",
        source_format="lang-json",
        pack_scope=scope,
    )


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "glossary-cache.db")
    database.ingest(
        V,
        [
            # 已審核詞庫：同包不同模組，以及另一個包的衝突譯文
            _item("demo", "term.valve", "Quantum Valve", "量子閥"),
            _item("sibling", "term.coil", "Plasma Coil", "電漿線圈"),
            _item("sibling", "term.valve", "Quantum Valve", "量子閥"),
            _item("other", "term.valve", "Quantum Valve", "錯誤閥", scope="pack-b"),
            # 候選：沒有譯文
            _item("demo", "tip.install", "Install the Quantum Valve"),
            _item("demo", "tip.both", "Plasma Coil and Quantum Valve"),
            _item("demo", "tip.coil", "Plasma Coil"),
            _item("demo", "tip.none", "Nothing to match here"),
        ],
    )
    for row in database.list_entries(V, limit=None)[0]:
        if row.zh_tw:
            database.review_manual(row.id, expected_zh_tw=row.zh_tw, propagate=False)
    yield database
    database.close()


def _candidates(database):
    return [
        row
        for row in database.list_entries(V, limit=None)[0]
        if row.mod_id == f"demo@{PACK}" and row.key.startswith("tip.")
    ]


def _contexts(database, monkeypatch, *, force_sql: bool) -> dict[str, list]:
    """逐筆建立候選 context；``force_sql`` 時把 bulk 詞庫視為被上限截斷。"""
    database._smart_review_context_cache = {}
    builder = context_module._LocalContextBuilder
    original = builder._bulk_glossary_rows
    if force_sql:

        def truncated(self):
            rows = original(self)
            scope = (*self._glossary_cache_scope(), self.mc_version)
            self.cache.setdefault("glossary_complete", {})[scope] = False
            return rows

        monkeypatch.setattr(builder, "_bulk_glossary_rows", truncated)
    return {
        row.key: database._smart_review_context(
            row.id, row.kind, V, row.mod_id, row.key, row.en_us
        )
        for row in _candidates(database)
    }


def _glossary_pairs(context: list) -> set[tuple[str, str]]:
    return {
        (item["en_us"], item["zh_tw"])
        for item in context
        if item["reference_type"] == "reviewed_lang_glossary"
    }


def test_cached_bulk_path_matches_database_query_path(db, monkeypatch):
    cached = _contexts(db, monkeypatch, force_sql=False)
    queried = _contexts(db, monkeypatch, force_sql=True)

    assert cached == queried
    # 同包其他模組的已審核詞組有被帶入，另一個包的衝突譯文沒有
    assert ("Quantum Valve", "量子閥") in _glossary_pairs(cached["tip.install"])
    assert ("Plasma Coil", "電漿線圈") in _glossary_pairs(cached["tip.both"])
    assert ("Plasma Coil", "電漿線圈") in _glossary_pairs(cached["tip.coil"])
    assert all(
        ("Quantum Valve", "錯誤閥") not in _glossary_pairs(context)
        for context in cached.values()
    )
    assert _glossary_pairs(cached["tip.none"]) == set()


def _phrase_queries(database, monkeypatch, *, force_sql: bool) -> int:
    statements: list[str] = []
    database._conn.set_trace_callback(statements.append)
    try:
        _contexts(database, monkeypatch, force_sql=force_sql)
    finally:
        database._conn.set_trace_callback(None)
    return sum(1 for sql in statements if "e.en_us IN (" in sql)


def test_complete_bulk_glossary_does_not_query_each_candidate(db, monkeypatch):
    assert _phrase_queries(db, monkeypatch, force_sql=False) == 0


def test_truncated_bulk_glossary_still_queries_by_phrase(db, monkeypatch):
    # 對照組：確認上面的計數方式確實抓得到逐筆查詢
    assert _phrase_queries(db, monkeypatch, force_sql=True) > 0
