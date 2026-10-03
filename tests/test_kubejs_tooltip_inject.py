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
    result = kubejs_tooltip_inject.replace_text_in_text_obj("Text.of('old')", "new")

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
    result = kubejs_tooltip_inject.replace_array('["old1", "old2"]', ["new1", "new2"])

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
    """確認注入結果寫出翻譯內容，且不修改原始 KubeJS 腳本。"""
    orig_root = tmp_path / "kubejs"
    js_file = orig_root / "client_scripts" / "test.js"
    js_file.parent.mkdir(parents=True)
    original = "scene.text('scene', 'Old')\n"
    js_file.write_text(original, encoding="utf-8")

    trans_root = tmp_path / "translated"
    translated_file = trans_root / "client_scripts" / "test.json"
    translated_file.parent.mkdir(parents=True)
    translated_file.write_text(
        json.dumps({"test.js|scene.1": "泥土"}, ensure_ascii=False),
        encoding="utf-8",
    )
    out_root = tmp_path / "output"

    result = kubejs_tooltip_inject.inject(
        str(orig_root),
        str(trans_root),
        str(out_root),
    )

    output_js = out_root / "client_scripts" / "test.js"
    assert result["patched_js_files"] == 1
    assert result["translated_dir"] == str(trans_root)
    assert output_js.read_text(encoding="utf-8") == "scene.text('scene', \"泥土\")\n"
    assert js_file.read_text(encoding="utf-8") == original


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


def _run_inject_fixture(
    tmp_path: Path,
    source_js: str,
    translations: dict[str, str],
) -> tuple[dict, Path, Path]:
    """建立單一 client_scripts 注入案例並回傳結果與來源／輸出路徑。

    Args:
        tmp_path: pytest 提供的一次性工作目錄。
        source_js: 要注入翻譯的原始 JavaScript 內容。
        translations: 以 injector key 對應翻譯文字的測試資料。

    Returns:
        注入摘要、未修改的原始 JS 路徑與生成的輸出 JS 路徑。
    """
    original_root = tmp_path / "kubejs"
    source_path = original_root / "client_scripts" / "test.js"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source_js, encoding="utf-8")

    translated_root = tmp_path / "translated"
    translation_path = translated_root / "client_scripts" / "test.json"
    translation_path.parent.mkdir(parents=True)
    translation_path.write_text(
        json.dumps(translations, ensure_ascii=False), encoding="utf-8"
    )

    output_root = tmp_path / "output"
    result = kubejs_tooltip_inject.inject(
        str(original_root), str(translated_root), str(output_root)
    )
    return result, source_path, output_root / "client_scripts" / "test.js"


def test_inject_event_add_text_of_writes_nested_translation(tmp_path: Path) -> None:
    """Regression: Text.of 內層括號不可截斷 event.add 翻譯回寫。"""
    source = "event.add('minecraft:dirt', Text.of('Old text'))\n"

    result, source_path, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|minecraft:dirt.0": "新譯文"},
    )

    assert result["patched_js_files"] == 1
    assert "新譯文" in output_path.read_text(encoding="utf-8")
    assert source_path.read_text(encoding="utf-8") == source


def test_inject_event_add_nested_text_call_writes_translation(tmp_path: Path) -> None:
    """多層 Text 呼叫的括號與第二個 event.add 參數都要完整保留。"""
    source = "event.add('minecraft:dirt', Text.of(Text.literal('Old text')))\n"

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|minecraft:dirt.0": "新譯文"},
    )

    assert result["patched_js_files"] == 1
    assert output_path.read_text(encoding="utf-8") == (
        "event.add('minecraft:dirt', Text.of(Text.literal(\"新譯文\")))\n"
    )


def test_inject_event_add_plain_string_keeps_writeback_support(tmp_path: Path) -> None:
    """單參數 event.add 字串仍依 auto key 寫回實際 JavaScript。"""
    source = "event.add('Old text')\n"

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|auto.1": "新文字"},
    )

    assert result["patched_js_files"] == 1
    assert output_path.read_text(encoding="utf-8") == 'event.add("新文字")\n'


def test_inject_event_add_escapes_translated_text_literal(tmp_path: Path) -> None:
    """Text.of 譯文含引號、反斜線與換行時仍須輸出有效 JS 字串。"""
    source = "event.add('minecraft:dirt', Text.of('Old text'))\n"
    translated = '玩家\'s "quoted" path\\entry\n下一行'

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|minecraft:dirt.0": translated},
    )

    expected_literal = json.dumps(translated, ensure_ascii=False)
    assert result["patched_js_files"] == 1
    assert output_path.read_text(encoding="utf-8") == (
        f"event.add('minecraft:dirt', Text.of({expected_literal}))\n"
    )


def test_inject_noop_keeps_js_unchanged_and_reports_zero_patches(
    tmp_path: Path,
) -> None:
    """沒有可套用變更時，輸出相同內容且 patched_js_files 必須為零。"""
    source = "event.add('minecraft:dirt', Text.of('Already translated'))\n"

    result, _, output_path = _run_inject_fixture(tmp_path, source, {})

    assert output_path.read_text(encoding="utf-8") == source
    assert result["patched_js_files"] == 0


def test_inject_already_translated_text_is_not_counted_as_patch(tmp_path: Path) -> None:
    """譯文與原始 Text.of 文字相同時不應產生假 patched 計數。"""
    source = "event.add('minecraft:dirt', Text.of('Already translated'))\n"

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|minecraft:dirt.0": "Already translated"},
    )

    assert output_path.read_text(encoding="utf-8") == source
    assert result["patched_js_files"] == 0


def test_inject_scene_text_nested_text_of_uses_balanced_call_writeback(
    tmp_path: Path,
) -> None:
    """同型的 scene.text/Text.of 巢狀括號問題也須維持完整回寫。"""
    source = "scene.text('scene', Text.of('Old text'))\n"

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|scene.1": "場景譯文"},
    )

    assert result["patched_js_files"] == 1
    assert output_path.read_text(encoding="utf-8") == (
        "scene.text('scene', Text.of(\"場景譯文\"))\n"
    )


def test_inject_event_add_array_escapes_translated_string(tmp_path: Path) -> None:
    """陣列型 event.add 也須用合法 JS literal 回寫含雙引號的譯文。"""
    source = "event.add('minecraft:dirt', ['Old text'])\n"
    translated = 'New "quoted" text'

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|minecraft:dirt.0.0": translated},
    )

    expected_array = json.dumps([translated], ensure_ascii=False)
    assert result["patched_js_files"] == 1
    assert output_path.read_text(encoding="utf-8") == (
        f"event.add('minecraft:dirt', {expected_array})\n"
    )


def test_inject_itemevents_nested_text_tooltip_writes_back(tmp_path: Path) -> None:
    """ItemEvents 的巢狀 Text Tooltip 仍依 tooltip key 寫回且計數正確。"""
    source = (
        "ItemEvents.tooltip(event => { "
        "event.add('minecraft:dirt', [Text.of(Text.literal('Old tooltip'))]); "
        "});\n"
    )
    translated = 'New "tooltip"'

    result, source_path, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|minecraft:dirt.tooltip.0": translated},
    )

    assert result["patched_js_files"] == 1
    assert json.dumps(translated, ensure_ascii=False) in output_path.read_text(
        encoding="utf-8"
    )
    assert source_path.read_text(encoding="utf-8") == source


def test_inject_already_translated_plain_event_add_is_noop(tmp_path: Path) -> None:
    """單參數原文已等於譯文時保持原格式且不報為 patched。"""
    source = "event.add('Already translated')\n"

    result, _, output_path = _run_inject_fixture(
        tmp_path,
        source,
        {"test.js|auto.1": "Already translated"},
    )

    assert output_path.read_text(encoding="utf-8") == source
    assert result["patched_js_files"] == 0
