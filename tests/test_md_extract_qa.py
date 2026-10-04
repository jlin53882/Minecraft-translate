"""translation_tool/plugins/md/md_extract_qa.py 模組測試。

用途：測試 md_extract_qa 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.md import md_extract_qa


def test_contains_cjk(tmp_path: Path) -> None:
    """測試 contains_cjk 檢測中日韓文字。"""
    assert md_extract_qa.contains_cjk("你好") is True
    assert md_extract_qa.contains_cjk("Hello") is False
    assert md_extract_qa.contains_cjk("Hello 你好") is True


def test_pass_lang_filter_cjk_only(tmp_path: Path) -> None:
    """測試 pass_lang_filter cjk_only 模式。"""
    assert md_extract_qa.pass_lang_filter("你好", "cjk_only") is True
    assert md_extract_qa.pass_lang_filter("Hello", "cjk_only") is False


def test_pass_lang_filter_non_cjk_only(tmp_path: Path) -> None:
    """測試 pass_lang_filter non_cjk_only 模式。"""
    assert md_extract_qa.pass_lang_filter("Hello", "non_cjk_only") is True
    assert md_extract_qa.pass_lang_filter("你好", "non_cjk_only") is False


def test_pass_lang_filter_all(tmp_path: Path) -> None:
    """測試 pass_lang_filter all 模式。"""
    assert md_extract_qa.pass_lang_filter("Hello", "all") is True
    assert md_extract_qa.pass_lang_filter("你好", "all") is True


def test_normalize_for_dedupe(tmp_path: Path) -> None:
    """測試 normalize_for_dedupe 正規化。"""
    result = md_extract_qa.normalize_for_dedupe("  Hello   World  ")
    
    assert "  " not in result  # 多餘空白被壓縮


def test_make_content_hash(tmp_path: Path) -> None:
    """測試 make_content_hash 產生雜湊。"""
    hash1 = md_extract_qa.make_content_hash("Hello World")
    hash2 = md_extract_qa.make_content_hash("Hello World")
    hash3 = md_extract_qa.make_content_hash("Different")
    
    assert hash1 == hash2  # 相同內容應該產生相同雜湊
    assert hash1 != hash3  # 不同內容應該產生不同雜湊


def test_is_splitter_line_token(tmp_path: Path) -> None:
    """測試 is_splitter_line token 行。"""
    assert md_extract_qa.is_splitter_line("§align:left") is True
    assert md_extract_qa.is_splitter_line("§stack[minecraft:dirt]") is True
    assert md_extract_qa.is_splitter_line("§rule{...}") is True


def test_is_splitter_line_heading(tmp_path: Path) -> None:
    """測試 is_splitter_line 標題行。"""
    assert md_extract_qa.is_splitter_line("# Title") is True
    assert md_extract_qa.is_splitter_line("## Subtitle") is True


def test_is_splitter_line_yaml(tmp_path: Path) -> None:
    """測試 is_splitter_line YAML。"""
    assert md_extract_qa.is_splitter_line("---") is True


def test_is_translatable_text_line(tmp_path: Path) -> None:
    """測試 is_translatable_text_line 可翻譯行。"""
    assert md_extract_qa.is_translatable_text_line("Hello World") is True
    assert md_extract_qa.is_translatable_text_line("  Text  ") is True


def test_is_translatable_text_line_skip(tmp_path: Path) -> None:
    """測試 is_translatable_text_line 跳過情況。"""
    assert md_extract_qa.is_translatable_text_line("") is False
    assert md_extract_qa.is_translatable_text_line("§token") is False
    assert md_extract_qa.is_translatable_text_line("![alt](url)") is False
    assert md_extract_qa.is_translatable_text_line("<ItemImage />") is False


def test_normalize_blank_lines(tmp_path: Path) -> None:
    """測試 normalize_blank_lines 正規化空行。"""
    result = md_extract_qa.normalize_blank_lines("Line1\n\n\n\nLine2")
    
    assert "\n\n\n" not in result  # 三個以上換行被壓縮


def test_extract_blocks_basic(tmp_path: Path) -> None:
    """測試 extract_blocks 基本功能。"""
    md_text = """# Heading

This is a paragraph.

Another paragraph.
"""
    items = md_extract_qa.extract_blocks(md_text, "test.md", "all")
    
    assert len(items) >= 2  # 標題和至少一個段落


def test_extract_blocks_with_token_lines(tmp_path: Path) -> None:
    """測試 extract_blocks 處理 token 行。"""
    md_text = """§align:left
Paragraph text.
"""
    items = md_extract_qa.extract_blocks(md_text, "test.md", "all")
    
    assert len(items) >= 1


def test_extract_blocks_yaml_frontmatter(tmp_path: Path) -> None:
    """測試 extract_blocks 處理 YAML frontmatter。"""
    md_text = """---
title: Test
---
# Heading

Content.
"""
    items = md_extract_qa.extract_blocks(md_text, "test.md", "all")
    
    # YAML 應該被跳過
    assert any("title" not in item.text for item in items)


def test_extract_blocks_lang_filter(tmp_path: Path) -> None:
    """測試 extract_blocks 語言過濾。"""
    md_text = """# English Title

English content.

中文內容。
"""
    # 過濾非中文
    items = md_extract_qa.extract_blocks(md_text, "test.md", "non_cjk_only")
    
    # 應該只有英文內容
    for item in items:
        assert not md_extract_qa.contains_cjk(item.text)


def test_build_pending_json(tmp_path: Path) -> None:
    """測試 build_pending_json 建立 JSON。"""
    items = [
        md_extract_qa.BlockItem(
            id="test.md:1-2",
            text="Hello",
            content_hash="abc123",
            start_line=1,
            end_line=2,
        )
    ]
    
    result = md_extract_qa.build_pending_json("test.md", Path("test.md"), items, "all")
    
    assert result["schema"] == "md_pending_blocks_v1"
    assert result["source_md"] == "test.md"
    assert len(result["items"]) == 1


def test_has_allowed_lang_segment(tmp_path: Path) -> None:
    """測試 has_allowed_lang_segment 語系段落檢測。"""
    assert md_extract_qa.has_allowed_lang_segment(Path("config/en_us/file.md")) is True
    assert md_extract_qa.has_allowed_lang_segment(Path("config/zh_tw/file.md")) is True
    assert md_extract_qa.has_allowed_lang_segment(Path("config/_en_us/file.md")) is True
    assert md_extract_qa.has_allowed_lang_segment(Path("config/folder/file.md")) is False


def test_detect_lang_segment(tmp_path: Path) -> None:
    """測試 detect_lang_segment 偵測語系。"""
    assert md_extract_qa.detect_lang_segment(["en_us"]) == "en_us"
    assert md_extract_qa.detect_lang_segment(["zh_tw"]) == "zh_tw"
    assert md_extract_qa.detect_lang_segment(["_EN_US"]) == "en_us"
    assert md_extract_qa.detect_lang_segment(["folder"]) is None


def test_map_rel_lang_path(tmp_path: Path) -> None:
    """測試 map_rel_lang_path 路徑映射。"""
    result = md_extract_qa.map_rel_lang_path("config/en_us/file.md", "en_us", "zh_tw")
    
    assert "zh_tw" in result
    assert "en_us" not in result


def test_map_rel_lang_path_with_prefix(tmp_path: Path) -> None:
    """測試 map_rel_lang_path 帶前綴。"""
    result = md_extract_qa.map_rel_lang_path("config/_en_us/file.md", "en_us", "zh_tw")
    
    assert "_zh_tw" in result
