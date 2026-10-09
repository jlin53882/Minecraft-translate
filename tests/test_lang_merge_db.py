"""語系合併（階段 1／階段 2）以 Mod 資料庫為譯文來源。"""

from __future__ import annotations

import json
import sqlite3
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services_impl.pipelines import merge_service
from translation_tool.core import lang_merge_db, lang_merger
from translation_tool.core.lang_merge_db import merge_db_fill
from translation_tool.core.lang_merge_extracted_assets import merge_extracted_to_assets
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_folder
from translation_tool.translation_db import (
    DbSettings,
    ScanItem,
    TranslationDB,
    TranslationResolver,
)
from translation_tool.translation_db.schema import (
    KIND_LANG,
    SRC_AI,
    SRC_JAR_TW,
    SRC_MANUAL,
)


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


def test_zip_batch_keeps_database_identity_after_global_path_switch(
    tmp_path, monkeypatch
):
    """批次開始後全域 DB 路徑改變，所有 ZIP 仍使用啟動時快照中的資料庫。"""
    database_a_path = tmp_path / "database-a.db"
    database_b_path = tmp_path / "database-b.db"
    for path, translations in (
        (
            database_a_path,
            {
                "item.foo.first": "資料庫 A 第一筆",
                "item.foo.second": "資料庫 A 第二筆",
            },
        ),
        (
            database_b_path,
            {
                "item.foo.first": "資料庫 B 第一筆",
                "item.foo.second": "資料庫 B 第二筆",
            },
        ),
    ):
        db = TranslationDB(path)
        db.ingest(
            "1.21.1",
            [
                ScanItem(KIND_LANG, "foo", key, english, translated)
                for key, english, translated in (
                    ("item.foo.first", "First English", translations["item.foo.first"]),
                    (
                        "item.foo.second",
                        "Second English",
                        translations["item.foo.second"],
                    ),
                )
            ],
        )
        db.close()

    settings_a = DbSettings(
        path=str(database_a_path.resolve()),
        merge_enabled=True,
        version="1.21.1",
        cross_version=False,
        priority=(4, 2, 1),
    )
    settings_b = DbSettings(
        path=str(database_b_path.resolve()),
        merge_enabled=True,
        version="1.21.1",
        cross_version=True,
        priority=(1, 2, 4),
    )
    current_global_settings = {"value": settings_a}
    monkeypatch.setattr(
        lang_merge_db,
        "load_db_settings",
        lambda: current_global_settings["value"],
    )
    monkeypatch.setattr(
        lang_merge_db, "value_fully_translated", lambda value: bool(value)
    )

    opened_settings = []
    opened_readonly = []
    real_open_db = lang_merge_db.open_db

    def tracking_open_db(settings, *, create=False, readonly=False):
        opened_settings.append(settings)
        opened_readonly.append(readonly)
        return real_open_db(settings, create=create, readonly=readonly)

    monkeypatch.setattr(lang_merge_db, "open_db", tracking_open_db)
    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)

    class _FakeUIHandler:
        def set_session(self, _session):
            pass

    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())
    monkeypatch.setattr(
        lang_merger,
        "load_config",
        lambda: {
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "待翻譯整理需翻譯",
                "filtered_pending_min_count": 1,
                "quarantine_folder_name": "skipped_json",
            },
            "translator": {
                "parallel_execution_workers": 1,
                "replace_rules_path": "replace_rules.json",
            },
        },
    )
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])

    zip_paths = []
    for name, key, english in (
        ("first.zip", "item.foo.first", "First English"),
        ("second.zip", "item.foo.second", "Second English"),
    ):
        zip_path = tmp_path / name
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr(
                "assets/foo/lang/en_us.json",
                json.dumps({key: english}),
            )
        zip_paths.append(str(zip_path))

    record_zip_result = merge_service._record_zip_result
    completed_zips = []

    def switch_global_database_after_first_zip(*args, **kwargs):
        record_zip_result(*args, **kwargs)
        completed_zips.append(args[2])
        if len(completed_zips) == 1:
            current_global_settings["value"] = settings_b

    monkeypatch.setattr(
        merge_service, "_record_zip_result", switch_global_database_after_first_zip
    )

    output_dir = tmp_path / "output"
    list(
        merge_service.run_merge_zip_batch_service(
            zip_paths,
            str(output_dir),
            MagicMock(),
            only_process_lang=True,
            use_translation_db=True,
            translation_db_version="1.21.1",
            translation_db_settings_snapshot=settings_a,
        )
    )

    translated = json.loads(
        (
            output_dir / "lang_output" / "assets" / "foo" / "lang" / "zh_tw.json"
        ).read_text(encoding="utf-8")
    )
    assert translated == {
        "item.foo.first": "資料庫 A 第一筆",
        "item.foo.second": "資料庫 A 第二筆",
    }
    assert [Path(settings.path) for settings in opened_settings] == [
        database_a_path.resolve(),
        database_a_path.resolve(),
    ]
    assert all(settings.version == "1.21.1" for settings in opened_settings)
    assert all(settings.cross_version is False for settings in opened_settings)
    assert all(settings.priority == (4, 2, 1) for settings in opened_settings)
    assert opened_readonly == [True, True]


def test_folder_stage1_and_stage2_keep_snapshot_priority_after_global_change(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "folder-shared-database.db"
    priority_a = (SRC_JAR_TW, SRC_AI, SRC_MANUAL)
    priority_b = (SRC_AI, SRC_JAR_TW, SRC_MANUAL)
    db = TranslationDB(database_path, priority=priority_a)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.stage1",
                "Stage One",
                "AI one",
                source=SRC_AI,
            ),
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.stage1",
                "Stage One",
                "Jar one",
                source=SRC_JAR_TW,
            ),
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.stage2",
                "Stage Two",
                "AI two",
                source=SRC_AI,
            ),
            ScanItem(
                KIND_LANG,
                "foo",
                "item.foo.stage2",
                "Stage Two",
                "Jar two",
                source=SRC_JAR_TW,
            ),
        ],
    )
    db.close()
    settings_a = DbSettings(
        path=str(database_path.resolve()),
        merge_enabled=True,
        version="1.21.1",
        cross_version=False,
        priority=priority_a,
    )
    _settings(monkeypatch, database_path, priority=priority_a)
    monkeypatch.setattr(
        lang_merge_db,
        "value_fully_translated",
        lambda value: bool(value),
    )

    source = tmp_path / "folder-input"
    _write(
        source / "assets" / "foo" / "lang" / "en_us.json",
        {"item.foo.stage1": "Stage One"},
    )
    output = tmp_path / "folder-output"
    list(
        merge_zhcn_to_zhtw_from_folder(
            str(source),
            str(output),
            only_process_lang=True,
            use_translation_db=True,
            translation_db_version="1.21.1",
            translation_db_settings_snapshot=settings_a,
        )
    )

    lang_output = output / "lang_output"
    stage1_target = lang_output / "assets" / "foo" / "lang" / "zh_tw.json"
    assert json.loads(stage1_target.read_text(encoding="utf-8")) == {
        "item.foo.stage1": "Jar one"
    }

    # Make B the actual shared DB state between Folder Stage 1 and Stage 2.
    current = TranslationDB(database_path, priority=priority_b, create=False)
    current.close()
    _settings(monkeypatch, database_path, priority=priority_b)
    _write(
        lang_output / "foo_extracted" / "assets" / "foo" / "lang" / "en_us.json",
        {"item.foo.stage2": "Stage Two"},
    )
    list(
        merge_extracted_to_assets(
            lang_output,
            use_translation_db=True,
            translation_db_version="1.21.1",
            translation_db_settings_snapshot=settings_a,
        )
    )

    stage2_target = lang_output / "assets" / "foo" / "lang" / "zh_tw.json"
    assert json.loads(stage2_target.read_text(encoding="utf-8")) == {
        "item.foo.stage1": "Jar one",
        "item.foo.stage2": "Jar two",
    }
    with sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro", uri=True
    ) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='priority'").fetchone()[
            0
        ] == ",".join(map(str, priority_b))


def test_zip_batch_uses_snapshot_priority_without_reverting_global_effective(
    tmp_path, monkeypatch
):
    """A merge reads its click-time source order without rewriting a newer DB order."""
    database_path = tmp_path / "shared-database.db"
    priority_a = (SRC_MANUAL, SRC_JAR_TW, SRC_AI)
    priority_b = (SRC_AI, SRC_JAR_TW, SRC_MANUAL)
    db = TranslationDB(database_path, priority=priority_a)
    for key, english in (
        ("item.foo.first", "First English"),
        ("item.foo.second", "Second English"),
        ("item.foo.reviewed", "Reviewed English"),
    ):
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    KIND_LANG,
                    "foo",
                    key,
                    english,
                    "AI translation",
                    source=SRC_AI,
                ),
                ScanItem(
                    KIND_LANG,
                    "foo",
                    key,
                    english,
                    "Jar translation",
                    source=SRC_JAR_TW,
                ),
            ],
        )
    reviewed = next(
        row
        for row in db.list_entries("1.21.1", mod_id="foo", limit=100)[0]
        if row.key == "item.foo.reviewed"
    )
    db.save_manual(reviewed.id, "Reviewed translation", actor="reviewer")
    db.review_manual(
        reviewed.id,
        expected_zh_tw="Reviewed translation",
        actor="reviewer",
    )
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.pending", "Pending English")],
    )
    db.close()

    settings_a = DbSettings(
        path=str(database_path.resolve()),
        merge_enabled=True,
        version="1.21.1",
        cross_version=False,
        priority=priority_a,
    )
    settings_b = DbSettings(
        path=str(database_path.resolve()),
        merge_enabled=True,
        version="1.21.1",
        cross_version=False,
        priority=priority_b,
    )
    current_settings = {"value": settings_a}
    changed_priority = threading.Event()
    monkeypatch.setattr(
        lang_merge_db,
        "load_db_settings",
        lambda: current_settings["value"],
    )
    monkeypatch.setattr(
        lang_merge_db, "value_fully_translated", lambda value: bool(value)
    )
    monkeypatch.setattr(merge_service, "ensure_pipeline_logging", lambda: None)

    class _FakeUIHandler:
        def set_session(self, _session):
            pass

    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", _FakeUIHandler())
    monkeypatch.setattr(
        lang_merger,
        "load_config",
        lambda: {
            "lang_merger": {
                "pending_folder_name": "待翻譯",
                "pending_organized_folder_name": "待翻譯整理需翻譯",
                "filtered_pending_min_count": 1,
                "quarantine_folder_name": "skipped_json",
            },
            "translator": {
                "parallel_execution_workers": 1,
                "replace_rules_path": "replace_rules.json",
            },
        },
    )
    monkeypatch.setattr(lang_merger, "load_replace_rules", lambda _path: [])

    zip_paths = []
    for name, entries in (
        ("first.zip", {"item.foo.first": "First English"}),
        (
            "second.zip",
            {
                "item.foo.second": "Second English",
                "item.foo.reviewed": "Reviewed English",
                "item.foo.pending": "Pending English",
            },
        ),
    ):
        zip_path = tmp_path / name
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("assets/foo/lang/en_us.json", json.dumps(entries))
        zip_paths.append(str(zip_path))

    record_zip_result = merge_service._record_zip_result
    completed_zips = []

    def change_global_priority_after_first_zip(*args, **kwargs):
        record_zip_result(*args, **kwargs)
        completed_zips.append(args[2])
        if len(completed_zips) == 1:
            current_settings["value"] = settings_b
            global_db = TranslationDB(database_path, priority=priority_b, create=False)
            global_db.close()
            changed_priority.set()

    monkeypatch.setattr(
        merge_service, "_record_zip_result", change_global_priority_after_first_zip
    )
    output_dir = tmp_path / "output"
    list(
        merge_service.run_merge_zip_batch_service(
            zip_paths,
            str(output_dir),
            MagicMock(),
            only_process_lang=True,
            use_translation_db=True,
            translation_db_version="1.21.1",
            translation_db_settings_snapshot=settings_a,
        )
    )

    assert changed_priority.is_set()
    translated = json.loads(
        (
            output_dir / "lang_output" / "assets" / "foo" / "lang" / "zh_tw.json"
        ).read_text(encoding="utf-8")
    )
    assert translated == {
        "item.foo.first": "Jar translation",
        "item.foo.second": "Jar translation",
        "item.foo.reviewed": "Reviewed translation",
    }

    # Inspect raw state with a read-only SQLite connection; constructing a
    # TranslationDB here would itself synchronize priority and hide a rollback.
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        meta_priority = conn.execute(
            "SELECT value FROM meta WHERE key='priority'"
        ).fetchone()[0]
        effective = {
            key: (translation, checker)
            for key, translation, checker in conn.execute(
                "SELECT e.key, f.zh_tw, f.checker FROM entry e "
                "JOIN effective f ON f.entry_id=e.id WHERE e.mod_id='foo'"
            ).fetchall()
        }
    assert meta_priority == ",".join(map(str, priority_b))
    assert effective["item.foo.second"] == ("AI translation", "")
    assert effective["item.foo.reviewed"] == ("Reviewed translation", "reviewer")


def test_valid_target_still_allows_configured_cross_version_lookup(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "cross-version.db"
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.anchor", "Anchor", "錨點")],
    )
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.cross", "Cross Entry", "跨版本譯文")],
    )
    db.close()
    _settings(monkeypatch, db_path, version="1.21.1", cross_version=True)

    with merge_db_fill() as fill:
        assert fill is not None
        final_tw, pending, hits = fill.fill(
            "foo", {}, {"item.foo.cross": "Cross Entry"}
        )

    assert final_tw == {"item.foo.cross": "跨版本譯文"}
    assert pending == {}
    assert hits == 1


def test_snapshot_priority_preserves_cross_version_distance_tie_order(tmp_path):
    db_path = tmp_path / "cross-version-tie.db"
    priority_a = (SRC_JAR_TW, SRC_AI, SRC_MANUAL)
    priority_b = (SRC_AI, SRC_JAR_TW, SRC_MANUAL)
    db = TranslationDB(db_path, priority=priority_b)
    for version, marker in (("1.20.1", "older"), ("1.22.1", "newer")):
        db.ingest(
            version,
            [
                ScanItem(
                    KIND_LANG,
                    "foo",
                    "item.foo.tie",
                    "Same English",
                    f"{marker} AI",
                    source=SRC_AI,
                ),
                ScanItem(
                    KIND_LANG,
                    "foo",
                    "item.foo.tie",
                    "Same English",
                    f"{marker} Jar",
                    source=SRC_JAR_TW,
                ),
            ],
        )

    resolver = TranslationResolver(
        db,
        "1.21.1",
        cross_version=True,
        source_priority=priority_a,
    )
    hit = resolver.lookup(KIND_LANG, "foo", "item.foo.tie", "Same English")
    db.close()

    # Versions are equally distant; retain the existing first-candidate tie rule.
    assert hit is not None
    assert (hit.zh_tw, hit.mc_version, hit.cross) == ("older Jar", "1.20.1", True)


def test_stage1_unchanged_when_database_disabled(tmp_path, monkeypatch, db_path):
    _settings(monkeypatch, db_path, merge_enabled=False)
    tw, pending = _run_stage1(tmp_path)
    assert "item.foo.a" not in tw
    assert "item.foo.a" in pending


def test_stage1_unchanged_without_version(tmp_path, monkeypatch, db_path):
    _settings(monkeypatch, db_path, version="")
    _tw, pending = _run_stage1(tmp_path)
    assert "item.foo.a" in pending


def test_stage1_invalid_target_version_skips_database_supplement(
    tmp_path, monkeypatch, db_path
):
    _settings(monkeypatch, db_path, version="1.19.4", cross_version=True)
    tw, pending = _run_stage1(tmp_path)
    assert "item.foo.a" not in tw
    assert "item.foo.a" in pending


def test_invalid_target_version_does_not_read_cross_version_entries(
    monkeypatch, db_path
):
    """即使啟用跨版本沿用，無效目標也不能建立 Resolver 或補譯。"""
    _settings(monkeypatch, db_path, version="1.19.4", cross_version=True)
    opened = TranslationDB(db_path)
    closed = []
    real_close = opened.close

    def close():
        closed.append(True)
        real_close()

    monkeypatch.setattr(opened, "close", close)
    monkeypatch.setattr(lang_merge_db, "open_db", lambda *_a, **_kw: opened)

    def unexpected_resolver(*_args, **_kwargs):
        pytest.fail("無效目標版本不得建立 TranslationResolver")

    monkeypatch.setattr(lang_merge_db, "TranslationResolver", unexpected_resolver)

    assert lang_merge_db.open_merge_db_fill() is None
    assert closed == [True]


def test_empty_database_is_closed_and_skipped_without_a_resolver(monkeypatch, tmp_path):
    db_path = tmp_path / "empty.db"
    empty_db = TranslationDB(db_path)
    closed = []
    real_close = empty_db.close

    def close():
        closed.append(True)
        real_close()

    monkeypatch.setattr(empty_db, "close", close)
    _settings(monkeypatch, db_path, version="1.21.1")
    monkeypatch.setattr(lang_merge_db, "open_db", lambda *_a, **_kw: empty_db)
    monkeypatch.setattr(
        lang_merge_db,
        "TranslationResolver",
        lambda *_a, **_kw: pytest.fail("無版本資料庫不得建立 Resolver"),
    )

    assert lang_merge_db.open_merge_db_fill() is None
    assert closed == [True]


def test_database_path_mismatch_is_closed_and_skipped(monkeypatch, tmp_path):
    actual_path = tmp_path / "actual.db"
    expected_path = tmp_path / "snapshot.db"
    db = TranslationDB(actual_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", "item.foo.key", "English", "繁中")],
    )
    db.close()
    opened = TranslationDB(actual_path, create=False, readonly=True)
    closed = []
    real_close = opened.close

    def close():
        closed.append(True)
        real_close()

    monkeypatch.setattr(opened, "close", close)
    _settings(monkeypatch, expected_path, version="1.21.1")
    monkeypatch.setattr(lang_merge_db, "open_db", lambda *_a, **_kw: opened)
    monkeypatch.setattr(
        lang_merge_db,
        "TranslationResolver",
        lambda *_a, **_kw: pytest.fail("路徑不符時不得建立 Resolver"),
    )

    assert lang_merge_db.open_merge_db_fill() is None
    assert closed == [True]


def test_stage2_invalid_target_keeps_pending_entries_unchanged(
    tmp_path, monkeypatch, db_path
):
    """Stage 2 同樣必須略過 DB 中不存在的目標版本。"""
    _settings(monkeypatch, db_path, version="1.19.4", cross_version=True)
    lang_output = tmp_path / "lang_output"
    _write(
        lang_output / "foo_extracted" / "assets" / "foo" / "lang" / "en_us.json",
        {"item.foo.a": "Steel Casing"},
    )

    list(merge_extracted_to_assets(lang_output))

    assert not (lang_output / "assets" / "foo" / "lang" / "zh_tw.json").exists()
    source = lang_output / "foo_extracted" / "assets" / "foo" / "lang" / "en_us.json"
    assert json.loads(source.read_text(encoding="utf-8")) == {
        "item.foo.a": "Steel Casing"
    }


@pytest.mark.parametrize("state", ["missing_database", "unset_version", "disabled"])
def test_stage2_safely_continues_without_a_usable_database(
    tmp_path, monkeypatch, db_path, state
):
    if state == "missing_database":
        missing = tmp_path / "not-created.db"
        _settings(monkeypatch, missing, version="1.21.1")
    elif state == "unset_version":
        _settings(monkeypatch, db_path, version="")
    else:
        _settings(monkeypatch, db_path, version="1.21.1", merge_enabled=False)

    lang_output = tmp_path / f"lang_output_{state}"
    source = lang_output / "foo_extracted" / "assets" / "foo" / "lang" / "en_us.json"
    _write(source, {"item.foo.a": "Steel Casing"})

    updates = list(merge_extracted_to_assets(lang_output))

    assert not any(update.get("error") for update in updates)
    assert not (lang_output / "assets" / "foo" / "lang" / "zh_tw.json").exists()
    assert source.is_file()
    if state == "missing_database":
        assert not missing.exists()


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
        assert fill.db.readonly is True
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


def test_resolver_stats_are_exact_under_concurrent_lookups(monkeypatch, db_path):
    """多執行緒共用 resolver 時，命中／未命中統計不可少計（計數遞增需在鎖內）。"""
    import threading
    import time

    from translation_tool.translation_db import TranslationResolver
    from translation_tool.translation_db.resolver import ResolverStats

    class SlowStats(ResolverStats):
        """讀取計數時讓出執行緒，放大『讀—加—寫』被打斷的機會。"""

        def __getattribute__(self, name):
            value = super().__getattribute__(name)
            if name in ("hit_target", "hit_cross", "en_mismatch", "miss"):
                time.sleep(0.0005)  # 讀到舊值之後才讓出，其他執行緒會讀到同一個舊值
            return value

    db = TranslationDB(db_path)
    resolver = TranslationResolver(db, "1.21.1")
    resolver.stats = SlowStats()

    def work():
        for _ in range(20):
            resolver.lookup(KIND_LANG, "foo", "item.foo.a", "Steel Casing")  # 命中
            resolver.lookup(KIND_LANG, "foo", "item.foo.zzz", "Nope")  # 未命中

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resolver.stats.hit_target == 8 * 20
    assert resolver.stats.miss == 8 * 20
    db.close()
