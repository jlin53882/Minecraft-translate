"""預翻譯資料庫唯讀查詢層的測試。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from translation_tool.utils.translation_db import (
    TranslationDB,
    mod_id_from_path,
    split_items_by_translation_db,
)


def _make_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE mod (s_num INTEGER PRIMARY KEY, name TEXT UNIQUE);
        CREATE TABLE mod_key (s_num INTEGER PRIMARY KEY, m_s_num INT, key_val TEXT, en_us TEXT);
        CREATE TABLE lang (s_num INTEGER PRIMARY KEY, mk_s_num INT, zh_tw TEXT, zh_cn TEXT,
                           s_s_num INT, checker INT);
        INSERT INTO mod VALUES (1, 'foo');
        INSERT INTO mod_key VALUES (1, 1, 'item.foo.a', 'Apple'), (2, 1, 'item.foo.b', 'Bread');
        INSERT INTO lang VALUES (1, 1, 'CC蘋果', NULL, 2, 0), (2, 1, '町宮蘋果', NULL, 3, 0),
                                (3, 2, '麵包', NULL, 0, 0), (4, 2, '校驗麵包', NULL, 2, 1);
        """
    )
    conn.commit()
    conn.close()
    return path


def _item(key: str, src: str, mod: str = "foo") -> dict:
    return {
        "file": f"/in/assets/{mod}/lang/en_us.json",
        "path": key,
        "text": src,
        "source_text": src,
        "cache_type": "lang",
    }


def test_mod_id_from_path():
    assert mod_id_from_path("/x/assets/foo/lang/en_us.json") == "foo"
    assert mod_id_from_path("/x/data/foo.json") is None


def test_priority_and_checked_first(tmp_path):
    db = TranslationDB(_make_db(tmp_path / "t.db"))
    assert db.lookup("foo", "item.foo.a", "Apple") == "町宮蘋果"
    assert db.lookup("foo", "item.foo.b", "Bread") == "校驗麵包"


def test_source_text_must_match(tmp_path):
    db = TranslationDB(_make_db(tmp_path / "t.db"))
    assert db.lookup("foo", "item.foo.a", "Apple!") is None
    assert db.lookup("foo", "missing", "Apple") is None
    assert db.lookup("bar", "item.foo.a", "Apple") is None


def test_missing_db_is_silent(tmp_path):
    db = TranslationDB(tmp_path / "nope.db")
    assert not db.available
    assert db.lookup("foo", "k", "v") is None


def test_split_items(tmp_path):
    db = TranslationDB(_make_db(tmp_path / "t.db"))
    hits, rest = split_items_by_translation_db(
        db, [_item("item.foo.a", "Apple"), _item("item.foo.a", "Pear")]
    )
    assert [h["text"] for h in hits] == ["町宮蘋果"]
    assert len(rest) == 1
    hits, rest = split_items_by_translation_db(None, [_item("k", "v")])
    assert hits == [] and len(rest) == 1


def test_db_is_read_only(tmp_path):
    path = _make_db(tmp_path / "t.db")
    db = TranslationDB(path)
    try:
        db._conn.execute("DELETE FROM lang")  # type: ignore[union-attr]
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("應為唯讀")
