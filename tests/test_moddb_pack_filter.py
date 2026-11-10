"""條目頁的模組包版本篩選：選了遊戲版本才有清單，篩選後清單、筆數與下拉選項一致。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb.panel_refresh import _resolve_entries_scope
from translation_tool.translation_db.models import EntryFilter, ScanItem
from translation_tool.translation_db.repository import TranslationDB

PACK_83 = "Pack 8.3@aaaaaaaaaaaa"
PACK_85 = "Pack 8.5@bbbbbbbbbbbb"


def _pack_item(namespace: str, scope: str, key: str, kind: str = "kubejs_lang"):
    return ScanItem(
        kind=kind,
        mod_id=f"{namespace}@{scope}",
        key=key,
        en_us=f"text {key}",
        source_path=f"assets/{namespace}/lang/en_us.json",
        source_selector=f"lang:en_us:{key}",
        source_hash="H",
        pack_scope=scope,
        source_locale="en_us",
    )


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    database.ingest(
        "1.21.1",
        [
            _pack_item("alpha", PACK_83, "a"),
            _pack_item("alpha", PACK_85, "a"),
            _pack_item("alpha", PACK_85, "b"),
            _pack_item("beta", PACK_85, "c", kind="ftb_quest_lang"),
            ScanItem(kind="lang", mod_id="jarmod", key="j", en_us="From a jar"),
        ],
    )
    database.ingest(
        "1.20.1", [ScanItem(kind="lang", mod_id="jarmod", key="j", en_us="From a jar")]
    )
    return database


def _scope(db, **request):
    criteria = request.pop("criteria", EntryFilter(version=request.get("version", "")))
    return _resolve_entries_scope(
        db, {"criteria": criteria, **request}, SimpleNamespace(version="")
    )


def test_pack_options_follow_the_selected_version(db):
    assert _scope(db, version="1.21.1")["packs"] == ["Pack 8.3", "Pack 8.5"]
    # 只有 jar 內容的版本沒有模組包版本可選：維持「全部」
    without_packs = _scope(db, version="1.20.1")
    assert without_packs["packs"] == [] and without_packs["pack"] is None
    assert without_packs["criteria"].pack_mod_ids is None


def test_pack_narrows_entries_counts_and_options(db):
    scope = _scope(db, version="1.21.1", pack="Pack 8.5")
    assert scope["pack"] == "Pack 8.5"
    assert scope["mods"] == [f"alpha@{PACK_85}", f"beta@{PACK_85}"]
    assert scope["kinds"] == ["ftb_quest_lang", "kubejs_lang"]

    rows, total = db.list_entries(criteria=scope["criteria"], limit=50)
    assert total == 3
    assert {row.mod_id for row in rows} == {f"alpha@{PACK_85}", f"beta@{PACK_85}"}

    all_rows, all_total = db.list_entries(
        criteria=_scope(db, version="1.21.1")["criteria"], limit=50
    )
    assert all_total == 5 and len(all_rows) == 5


def test_search_inside_a_pack_stays_inside_it(db):
    criteria = EntryFilter(version="1.21.1", query="text a")
    scope = _scope(db, version="1.21.1", pack="Pack 8.3", criteria=criteria)
    rows, total = db.list_entries(criteria=scope["criteria"], limit=50)
    assert total == 1 and rows[0].mod_id == f"alpha@{PACK_83}"
    assert scope["mods"] == [f"alpha@{PACK_83}"]


def test_mod_from_another_pack_is_dropped_when_the_pack_changes(db):
    scope = _scope(db, version="1.21.1", pack="Pack 8.3", mod_id=f"alpha@{PACK_85}")
    assert scope["mod_id"] is None

    kept = _scope(db, version="1.21.1", pack="Pack 8.5", mod_id=f"beta@{PACK_85}")
    assert kept["mod_id"] == f"beta@{PACK_85}"
    _rows, total = db.list_entries(criteria=kept["criteria"], limit=50)
    assert total == 1


def test_unknown_pack_falls_back_to_all(db):
    scope = _scope(db, version="1.21.1", pack="Pack 9.9")
    assert scope["pack"] is None and scope["criteria"].pack_mod_ids is None
