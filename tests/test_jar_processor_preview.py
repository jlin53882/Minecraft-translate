"""測試 jar_processor_preview.py - JAR 預覽邏輯。

用途：測試 jar_processor_preview.py 的 ExtractionSummary 與預覽報告生成功能。
"""

import pytest
import os
import zipfile
import re
from pathlib import Path
from translation_tool.core.jar_processor_preview import (
    ExtractionSummary,
    preview_extraction_generator_impl,
    generate_preview_report,
)


class TestExtractionSummaryPreview:
    """測試 ExtractionSummary 類別（preview 版本）"""

    def test_initialization(self):
        """測試初始化"""
        summary = ExtractionSummary()
        assert summary.success == []
        assert summary.warnings == []
        assert summary.failures == []

    def test_multiple_success_records(self):
        """測試多個成功記錄"""
        summary = ExtractionSummary()
        summary.add_success("mod1.jar", 10)
        summary.add_success("mod2.jar", 20)
        summary.add_success("mod3.jar", 5)

        assert len(summary.success) == 3
        assert summary.success[0]["files"] == 10
        assert summary.success[2]["files"] == 5

    def test_multiple_warning_records(self):
        """測試多個警告記錄"""
        summary = ExtractionSummary()
        summary.add_warning("mod1.jar", "No lang folder")
        summary.add_warning("mod2.jar", "Empty file")

        assert len(summary.warnings) == 2

    def test_summary_limits_warnings_and_failures(self):
        """測試摘要限制警告和失敗數量"""
        summary = ExtractionSummary()
        for i in range(10):
            summary.add_warning(f"mod{i}.jar", f"warning {i}")
        for i in range(10):
            summary.add_failure(f"mod{i}.jar", f"error {i}")

        result = summary.get_summary()

        assert result["warning_count"] == 10
        assert result["failure_count"] == 10
        # 應該只保留前 5 筆
        assert len(result["warnings"]) == 5
        assert len(result["failures"]) == 5


class TestPreviewExtractionGeneratorImpl:
    """測試 preview_extraction_generator_impl 函數"""

    def test_empty_mods_dir(self, tmp_path):
        """測試空的 mods 目錄"""
        def mock_find_jars(path):
            return []

        results = list(preview_extraction_generator_impl(
            str(tmp_path),
            "lang",
            find_jar_files_fn=mock_find_jars,
            book_path_regex=re.compile(r".*")
        ))

        assert len(results) == 1
        assert results[0]["result"]["total_jars"] == 0

    def test_invalid_mode(self, tmp_path):
        """測試無效模式"""
        # 建立一個 JAR 檔案讓 find_jars 不會回傳空列表
        jar_path = tmp_path / "test.jar"
        jar_path.write_bytes(b"PK\x03\x04")

        def mock_find_jars(path):
            return [str(jar_path)]

        results = list(preview_extraction_generator_impl(
            str(tmp_path),
            "invalid",
            find_jar_files_fn=mock_find_jars,
            book_path_regex=re.compile(r".*")
        ))

        assert len(results) >= 1
        last = results[-1]
        assert "error" in last

    def test_lang_mode_finds_files(self, tmp_path):
        """測試 lang 模式找到語言檔"""
        # 建立 JAR
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar_path = mods_dir / "test.jar"
        with zipfile.ZipFile(jar_path, "w") as zf:
            zf.writestr("assets/modid/lang/en_us.json", '{"key": "value"}')
            zf.writestr("assets/modid/lang/zh_tw.json", '{"key": "測試"}')

        def find_jars(path):
            return [str(jar_path)]

        results = list(preview_extraction_generator_impl(
            str(mods_dir),
            "lang",
            find_jar_files_fn=find_jars,
            book_path_regex=re.compile(r".*")
        ))

        # 最後應該有結果
        last = results[-1]
        assert "result" in last
        assert last["result"]["total_jars"] == 1

    def test_book_mode_finds_files(self, tmp_path):
        """測試 book 模式找到書本檔"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar_path = mods_dir / "patchouli.jar"
        with zipfile.ZipFile(jar_path, "w") as zf:
            zf.writestr("assets/patchouli_books/book/en_us/book.json", "{}")
            zf.writestr("data/manual/zh_tw/book.json", "{}")

        def find_jars(path):
            return [str(jar_path)]

        book_regex = re.compile(
            r"(assets|data)/([^/]+)/(patchouli_books|book|manual|guidebook)/.*",
            re.IGNORECASE
        )

        results = list(preview_extraction_generator_impl(
            str(mods_dir),
            "book",
            find_jar_files_fn=find_jars,
            book_path_regex=book_regex
        ))

        last = results[-1]
        assert "result" in last


class TestGeneratePreviewReportFiles:
    """測試 generate_preview_report 檔案生成"""

    def test_creates_output_directory(self, tmp_path):
        """測試建立輸出目錄"""
        output_path = tmp_path / "new_dir" / "nested"
        result = {
            "total_jars": 0,
            "preview_results": [],
            "total_files": 0,
            "total_size_mb": 0
        }

        report_path = generate_preview_report(result, "test", str(output_path))

        assert os.path.exists(report_path)
        assert "new_dir" in report_path
        assert "nested" in report_path

    def test_report_content_lang_mode(self, tmp_path):
        """測試語言模式報告內容"""
        result = {
            "total_jars": 2,
            "total_files": 5,
            "total_size_mb": 2.5,
            "preview_results": [
                {
                    "jar": "mod_a.jar",
                    "files": ["assets/a/lang/en_us.json", "assets/a/lang/zh_tw.json"],
                    "count": 2,
                    "size_mb": 1.2
                },
                {
                    "jar": "mod_b.jar",
                    "files": ["assets/b/lang/en_us.json"],
                    "count": 1,
                    "size_mb": 0.8
                }
            ]
        }

        report_path = generate_preview_report(result, "lang", str(tmp_path))
        content = Path(report_path).read_text(encoding="utf-8")

        assert "語言模式" in content or "LANG" in content
        assert "mod_a.jar" in content
        assert "mod_b.jar" in content
        assert "2" in content  # 總 JAR 數

    def test_report_content_book_mode(self, tmp_path):
        """測試書本模式報告內容"""
        result = {
            "total_jars": 1,
            "total_files": 3,
            "total_size_mb": 1.0,
            "preview_results": [
                {
                    "jar": "patchouli.jar",
                    "files": [
                        "assets/patchouli_books/guide/en_us/book.json",
                        "assets/patchouli_books/guide/zh_tw/book.json"
                    ],
                    "count": 2,
                    "size_mb": 1.0
                }
            ]
        }

        report_path = generate_preview_report(result, "book", str(tmp_path))
        content = Path(report_path).read_text(encoding="utf-8")

        assert "書本" in content or "BOOK" in content or "patchouli" in content

    def test_report_truncates_long_file_list(self, tmp_path):
        """測試長檔案列表會被截斷"""
        # 建立超過 50 個檔案
        files = [f"assets/mod/lang/file_{i}.json" for i in range(60)]
        result = {
            "total_jars": 1,
            "total_files": 60,
            "total_size_mb": 1.0,
            "preview_results": [
                {
                    "jar": "big_mod.jar",
                    "files": files,
                    "count": 60,
                    "size_mb": 1.0
                }
            ]
        }

        report_path = generate_preview_report(result, "lang", str(tmp_path))
        content = Path(report_path).read_text(encoding="utf-8")

        # 應該有 "還有 X 個檔案" 的提示
        assert "還有" in content or "..." in content
