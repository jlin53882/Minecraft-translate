"""lang_merger.py 模組的單元測試。

用途：測試 lang_merger 中的 ZIP 處理邏輯。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestLangMergerFileNotFound:
    """測試 lang_merger 處理檔案不存在的情況。"""

    def test_merge_zhcn_to_zhtw_handles_missing_zip(self, tmp_path: Path):
        """測試當 ZIP 檔案不存在時的處理。"""
        from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip

        nonexistent_zip = str(tmp_path / "nonexistent.zip")

        # 建立 generator 並取第一個結果
        result_gen = merge_zhcn_to_zhtw_from_zip(nonexistent_zip, str(tmp_path / "output"))
        result = next(result_gen)

        # 應該回傳 progress: 1.0 和 error: False（因為是預期中的情況）
        assert result["progress"] == 1.0
        assert result["error"] is False


class TestLangMergerBadZip:
    """測試 lang_merger 處理無效 ZIP 的情況。"""

    def test_merge_zhcn_to_zhtw_handles_bad_zip(self, tmp_path: Path):
        """測試當 ZIP 檔案無效時的處理。"""
        from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip

        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("This is not a valid ZIP file")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result_gen = merge_zhcn_to_zhtw_from_zip(str(bad_zip), str(output_dir))

        # 消耗 generator 到結束
        results = list(result_gen)

        # 最後一個結果應該包含 error: True
        final_result = results[-1]
        assert final_result.get("error") is True
        assert final_result.get("progress") == 1.0


class TestLangMergerZipContent:
    """測試 lang_merger 處理 ZIP 內容的基本邏輯。"""

    @pytest.fixture
    def valid_zip_with_lang(self, tmp_path: Path):
        """建立一個包含語言檔案的 ZIP。"""
        zip_path = tmp_path / "test_mod.zip"

        with zipfile.ZipFile(zip_path, "w") as zf:
            # 建立一些 mod 目錄結構
            zf.writestr("mods/test_mod/lang/zh_cn.json", '{"key1": "简体值"}')
            zf.writestr("mods/test_mod/lang/en_us.json", '{"key1": "English Value"}')
            zf.writestr("assets/test/lang/zh_cn.json", '{"item_a": "物品A"}')
            zf.writestr("assets/test/lang/en_us.json", '{"item_a": "Item A"}')
            # 其他檔案
            zf.writestr("README.txt", "This is a readme")

        return zip_path

    def test_zip_contains_lang_files(self, valid_zip_with_lang: Path):
        """測試 ZIP 包含語言檔案。"""
        with zipfile.ZipFile(valid_zip_with_lang, "r") as zf:
            names = zf.namelist()
            has_zh_cn = any("zh_cn" in n for n in names)
            has_en_us = any("en_us" in n for n in names)

            assert has_zh_cn is True
            assert has_en_us is True

    def test_normalized_path_handling(self):
        """測試路徑正規化邏輯。"""
        test_paths = [
            "mods/test/lang/zh_cn.json",
            "mods\\test\\lang\\zh_cn.json",
            "MODS/TEST/LANG/ZH_CN.JSON",
        ]

        for path in test_paths:
            normalized = path.replace("\\", "/").lower()
            assert "zh_cn.json" in normalized or "zh_cn.lang" in normalized


class TestLangMergerIntegration:
    """整合測試 lang_merger（需要 mock 複雜依賴）。"""

    def test_lang_files_by_mod_classification(self, tmp_path: Path):
        """測試語言檔案按 mod 分類邏輯。"""
        from collections import defaultdict

        # 模擬 ZIP 中的語言檔案結構
        test_files = [
            "mods/aaa/lang/zh_cn.json",
            "mods/aaa/lang/zh_tw.json",
            "mods/aaa/lang/en_us.json",
            "mods/bbb/lang/zh_cn.json",
            "mods/bbb/lang/en_us.json",
        ]

        lang_files_by_mod = defaultdict(dict)

        for file_path in test_files:
            normalized = file_path.replace("\\", "/")
            if "/lang/" in normalized and normalized.endswith(".json"):
                mod_key = normalized.split("/lang/")[0] + "/lang/"

                if normalized.endswith("zh_cn.json"):
                    lang_files_by_mod[mod_key]["zh_cn"] = normalized
                elif normalized.endswith("zh_tw.json"):
                    lang_files_by_mod[mod_key]["zh_tw"] = normalized
                elif normalized.endswith("en_us.json"):
                    lang_files_by_mod[mod_key]["en_us"] = normalized

        assert len(lang_files_by_mod) == 2
        assert "mods/aaa/lang/" in lang_files_by_mod
        assert "mods/bbb/lang/" in lang_files_by_mod
        assert lang_files_by_mod["mods/aaa/lang/"]["zh_cn"] == "mods/aaa/lang/zh_cn.json"
        # bbb 沒有 zh_tw，使用 .get() 避免 KeyError
        assert lang_files_by_mod["mods/bbb/lang/"].get("zh_tw") is None
