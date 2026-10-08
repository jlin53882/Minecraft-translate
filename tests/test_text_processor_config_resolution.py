import json
from pathlib import Path

import pytest

from translation_tool.utils import text_processor


def test_load_replace_rules_uses_explicit_runtime_path_helper(tmp_path, monkeypatch):
    rules_path = tmp_path / "replace_rules.json"
    rules_path.write_text(
        json.dumps(
            [
                {"from": "abcdef", "to": "A"},
                {"from": "abc", "to": "B"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(text_processor, "_resolve_rules_path", lambda path: rules_path)

    rules = text_processor.load_replace_rules("replace_rules.json")
    assert [r["from"] for r in rules] == ["abcdef", "abc"]


def test_save_replace_rules_can_propagate_durable_write_failure(tmp_path, monkeypatch):
    rules_path = tmp_path / "replace_rules.json"
    monkeypatch.setattr(text_processor, "_resolve_rules_path", lambda _path: rules_path)

    def fail_open(*_args, **_kwargs):
        raise PermissionError("read-only destination")

    monkeypatch.setattr(Path, "open", fail_open)

    # Legacy best-effort callers retain the historical log-and-continue behavior.
    text_processor.save_replace_rules("replace_rules.json", [])
    with pytest.raises(PermissionError, match="read-only destination"):
        text_processor.save_replace_rules("replace_rules.json", [], raise_on_error=True)
