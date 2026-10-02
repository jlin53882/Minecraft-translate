"""Regression coverage for translator.replace_rules_path in language merge flows."""

from __future__ import annotations

from translation_tool.core import lang_merger

CUSTOM_RULES = "rules/custom-replace-rules.json"


def _patch_custom_rules(monkeypatch):
    """Return a list recording the rule path requested by the merge flow."""
    requested: list[str] = []
    config = {
        "translator": {
            "replace_rules_path": CUSTOM_RULES,
            "parallel_execution_workers": 1,
        },
        "lang_merger": {},
    }
    monkeypatch.setattr(lang_merger, "load_config", lambda: config)
    monkeypatch.setattr(
        lang_merger,
        "load_replace_rules",
        lambda path: requested.append(path) or [],
    )
    return requested


def test_folder_merge_uses_nested_translator_replace_rules_path(tmp_path, monkeypatch):
    """Folder merge must honor the same nested config path as other translators."""
    requested = _patch_custom_rules(monkeypatch)
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    list(
        lang_merger.merge_zhcn_to_zhtw_from_folder(
            str(input_dir), str(tmp_path / "output")
        )
    )

    assert requested == [CUSTOM_RULES]


def test_zip_merge_uses_nested_translator_replace_rules_path(tmp_path, monkeypatch):
    """ZIP merge must honor translator.replace_rules_path before opening the ZIP."""
    requested = _patch_custom_rules(monkeypatch)

    list(
        lang_merger.merge_zhcn_to_zhtw_from_zip(
            str(tmp_path / "missing.zip"), str(tmp_path / "output")
        )
    )

    assert requested == [CUSTOM_RULES]
