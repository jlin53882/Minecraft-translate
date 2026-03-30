"""md_translation_stats.py 單元測試。

用途：測試 Markdown 翻譯統計相關功能。
"""
import json
from pathlib import Path
from unittest.mock import Mock, patch

from translation_tool.core.md_translation_stats import (
    _LANG_MODE_LABELS,
    count_json_files,
    count_md_pending_docs,
    log_md_step2_stats,
    normalize_lang_mode,
)


class TestNormalizeLangMode:
    """測試 normalize_lang_mode 函數。"""

    def test_non_cjk_only(self):
        """測試 non_cjk_only 模式。"""
        result = normalize_lang_mode("non_cjk_only")
        assert result == "non_cjk_only"

    def test_cjk_only(self):
        """測試 cjk_only 模式。"""
        result = normalize_lang_mode("cjk_only")
        assert result == "cjk_only"

    def test_all(self):
        """測試 all 模式。"""
        result = normalize_lang_mode("all")
        assert result == "all"

    def test_case_insensitive(self):
        """測試大小寫不敏感。"""
        result = normalize_lang_mode("NON_CJK_ONLY")
        assert result == "non_cjk_only"

    def test_invalid_mode_defaults(self):
        """測試無效模式預設為 non_cjk_only。"""
        result = normalize_lang_mode("invalid_mode")
        assert result == "non_cjk_only"

    def test_empty_string(self):
        """測試空字串。"""
        result = normalize_lang_mode("")
        assert result == "non_cjk_only"

    def test_whitespace(self):
        """測試空白字元。"""
        result = normalize_lang_mode("  non_cjk_only  ")
        assert result == "non_cjk_only"


class TestCountJsonFiles:
    """測試 count_json_files 函數。"""

    def test_empty_directory(self, tmp_path):
        """測試空目錄。"""
        result = count_json_files(tmp_path)
        assert result == 0

    def test_with_json_files(self, tmp_path):
        """測試包含 JSON 檔案。"""
        (tmp_path / "file1.json").touch()
        (tmp_path / "file2.json").touch()
        (tmp_path / "file3.txt").touch()

        result = count_json_files(tmp_path)
        assert result == 2

    def test_nested_json_files(self, tmp_path):
        """測試嵌套 JSON 檔案。"""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        (tmp_path / "root.json").touch()
        (subdir / "nested.json").touch()

        result = count_json_files(tmp_path)
        assert result == 2

    def test_nonexistent_path(self):
        """測試不存在的路徑。"""
        result = count_json_files(Path("nonexistent"))
        assert result == 0


class TestCountMdPendingDocs:
    """測試 count_md_pending_docs 函數。"""

    def test_empty_directory(self, tmp_path):
        """測試空目錄。"""
        result = count_md_pending_docs(tmp_path)
        assert result == 0

    def test_valid_pending_docs(self, tmp_path):
        """測試有效的待翻譯文檔。"""
        pending_data = {
            "schema": "md_pending_blocks_v1",
            "blocks": ["test1", "test2"]
        }

        file1 = tmp_path / "doc1.json"
        file1.write_text(json.dumps(pending_data), encoding="utf-8")

        result = count_md_pending_docs(tmp_path)
        assert result == 1

    def test_multiple_pending_docs(self, tmp_path):
        """測試多個待翻譯文檔。"""
        pending_data = {
            "schema": "md_pending_blocks_v1",
            "blocks": ["test"]
        }

        for i in range(3):
            file = tmp_path / f"doc{i}.json"
            file.write_text(json.dumps(pending_data), encoding="utf-8")

        # 添加一個非 pending 格式的檔案
        other = tmp_path / "other.json"
        other.write_text('{"data": "value"}', encoding="utf-8")

        result = count_md_pending_docs(tmp_path)
        assert result == 3

    def test_invalid_json_file(self, tmp_path):
        """測試無效的 JSON 檔案。"""
        file = tmp_path / "invalid.json"
        file.write_text("not valid json", encoding="utf-8")

        result = count_md_pending_docs(tmp_path)
        assert result == 0

    def test_wrong_schema(self, tmp_path):
        """測試錯誤的 schema。"""
        data = {
            "schema": "different_schema",
            "blocks": ["test"]
        }
        file = tmp_path / "doc.json"
        file.write_text(json.dumps(data), encoding="utf-8")

        result = count_md_pending_docs(tmp_path)
        assert result == 0


class TestLogMdStep2Stats:
    """測試 log_md_step2_stats 函數。"""

    def test_skipped_step(self):
        """測試略過的步驟。"""
        mock_log = Mock()
        step2_res = {"skipped": True, "reason": "no_pending_json"}

        log_md_step2_stats(step2_res, log_info_fn=mock_log)

        mock_log.assert_called_once()
        assert "已略過翻譯" in mock_log.call_args.args[0]

    def test_dry_run_stats(self):
        """測試 dry-run 統計。"""
        mock_log = Mock()
        step2_res = {
            "dry_run": True,
            "files": 5,
            "total_blocks": 100,
            "unique_blocks": 80,
            "duplicate_blocks": 20,
            "cache_hit": 50,
            "cache_miss": 30,
            "already_zh_skipped": 10,
        }

        log_md_step2_stats(step2_res, log_info_fn=mock_log)

        assert mock_log.call_count > 0

    def test_full_stats(self):
        """測試完整統計。"""
        mock_log = Mock()
        step2_res = {
            "files": 10,
            "total_blocks": 200,
            "unique_blocks": 150,
            "duplicate_blocks": 50,
            "cache_hit": 80,
            "cache_miss": 70,
            "already_zh_skipped": 20,
            "missing_hash": 5,
            "out_dir": "/output/path",
            "avg_batch_sec": 2.5,
        }

        with patch('translation_tool.core.md_translation_stats._get_default_batch_size', return_value=10):
            log_md_step2_stats(step2_res, log_info_fn=mock_log)

        # 驗證多個日誌被呼叫
        assert mock_log.call_count > 0

    def test_invalid_input(self):
        """測試無效輸入。"""
        mock_log = Mock()

        # None 輸入
        log_md_step2_stats(None, log_info_fn=mock_log)
        mock_log.assert_not_called()

        # 非字典輸入
        mock_log.reset_mock()
        log_md_step2_stats("not a dict", log_info_fn=mock_log)
        mock_log.assert_not_called()


class TestLangModeLabels:
    """測試語言模式標籤常量。"""

    def test_labels_exist(self):
        """測試標籤存在。"""
        assert "non_cjk_only" in _LANG_MODE_LABELS
        assert "cjk_only" in _LANG_MODE_LABELS
        assert "all" in _LANG_MODE_LABELS

    def test_labels_are_strings(self):
        """測試標籤都是字串。"""
        for key, value in _LANG_MODE_LABELS.items():
            assert isinstance(key, str)
            assert isinstance(value, str)


class TestModuleExports:
    """測試模組導出。"""

    def test_exports(self):
        """測試導出的函數和常量。"""
        from translation_tool.core.md_translation_stats import (
            _LANG_MODE_LABELS,
            count_json_files,
            count_md_pending_docs,
            log_md_step2_stats,
            normalize_lang_mode,
        )
        assert callable(normalize_lang_mode)
        assert callable(count_json_files)
        assert callable(count_md_pending_docs)
        assert callable(log_md_step2_stats)
        assert isinstance(_LANG_MODE_LABELS, dict)
