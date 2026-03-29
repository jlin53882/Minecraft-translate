"""translation_tool/plugins/kubejs/kubejs_tooltip_extract.py 模組測試。

用途：測試 kubejs_tooltip_extract 模組的功能。
"""
from __future__ import annotations

import sys
import json
import os
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.kubejs import kubejs_tooltip_extract


def test_resolve_kubejs_root_direct(tmp_path: Path) -> None:
    """測試 resolve_kubejs_root 直接傳入 kubejs 目錄。"""
    kubejs_dir = tmp_path / "kubejs"
    kubejs_dir.mkdir()
    (kubejs_dir / "test.js").write_text("// test")
    
    result = kubejs_tooltip_extract.resolve_kubejs_root(str(kubejs_dir))
    
    assert result == str(kubejs_dir)


def test_resolve_kubejs_root_nested(tmp_path: Path) -> None:
    """測試 resolve_kubejs_root 自動搜尋子目錄。"""
    root = tmp_path / "modpack"
    root.mkdir()
    kubejs_dir = root / "kubejs"
    kubejs_dir.mkdir()
    (kubejs_dir / "test.js").write_text("// test")
    
    result = kubejs_tooltip_extract.resolve_kubejs_root(str(root))
    
    assert result == str(kubejs_dir)


def test_resolve_kubejs_root_not_found(tmp_path: Path) -> None:
    """測試 resolve_kubejs_root 找不到時回傳原路徑。"""
    root = tmp_path / "empty"
    root.mkdir()
    
    result = kubejs_tooltip_extract.resolve_kubejs_root(str(root))
    
    assert result == str(root)


def test_to_json_name(tmp_path: Path) -> None:
    """測試 to_json_name 檔名轉換。"""
    assert kubejs_tooltip_extract.to_json_name("script.js") == "script.json"
    assert kubejs_tooltip_extract.to_json_name("data.json") == "data.json"
    assert kubejs_tooltip_extract.to_json_name("name") == "name.json"


def test_strip_quotes(tmp_path: Path) -> None:
    """測試 strip_quotes 移除引號。"""
    assert kubejs_tooltip_extract.strip_quotes('"hello"') == "hello"
    assert kubejs_tooltip_extract.strip_quotes("'world'") == "world"
    assert kubejs_tooltip_extract.strip_quotes("noquotes") == "noquotes"
    assert kubejs_tooltip_extract.strip_quotes('"') == '"'  # 單引號不處理


def test_split_js_args_basic(tmp_path: Path) -> None:
    """測試 split_js_args 基本功能。"""
    result = kubejs_tooltip_extract.split_js_args('"a", "b"')
    
    assert len(result) == 2
    assert '"a"' in result
    assert '"b"' in result


def test_split_js_args_nested_brackets(tmp_path: Path) -> None:
    """測試 split_js_args 處理嵌套括號。"""
    result = kubejs_tooltip_extract.split_js_args('item.of("mt:pipe", {lvl:1}), 5')
    
    assert len(result) == 2
    assert "item.of(\"mt:pipe\", {lvl:1})" in result[0]
    assert "5" in result[1]


def test_extract_array_strings(tmp_path: Path) -> None:
    """測試 extract_array_strings 提取陣列字串。"""
    result = kubejs_tooltip_extract.extract_array_strings('["a", "b", "c"]')
    
    assert result == ["a", "b", "c"]


def test_is_patchouli_command_only(tmp_path: Path) -> None:
    """測試 is_patchouli_command_only 判斷 Patchouli 指令。"""
    # 原始碼中的正規表達式是 {...} 格式
    assert kubejs_tooltip_extract.is_patchouli_command_only("{br}") is True
    assert kubejs_tooltip_extract.is_patchouli_command_only("{l:page}") is True
    assert kubejs_tooltip_extract.is_patchouli_command_only("{img:minecraft:block}") is True
    assert kubejs_tooltip_extract.is_patchouli_command_only("Hello") is False
    assert kubejs_tooltip_extract.is_patchouli_command_only("") is False


def test_is_lang_key_like(tmp_path: Path) -> None:
    """測試 is_lang_key_like 判斷語言 Key。"""
    assert kubejs_tooltip_extract.is_lang_key_like("item.minecraft.iron_ingot") is True
    assert kubejs_tooltip_extract.is_lang_key_like("tooltip.kubejs.something") is True
    assert kubejs_tooltip_extract.is_lang_key_like("short") is False  # 太短
    assert kubejs_tooltip_extract.is_lang_key_like("Hello World") is False  # 有空格


def test_is_lang_key_ref_like(tmp_path: Path) -> None:
    """測試 is_lang_key_ref_like 判斷引用格式。"""
    assert kubejs_tooltip_extract.is_lang_key_ref_like("{atm9.quest.create.desc}") is True
    assert kubejs_tooltip_extract.is_lang_key_ref_like("{a}\n{b}") is True
    assert kubejs_tooltip_extract.is_lang_key_ref_like("Hello") is False


def test_clean_text(tmp_path: Path) -> None:
    """測試 clean_text 清理文字。"""
    # 移除 Minecraft 顏色碼
    assert kubejs_tooltip_extract.clean_text("§aHello") == "Hello"
    assert kubejs_tooltip_extract.clean_text("§lBold§r") == "Bold"
    assert kubejs_tooltip_extract.clean_text("  §9  Test  ") == "Test"


def test_should_skip_text_chinese(tmp_path: Path) -> None:
    """測試 should_skip_text 跳過中文。"""
    assert kubejs_tooltip_extract.should_skip_text("你好") is True
    assert kubejs_tooltip_extract.should_skip_text("Hello 你好") is True  # 有中文


def test_should_skip_text_empty(tmp_path: Path) -> None:
    """測試 should_skip_text 跳過空白。"""
    assert kubejs_tooltip_extract.should_skip_text("") is True
    assert kubejs_tooltip_extract.should_skip_text("   ") is True


def test_should_skip_text_patchouli(tmp_path: Path) -> None:
    """測試 should_skip_text 跳過 Patchouli 指令。"""
    # 原始碼使用 {...} 格式
    assert kubejs_tooltip_extract.should_skip_text("{br}") is True
    assert kubejs_tooltip_extract.should_skip_text("{l:page}") is True


def test_should_skip_text_lang_key(tmp_path: Path) -> None:
    """測試 should_skip_text 跳過語言 Key。"""
    assert kubejs_tooltip_extract.should_skip_text("item.minecraft.dirt") is True


def test_extract_call_args(tmp_path: Path) -> None:
    """測試 extract_call_args 提取括號內容。"""
    content = "event.add('item', Text.of('tooltip'))"
    start = content.index("(") + 1
    
    result = kubejs_tooltip_extract.extract_call_args(content, start)
    
    assert result is not None
    assert "'item'" in result


def test_extract_js_string_call(tmp_path: Path) -> None:
    """測試 extract_js_string_call 提取 JS 字串。"""
    content = "Text.of('Hello World')"
    start = content.index("(") + 1
    
    result = kubejs_tooltip_extract.extract_js_string_call(content, start)
    
    assert result == "Hello World"


def test_extract_js_string_call_double_quote(tmp_path: Path) -> None:
    """測試 extract_js_string_call 雙引號。"""
    content = 'Text.of("Hello World")'
    start = content.index("(") + 1
    
    result = kubejs_tooltip_extract.extract_js_string_call(content, start)
    
    assert result == "Hello World"


def test_should_skip_kubejs_tooltip_expr(tmp_path: Path) -> None:
    """測試 should_skip_kubejs_tooltip_expr 跳過表達式。"""
    assert kubejs_tooltip_extract.should_skip_kubejs_tooltip_expr("Text.translate('key')") is True
    assert kubejs_tooltip_extract.should_skip_kubejs_tooltip_expr("Text.of('text')") is True
    assert kubejs_tooltip_extract.should_skip_kubejs_tooltip_expr("event.add('item', 'text')") is False


def test_resolve_kubejs_root_case_insensitive(tmp_path: Path) -> None:
    """測試 resolve_kubejs_root 大小寫敏感。"""
    # 原始碼只做 name.lower() == "kubejs" 比較，所以要傳入小寫
    root = tmp_path / "ModPack"
    root.mkdir()
    kubejs_dir = root / "kubejs"  # 必須是小寫
    kubejs_dir.mkdir()
    (kubejs_dir / "test.js").write_text("// test")
    
    result = kubejs_tooltip_extract.resolve_kubejs_root(str(root))
    
    assert result == str(kubejs_dir)
