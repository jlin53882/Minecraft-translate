from __future__ import annotations

from types import SimpleNamespace

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
