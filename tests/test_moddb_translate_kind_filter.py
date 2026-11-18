"""Mod 資料庫批次機翻的「類型」篩選：查詢、沿用、服務與頁籤範圍都只處理所選類型。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services_impl import moddb_service, moddb_translate_service
from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    run_moddb_translate_service,
)
from app.tasks.task_session import TaskSession
from app.views.moddb import translate_panel
from app.views.moddb.kind_scope_dropdown import ALL_KINDS
from app.views.moddb.panel_refresh import ALL_MODS, _load_translate_snapshot
from tests.conftest import mock_page
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.schema import KIND_GUIDE, SRC_AI


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "tr.db"
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(path), version="1.21.1"),
    )
    return path


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch):
    """不碰真正的翻譯快取。"""
    monkeypatch.setattr(
        moddb_translate_service,
        "add_to_cache_with_receipt",
        lambda *a, **k: SimpleNamespace(accepted=True, changed=True),
    )
    monkeypatch.setattr(
        moddb_translate_service, "save_translation_cache", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_shared_loop.reload_translation_cache",
        lambda *a, **k: None,
    )


def _item(kind: str, mod_id: str, key: str, en_us: str, zh_tw: str = "") -> ScanItem:
    folder = "lang" if kind == KIND_LANG else "guide"
    return ScanItem(
        kind,
        mod_id,
        key,
        en_us,
        zh_tw,
        source_path=f"assets/{mod_id}/{folder}/en_us.json",
        source_selector=f"{kind}:{key}",
        source_locale="en_us",
    )


def seed(path) -> None:
    """1.21.1：lang 兩筆、guide 兩筆未翻譯；1.20.1 各有一筆可沿用的相同譯文。"""
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            _item(KIND_LANG, "foo", "item.foo.a", "Steel Casing"),
            _item(KIND_LANG, "foo", "item.foo.b", "Bronze Gear"),
            _item(KIND_GUIDE, "foo", "intro#1", "Welcome to Foo."),
            _item(KIND_GUIDE, "foo", "intro#2", "Craft a casing."),
        ],
    )
    db.ingest(
        "1.20.1",
        [
            _item(KIND_LANG, "foo", "item.foo.b", "Bronze Gear", "青銅齒輪"),
            _item(KIND_GUIDE, "foo", "intro#1", "Welcome to Foo.", "歡迎來到 Foo。"),
        ],
    )
    db.close()


def test_untranslated_queries_respect_kind(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    try:
        assert db.count_untranslated("1.21.1") == 4
        assert db.count_untranslated("1.21.1", kind=KIND_LANG) == 2
        assert db.count_untranslated("1.21.1", ["foo"], kind=KIND_GUIDE) == 2
        assert db.count_untranslated("1.21.1", kind="kubejs_script") == 0
        assert db.count_reusable("1.21.1") == 2
        assert db.count_reusable("1.21.1", kind=KIND_GUIDE) == 1
        rows = db.untranslated_entries("1.21.1", kind=KIND_LANG)
        assert {r[1] for r in rows} == {KIND_LANG} and len(rows) == 2
        rows = db.untranslated_entries("1.21.1", kind=KIND_GUIDE, exclude_reusable=True)
        assert [(r[1], r[3]) for r in rows] == [(KIND_GUIDE, "intro#2")]
    finally:
        db.close()


def test_reuse_only_fills_the_selected_kind(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    try:
        assert db.reuse_from_other_versions("1.21.1", kind=KIND_LANG) == 1
        # guide 的可沿用譯文還在，沒有被這次（只選 lang）一起補上
        assert db.count_untranslated("1.21.1", kind=KIND_GUIDE) == 2
        assert db.count_untranslated("1.21.1", kind=KIND_LANG) == 1
    finally:
        db.close()


def _run(options: TranslateOptions) -> dict:
    session = TaskSession()
    run_moddb_translate_service(options, session)
    return session.snapshot()


def test_service_translates_and_reuses_only_the_selected_kind(db_path, monkeypatch):
    seed(db_path)
    sent: list[str] = []

    def fake_batch(batch, total=None, dry_run=False):
        sent.extend(it["_kind"] for it in batch)
        return [{**it, "text": "譯：" + it["source_text"]} for it in batch], "DONE"

    monkeypatch.setattr(moddb_translate_service, "translate_batch_smart", fake_batch)
    preview = _run(TranslateOptions(version="1.21.1", kind=KIND_GUIDE, dry_run=True))
    summary = preview["summary"]
    assert summary["remaining"] == 2 and summary["reused"] == 1
    assert summary["candidates"] == 1 and not sent

    summary = _run(TranslateOptions(version="1.21.1", kind=KIND_GUIDE))["summary"]
    assert summary["reused"] == 1 and summary["written"] == 1
    assert sent == [KIND_GUIDE]
    db = TranslationDB(db_path)
    try:
        assert db.count_untranslated("1.21.1", kind=KIND_GUIDE) == 0
        assert db.count_untranslated("1.21.1", kind=KIND_LANG) == 2
        rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
        assert rows["intro#2"].source == SRC_AI
        assert summary["scope_remaining"] == 0
    finally:
        db.close()


def test_translate_snapshot_lists_kinds_and_counts_the_selected_one(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    try:
        request = {"version": "1.21.1", "mod": ALL_MODS, "reuse": True}
        snapshot = _load_translate_snapshot(db, {**request, "kind": KIND_LANG}, {})
        assert set(snapshot["kinds"]) == {KIND_LANG, KIND_GUIDE}
        assert snapshot["kind"] == KIND_LANG
        assert snapshot["missing"] == 2 and snapshot["reusable"] == 1
        # 這個版本沒有的類型：退回全部類型，筆數是整個版本
        missing_kind = _load_translate_snapshot(
            db, {**request, "kind": "kubejs_script"}, {}
        )
        assert missing_kind["kind"] is None and missing_kind["missing"] == 4
    finally:
        db.close()


def test_translate_panel_kind_scope_and_options(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    try:
        panel = translate_panel.TranslatePanel(mock_page(), lambda: db)
        panel.refresh_scope()
        keys = [o.key for o in panel.kind_dd.options]
        assert keys[0] == ALL_KINDS and set(keys[1:]) == {KIND_LANG, KIND_GUIDE}
        assert panel.build_options().kind is None
        panel.kind_dd.value = KIND_GUIDE
        panel._refresh_counts()
        assert panel.build_options().kind == KIND_GUIDE
        assert panel._last_counts[:2] == (2, 1)
        # 類型是預覽批數範圍的一部分：換類型後預覽的批數不再適用
        guide_scope = panel._scope_key()
        panel.kind_dd.value = KIND_LANG
        assert panel._scope_key() != guide_scope
        # 套用背景快照：類型不在新清單時退回全部
        panel.apply_refresh_snapshot(
            {
                "versions": ["1.21.1"],
                "version": "1.21.1",
                "mods": ["foo"],
                "mod": ALL_MODS,
                "kinds": [KIND_LANG],
                "kind": None,
                "missing": 2,
                "reusable": 1,
            }
        )
        assert panel.kind_dd.value == ALL_KINDS and panel.kind_filter.kind is None
    finally:
        db.close()
