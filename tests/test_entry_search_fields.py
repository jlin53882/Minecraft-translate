"""條目搜尋：鍵值、模組識別（含 KubeJS／FTB 的整合包資料夾與雜湊）都找得到。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.schema import KIND_KUBEJS_LANG

PACK = "atm@All the Mods 10 8.5@93985ec940f7"


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    database.ingest(
        "1.21.1",
        [
            ScanItem(KIND_KUBEJS_LANG, PACK, "kubejs.atm.click_here", "Click here"),
            ScanItem(KIND_KUBEJS_LANG, PACK, "kubejs.atm.other", "Other"),
        ],
    )
    yield database
    database.close()


def _keys(db, query, field="all"):
    rows, _ = db.list_entries("1.21.1", query=query, query_field=field)
    return sorted(row.key for row in rows)


def test_search_by_key_finds_underscore_keys(db):
    assert _keys(db, "kubejs.atm.click_here", "key") == ["kubejs.atm.click_here"]
    assert _keys(db, "click_here") == ["kubejs.atm.click_here"]


@pytest.mark.parametrize("text", [PACK, "All the Mods 10 8.5", "93985ec940f7"])
def test_search_by_module_identity(db, text):
    expected = ["kubejs.atm.click_here", "kubejs.atm.other"]
    assert _keys(db, text) == expected  # 全部內容
    assert _keys(db, text, "mod") == expected
    assert _keys(db, text, "key") == []  # 只搜鍵值時不納入模組識別
