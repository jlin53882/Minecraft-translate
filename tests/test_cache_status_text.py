"""快取類型狀態文字：總覽卡與分片卡共用同一組措辭與數字格式。"""

from __future__ import annotations

from pathlib import Path

from app.views.cache_manager.cache_status_text import (
    dirty_label,
    entries_line,
    shard_line,
)
from translation_tool.utils.cache_overview import count_shard_files


def test_lines_label_the_active_shard_and_total_shard_count():
    state = {
        "entries_count": 23992,
        "session_new_count": 0,
        "active_shard_id": "00001",
        "active_shard_entries": 0,
        "shard_capacity": 2500,
        "shard_count": 103,
    }
    assert entries_line(state) == "筆數 23,992"  # 新增為 0 不顯示
    assert shard_line(state) == "目前寫入分片 00001（共 103 片）　已寫入 0 / 2,500 筆"

    state["session_new_count"] = 12
    assert entries_line(state) == "筆數 23,992　本次新增 12"


def test_shard_line_without_shard_count_and_dirty_label():
    line = shard_line({"active_shard_id": "", "active_shard_entries": 5})
    assert line == "目前寫入分片 -　已寫入 5 / 2,500 筆"
    assert (dirty_label(True), dirty_label(False)) == ("有變更", "無變更")


def test_count_shard_files_skips_the_main_cache_file(tmp_path: Path):
    type_dir = tmp_path / "lang"
    type_dir.mkdir()
    for name in ("lang_00001.json", "lang_00002.json", "lang_cache_main.json"):
        (type_dir / name).write_text("{}", encoding="utf-8")
    (type_dir / "notes.txt").write_text("x", encoding="utf-8")

    paths = {"lang": type_dir / "lang_cache_main.json"}
    assert count_shard_files(paths, "lang") == 2
    assert count_shard_files(paths, "missing") == 0
