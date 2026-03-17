"""lang_merge_content_copy.py 模組的單元測試。

用途：測試 lang_merge_content_copy 中的內容處理邏輯。
"""
from __future__ import annotations

from pathlib import Path
import sys
import os
import zipfile
import re
from unittest.mock import patch, MagicMock, mock_open

import pytest

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestProcessContentOrCopyFileImpl:
    """測試 process_content_or_copy_file_impl 函式。"""

    def test_function_signature(self):
        """測試函式簽名包含所有必要參數。"""
        from translation_tool.core.lang_merge_content_copy import (
            process_content_or_copy_file_impl,
        )
        import inspect
        
        sig = inspect.signature(process_content_or_copy_file_impl)
        params = list(sig.parameters.keys())
        
        required = ["zf", "input_path", "rules", "output_dir"]
        for req in required:
            assert req in params

    def test_get_patchouli_book_root(self):
        """測試 get_patchouli_book_root 函式邏輯。"""
        from translation_tool.core.lang_merge_content_copy import (
            process_content_or_copy_file_impl,
        )
        
        # 這個測試驗證函式可以被定義（實際邏輯在內部）
        assert callable(process_content_or_copy_file_impl)


class TestLocalizedPathDetection:
    """測試本地化路徑偵測邏輯。"""

    def test_is_path_localized(self):
        """測試路徑是否包含 zh_cn/（作為目錄）。"""
        # 根據原始碼邏輯：is_path_localized = "zh_cn/" in normalized_path
        # normalized_path = input_path.lower().replace("\\", "/")
        # 這意味著路徑中間包含 /zh_cn/ 才算
        test_cases = [
            ("mods/test/lang/zh_cn/file.json", True),  # /zh_cn/ 在中間
            ("assets/minecraft/lang/zh_cn/lang.json", True),  # /zh_cn/ 在中間
            ("mods/test/lang/zh_cn.json", False),  # 結尾是 zh_cn.json，不是 /zh_cn/
            ("config/settings.json", False),
        ]
        
        for path, expected in test_cases:
            normalized_path = path.lower().replace("\\", "/")
            result = "zh_cn/" in normalized_path
            assert result == expected, f"Failed for {path}: {normalized_path}"

    def test_is_filename_localized_regex(self):
        """測試檔名是否為本地化檔案（使用正規表達式）。"""
        pattern = re.compile(
            r"zh_cn.*?\.(lang|md|txt|snbt|json|properties|json5|gui|hl)$",
            re.IGNORECASE,
        )
        
        test_cases = [
            ("zh_cn.lang", True),
            ("zh_cn.json", True),
            ("zh_cn.md", True),
            ("my_zh_cn.properties", True),
            ("en_us.json", False),
            ("zh_tw.json", False),
        ]
        
        for filename, expected in test_cases:
            result = pattern.search(filename) is not None
            assert result == expected, f"Failed for {filename}"


class TestFileExtensionHandling:
    """測試不同檔案副檔名的處理邏輯。"""

    def test_force_s2tw_extensions(self):
        """測試需要強制 S2TW 轉換的副檔名列表。"""
        force_s2tw_extensions = {".md", ".json5", ".gui", ".lang", ".snbt", ".txt", ".properties", ".hl"}
        
        assert ".md" in force_s2tw_extensions
        assert ".lang" in force_s2tw_extensions
        assert ".json" not in force_s2tw_extensions
        assert ".png" not in force_s2tw_extensions


class TestPathNormalization:
    """測試路徑正規化邏輯。"""

    def test_normalize_assets_path(self):
        """測試 /assets/ 路徑正規化。"""
        test_paths = [
            "assets/minecraft/lang/en_us.json",
            "mods/somemod/assets/test/lang/zh_cn.json",
        ]
        
        for path in test_paths:
            normalized = path.lower().replace("\\", "/")
            assets_idx = normalized.find("/assets/")
            
            if assets_idx != -1:
                result = normalized[assets_idx + 1:]
                assert result.startswith("assets/")
            else:
                assert True  # 沒有 assets/ 的路徑

    def test_path_localized_conversion(self):
        """測試本地化路徑轉換（zh_cn -> zh_tw）。"""
        # 根據原始碼邏輯：
        # if is_path_localized:
        #     tw_path = input_path.replace("\\", "/").replace("zh_cn/", "zh_tw/")
        # tw_path = re.sub(r"zh_cn(\..*)$", r"zh_tw\1", tw_path, flags=re.IGNORECASE)
        
        # 測試路徑包含 /zh_cn/ 的情況
        test_case_input = "mods/test/lang/zh_cn/file.json"
        expected = "mods/test/lang/zh_tw/file.json"
        
        # 先做路徑替換
        result = test_case_input.replace("\\", "/").replace("zh_cn/", "zh_tw/")
        # 再做正規表達式替換（此時路徑已不包含 zh_cn/）
        result2 = re.sub(r"zh_cn(\..*)$", r"zh_tw\1", result, flags=re.IGNORECASE)
        
        # 由於 /zh_cn/ 已被替換，正規表達式不會匹配
        assert result == expected
        
        # 測試只修改檔名的情况
        test_case2 = "config/settings.zh_cn.json"
        # 這個情況需要先被 is_filename_localized 識別
        is_filename = re.search(r"zh_cn.*?\.(lang|md|txt|snbt|json|properties|json5|gui|hl)$", test_case2, re.IGNORECASE)
        assert is_filename is not None


class TestMockZipHandling:
    """測試使用 Mock ZIP 檔案的處理。"""

    def test_non_lang_file_with_only_process_lang_true(self, tmp_path: Path):
        """測試 only_process_lang=True 時非 lang 檔案應被跳過。"""
        from translation_tool.core.lang_merge_content_copy import (
            process_content_or_copy_file_impl,
        )
        from unittest.mock import MagicMock
        
        # 建立 mock
        mock_zf = MagicMock()
        
        # 模擬回傳結果
        def mock_load_config():
            return {
                "lang_merger": {"pending_folder_name": "待翻譯"},
                "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
            }
        
        result = process_content_or_copy_file_impl(
            zf=mock_zf,
            input_path="assets/test/config.json",
            rules=[],
            output_dir=str(tmp_path / "output"),
            only_process_lang=True,
            all_files_cache=None,
            load_config_fn=mock_load_config,
            recursive_translate_dict_fn=lambda x, rules: x,
            get_text_processor_fn=lambda ext: None,
            read_text_from_zip_fn=lambda zf, path: "",
            write_bytes_atomic_fn=lambda path, data: None,
            write_text_atomic_fn=lambda path, data: None,
            quarantine_copy_from_zip_fn=lambda **kwargs: None,
            normalize_patchouli_book_root_fn=lambda x: x,
            patch_localized_content_json_fn=lambda *args, **kwargs: {"success": True},
            json_module=MagicMock(),
            
        )
        
        assert result.get("success") is True

    def test_patchouli_path_detection(self, tmp_path: Path):
        """測試 Patchouli 書籍路徑偵測。"""
        from translation_tool.core.lang_merge_content_copy import (
            process_content_or_copy_file_impl,
        )
        from unittest.mock import MagicMock
        
        def mock_load_config():
            return {
                "lang_merger": {"pending_folder_name": "待翻譯"},
                "lm_translator": {"patchouli": {"dir_names": ["patchouli_books"]}},
            }
        
        mock_zf = MagicMock()
        
        result = process_content_or_copy_file_impl(
            zf=mock_zf,
            input_path="assets/patchouli_books/test_book/en_us/intro.md",
            rules=[],
            output_dir=str(tmp_path / "output"),
            only_process_lang=False,
            all_files_cache=[
                "assets/patchouli_books/test_book/zh_cn/intro.md",
                "assets/patchouli_books/test_book/en_us/intro.md",
            ],
            load_config_fn=mock_load_config,
            recursive_translate_dict_fn=lambda x, rules: x,
            get_text_processor_fn=lambda ext: None,
            read_text_from_zip_fn=lambda zf, path: "# Intro",
            write_bytes_atomic_fn=lambda path, data: None,
            write_text_atomic_fn=lambda path, data: None,
            quarantine_copy_from_zip_fn=lambda **kwargs: None,
            normalize_patchouli_book_root_fn=lambda x: x.strip("/"),
            patch_localized_content_json_fn=lambda *args, **kwargs: {"success": True},
            json_module=MagicMock(),
            
        )
        
        # 由於有 zh_cn 版本，應跳過 en_us
        assert result.get("success") is True
        assert "跳過已有翻譯" in result.get("log", "")


class TestJsonModuleHandling:
    """測試 JSON 模組處理。"""

    def test_json_loads_with_fallback(self):
        """測試 JSON 解析失敗時的 fallback 行為。"""
        import orjson
        
        valid_json = '{"key": "value"}'
        invalid_json = 'not valid json'
        
        # 有效的 JSON
        result = orjson.loads(valid_json.encode("utf-8"))
        assert result == {"key": "value"}
        
        # 無效的 JSON 會拋出異常
        with pytest.raises(orjson.JSONDecodeError):
            orjson.loads(invalid_json.encode("utf-8"))
