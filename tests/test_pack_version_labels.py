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


def test_many_appearances_collapse_into_a_count_chip(db):
    from app.views.moddb.entry_metadata import MAX_VERSION_CHIPS

    packs = [f"atm@All the Mods 10 8.{n}@{n:012x}" for n in range(1, 9)]
    for mod in packs:
        db.ingest("1.21.1", [ScanItem(KIND_KUBEJS_LANG, mod, "k", "Click")])
    entry_id = db._one(
        "SELECT id FROM entry WHERE mod_id=? AND mc_version='1.21.1'", (packs[6],)
    )[0]

    chips = _chips(build_entry_metadata(db.entry_detail(entry_id)))
    texts = [chip_text(c) for c in chips]
    assert len(chips) == MAX_VERSION_CHIPS + 1
    assert texts[0] == "1.21.1 · All the Mods 10 8.7"  # 目前這一筆排最前面
    assert texts[-1] == f"另 {8 - MAX_VERSION_CHIPS} 個"
    assert chips[-1].tooltip


def test_reference_options_list_game_versions_and_pack_versions():
    from app.views.moddb.version_diff_dialog import (
        reference_options,
        split_reference,
    )

    options = reference_options(
        ["1.21.1", "26.1"], {"1.21.1": ["ATM 8.2", "ATM 8.5"]}, "26.1"
    )
    values = [value for value, _ in options]
    assert values[0] == "__latest__"
    assert "26.1" not in values  # 目標版本本身不當整個遊戲版本的參考
    assert split_reference("1.21.1") == ("1.21.1", "")
    assert split_reference("1.21.1\tATM 8.5") == ("1.21.1", "ATM 8.5")
    assert split_reference("__latest__") == ("", "")
    assert dict(options)["1.21.1\tATM 8.5"] == "固定 1.21.1 · ATM 8.5"

    # 與目標完全相同的選項不列：目標是整個 1.21.1 時沒有「固定 1.21.1」，整合包版本照列
    packs = {"1.21.1": ["ATM 8.2", "ATM 8.6"]}
    whole = reference_options(["1.21.1"], packs, "1.21.1")
    assert [v for v, _ in whole] == ["__latest__", "1.21.1\tATM 8.2", "1.21.1\tATM 8.6"]
    # 目標是 1.21.1 · ATM 8.6：可指定整個 1.21.1（同版本其他整合包）與其他整合包版本
    pinned = reference_options(["1.21.1"], packs, "1.21.1\tATM 8.6")
    assert [v for v, _ in pinned] == ["__latest__", "1.21.1", "1.21.1\tATM 8.2"]
    assert dict(pinned)["1.21.1"] == "固定 1.21.1（同版本其他整合包）"
    # 沒有整合包的遊戲版本：同版本不會有參考，不列
    assert [v for v, _ in reference_options(["1.21.1"], {}, "1.21.1")] == ["__latest__"]


def test_target_options_list_game_versions_and_pack_versions():
    from app.views.moddb.version_diff_dialog import split_reference, target_options

    options = target_options(["1.21.1", "26.1"], {"1.21.1": ["ATM 8.2", "ATM 8.5"]})
    assert [value for value, _ in options] == [
        "1.21.1",
        "1.21.1\tATM 8.2",
        "1.21.1\tATM 8.5",
        "26.1",
    ]
    assert dict(options)["1.21.1\tATM 8.5"] == "1.21.1 · ATM 8.5"
    assert split_reference("1.21.1\tATM 8.5") == ("1.21.1", "ATM 8.5")


def _texts(controls) -> list[str]:
    out: list[str] = []

    def walk(node) -> None:
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        content = getattr(node, "content", None)
        if content is not None:
            walk(content)
        for child in getattr(node, "controls", None) or []:
            walk(child)

    for control in controls:
        walk(control)
    return out


def test_entry_metadata_lists_other_originals_with_translation_state(db):
    """條目詳情列出其他原文：有譯文顯示來源與譯文，沒有則提示下次會翻譯。"""
    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Show Background")])
    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Pending Wording")])
    db.ingest(
        "1.21.1",
        [ScanItem("lang", "foo", "item.foo.a", "Background Style", "背景樣式")],
    )
    entry_id = db._one("SELECT id FROM entry")[0]
    texts = _texts(build_entry_metadata(db.entry_detail(entry_id)))
    assert "原文：Background Style" in texts and "原文：Pending Wording" in texts
    assert any(t.startswith("譯文（模組自帶繁中）：背景樣式") for t in texts)
    assert any(t.startswith("尚無譯文") for t in texts)
    assert "另有 2 個原文版本（流水線專用）" in texts


def test_entry_metadata_collapses_many_and_long_variants(db):
    """其他原文最多展開 3 份、長原文截斷，避免把儲存／審核按鈕推到畫面外。"""
    from app.views.moddb.entry_metadata import MAX_VARIANT_CHARS, MAX_VARIANTS_SHOWN

    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Main")])
    long_text = "L" * (MAX_VARIANT_CHARS + 50)
    for i in range(MAX_VARIANTS_SHOWN + 2):
        db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", f"{long_text}{i}")])
    entry_id = db._one("SELECT id FROM entry")[0]
    texts = _texts(build_entry_metadata(db.entry_detail(entry_id)))
    originals = [t for t in texts if t.startswith("原文：")]
    assert len(originals) == MAX_VARIANTS_SHOWN
    assert all(len(t) <= len("原文：") + MAX_VARIANT_CHARS + 1 for t in originals)
    assert "另有 2 份較舊的原文未展開" in texts


def _buttons(controls) -> list:
    found: list = []

    def walk(node) -> None:
        if type(node).__name__ == "Button":
            found.append(node)
        content = getattr(node, "content", None)
        if content is not None:
            walk(content)
        for child in getattr(node, "controls", None) or []:
            walk(child)

    for control in controls:
        walk(control)
    return found


def test_adopt_button_only_for_variants_without_translation_and_runs_the_action(
    db, monkeypatch
):
    """有目前譯文時，沒有譯文的其他原文才有「沿用目前譯文」；按下去交給動作模組。"""
    from types import SimpleNamespace

    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Main", "主譯文")])
    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Pending Wording")])
    db.ingest(
        "1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Translated Wording", "已翻")]
    )
    entry_id = db._one("SELECT id FROM entry")[0]
    detail = db.entry_detail(entry_id)
    assert detail.entry.zh_tw == "主譯文"

    calls = []
    monkeypatch.setattr(
        "app.views.moddb.entry_metadata.adopt_current_translation",
        lambda panel, variant: calls.append((panel, variant.en_us)),
    )
    panel = SimpleNamespace()
    buttons = _buttons(build_entry_metadata(detail, None, panel))
    assert len(buttons) == 1  # 只有「尚無譯文」的那份
    buttons[0].on_click(None)
    assert calls == [(panel, "Pending Wording")]
    assert (
        _buttons(build_entry_metadata(detail, None, None)) == []
    )  # 沒有面板就不給按鈕


def test_adopt_action_writes_then_reloads_and_reports_refusals(db, monkeypatch):
    """動作模組：成功時寫入並重整，被拒絕（格式不一致）時只顯示原因、不寫入。"""
    from types import SimpleNamespace

    from app.views.moddb import entry_variant_actions

    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Main", "主譯文")])
    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Changed")])
    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Has %s token")])
    entry_id = db._one("SELECT id FROM entry")[0]
    detail = db.entry_detail(entry_id)
    snacks: list[str] = []
    monkeypatch.setattr(
        entry_variant_actions, "show_snack", lambda _p, text, *_a: snacks.append(text)
    )
    reloaded: list[int] = []
    panel = SimpleNamespace(
        selected=detail.entry,
        db=lambda: db,
        _page=SimpleNamespace(),
        _on_changed=None,
        saved_text=SimpleNamespace(value=""),
        reload_page_in_place=lambda: reloaded.append(1),
        _safe_update=lambda: None,
    )
    by_en = {v.en_us: v for v in detail.variants}

    entry_variant_actions.adopt_current_translation(panel, by_en["Has %s token"])
    assert snacks and snacks[-1].startswith("舊譯文與這份原文的格式不一致")
    assert db._one("SELECT COUNT(*) FROM variant_translation")[0] == 0 and not reloaded

    entry_variant_actions.adopt_current_translation(panel, by_en["Changed"])
    assert db._q("SELECT zh_tw FROM variant_translation") == [("主譯文",)]
    assert reloaded == [1] and panel.saved_text.value.startswith("已沿用目前譯文")


def test_adopt_action_logs_database_errors_and_reports_when_it_cannot_start(
    db, monkeypatch
):
    """資料庫錯誤寫後台 log 並提示；背景無法啟動時也要告訴使用者。"""
    import sqlite3
    from types import SimpleNamespace

    from app.views.moddb import entry_variant_actions

    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Main", "主譯文")])
    db.ingest("1.21.1", [ScanItem("lang", "foo", "item.foo.a", "Changed")])
    detail = db.entry_detail(db._one("SELECT id FROM entry")[0])
    snacks: list[str] = []
    logged: list[str] = []
    monkeypatch.setattr(
        entry_variant_actions, "show_snack", lambda _p, text, *_a: snacks.append(text)
    )
    monkeypatch.setattr(
        entry_variant_actions, "log_exception", lambda text: logged.append(text)
    )
    panel = SimpleNamespace(
        selected=detail.entry, db=lambda: db, _page=SimpleNamespace(), _on_changed=None
    )
    variant = detail.variants[0]

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "adopt_current_translation_for_variant", boom)
    entry_variant_actions.adopt_current_translation(panel, variant)
    assert logged and "沿用失敗（資料庫錯誤" in snacks[-1]

    monkeypatch.setattr(
        entry_variant_actions.entry_writes, "run_write", lambda *a, **k: False
    )
    entry_variant_actions.adopt_current_translation(panel, variant)
    assert snacks[-1].startswith("沿用無法啟動")
