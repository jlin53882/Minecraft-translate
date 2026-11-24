from __future__ import annotations

from pathlib import Path

from translation_tool.core.ftb_translator_export import (
    resolve_ftbquests_quests_root_impl,
)


def test_resolve_ftbquests_quests_root_finds_nested_config_tree(tmp_path: Path) -> None:
    quests_root = tmp_path / "pack" / "config" / "ftbquests" / "quests"
    quests_root.mkdir(parents=True)

    found = resolve_ftbquests_quests_root_impl(str(tmp_path / "pack"))

    assert found == str(quests_root.resolve())
