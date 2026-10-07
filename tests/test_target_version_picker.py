from __future__ import annotations

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
