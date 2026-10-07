"""語系合併（階段 1／階段 2）以 Mod 資料庫為譯文來源。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core import lang_merge_db
from translation_tool.core.lang_merge_db import merge_db_fill
from translation_tool.core.lang_merge_extracted_assets import merge_extracted_to_assets
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_folder
from translation_tool.translation_db import DbSettings, ScanItem, TranslationDB
from translation_tool.translation_db.schema import KIND_LANG


def _settings(monkeypatch, db_path: Path, **kw) -> None:
    base = {"path": str(db_path), "version": "1.21.1"}
    base.update(kw)
    monkeypatch.setattr(lang_merge_db, "load_db_settings", lambda: DbSettings(**base))
    monkeypatch.setattr(
        lang_merge_db, "value_fully_translated", lambda v: bool(v)
    )  # 與 CJK 判斷無關，專注測資料庫補譯


def _seed(db_path: Path) -> None:
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "鋼製外殼"),
            ScanItem(KIND_LANG, "foo", "item.foo.b", "Old Text", "舊文"),
        ],
    )
    db.close()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "mod.db"
    _seed(path)
    return path


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _run_stage1(tmp_path: Path) -> tuple[dict, dict]:
    inp = tmp_path / "in"
    _write(
        inp / "assets" / "foo" / "lang" / "en_us.json",
        {
            "item.foo.a": "Steel Casing",  # 資料庫命中 → 不進待翻譯
            "item.foo.b": "New Text",  # 原文不同 → 不命中
            "item.foo.c": "Not In Database",
            "item.foo.d": "Jar Provided",
        },
    )
    _write(
        inp / "assets" / "foo" / "lang" / "zh_tw.json",
        {"item.foo.d": "模組自帶繁中"},  # jar 自帶譯文不被資料庫覆蓋
    )
    out = tmp_path / "out"
    list(merge_zhcn_to_zhtw_from_folder(str(inp), str(out), only_process_lang=True))
    lang_dir = out / "lang_output"
    tw = json.loads(next(lang_dir.rglob("zh_tw.json")).read_text(encoding="utf-8"))
    pending_files = [p for p in lang_dir.rglob("en_us.json")]
    pending = (
        json.loads(pending_files[0].read_text(encoding="utf-8"))
        if pending_files
        else {}
    )
    return tw, pending


def test_stage1_fills_pending_from_database(tmp_path, monkeypatch, db_path):
    _settings(monkeypatch, db_path)
    tw, pending = _run_stage1(tmp_path)
    assert tw["item.foo.a"] == "鋼製外殼"
    assert tw["item.foo.d"] == "模組自帶繁中"
    assert "item.foo.a" not in pending
    assert set(pending) == {
        "item.foo.b",
        "item.foo.c",
    }  # 原文不同／資料庫沒有 → 仍待翻譯


def test_stage1_unchanged_when_database_disabled(tmp_path, monkeypatch, db_path):
    _settings(monkeypatch, db_path, merge_enabled=False)
    tw, pending = _run_stage1(tmp_path)
    assert "item.foo.a" not in tw
    assert "item.foo.a" in pending


def test_stage1_unchanged_without_version(tmp_path, monkeypatch, db_path):
    _settings(monkeypatch, db_path, version="")
    _tw, pending = _run_stage1(tmp_path)
    assert "item.foo.a" in pending


def test_stage2_fills_pending_from_database(tmp_path, monkeypatch, db_path):
    _settings(monkeypatch, db_path)
    lang_output = tmp_path / "lang_output"
    extracted = lang_output / "foo_extracted" / "assets" / "foo" / "lang"
    _write(
        extracted / "en_us.json",
        {"item.foo.a": "Steel Casing", "item.foo.c": "Not In Database"},
    )
    list(merge_extracted_to_assets(lang_output))
    target = lang_output / "assets" / "foo" / "lang" / "zh_tw.json"
    tw = json.loads(target.read_text(encoding="utf-8"))
    assert tw == {"item.foo.a": "鋼製外殼"}


def test_database_never_overrides_existing_output_translation(
    tmp_path, monkeypatch, db_path
):
    """輸出資料夾既有（人工）譯文優先，資料庫只補沒有譯文的條目。"""
    _settings(monkeypatch, db_path)
    lang_output = tmp_path / "lang_output"
    _write(
        lang_output / "foo_extracted" / "assets" / "foo" / "lang" / "en_us.json",
        {"item.foo.a": "Steel Casing"},
    )
    _write(
        lang_output / "assets" / "foo" / "lang" / "zh_tw.json",
        {"item.foo.a": "人工修過的譯文"},
    )
    list(merge_extracted_to_assets(lang_output))
    tw = json.loads(
        (lang_output / "assets" / "foo" / "lang" / "zh_tw.json").read_text(
            encoding="utf-8"
        )
    )
    assert tw["item.foo.a"] == "人工修過的譯文"


def test_context_manager_closes_database(monkeypatch, db_path):
    _settings(monkeypatch, db_path)
    with merge_db_fill() as fill:
        assert fill is not None
        tw, pending, hits = fill.fill("foo", {}, {"item.foo.a": "Steel Casing"})
    assert (tw, pending, hits) == ({"item.foo.a": "鋼製外殼"}, {}, 1)


def test_stage1_skips_when_database_not_created(tmp_path, monkeypatch):
    """設定啟用、也有版本，但資料庫檔案不存在：略過，不建立檔案、合併照舊。"""
    missing = tmp_path / "never_created.db"
    _settings(monkeypatch, missing)
    _tw, pending = _run_stage1(tmp_path)
    assert "item.foo.a" in pending
    assert not missing.exists()


def test_merge_switch_is_independent_of_translation_switch(monkeypatch, db_path):
    """語系合併開關與「翻譯時使用資料庫」各自獨立。"""
    _settings(monkeypatch, db_path, enabled=False, merge_enabled=True)
    with merge_db_fill() as fill:
        assert fill is not None
    _settings(monkeypatch, db_path, enabled=True, merge_enabled=False)
    with merge_db_fill() as fill:
        assert fill is None
