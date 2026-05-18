"""測試 jar_processor.py - JAR 處理器入口。

用途：測試 jar_processor.py 的主要導出函數與常數。
"""

import re
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from translation_tool.core.jar_processor import (
    extract_lang_files_generator,
    extract_book_files_generator,
    extract_dual_files_generator,
    preview_extraction_generator,
    ExtractionSummary,
    generate_preview_report,
    BOOK_PATH_REGEX_DUAL_STRUCTURE,
    get_lang_codes,
    build_lang_file_regex,
)


class TestJarProcessorExports:
    """測試 jar_processor 導出的常數與函數"""

    def test_lang_codes_defined(self):
        """測試 get_lang_codes() 有定義且預設包含 en_us/zh_tw/zh_cn"""
        lang_codes = get_lang_codes()
        assert isinstance(lang_codes, list)
        assert len(lang_codes) > 0
        assert "en_us" in lang_codes
        assert "zh_tw" in lang_codes
        assert "zh_cn" in lang_codes

    def test_build_lang_file_regex(self):
        """測試 build_lang_file_regex() 能正確產出 Pattern"""
        regex = build_lang_file_regex()
        assert regex is not None
        assert isinstance(regex, re.Pattern)
        # 確認 regex 能正確匹配 lang 檔案路徑
        assert regex.search("assets/mymod/lang/en_us.json")
        assert regex.search("assets/mymod/lang/zh_cn.json")
        assert regex.search("assets/mymod/lang/zh_tw.json")

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


class TestExtractDualFilesGenerator:
    """測試 extract_dual_files_generator 錯誤處理"""

    def test_dual_mode_no_errors_when_both_pass(self, tmp_path):
        """測試兩個階段都成功時，沒有 dual_errors"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        jar_path = mods_dir / "test_mod.jar"
        with zipfile.ZipFile(jar_path, "w") as zf:
            zf.writestr("assets/testmod/lang/en_us.json", '{"key": "value"}')
            zf.writestr("assets/patchouli_books/guide/en_us/book.json", '{}')

        results = list(extract_dual_files_generator(str(mods_dir), str(output_dir)))

        dual_error_updates = [r for r in results if "dual_errors" in r]
        assert len(dual_error_updates) == 0

    def test_dual_mode_lang_error_captured(self, tmp_path, monkeypatch):
        """測試 Lang 階段失敗時，dual_errors 包含 lang 錯誤，Book 階段繼續執行"""
        from translation_tool.core.jar_processor_extract import run_extraction_process_impl

        def mock_impl(mods_dir, output_dir, target_regex, process_name, **kwargs):
            if process_name == "Lang":
                raise RuntimeError("Lang extraction failed")
            yield from run_extraction_process_impl(
                mods_dir, output_dir, target_regex, "Patchouli Book",
                find_jar_files_fn=lambda d: [],
                extract_from_jar_fn=lambda *a, **kw: {},
            )

        monkeypatch.setattr(
            "translation_tool.core.jar_processor._run_extraction_process",
            mock_impl,
        )

        results = list(extract_dual_files_generator(str(tmp_path / "mods"), str(tmp_path / "output")))

        dual_error_updates = [r for r in results if "dual_errors" in r]
        assert len(dual_error_updates) == 1
        assert dual_error_updates[0]["dual_errors"]["lang"] == "Lang extraction failed"
        assert dual_error_updates[0]["dual_errors"]["book"] is None

    def test_dual_mode_book_error_captured(self, tmp_path, monkeypatch):
        """測試 Book 階段失敗時，dual_errors 包含 book 錯誤"""
        from translation_tool.core.jar_processor_extract import run_extraction_process_impl

        def mock_impl(mods_dir, output_dir, target_regex, process_name, **kwargs):
            if process_name == "Patchouli Book":
                raise RuntimeError("Book extraction failed")
            yield from run_extraction_process_impl(
                mods_dir, output_dir, target_regex, "Lang",
                find_jar_files_fn=lambda d: [],
                extract_from_jar_fn=lambda *a, **kw: {},
            )

        monkeypatch.setattr(
            "translation_tool.core.jar_processor._run_extraction_process",
            mock_impl,
        )

        results = list(extract_dual_files_generator(str(tmp_path / "mods"), str(tmp_path / "output")))

        dual_error_updates = [r for r in results if "dual_errors" in r]
        assert len(dual_error_updates) == 1
        assert dual_error_updates[0]["dual_errors"]["lang"] is None
        assert dual_error_updates[0]["dual_errors"]["book"] == "Book extraction failed"

    def test_dual_mode_stats_are_merged(self, tmp_path, monkeypatch):
        """測試 dual mode 完成時，stats 是 Lang + Book 的合併"""
        from translation_tool.core.jar_processor_extract import run_extraction_process_impl

        call_count = [0]

        def mock_impl(mods_dir, output_dir, target_regex, process_name, **kwargs):
            call_count[0] += 1
            if process_name == "Lang":
                yield {"progress": 1.0, "stats": {"success": 5, "failures": 0, "warnings": 2, "total_files": 5}}
            elif process_name == "Patchouli Book":
                yield {"progress": 1.0, "stats": {"success": 3, "failures": 0, "warnings": 1, "total_files": 3}}

        monkeypatch.setattr(
            "translation_tool.core.jar_processor._run_extraction_process",
            mock_impl,
        )

        results = list(extract_dual_files_generator(str(tmp_path / "mods"), str(tmp_path / "output")))

        stats_updates = [r for r in results if "stats" in r]
        merged_stats = stats_updates[-1]["stats"]
        assert merged_stats["success"] == 8  # 5 + 3
        assert merged_stats["warnings"] == 3  # 2 + 1
        assert merged_stats["failures"] == 0
        assert merged_stats["total_files"] == 8  # 5 + 3
