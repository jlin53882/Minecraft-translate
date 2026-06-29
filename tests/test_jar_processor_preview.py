"""測試 jar_processor_preview.py - JAR 預覽多執行緒實作。

用途：測試 jar_processor_preview.py 的多執行緒掃描邏輯。
包含 ExtractionSummary、_scan_single_jar_for_preview、_get_preview_workers、
以及 preview_extraction_generator_impl 的完整斷言。
"""

import os
import re
import zipfile
import time
from pathlib import Path

import pytest

from translation_tool.core.jar_processor_preview import (
    ExtractionSummary,
    _get_preview_workers,
    _scan_single_jar_for_preview,
    preview_extraction_generator_impl,
    generate_preview_report,
)


# =============================================================================
# Test _get_preview_workers
# =============================================================================
class TestGetPreviewWorkers:
    """測試 _get_preview_workers 函數"""

    def test_returns_positive_int(self):
        """回傳值應為正整數"""
        workers = _get_preview_workers()
        assert isinstance(workers, int)
        assert workers >= 1

    def test_fallback_value_is_reasonable(self):
        """Fallback 值應為 min(4, cpu_count)，且 >= 1"""
        workers = _get_preview_workers()
        cpu = os.cpu_count() or 2
        assert workers <= 4
        assert workers <= cpu
        assert workers >= 1


# =============================================================================
# Test _scan_single_jar_for_preview
# =============================================================================
class TestScanSingleJarForPreview:
    """測試 _scan_single_jar_for_preview 單 JAR 處理函式"""

    def test_lang_mode_returns_matched_files(self, tmp_path):
        """lang 模式應正確找出符合 regex 的檔案"""
        jar = tmp_path / "test.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/modid/lang/en_us.json", '{"key": "value"}')
            zf.writestr("assets/modid/lang/zh_tw.json", '{"key": "測試"}')
            zf.writestr("assets/modid/lang/zh_cn.json", '{"key": "简体"}')
            zf.writestr("assets/modid/textures/icon.png", "png data")  # 不應被匹配

        regex = re.compile(r"assets/([^/]+)/lang/(en_us|zh_tw|zh_cn)\.json$", re.IGNORECASE)
        result = _scan_single_jar_for_preview(
            str(jar), "lang", regex, book_path_regex=None, lang_regex=None
        )

        assert result["jar"] == "test.jar"
        assert result["error"] is None
        assert result["count"] == 3
        assert len(result["matched_files"]) == 3
        assert "assets/modid/lang/en_us.json" in result["matched_files"]

    def test_book_mode_returns_matched_files(self, tmp_path):
        """book 模式應正確找出書本相關檔案"""
        from translation_tool.core.jar_processor import build_book_path_regex

        jar = tmp_path / "patchouli.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/modid/book/en_us/book.json", "{}")
            zf.writestr("assets/modid/manual/zh_tw/book.json", "{}")
            zf.writestr("assets/other/lang/en_us.json", "{}")  # 不應被匹配

        book_regex = build_book_path_regex()
        result = _scan_single_jar_for_preview(
            str(jar), "book", book_regex, book_path_regex=None, lang_regex=None
        )

        assert result["jar"] == "patchouli.jar"
        assert result["error"] is None
        assert result["count"] == 2, f"expected 2 matched files, got {result['count']}, matched={result['matched_files']}"

    def test_dual_mode_returns_both_lang_and_book(self, tmp_path):
        """dual 模式應同時傳回 lang_matched 和 book_matched"""
        from translation_tool.core.jar_processor import build_lang_file_regex, build_book_path_regex

        jar = tmp_path / "dual.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            # 寫入真實的 book 檔案路徑（符合 build_book_path_regex 的預期格式）
            zf.writestr("assets/modid/lang/en_us.json", '{"key": "value"}')
            zf.writestr("assets/modid/book/en_us/book.json", "{}")
            zf.writestr("assets/modid/manual/zh_tw/book.json", "{}")

        lang_regex = build_lang_file_regex()
        book_regex = build_book_path_regex()
        result = _scan_single_jar_for_preview(
            str(jar), "dual", target_regex=None,
            book_path_regex=book_regex, lang_regex=lang_regex
        )

        assert result["error"] is None
        assert result["lang_count"] == 1, f"expected 1 lang, got {result['lang_count']}, lang={result['lang_matched']}"
        assert result["book_count"] == 2, f"expected 2 books, got {result['book_count']}, books={result['book_matched']}"
        assert len(result["lang_matched"]) == 1
        assert len(result["book_matched"]) == 2

    def test_corrupt_jar_returns_error(self, tmp_path):
        """損壞的 JAR 應不回報 exception，而是回傳 error 欄位"""
        bad = tmp_path / "corrupt.jar"
        bad.write_bytes(b"PK\x03\x04" + b"\x00" * 8)  # 截斷的 ZIP

        lang_regex = re.compile(r"\.json$", re.IGNORECASE)
        result = _scan_single_jar_for_preview(
            str(bad), "lang", lang_regex, book_path_regex=None, lang_regex=None
        )

        assert result["jar"] == "corrupt.jar"
        assert result["error"] is not None
        assert result["count"] == 0
        assert result["matched_files"] == []

    def test_jar_size_reported_correctly(self, tmp_path):
        """JAR 大小（MB）應正確計算"""
        jar = tmp_path / "small.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/mod/lang/en_us.json", '{"key": "value"}')

        lang_regex = re.compile(r"\.json$", re.IGNORECASE)
        result = _scan_single_jar_for_preview(
            str(jar), "lang", lang_regex, book_path_regex=None, lang_regex=None
        )

        assert result["size_mb"] > 0
        assert isinstance(result["size_mb"], float)


# =============================================================================
# Test ExtractionSummary
# =============================================================================
class TestExtractionSummary:
    """測試 ExtractionSummary 類別"""

    def test_initialization(self):
        summary = ExtractionSummary()
        assert summary.success == []
        assert summary.warnings == []
        assert summary.failures == []

    def test_add_success(self):
        summary = ExtractionSummary()
        summary.add_success("mod1.jar", 10)
        assert len(summary.success) == 1
        assert summary.success[0]["jar"] == "mod1.jar"
        assert summary.success[0]["files"] == 10

    def test_add_warning(self):
        summary = ExtractionSummary()
        summary.add_warning("mod1.jar", "No lang folder")
        assert len(summary.warnings) == 1
        assert summary.warnings[0]["reason"] == "No lang folder"

    def test_add_failure(self):
        summary = ExtractionSummary()
        summary.add_failure("mod1.jar", "Corrupt ZIP")
        assert len(summary.failures) == 1
        assert summary.failures[0]["jar"] == "mod1.jar"
        assert summary.failures[0]["error"] == "Corrupt ZIP"

    def test_summary_includes_all_successes(self):
        summary = ExtractionSummary()
        for i in range(3):
            summary.add_success(f"mod{i}.jar", i * 5)
        result = summary.get_summary()
        assert result["success_count"] == 3
        assert len(result["success"]) == 3

    def test_summary_limits_warnings_and_failures_to_five(self):
        summary = ExtractionSummary()
        for i in range(10):
            summary.add_warning(f"mod{i}.jar", f"warning {i}")
        for i in range(10):
            summary.add_failure(f"mod{i}.jar", f"error {i}")
        result = summary.get_summary()
        # 總數記錄正確
        assert result["warning_count"] == 10
        assert result["failure_count"] == 10
        # 但只保留前 5 筆
        assert len(result["warnings"]) == 5
        assert len(result["failures"]) == 5


# =============================================================================
# Test preview_extraction_generator_impl - 基本斷言
# =============================================================================
class TestPreviewExtractionGeneratorImpl:
    """測試 preview_extraction_generator_impl generator 函式"""

    def test_empty_mods_dir_yields_one_result(self, tmp_path):
        """空目錄只 yield 一個 result（progress=1.0）"""
        def no_jars(path):
            return []

        results = list(preview_extraction_generator_impl(
                str(tmp_path), "lang",
                find_jar_files_fn=no_jars,
                book_path_regex=re.compile(r".*")
            ))

        assert len(results) == 1
        assert results[0]["progress"] == 1.0
        assert results[0]["result"]["total_jars"] == 0

    def test_invalid_mode_yields_error(self, tmp_path):
        """無效 mode 應 yield error"""
        jar = tmp_path / "test.jar"
        jar.write_bytes(b"PK\x03\x04")

        def one_jar(path):
            return [str(jar)]

        results = list(preview_extraction_generator_impl(
            str(tmp_path), "invalid_mode",
            find_jar_files_fn=one_jar,
            book_path_regex=re.compile(r".*")
        ))

        # 第一個或最後一個 update 會有 error
        has_error = any("error" in r for r in results)
        assert has_error

    def test_lang_mode_finds_correct_files(self, tmp_path):
        """lang 模式產生的 result 中 total_files 應正確"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar = mods_dir / "Botania.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/botania/lang/en_us.json", '{"item": "Flower"}')
            zf.writestr("assets/botania/lang/zh_tw.json", '{"item": "花"}')
            zf.writestr("assets/botania/lang/zh_cn.json", '{"item": "花"}')

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=lambda p: [str(jar)],
            book_path_regex=re.compile(r".*")
        ))

        final = results[-1]
        assert "result" in final
        assert final["result"]["total_jars"] == 1
        assert final["result"]["total_files"] == 3
        assert final["progress"] == 1.0

    def test_book_mode_finds_correct_files(self, tmp_path):
        """book 模式應正確統計 book 檔"""
        from translation_tool.core.jar_processor import build_book_path_regex

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar = mods_dir / "patchouli.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/modid/book/en_us/book.json", "{}")
            zf.writestr("assets/modid/manual/zh_tw/book.json", "{}")

        book_regex = build_book_path_regex()

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "book",
            find_jar_files_fn=lambda p: [str(jar)],
            book_path_regex=book_regex
        ))

        final = results[-1]
        assert final["result"]["total_jars"] == 1
        assert final["result"]["total_files"] == 2, f"expected 2 files, got {final['result']['total_files']}, preview_results={final['result']['preview_results']}"

    def test_dual_mode_reports_both_counts(self, tmp_path):
        """dual 模式 preview_results 中每個 JAR 應同時有 lang_count 和 book_count"""
        from translation_tool.core.jar_processor import build_lang_file_regex, build_book_path_regex

        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar = mods_dir / "dual_mod.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/modid/lang/en_us.json", '{"key": "value"}')
            zf.writestr("assets/modid/book/en_us/book.json", "{}")
            zf.writestr("assets/modid/manual/zh_tw/book.json", "{}")

        book_regex = build_book_path_regex()

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "dual",
            find_jar_files_fn=lambda p: [str(jar)],
            book_path_regex=book_regex
        ))

        final = results[-1]
        preview_results = final["result"]["preview_results"]
        assert len(preview_results) == 1
        assert preview_results[0]["lang_count"] == 1, f"expected 1 lang, got {preview_results[0]}"
        assert preview_results[0]["book_count"] == 2, f"expected 2 books, got {preview_results[0]}"


# =============================================================================
# Test preview_extraction_generator_impl - 多執行緒斷言
# =============================================================================
class TestPreviewExtractionGeneratorImplMultiThreaded:
    """測試 preview_extraction_generator_impl 的多執行緒行為"""

    def test_multiple_jars_processed(self, tmp_path):
        """多個 JAR 都應被正確處理"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar_count = 8
        jars = []
        for i in range(jar_count):
            jar = mods_dir / f"mod_{i}.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr(f"assets/mod{i}/lang/en_us.json", f'{{"key": "{i}"}}')
            jars.append(jar)

        def find_jars(path):
            return [str(j) for j in jars]

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=find_jars,
            book_path_regex=re.compile(r".*")
        ))

        final = results[-1]
        assert final["result"]["total_jars"] == jar_count
        assert final["result"]["total_files"] == jar_count
        assert final["progress"] == 1.0

    def test_progress_updates_for_each_jar(self, tmp_path):
        """驗證每個 JAR 完成都會 yield 一次進度更新"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jars = []
        for i in range(5):
            jar = mods_dir / f"mod_{i}.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr("assets/mod/lang/en_us.json", '{"key": "value"}')
            jars.append(jar)

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=lambda p: [str(j) for j in jars],
            book_path_regex=re.compile(r".*")
        ))

        # 應有 5 次進度更新（每個 JAR 一次）+ 最後一次 final（含有 result）
        progress_updates = [r for r in results if "progress" in r and "current" in r and "result" not in r]
        final_updates = [r for r in results if "result" in r]
        assert len(progress_updates) == 5, f"預期 5 次進度更新，實際 {len(progress_updates)} 次"
        assert len(final_updates) == 1, f"預期 1 次最終更新，實際 {len(final_updates)} 次"

        # 進度應遞增
        progresses = [r["progress"] for r in progress_updates]
        assert progresses == sorted(progresses), "進度應單調遞增"

    def test_partial_failure_reported_correctly(self, tmp_path):
        """部分 JAR 失敗時，failed_jars 應正確記錄，且 progress < 1.0"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        # 好 JAR
        good = mods_dir / "good.jar"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("assets/mod/lang/en_us.json", '{"key": "value"}')

        # 壞 JAR
        bad = mods_dir / "bad.jar"
        bad.write_bytes(b"PK\x03\x04" + b"\x00" * 10)

        def find_jars(path):
            return [str(good), str(bad)]

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=find_jars,
            book_path_regex=re.compile(r".*")
        ))

        final = results[-1]
        assert len(final["result"]["failed_jars"]) == 1
        assert final["result"]["failed_jars"][0]["jar"] == "bad.jar"
        assert final["progress"] == 0.5  # 1/2 失敗

    def test_concurrent_writes_are_thread_safe(self, tmp_path):
        """驗證執行緒安全：結果字典不會因並行寫入而損壞"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jars = []
        for i in range(20):
            jar = mods_dir / f"mod_{i}.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr(f"assets/mod{i}/lang/en_us.json", f'{{"key": "{i}"}}')
            jars.append(jar)

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=lambda p: [str(j) for j in jars],
            book_path_regex=re.compile(r".*")
        ))

        final = results[-1]
        # 所有 JAR 都應被處理（無競爭導致遺失）
        assert final["result"]["total_jars"] == 20
        assert final["result"]["total_files"] == 20
        # preview_results 數量應等於有檔案的 JAR 數量
        assert len(final["result"]["preview_results"]) == 20

    def test_each_progress_update_has_current_and_total(self, tmp_path):
        """每個進度 update 都應包含 current 和 total 欄位"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar = mods_dir / "test.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/mod/lang/en_us.json", '{"key": "value"}')

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=lambda p: [str(jar)],
            book_path_regex=re.compile(r".*")
        ))

        for r in results:
            if "progress" in r:
                assert "current" in r, f"缺少 current: {r}"
                assert "total" in r, f"缺少 total: {r}"
                assert r["total"] == 1
                assert r["current"] in (1,)

    def test_log_field_present_in_progress_updates(self, tmp_path):
        """每個進度更新都應有 log 欄位（用於 UI 顯示）"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar = mods_dir / "test.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/mod/lang/en_us.json", '{"key": "value"}')

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=lambda p: [str(jar)],
            book_path_regex=re.compile(r".*")
        ))

        for r in results:
            if "log" in r:
                assert isinstance(r["log"], str)
                assert len(r["log"]) > 0

    def test_jar_order_does_not_affect_final_result(self, tmp_path):
        """執行緒池完成 JAR 的順序不影響最終統計結果"""
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()

        jar_a = mods_dir / "aaa_mod.jar"
        jar_z = mods_dir / "zzz_mod.jar"
        for jar, content in [
            (jar_a, '{"a": "1"}'),
            (jar_z, '{"z": "9"}'),
        ]:
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr("assets/mod/lang/en_us.json", content)

        def find_jars(path):
            return [str(jar_a), str(jar_z)]

        results = list(preview_extraction_generator_impl(
            str(mods_dir), "lang",
            find_jar_files_fn=find_jars,
            book_path_regex=re.compile(r".*")
        ))

        final = results[-1]
        assert final["result"]["total_jars"] == 2
        assert final["result"]["total_files"] == 2
        # 兩個 JAR 都應出現在 preview_results 中
        jar_names = {r["jar"] for r in final["result"]["preview_results"]}
        assert jar_names == {"aaa_mod.jar", "zzz_mod.jar"}


# =============================================================================
# Test generate_preview_report
# =============================================================================
class TestGeneratePreviewReport:
    """測試 generate_preview_report 檔案生成"""

    def test_creates_nested_output_directory(self, tmp_path):
        """應自動建立多層目錄"""
        output_path = tmp_path / "a" / "b" / "c"
        result = {
            "total_jars": 0, "preview_results": [],
            "total_files": 0, "total_size_mb": 0
        }
        report_path = generate_preview_report(result, "lang", str(output_path))
        assert os.path.exists(report_path)

    def test_report_filename_contains_mode_and_timestamp(self, tmp_path):
        """檔名應包含 mode 和時間戳"""
        result = {
            "total_jars": 0, "preview_results": [],
            "total_files": 0, "total_size_mb": 0
        }
        report_path = generate_preview_report(result, "lang", str(tmp_path))
        filename = os.path.basename(report_path)
        assert "lang" in filename
        # 包含時間戳（格式 YYYYMMDD_HHMMSS）
        import re as re_module
        assert re_module.search(r"\d{8}_\d{6}", filename)

    def test_report_truncates_file_list_at_50(self, tmp_path):
        """當單一 JAR 有超過 50 個檔案時，報告應截斷並標注剩餘數量"""
        files = [f"assets/mod/lang/file_{i}.json" for i in range(60)]
        result = {
            "total_jars": 1,
            "total_files": 60,
            "total_size_mb": 1.0,
            "preview_results": [{
                "jar": "big_mod.jar",
                "files": files,
                "count": 60,
                "size_mb": 1.0
            }]
        }
        report_path = generate_preview_report(result, "lang", str(tmp_path))
        content = Path(report_path).read_text(encoding="utf-8")
        assert "還有 10" in content

    def test_dual_mode_report_shows_both_lang_and_book_counts(self, tmp_path):
        """dual 模式報告應同時顯示 Lang 和 Book 數量"""
        result = {
            "total_jars": 1,
            "total_files": 5,
            "total_size_mb": 1.0,
            "preview_results": [{
                "jar": "dual_mod.jar",
                "lang_files": ["a.json", "b.json"],
                "book_files": ["c.json"],
                "lang_count": 2,
                "book_count": 1,
                "size_mb": 1.0
            }]
        }
        report_path = generate_preview_report(result, "dual", str(tmp_path))
        content = Path(report_path).read_text(encoding="utf-8")
        assert "Lang" in content
        assert "Book" in content or "book" in content.lower()
