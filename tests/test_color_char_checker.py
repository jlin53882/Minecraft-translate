"""color_char_checker.py 模組的單元測試。

用途：測試 KubeJS 翻譯檔案中的非法顏色字元檢查邏輯。
Minecraft formatting codes: 0-9, a-f (顏色), k-o (格式), r (reset)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.checkers.color_char_checker import (  # noqa: E402
    COLOR_PATTERN,
    check_color_chars,
    _check_value,
    check_json_file,
    check_directory,
)


class TestColorPattern:
    """測試 COLOR_PATTERN 正則表達式。

    Minecraft 合法格式化代碼：
    - 0-9: 數字（顏色）
    - a-f: 顏色代碼
    - k-o: 格式化代碼（粗體、斜體、刪除線等）
    - r: 重置

    非法代碼：g, h, i, j, p, q, s, t, u, w, x, y, z，以及任何非 ASCII 字元
    """

    def test_valid_color_codes_allowed(self):
        """合法的顏色代碼 0-9, a-f 不應被認定為非法。"""
        valid_chars = "0123456789abcdef"
        for ch in valid_chars:
            text = f"Hello &{ch}world"
            assert COLOR_PATTERN.search(text) is None, f"&{ch} should be valid"

    def test_valid_formatting_codes_allowed(self):
        """合法的格式化代碼 k-o, r 不應被認定為非法。"""
        valid_chars = "klmnor"
        for ch in valid_chars:
            text = f"Hello &{ch}bold"
            assert COLOR_PATTERN.search(text) is None, f"&{ch} should be valid"

    def test_invalid_wxyz_rejected(self):
        """非法代碼 w, x, y, z 應被認定為錯誤。"""
        invalid_chars = "wxyz"
        for ch in invalid_chars:
            text = f"Hello &{ch}invalid"
            match = COLOR_PATTERN.search(text)
            assert match is not None, f"&{ch} should be flagged as invalid"
            assert match.group(1) == ch

    def test_invalid_gijmpqstu_rejected(self):
        """其他非法代碼（g, h, i, j, p, q, s, t, u）應被認定為錯誤。

        注意：k-o（含 l/m/n/o）與 r 都是合法的 Minecraft 格式化代碼。
        """
        invalid_chars = "ghijpqstu"
        for ch in invalid_chars:
            text = f"Test &{ch}text"
            match = COLOR_PATTERN.search(text)
            assert match is not None, f"&{ch} should be flagged as invalid"
            assert match.group(1) == ch

    def test_multiple_illegal_chars_all_found(self):
        """多個非法字元應全部被發現。"""
        text = "&w &x &y &z"
        matches = COLOR_PATTERN.findall(text)
        assert sorted(matches) == sorted(["w", "x", "y", "z"])


class TestCheckColorChars:
    """測試 check_color_chars 函式。"""

    def test_no_errors_returns_none(self):
        """無錯誤時回傳 None。"""
        result = check_color_chars("Hello &aworld &6color")
        assert result is None

    def test_single_error_returns_list(self):
        """單一錯誤時回傳包含錯誤的 list。"""
        result = check_color_chars("Hello &zbad")
        assert result is not None
        assert len(result) == 1
        assert result[0].illegal_char == "z"
        assert result[0].position == 6

    def test_multiple_errors(self):
        """多個錯誤時全部回傳。"""
        result = check_color_chars("&w&x&y&z")
        assert result is not None
        assert len(result) == 4
        assert [e.illegal_char for e in result] == ["w", "x", "y", "z"]

    def test_error_position_correct(self):
        """錯誤位置應準確。"""
        result = check_color_chars("prefix &w illegal")
        assert result is not None
        assert result[0].position == 7  # "&" 的位置

    def test_error_message_format(self):
        """錯誤訊息格式應正確。"""
        result = check_color_chars("&w")
        assert result is not None
        assert "0-9, a-f, k-o, r" in result[0].message
        assert "&w" in result[0].message

    def test_empty_string_no_errors(self):
        """空字串無錯誤。"""
        assert check_color_chars("") is None
        assert check_color_chars("   ") is None

    def test_valid_formatting_preserved(self):
        """合法格式代碼不應引發錯誤。"""
        # 粗體、斜體、亂碼等
        valid_texts = [
            "&k random",
            "&l bold",
            "&m strike",
            "&n underline",
            "&o italic",
            "&r reset",
            "&0 black",
            "&f white",
        ]
        for text in valid_texts:
            assert check_color_chars(text) is None, f"{text} should be valid"


class TestCheckValue:
    """測試 _check_value 遞迴檢查邏輯。"""

    def test_nested_dict_key_path(self):
        """Nested dict 應報告完整的 key path。

        當從 check_json_file 呼叫時（parent_path=""）：
        _check_value("test.json", "parent", {"child": "&z invalid"}, parent_path="")
        報告的 nested path 為 parent.child。

        測試直接呼叫 _check_value 時的巢狀路徑行為（使用空 parent_path 模擬頂層呼叫）。
        """
        data = {"child": "&z invalid"}
        # parent_path="" 表示從 check_json_file 的頂層呼叫
        errors = list(_check_value("test.json", "parent", data, parent_path=""))
        assert len(errors) == 1
        # 完整的 nested path：parent.child（不含 entry key 前綴）
        assert errors[0].key == "parent.child"

    def test_deeply_nested_dict(self):
        """深度巢狀 dict 應報告完整路徑。"""
        data = {"a": {"b": {"c": "&x deep"}}}
        errors = list(_check_value("test.json", "root", data))
        assert len(errors) == 1
        assert errors[0].key == "root.a.b.c"

    def test_list_item_index_in_key(self):
        """List 中的元素應包含索引。"""
        data = {"items": ["&w item0", "ok item1"]}
        errors = list(_check_value("test.json", "root", data))
        assert len(errors) == 1
        assert "items[0]" in errors[0].key

    def test_dict_inside_list(self):
        """List 中的 dict 應正確報告路徑。"""
        data = {"list": [{"key": "&y bad"}]}
        errors = list(_check_value("test.json", "root", data))
        assert len(errors) == 1
        assert "list[0].key" in errors[0].key

    def test_mixed_valid_invalid(self):
        """混合有效/無效值時，只回報無效的。"""
        data = {
            "valid": "&a ok",
            "invalid": "&w bad",
        }
        errors = list(_check_value("test.json", "root", data))
        assert len(errors) == 1
        assert errors[0].key == "root.invalid"
        assert errors[0].illegal_char == "w"

    def test_string_value_file_path_propagated(self):
        """file_path 應傳播到所有錯誤。"""
        data = {"key": "&z bad"}
        errors = list(_check_value("/path/to/file.json", "root", data))
        assert all(e.file_path == "/path/to/file.json" for e in errors)


class TestCheckJsonFile:
    """測試 check_json_file 函式。"""

    def test_valid_file_no_errors(self, tmp_path: Path):
        """有效的 JSON 檔（無非法顏色字元）應無錯誤。"""
        file_path = tmp_path / "valid.json"
        file_path.write_text(json.dumps({"key": "Hello &aworld"}), "utf-8")
        errors = list(check_json_file(str(file_path)))
        assert len(errors) == 0

    def test_invalid_color_char_reported(self, tmp_path: Path):
        """包含非法顏色字元的檔案應報告錯誤。"""
        file_path = tmp_path / "invalid.json"
        file_path.write_text(json.dumps({"key": "Hello &zbad"}), "utf-8")
        errors = list(check_json_file(str(file_path)))
        assert len(errors) == 1
        assert errors[0].file_path == str(file_path)
        assert errors[0].key == "key"

    def test_malformed_json_skipped(self, tmp_path: Path):
        """格式錯誤的 JSON 不應阻斷（靜默跳過）。"""
        file_path = tmp_path / "malformed.json"
        file_path.write_text("{invalid json", "utf-8")
        errors = list(check_json_file(str(file_path)))
        assert len(errors) == 0  # 不阻断

    def test_nested_json_structure(self, tmp_path: Path):
        """巢狀 JSON 結構應正確檢查。"""
        file_path = tmp_path / "nested.json"
        nested = {"top": {"middle": {"bottom": "&x bad"}}}
        file_path.write_text(json.dumps(nested), "utf-8")
        errors = list(check_json_file(str(file_path)))
        assert len(errors) == 1
        assert "top.middle.bottom" in errors[0].key


class TestCheckDirectory:
    """測試 check_directory 函式。"""

    def test_multiple_files_checked(self, tmp_path: Path):
        """目錄下多個檔案應全部檢查。"""
        (tmp_path / "file1.json").write_text(json.dumps({"k": "&w bad"}), "utf-8")
        (tmp_path / "file2.json").write_text(json.dumps({"k": "&y bad"}), "utf-8")
        (tmp_path / "file3.json").write_text(json.dumps({"k": "&a ok"}), "utf-8")

        errors = list(check_directory(str(tmp_path)))
        assert len(errors) == 2
        file_paths = {e.file_path for e in errors}
        assert len(file_paths) == 2

    def test_subdirectory_files_checked(self, tmp_path: Path):
        """子目錄中的檔案也應檢查。"""
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "nested.json").write_text(json.dumps({"key": "&z bad"}), "utf-8")

        errors = list(check_directory(str(tmp_path)))
        assert len(errors) == 1
        assert "nested.json" in errors[0].file_path

    def test_non_json_files_ignored(self, tmp_path: Path):
        """非 .json 檔案應忽略。"""
        (tmp_path / "file.txt").write_text("not json &w", "utf-8")
        (tmp_path / "file.json").write_text(json.dumps({"key": "&w bad"}), "utf-8")

        errors = list(check_directory(str(tmp_path)))
        assert len(errors) == 1
        assert errors[0].file_path.endswith("file.json")
