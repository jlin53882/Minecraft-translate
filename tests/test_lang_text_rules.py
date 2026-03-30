"""translation_tool/plugins/shared/lang_text_rules.py 模組測試。

用途：測試 lang_text_rules 模組的功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.shared import lang_text_rules


def test_strip_fmt_basic(tmp_path: Path) -> None:
    """測試 _strip_fmt 移除格式標記。"""
    # § 格式碼
    assert lang_text_rules._strip_fmt("§aHello") == "Hello"
    assert lang_text_rules._strip_fmt("§lBold§r") == "Bold"

    # & 格式碼
    assert lang_text_rules._strip_fmt("&cRed") == "Red"
    assert lang_text_rules._strip_fmt("&kMagic&r") == "Magic"


def test_strip_fmt_mixed(tmp_path: Path) -> None:
    """測試 _strip_fmt 混合格式碼。"""
    assert lang_text_rules._strip_fmt("§aHello &cWorld") == "Hello World"
    assert lang_text_rules._strip_fmt("&l&oItalic Bold&r") == "Italic Bold"


def test_strip_fmt_no_format(tmp_path: Path) -> None:
    """測試 _strip_fmt 沒有格式碼的情況。"""
    assert lang_text_rules._strip_fmt("Hello World") == "Hello World"
    assert lang_text_rules._strip_fmt("") == ""


def test_is_already_zh_pure_chinese(tmp_path: Path) -> None:
    """測試 is_already_zh 純中文。"""
    assert lang_text_rules.is_already_zh("你好世界") is True
    assert lang_text_rules.is_already_zh("翻譯") is True


def test_is_already_zh_with_english(tmp_path: Path) -> None:
    """測試 is_already_zh 中英文混合。"""
    # 超過 2 個英文字母，應該回傳 False
    assert lang_text_rules.is_already_zh("Hello 你好") is False
    assert lang_text_rules.is_already_zh("Hello World 你好") is False


def test_is_already_zh_minimal_english(tmp_path: Path) -> None:
    """測試 is_already_zh 少於 3 個英文字母視為已翻譯。"""
    # 2 個英文字母以內，應該視為已翻譯
    assert lang_text_rules.is_already_zh("OK 你好") is True
    assert lang_text_rules.is_already_zh("A 你好") is True
    assert lang_text_rules.is_already_zh("你好 OK") is True


def test_is_already_zh_no_chinese(tmp_path: Path) -> None:
    """測試 is_already_zh 沒有中文。"""
    assert lang_text_rules.is_already_zh("Hello World") is False
    # 空字串視為已翻譯（因為沒有內容需要翻譯）
    assert lang_text_rules.is_already_zh("") is True


def test_is_already_zh_with_format_codes(tmp_path: Path) -> None:
    """測試 is_already_zh 包含格式碼。"""
    # 格式碼不應該影響中文檢測
    assert lang_text_rules.is_already_zh("§a你好") is True
    assert lang_text_rules.is_already_zh("&cHello 你好") is False  # Hello 超過 2 個字母
    assert lang_text_rules.is_already_zh("§aOK 你好") is True  # OK 只有 2 個字母


def test_is_already_zh_empty_string(tmp_path: Path) -> None:
    """測試 is_already_zh 空字串。"""
    # 空字串應該視為已翻譯（因為沒有內容需要翻譯）
    assert lang_text_rules.is_already_zh("") is True
    assert lang_text_rules.is_already_zh("   ") is True  # 只有空白


def test_is_already_zh_whitespace(tmp_path: Path) -> None:
    """測試 is_already_zh 只含空白。"""
    assert lang_text_rules.is_already_zh("  ") is True


def test_is_already_zh_edge_cases(tmp_path: Path) -> None:
    """測試 is_already_zh 邊界情況。"""
    # 數字不計入英文字母計數
    assert lang_text_rules.is_already_zh("123 你好") is True

    # 特殊符號不計入
    assert lang_text_rules.is_already_zh("!!! 你好") is True
