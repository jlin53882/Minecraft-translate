"""語言合併流程中 translator.replace_rules_path 的回歸測試。"""

from __future__ import annotations

from translation_tool.core import lang_merger

CUSTOM_RULES = "rules/custom-replace-rules.json"


def _patch_custom_rules(monkeypatch):
    """回傳一個清單，記錄合併流程所要求的規則路徑。"""
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
    """資料夾合併必須與其他翻譯器一樣，遵循巢狀設定路徑。"""
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
    """ZIP 合併必須在開啟 ZIP 之前，先遵循 translator.replace_rules_path。"""
    requested = _patch_custom_rules(monkeypatch)

    list(
        lang_merger.merge_zhcn_to_zhtw_from_zip(
            str(tmp_path / "missing.zip"), str(tmp_path / "output")
        )
    )

    assert requested == [CUSTOM_RULES]
