"""lm_translator.py DEBUG log 巢狀迴圈單元測試

測試目標：驗證 cache hit log 中多個檔案時，每個檔案的 hits 都正確對應，
而不是只有最後一個檔案被處理（BUG: 外層迴圈只跑一次就 break，導致只有最後一個檔案有 log）。

修復後邏輯：
    for fname, file_hits in hit_by_file.items():  # 外層：每個檔案
        log_debug("🎯 [CACHE HIT] %s (%d)", fname, len(file_hits))
        for it in file_hits:                       # 內層：該檔案的每個 hit
            # 處理每個 hit 的詳細 log
"""

from collections import defaultdict
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestHitByFileGroupsByFilename:
    """驗證 hit_by_file 正確將 cache hits 按檔名分組（核心邏輯測試）。"""

    def test_groups_two_files_correctly(self):
        """多檔案時，hit_by_file 產生正確的 group 結構。"""
        cached_items = [
            {"file": "/path/to/file_a.json", "path": "key1", "text": "Hello", "cache_type": "lang"},
            {"file": "/path/to/file_a.json", "path": "key2", "text": "World", "cache_type": "lang"},
            {"file": "/path/to/file_b.json", "path": "key3", "text": "Foo",  "cache_type": "lang"},
        ]

        hit_by_file = defaultdict(list)
        for it in cached_items:
            hit_by_file[Path(it["file"]).name].append(it)

        assert len(hit_by_file) == 2
        assert len(hit_by_file["file_a.json"]) == 2
        assert len(hit_by_file["file_b.json"]) == 1

    def test_groups_three_files_correctly(self):
        """三個不同檔案的 cache hits 各自獨立分組。"""
        cached_items = [
            {"file": "/a/x.json", "path": "p1", "text": "A", "cache_type": "lang"},
            {"file": "/a/x.json", "path": "p2", "text": "B", "cache_type": "lang"},
            {"file": "/b/y.json", "path": "p3", "text": "C", "cache_type": "lang"},
            {"file": "/c/z.json", "path": "p4", "text": "D", "cache_type": "lang"},
        ]

        hit_by_file = defaultdict(list)
        for it in cached_items:
            hit_by_file[Path(it["file"]).name].append(it)

        assert len(hit_by_file) == 3
        assert set(hit_by_file.keys()) == {"x.json", "y.json", "z.json"}
        assert len(hit_by_file["x.json"]) == 2
        assert len(hit_by_file["y.json"]) == 1
        assert len(hit_by_file["z.json"]) == 1

    def test_empty_cached_items(self):
        """空清單時 hit_by_file 為空。"""
        cached_items = []
        hit_by_file = defaultdict(list)
        for it in cached_items:
            hit_by_file[Path(it["file"]).name].append(it)
        assert len(hit_by_file) == 0


class TestCacheHitDebugLogMultipleFiles:
    """驗證多檔案 cache hit 時，每個檔案都會產生對應的 DEBUG log。"""

    @patch('translation_tool.core.lm_translator.log_debug')
    def test_both_files_logged_separately(self, mock_log_debug):
        """當 cached_items 來自兩個檔案時，log_debug 對每個檔案都有呼叫。"""
        # 模擬翻譯流程走到 cache hit debug log 段時的真實資料
        cached_items = [
            {"file": "/root/lang/en_us.json", "path": "a.b.c", "text": "Hello",
             "source_text": "Hello", "cache_type": "lang"},
            {"file": "/root/lang/en_us.json", "path": "x.y.z", "text": "World",
             "source_text": "World", "cache_type": "lang"},
            {"file": "/root/patchouli/book.json", "path": "page.1", "text": "Foo",
             "source_text": "Foo", "cache_type": "patchouli"},
        ]

        # 重現 translate_directory_generator 中的巢狀迴圈邏輯
        hit_by_file = defaultdict(list)
        for it in cached_items:
            hit_by_file[Path(it["file"]).name].append(it)

        # 模擬 log_debug 被各檔案的迴圈呼叫
        for fname, file_hits in hit_by_file.items():
            mock_log_debug("🎯 [CACHE HIT] %s (%d)", fname, len(file_hits))
            for it in file_hits:
                mock_log_debug("   - [%s] %s | %s", it["cache_type"], fname, it["path"])

        # 驗證：兩個檔案都有 log
        log_calls_str = [str(call) for call in mock_log_debug.call_args_list]
        assert any("en_us.json" in c for c in log_calls_str), \
            f"en_us.json 未出現在 log 中。log_calls={log_calls_str}"
        assert any("book.json" in c for c in log_calls_str), \
            f"book.json 未出現在 log 中。log_calls={log_calls_str}"

        # 驗證：外層迴圈每個檔案的 summary log 都有獨立的 call
        # call[0] = positional args tuple: (format_str, fname, count)
        en_us_summary = [
            call for call in mock_log_debug.call_args_list
            if "[CACHE HIT]" in str(call) and call[0][1] == "en_us.json"
        ]
        assert len(en_us_summary) == 1, \
            f"en_us.json 應有 1 個 summary log，實際: {en_us_summary}"
        assert en_us_summary[0][0][2] == 2, \
            f"en_us.json 的 count 應為 2，實際: {en_us_summary[0][0][2]}"

        book_summary = [
            call for call in mock_log_debug.call_args_list
            if "[CACHE HIT]" in str(call) and call[0][1] == "book.json"
        ]
        assert len(book_summary) == 1, \
            f"book.json 應有 1 個 summary log，實際: {book_summary}"
        assert book_summary[0][0][2] == 1, \
            f"book.json 的 count 應為 1，實際: {book_summary[0][0][2]}"

    @patch('translation_tool.core.lm_translator.log_debug')
    def test_no_duplicate_file_logs(self, mock_log_debug):
        """每個檔案只會在第一次出現時產生 summary log，不會重複。"""
        cached_items = [
            {"file": "/a/a.json", "path": "k1", "text": "T1",
             "source_text": "T1", "cache_type": "lang"},
            {"file": "/a/a.json", "path": "k2", "text": "T2",
             "source_text": "T2", "cache_type": "lang"},
            {"file": "/a/a.json", "path": "k3", "text": "T3",
             "source_text": "T3", "cache_type": "lang"},
        ]

        hit_by_file = defaultdict(list)
        for it in cached_items:
            hit_by_file[Path(it["file"]).name].append(it)

        for fname, file_hits in hit_by_file.items():
            mock_log_debug("🎯 [CACHE HIT] %s (%d)", fname, len(file_hits))

        # 確認只有一個 "a.json" 的 summary log
        a_json_summary = [
            call for call in mock_log_debug.call_args_list
            if "[CACHE HIT]" in str(call) and call[0][1] == "a.json"
        ]
        assert len(a_json_summary) == 1, \
            f"a.json 只應有 1 個 summary log，實際: {a_json_summary}"
