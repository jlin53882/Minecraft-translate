"""測試 jar_processor.py - JAR 處理器入口。

用途：測試 jar_processor.py 的主要導出函數與常數。
"""

import pytest
import re
import os
import zipfile
import tempfile
from pathlib import Path

from translation_tool.core.jar_processor import (
    find_jar_files,
    extract_lang_files_generator,
    extract_book_files_generator,
    preview_extraction_generator,
    ExtractionSummary,
    generate_preview_report,
    BOOK_PATH_REGEX_DUAL_STRUCTURE,
    LANG_CODES,
    lang_pattern,
)


class TestJarProcessorExports:
    """測試 jar_processor 導出的常數與函數"""

    def test_lang_codes_defined(self):
        """測試 LANG_CODES 有定義"""
        assert "en_us" in LANG_CODES
        assert "zh_tw" in LANG_CODES
        assert "zh_cn" in LANG_CODES
        assert len(LANG_CODES) == 3

    def test_lang_pattern_defined(self):
        """測試 lang_pattern 有定義"""
        assert lang_pattern is not None
        assert isinstance(lang_pattern, str)

    def test_book_regex_defined(self):
        """測試 BOOK_PATH_REGEX_DUAL_STRUCTURE 有定義"""
        assert BOOK_PATH_REGEX_DUAL_STRUCTURE is not None
        assert isinstance(BOOK_PATH_REGEX_DUAL_STRUCTURE, re.Pattern)


class TestExtractLangFilesGenerator:
    """測試 extract_lang_files_generator 函數"""

    def test_empty_dir(self, tmp_path):
        """測試空目錄"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        results = list(extract_lang_files_generator(str(tmp_path), str(output_dir)))

        # 應該沒有找到任何檔案，但流程正常完成
        assert isinstance(results, list)

    def test_with_lang_files(self, tmp_path):
        """測試包含語言檔的 JAR"""
        # 建立模擬 JAR
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar_path = mods_dir / "test_mod.jar"
        with zipfile.ZipFile(jar_path, "w") as zf:
            zf.writestr("assets/testmod/lang/en_us.json", '{"key": "value"}')
            zf.writestr("assets/testmod/lang/zh_tw.json", '{"key": "測試"}')

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        results = list(extract_lang_files_generator(str(mods_dir), str(output_dir)))

        # 應該有提取結果
        assert len(results) > 0


class TestExtractBookFilesGenerator:
    """測試 extract_book_files_generator 函數"""

    def test_empty_dir(self, tmp_path):
        """測試空目錄"""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        results = list(extract_book_files_generator(str(tmp_path), str(output_dir)))
        assert isinstance(results, list)

    def test_with_book_files(self, tmp_path):
        """測試包含書本檔的 JAR"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar_path = mods_dir / "patchouli.jar"
        with zipfile.ZipFile(jar_path, "w") as zf:
            zf.writestr("assets/patchouli_books/test_book/en_us/book.json", "{}")
            zf.writestr("data/guidebook/manual/zh_tw/book.json", "{}")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        results = list(extract_book_files_generator(str(mods_dir), str(output_dir)))
        assert isinstance(results, list)


class TestPreviewExtractionGenerator:
    """測試 preview_extraction_generator 函數"""

    def test_preview_lang_mode(self, tmp_path):
        """測試 lang 模式預覽"""
        results = list(preview_extraction_generator(str(tmp_path), "lang"))
        assert len(results) > 0

        # 最後一個結果應該包含 result
        last = results[-1]
        assert "result" in last or "error" in last

    def test_preview_book_mode(self, tmp_path):
        """測試 book 模式預覽"""
        results = list(preview_extraction_generator(str(tmp_path), "book"))
        assert len(results) > 0

    def test_preview_invalid_mode(self, tmp_path):
        """測試無效模式"""
        # 先建立一個 JAR 檔案，讓 find_jar_files 有東西可找
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        jar_path = mods_dir / "test.jar"
        jar_path.write_bytes(b"PK\x03\x04")  # 模擬 JAR 檔案

        results = list(preview_extraction_generator(str(mods_dir), "invalid_mode"))

        # 應該有錯誤訊息
        assert len(results) > 0
        last = results[-1]
        assert "error" in last


class TestExtractionSummary:
    """測試 ExtractionSummary 類別"""

    def test_add_success(self):
        """測試新增成功記錄"""
        summary = ExtractionSummary()
        summary.add_success("test.jar", 5)

        assert len(summary.success) == 1
        assert summary.success[0]["jar"] == "test.jar"
        assert summary.success[0]["files"] == 5

    def test_add_warning(self):
        """測試新增警告記錄"""
        summary = ExtractionSummary()
        summary.add_warning("test.jar", "Missing manifest")

        assert len(summary.warnings) == 1
        assert summary.warnings[0]["jar"] == "test.jar"
        assert summary.warnings[0]["reason"] == "Missing manifest"

    def test_add_failure(self):
        """測試新增失敗記錄"""
        summary = ExtractionSummary()
        summary.add_failure("test.jar", "File not found")

        assert len(summary.failures) == 1
        assert summary.failures[0]["jar"] == "test.jar"
        assert summary.failures[0]["error"] == "File not found"

    def test_get_summary(self):
        """測試摘要生成"""
        summary = ExtractionSummary()
        summary.add_success("jar1.jar", 10)
        summary.add_success("jar2.jar", 5)
        summary.add_warning("jar3.jar", "test warning")

        result = summary.get_summary()

        assert result["success_count"] == 2
        assert result["warning_count"] == 1
        assert result["failure_count"] == 0
        assert len(result["success"]) == 2
        assert len(result["warnings"]) == 1


class TestGeneratePreviewReport:
    """測試 generate_preview_report 函數"""

    def test_generate_report(self, tmp_path):
        """測試報告生成"""
        output_dir = tmp_path / "reports"
        output_dir.mkdir()

        result = {
            "total_jars": 2,
            "preview_results": [
                {
                    "jar": "mod1.jar",
                    "files": ["assets/mod/lang/en_us.json"],
                    "count": 1,
                    "size_mb": 1.5
                }
            ],
            "total_files": 1,
            "total_size_mb": 1.5
        }

        report_path = generate_preview_report(result, "lang", str(output_dir))

        assert os.path.exists(report_path)
        content = Path(report_path).read_text(encoding="utf-8")
        assert "JAR 提取預覽報告" in content
        assert "mod1.jar" in content
