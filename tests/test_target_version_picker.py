from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb import version_picker


def test_target_version_choices_read_without_creating_database(monkeypatch):
    calls = []

    class Database:
        closed = False

        def close(self):
            self.closed = True

    db = Database()

    def open_database(*, create):
        calls.append(create)
        return db

    monkeypatch.setattr(version_picker, "open_database", open_database)
    monkeypatch.setattr(
        version_picker, "version_choices", lambda opened: ["1.22.0", "1.21.1"]
    )

    assert version_picker.target_version_choices() == ["1.22.0", "1.21.1"]
    assert calls == [False]
    assert db.closed is True


def test_target_version_picker_builds_when_all_suggestions_fail(monkeypatch):
    monkeypatch.setattr(version_picker, "open_database", lambda *, create: None)

    def fail_to_read_versions(_db=None):
        raise OSError("version list unavailable")

    monkeypatch.setattr(version_picker, "version_choices", fail_to_read_versions)

    assert version_picker.target_version_choices() == []
    field = version_picker.target_version_dropdown()
    assert field.options == []


def test_target_version_picker_preserves_custom_text_and_known_selection(monkeypatch):
    monkeypatch.setattr(
        version_picker, "target_version_choices", lambda: ["1.21.1", "1.20.1"]
    )
    field = version_picker.target_version_dropdown(value="custom-version")

    assert field.editable and field.enable_filter
    assert field.value is None
    assert version_picker.target_version_value(field) == "custom-version"

    field.text = "1.20.1"
    field.value = "1.20.1"
    assert version_picker.target_version_value(field) == "1.20.1"


def test_target_version_picker_keeps_typed_value_across_edit_clear_and_refresh(
    monkeypatch,
):
    monkeypatch.setattr(
        version_picker, "target_version_choices", lambda: ["1.21.1", "1.20.1"]
    )
    changed = []
    field = version_picker.target_version_dropdown(
        value="1.21.1", on_change=lambda event: changed.append(event.control.value)
    )

    field.text = "26.2"
    field.on_text_change(SimpleNamespace(control=field))
    assert field.value == "26.2"
    assert "26.2" in [option.key for option in field.options]
    assert version_picker.target_version_value(field) == "26.2"

    version_picker.refresh_target_version_options(field)
    assert field.value == field.text == "26.2"
    assert [option.key for option in field.options] == [
        "1.21.1",
        "1.20.1",
        "26.2",
    ]

    field.text = ""
    field.on_text_change(SimpleNamespace(control=field))
    assert field.value is None
    assert version_picker.target_version_value(field) is None
    assert "26.2" not in [option.key for option in field.options]
    assert changed == ["26.2", None]


def test_merge_target_version_choices_are_only_versions_stored_in_the_database(
    tmp_path, monkeypatch
):
    from app.services_impl import moddb_service
    from translation_tool.translation_db import (
        KIND_LANG,
        DbSettings,
        ScanItem,
        TranslationDB,
    )

    db_path = tmp_path / "mod.db"
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "mod", "k1", "English", "繁中")],
    )
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "mod", "k2", "English 2", "繁中 2")],
    )
    db.close()
    monkeypatch.setattr(
        moddb_service, "current_settings", lambda: DbSettings(path=str(db_path))
    )
    # The shared picker intentionally retains its resource-pack-inclusive contract.
    monkeypatch.setattr(
        version_picker,
        "target_version_choices",
        lambda: ["1.21.1", "1.20.1", "1.21.6~1.21.8"],
    )

    assert version_picker.target_version_choices() == [
        "1.21.1",
        "1.20.1",
        "1.21.6~1.21.8",
    ]
    assert set(version_picker.merge_target_version_choices()) == {"1.21.1", "1.20.1"}


@pytest.mark.parametrize("database_state", ["missing", "corrupt", "empty"])
def test_merge_database_version_choices_fail_closed_without_creating_database(
    tmp_path, monkeypatch, database_state
):
    from app.services_impl import moddb_service
    from translation_tool.translation_db import DbSettings, TranslationDB

    db_path = tmp_path / f"{database_state}.db"
    if database_state == "corrupt":
        db_path.write_bytes(b"not a sqlite database")
    elif database_state == "empty":
        TranslationDB(db_path).close()
    monkeypatch.setattr(
        moddb_service, "current_settings", lambda: DbSettings(path=str(db_path))
    )

    assert version_picker.merge_target_version_choices() == []
    assert db_path.exists() is (database_state != "missing")


def test_merge_target_version_dropdown_is_database_only_and_not_editable(monkeypatch):
    monkeypatch.setattr(
        version_picker,
        "merge_target_version_choices",
        lambda: ["1.21.1", "1.20.1"],
    )
    field = version_picker.merge_target_version_dropdown(global_version="1.21.1")

    assert field.editable is False
    assert field.value == version_picker.MERGE_INHERIT_VERSION
    assert [(option.key, option.text) for option in field.options] == [
        (version_picker.MERGE_INHERIT_VERSION, "沿用全域設定（目前：1.21.1）"),
        ("1.21.1", "1.21.1"),
        ("1.20.1", "1.20.1"),
    ]

    # Other consumers continue to use the shared editable picker.
    shared = version_picker.target_version_dropdown()
    assert shared.editable is True


def test_merge_database_version_supplier_closes_the_open_database(monkeypatch):
    class Database:
        closed = False

        def versions(self):
            return ["1.21.1"]

        def close(self):
            self.closed = True

    db = Database()
    monkeypatch.setattr(version_picker, "open_database", lambda *, create: db)

    assert version_picker.merge_target_version_choices() == ["1.21.1"]
    assert db.closed is True


def test_merge_database_version_supplier_closes_database_when_read_fails(monkeypatch):
    class Database:
        closed = False

        def versions(self):
            raise OSError("database became unavailable")

        def close(self):
            self.closed = True

    db = Database()
    monkeypatch.setattr(version_picker, "open_database", lambda *, create: db)

    assert version_picker.merge_target_version_choices() == []
    assert db.closed is True
