"""同一個遊戲版本有多個整合包版本（例如 ATM10 8.1～8.5 都是 1.21.1）時的標示。"""

from __future__ import annotations

import pytest

from app.views.moddb.entry_metadata import build_entry_metadata
from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.content_identity import pack_label
from translation_tool.translation_db.schema import KIND_KUBEJS_LANG, KIND_LANG

PACKS = [f"atm@All the Mods 10 8.{n}@{n:012x}" for n in range(1, 6)]


@pytest.mark.parametrize(
    ("kind", "mod_id", "expected"),
    [
        (
            KIND_KUBEJS_LANG,
            "atm@All the Mods 10 8.3@40b3569cc156",
            "All the Mods 10 8.3",
        ),
        (KIND_KUBEJS_LANG, "atm@My@Pack@abc", "My@Pack"),  # 資料夾名稱本身含 @
        (KIND_KUBEJS_LANG, "atm", ""),  # 格式不符
        (KIND_LANG, "a@b@c", ""),  # 一般語系內容不套用
    ],
)
def test_pack_label(kind, mod_id, expected):
    assert pack_label(kind, mod_id) == expected


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    yield database
    database.close()


def _chips(controls):
    row = next(c for c in controls if getattr(c.controls[0], "value", "") == "出現於")
    return [chip for chip in row.controls[1:]]


def test_five_pack_versions_are_told_apart_in_the_appearance_chips(db):
    for mod in PACKS:
        db.ingest("1.21.1", [ScanItem(KIND_KUBEJS_LANG, mod, "k", "Click")])
    db.ingest("26.1", [ScanItem(KIND_KUBEJS_LANG, PACKS[4], "k", "Click")])
    entry_id = db._one(
        "SELECT id FROM entry WHERE mod_id=? AND mc_version='1.21.1'", (PACKS[2],)
    )[0]

    detail = db.entry_detail(entry_id)
    assert detail.versions == ["1.21.1"] * 5 + ["26.1"]
    assert sorted(detail.version_packs[:5]) == [
        f"All the Mods 10 8.{n}" for n in range(1, 6)
    ]
    assert detail.version_packs[5] == "All the Mods 10 8.5"

    chips = _chips(build_entry_metadata(detail))
    assert len(chips) == 6
    texts = [chip_text(c) for c in chips]
    assert len(set(texts)) == 6  # 不再出現好幾顆一樣的標籤
    assert "1.21.1 · All the Mods 10 8.3" in texts


def chip_text(chip) -> str:
    """kit.chip 的文字（容器內的 Text）。"""
    node = chip
    while not hasattr(node, "value"):
        node = node.content if hasattr(node, "content") else node.controls[0]
    return node.value


def test_plain_lang_entries_keep_the_game_version_only(db):
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "mod", "k", "Hi")])
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "mod", "k", "Hi")])
    entry_id = db._one("SELECT id FROM entry WHERE mc_version='1.21.1'")[0]

    detail = db.entry_detail(entry_id)
    assert detail.version_packs == ["", ""]
    assert sorted(chip_text(c) for c in _chips(build_entry_metadata(detail))) == [
        "1.20.1",
        "1.21.1",
    ]
