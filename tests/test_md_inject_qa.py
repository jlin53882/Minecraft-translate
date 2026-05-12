"""translation_tool/plugins/md/md_inject_qa.py 模組測試。

用途：測試 md_inject_qa 模組的功能。
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.md import md_inject_qa


def test_map_lang_in_rel_path(tmp_path: Path) -> None:
    """測試 map_lang_in_rel_path 基本功能。"""
    result = md_inject_qa.map_lang_in_rel_path("config/en_us/file.md", "en_us", "zh_tw")
    
    assert "zh_tw" in result
    assert "en_us" not in result


def test_map_lang_in_rel_path_with_prefix(tmp_path: Path) -> None:
    """測試 map_lang_in_rel_path 帶前綴。"""
    result = md_inject_qa.map_lang_in_rel_path("config/_en_us/file.md", "en_us", "zh_tw")
    
    assert "_zh_tw" in result


def test_map_lang_in_rel_path_case_insensitive(tmp_path: Path) -> None:
    """測試 map_lang_in_rel_path 大小寫不敏感。"""
    result = md_inject_qa.map_lang_in_rel_path("config/EN_US/file.md", "en_us", "zh_tw")
    
    assert "zh_tw" in result


def test_map_lang_in_rel_path_allow_zh_src_en(tmp_path: Path) -> None:
    """測試 map_lang_in_rel_path_allow_zh 來源是 en_us。"""
    result, status = md_inject_qa.map_lang_in_rel_path_allow_zh(
        "config/en_us/file.md", "en_us", "zh_tw"
    )
    
    assert status == "SRC_EN"
    assert "zh_tw" in result


def test_map_lang_in_rel_path_allow_zh_src_zh(tmp_path: Path) -> None:
    """測試 map_lang_in_rel_path_allow_zh 來源是 zh_tw。"""
    result, status = md_inject_qa.map_lang_in_rel_path_allow_zh(
        "config/zh_tw/file.md", "en_us", "zh_tw"
    )
    
    assert status == "SRC_ZH"


def test_map_lang_in_rel_path_allow_zh_no_lang(tmp_path: Path) -> None:
    """測試 map_lang_in_rel_path_allow_zh 無語系。"""
    result, status = md_inject_qa.map_lang_in_rel_path_allow_zh(
        "config/folder/file.md", "en_us", "zh_tw"
    )
    
    assert status == "NO_LANG"


def test_is_token_line(tmp_path: Path) -> None:
    """測試 is_token_line token 行判斷。"""
    assert md_inject_qa.is_token_line("§rule{...}") is True
    assert md_inject_qa.is_token_line("§recipe[...") is True
    assert md_inject_qa.is_token_line("§align:left") is True
    assert md_inject_qa.is_token_line("Hello") is False
    assert md_inject_qa.is_token_line("") is False  # 空行不是 token


def test_is_text_line(tmp_path: Path) -> None:
    """測試 is_text_line 文字行判斷。"""
    assert md_inject_qa.is_text_line("Hello World") is True
    assert md_inject_qa.is_text_line("  Text  ") is True
    assert md_inject_qa.is_text_line("") is False
    assert md_inject_qa.is_text_line("§token") is False
    assert md_inject_qa.is_text_line("![alt](url)") is False
    assert md_inject_qa.is_text_line("<ItemImage />") is False


def test_flatten_for_md(tmp_path: Path) -> None:
    """測試 flatten_for_md 扁平化。"""
    # 多行段落應該被壓縮
    result = md_inject_qa.flatten_for_md("Line1\n\nLine2\n\nLine3")
    
    assert "\n\n\n" not in result


def test_load_items_from_json(tmp_path: Path) -> None:
    """測試 load_items_from_json 載入項目。"""
    json_data = {
        "source_md": "test.md",
        "items": [
            {
                "id": "test.md:1-2",
                "text": "Hello",
                "content_hash": "abc",
                "start_line": 1,
                "end_line": 2,
            }
        ]
    }
    
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps(json_data))
    
    source_md, items = md_inject_qa.load_items_from_json(json_file)
    
    assert source_md == "test.md"
    assert len(items) == 1
    assert items[0].content == "Hello"


def test_apply_item_to_md_lines_basic(tmp_path: Path) -> None:
    """測試 apply_item_to_md_lines 基本替換。"""
    md_lines = ["# Title", "", "Original text"]
    item = md_inject_qa.Item(
        source_md="test.md",
        start_line=3,
        end_line=3,
        text="Translated text",
    )
    
    md_inject_qa.apply_item_to_md_lines(md_lines, item)
    
    assert "Translated text" in md_lines


def test_apply_item_to_md_lines_multiline(tmp_path: Path) -> None:
    """測試 apply_item_to_md_lines 多行翻譯。"""
    md_lines = ["# Title", "", "Line1", "Line2"]
    item = md_inject_qa.Item(
        source_md="test.md",
        start_line=3,
        end_line=4,
        text="NewLine1\nNewLine2",
    )
    
    md_inject_qa.apply_item_to_md_lines(md_lines, item)
    
    assert "NewLine1" in md_lines[2]
    assert "NewLine2" in md_lines[3]


def test_apply_item_to_md_lines_preserve_indent(tmp_path: Path) -> None:
    """測試 apply_item_to_md_lines 保留縮排。"""
    md_lines = ["  Indented text"]
    item = md_inject_qa.Item(
        source_md="test.md",
        start_line=1,
        end_line=1,
        text="New text",
    )
    
    md_inject_qa.apply_item_to_md_lines(md_lines, item)
    
    assert md_lines[0].startswith("  ")
    assert "New text" in md_lines[0]


def test_apply_item_to_md_lines_fewer_lines(tmp_path: Path) -> None:
    """測試 apply_item_to_md_lines 翻譯行數少於原文。"""
    md_lines = ["Line1", "Line2", "Line3"]
    item = md_inject_qa.Item(
        source_md="test.md",
        start_line=1,
        end_line=3,
        text="NewLine1",  # 只有一行
    )
    
    md_inject_qa.apply_item_to_md_lines(md_lines, item)
    
    # 第一行應該被替換，後面的行應該被清空
    assert "NewLine1" in md_lines[0]
