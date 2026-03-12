from __future__ import annotations

from pathlib import Path

import orjson

from translation_tool.core import ftb_translator


def test_resolve_ftbquests_quests_root_finds_nested_config_tree(tmp_path: Path) -> None:
    quests_root = tmp_path / "pack" / "config" / "ftbquests" / "quests"
    quests_root.mkdir(parents=True)

    found = ftb_translator.resolve_ftbquests_quests_root(str(tmp_path / "pack"))

    assert found == str(quests_root.resolve())


def test_export_ftbquests_raw_json_writes_only_non_empty_languages(tmp_path: Path, monkeypatch) -> None:
    quests_root = tmp_path / "config" / "ftbquests" / "quests"
    quests_root.mkdir(parents=True)

    monkeypatch.setattr(ftb_translator, "resolve_ftbquests_quests_root", lambda base_dir: str(quests_root))
    monkeypatch.setattr(
        ftb_translator,
        "process_quest_folder",
        lambda root: {
            "en_us": {"lang": {"a": "A"}, "quests": {"q": "Q"}},
            "zh_cn": {"lang": {}, "quests": {}},
        },
    )

    result = ftb_translator.export_ftbquests_raw_json(str(tmp_path), output_dir=str(tmp_path / "Output"))

    raw_root = Path(result["raw_root"])
    assert result["written_langs"] == ["en_us"]
    assert (raw_root / "en_us" / "ftb_lang.json").exists()
    assert not (raw_root / "zh_cn").exists()
    assert orjson.loads((raw_root / "en_us" / "ftb_lang.json").read_bytes()) == {"a": "A"}
