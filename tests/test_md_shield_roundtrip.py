"""Markdown 注入：翻譯對照表檔不可被當成待翻譯文件讀入。"""

from __future__ import annotations

from pathlib import Path

from translation_tool.plugins.md import md_inject_qa


def test_translation_map_is_not_read_as_a_pending_document(tmp_path: Path):
    (tmp_path / "translation_map_md.json").write_text("[]", encoding="utf-8")
    (tmp_path / "a.md.json").write_text("{}", encoding="utf-8")
    names = [p.name for p in md_inject_qa.iter_json_files(tmp_path)]
    assert names == ["a.md.json"]
