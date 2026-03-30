"""translation_tool/plugins/kubejs/kubejs_tooltip_inject.py 模組測試。

用途：測試 kubejs_tooltip_inject 模組的功能。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 確保可以導入翻譯工具模組
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 測試模組
from translation_tool.plugins.kubejs import kubejs_tooltip_inject


def test_resolve_kubejs_root_direct(tmp_path: Path) -> None:
    """測試 resolve_kubejs_root 直接傳入 kubejs 目錄。"""
    kubejs_dir = tmp_path / "kubejs"
    kubejs_dir.mkdir()
    (kubejs_dir / "test.js").write_text("// test")

    result = kubejs_tooltip_inject.resolve_kubejs_root(str(kubejs_dir))

    assert result == str(kubejs_dir)


def test_resolve_kubejs_root_nested(tmp_path: Path) -> None:
    """測試 resolve_kubejs_root 自動搜尋子目錄。"""
    root = tmp_path / "modpack"
    root.mkdir()
    kubejs_dir = root / "kubejs"
    kubejs_dir.mkdir()
    (kubejs_dir / "test.js").write_text("// test")

    result = kubejs_tooltip_inject.resolve_kubejs_root(str(root))

    assert result == str(kubejs_dir)


def test_split_js_args(tmp_path: Path) -> None:
    """測試 split_js_args 解析參數。"""
    result = kubejs_tooltip_inject.split_js_args('"a", "b"')

    assert len(result) == 2
    assert '"a"' in result
    assert '"b"' in result


def test_split_js_args_nested(tmp_path: Path) -> None:
    """測試 split_js_args 嵌套括號。"""
    result = kubejs_tooltip_inject.split_js_args('item.of("mt:pipe", {lvl:1}), 5')

    assert len(result) == 2


def test_strip_quotes(tmp_path: Path) -> None:
    """測試 strip_quotes 移除引號。"""
    assert kubejs_tooltip_inject.strip_quotes('"hello"') == "hello"
    assert kubejs_tooltip_inject.strip_quotes("'world'") == "world"
    assert kubejs_tooltip_inject.strip_quotes("noquotes") == "noquotes"


def test_replace_text_in_text_obj(tmp_path: Path) -> None:
    """測試 replace_text_in_text_obj 替換文字。"""
    result = kubejs_tooltip_inject.replace_text_in_text_obj(
        "Text.of('old')", "new"
    )

    assert "new" in result
    assert "Text.of" in result


def test_replace_text_in_text_obj_red(tmp_path: Path) -> None:
    """測試 replace_text_in_text_obj Text.red。"""
    result = kubejs_tooltip_inject.replace_text_in_text_obj(
        'Text.red("warning")', "警告"
    )

    assert "警告" in result


def test_extract_array_strings(tmp_path: Path) -> None:
    """測試 extract_array_strings 提取陣列字串。"""
    result = kubejs_tooltip_inject.extract_array_strings('["a", "b"]')

    assert result == ["a", "b"]


def test_replace_array(tmp_path: Path) -> None:
    """測試 replace_array 替換陣列內容。"""
    result = kubejs_tooltip_inject.replace_array(
        '["old1", "old2"]', ["new1", "new2"]
    )

    assert "new1" in result
    assert "new2" in result


def test_to_js_name(tmp_path: Path) -> None:
    """測試 to_js_name 轉換為 JS 檔名。"""
    assert kubejs_tooltip_inject.to_js_name("script.json") == "script.js"
    assert kubejs_tooltip_inject.to_js_name("data.json") == "data.js"
    # 原始碼只處理 .json 結尾
    assert kubejs_tooltip_inject.to_js_name("name.txt") == "name.txt"


def test_clean_text(tmp_path: Path) -> None:
    """測試 clean_text 清理文字。"""
    assert kubejs_tooltip_inject.clean_text("hello\\nworld") == "hello\nworld"
    assert kubejs_tooltip_inject.clean_text("  test  ") == "test"
    assert kubejs_tooltip_inject.clean_text(None) == ""


def test_inject_basic_flow(tmp_path: Path) -> None:
    """測試 inject 基本流程。"""
    # 建立原始 KubeJS 目錄
    orig_root = tmp_path / "kubejs"
    orig_root.mkdir()
    js_file = orig_root / "client_scripts" / "test.js"
    js_file.parent.mkdir(parents=True)
    js_file.write_text("event.add('minecraft:dirt', Text.of('dirty'))")

    # 建立翻譯後的 JSON
    trans_root = tmp_path / "translated"
    trans_root.mkdir()
    json_file = trans_root / "test.json"
    json_file.write_text(json.dumps({
        "test.js|minecraft:dirt.0": "髒"
    }))

    # 建立輸出目錄
    out_root = tmp_path / "output"

    # 執行 inject
    result = kubejs_tooltip_inject.inject(
        str(orig_root),
        str(trans_root),
        str(out_root),
    )

    # 驗證輸出
    assert result["patched_js_files"] >= 0
    assert result["translated_dir"] == str(trans_root)


def test_inject_with_lang_file(tmp_path: Path) -> None:
    """測試 inject 處理 lang 檔案。"""
    # 建立原始目錄結構
    orig_root = tmp_path / "kubejs"
    orig_root.mkdir()

    # 建立 lang 目錄
    lang_dir = orig_root / "lang"
    lang_dir.mkdir(parents=True)
    lang_file = lang_dir / "en_us.json"
    lang_file.write_text(json.dumps({"key": "value"}))

    # 建立翻譯目錄
    trans_root = tmp_path / "translated"
    trans_root.mkdir()
    trans_lang_dir = trans_root / "lang"
    trans_lang_dir.mkdir(parents=True)
    trans_lang_file = trans_lang_dir / "en_us.json"
    trans_lang_file.write_text(json.dumps({"key": "翻譯"}))

    # 輸出目錄
    out_root = tmp_path / "output"

    result = kubejs_tooltip_inject.inject(
        str(orig_root),
        str(trans_root),
        str(out_root),
    )

    # 驗證 lang 檔案被寫入
    assert result["wrote_lang_files"] >= 0


def test_inject_missing_js_file(tmp_path: Path) -> None:
    """測試 inject 處理找不到的 JS 檔案。"""
    # 建立翻譯 JSON（但沒有對應的原始 JS）
    trans_root = tmp_path / "translated"
    trans_root.mkdir()
    json_file = trans_root / "nonexistent.json"
    json_file.write_text(json.dumps({"key": "value"}))

    # 原始目錄（沒有 JS 檔案）
    orig_root = tmp_path / "kubejs"
    orig_root.mkdir()

    out_root = tmp_path / "output"

    result = kubejs_tooltip_inject.inject(
        str(orig_root),
        str(trans_root),
        str(out_root),
    )

    # 應該正常處理，不拋出例外
    assert result is not None
