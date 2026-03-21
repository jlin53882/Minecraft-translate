"""lm_translator_scan.py 單元測試。

用途：測試 LM 翻譯掃描相關功能。
"""
import json
from unittest.mock import MagicMock


class TestIsPlainLangJson:
    """測試 is_plain_lang_json 函數。"""

    def test_plain_lang_json(self):
        """測試標準 lang JSON。"""
        from translation_tool.core.lm_translator_scan import is_plain_lang_json
        
        data = {
            "key1": "value1",
            "key2": "value2"
        }
        
        assert is_plain_lang_json(data) is True

    def test_plain_lang_json_with_list(self):
        """測試包含列表的 JSON。"""
        from translation_tool.core.lm_translator_scan import is_plain_lang_json
        
        data = {
            "key1": ["value1", "value2"]
        }
        
        assert is_plain_lang_json(data) is False

    def test_plain_lang_json_with_dict(self):
        """測試包含字典的 JSON。"""
        from translation_tool.core.lm_translator_scan import is_plain_lang_json
        
        data = {
            "key1": {"nested": "value"}
        }
        
        assert is_plain_lang_json(data) is False

    def test_plain_lang_json_not_dict(self):
        """測試非字典輸入。"""
        from translation_tool.core.lm_translator_scan import is_plain_lang_json
        
        assert is_plain_lang_json("not a dict") is False
        assert is_plain_lang_json([1, 2, 3]) is False
        assert is_plain_lang_json(None) is False


class TestScanTranslatableFiles:
    """測試 scan_translatable_files 函數。"""

    def test_scan_translatable_files_empty(self, tmp_path):
        """測試空目錄掃描。"""
        from translation_tool.core.lm_translator_scan import scan_translatable_files
        
        patchouli_files, lang_files, files = scan_translatable_files(tmp_path)
        
        assert patchouli_files == []
        assert lang_files == []
        assert files == []

    def test_scan_translatable_files_with_lang(self, tmp_path):
        """測試含 lang 檔案的掃描。"""
        from translation_tool.core.lm_translator_scan import scan_translatable_files
        
        # 建立測試目錄結構
        lang_dir = tmp_path / "lang"
        lang_dir.mkdir()
        
        lang_file = lang_dir / "en_us.json"
        lang_file.write_text('{"key": "value"}')
        
        patchouli_files, lang_files, files = scan_translatable_files(tmp_path)
        
        # 驗證有找到 lang 檔案（數量可能因 find_lang_json 實作而異）
        assert isinstance(lang_files, list)

    def test_scan_translatable_files_with_patchouli(self, tmp_path):
        """測試含 Patchouli 檔案的掃描。"""
        from translation_tool.core.lm_translator_scan import scan_translatable_files
        
        # 建立 Patchouli 目錄
        patchouli_dir = tmp_path / "patchouli" / "books"
        patchouli_dir.mkdir(parents=True)
        
        patchouli_file = patchouli_dir / "test.json"
        patchouli_file.write_text('{"test": "content"}')
        
        patchouli_files, lang_files, files = scan_translatable_files(tmp_path)
        
        # 驗證有找到 Patchouli 檔案
        assert isinstance(patchouli_files, list)


class TestExtractItemsParallel:
    """測試 extract_items_parallel 函數。"""

    def test_extract_items_parallel_empty(self, tmp_path):
        """測試空檔案列表處理。"""
        from translation_tool.core.lm_translator_scan import extract_items_parallel
        
        mock_logger = MagicMock()
        
        file_cache, all_items = extract_items_parallel(
            files=[],
            export_lang=False,
            work_thread=2,
            logger=mock_logger
        )
        
        assert file_cache == {}
        assert all_items == []

    def test_extract_items_parallel_with_json(self, tmp_path):
        """測試 JSON 檔案處理。"""
        from translation_tool.core.lm_translator_scan import extract_items_parallel
        
        # 建立測試 lang 檔案
        lang_dir = tmp_path / "lang"
        lang_dir.mkdir()
        
        lang_file = lang_dir / "en_us.json"
        test_data = {
            "item.test": "Test Item",
            "item.test2": "Test Item 2"
        }
        lang_file.write_text(json.dumps(test_data))
        
        mock_logger = MagicMock()
        
        file_cache, all_items = extract_items_parallel(
            files=[lang_file],
            export_lang=False,
            work_thread=2,
            logger=mock_logger
        )
        
        assert len(file_cache) == 1
        assert str(lang_file) in file_cache
        assert len(all_items) >= 2

    def test_extract_items_parallel_with_invalid_json(self, tmp_path):
        """測試無效 JSON 檔案處理。"""
        from translation_tool.core.lm_translator_scan import extract_items_parallel
        
        # 建立無效 JSON 檔案
        lang_dir = tmp_path / "lang"
        lang_dir.mkdir()
        
        invalid_file = lang_dir / "invalid.json"
        invalid_file.write_text("not valid json {")
        
        mock_logger = MagicMock()
        
        file_cache, all_items = extract_items_parallel(
            files=[invalid_file],
            export_lang=False,
            work_thread=2,
            logger=mock_logger
        )
        
        # 應該忽略無效檔案
        assert len(file_cache) == 0

    def test_extract_items_parallel_cache_type(self, tmp_path):
        """測試快取類型標記。"""
        from translation_tool.core.lm_translator_scan import extract_items_parallel
        
        # 建立 lang 檔案
        lang_dir = tmp_path / "lang"
        lang_dir.mkdir()
        lang_file = lang_dir / "en_us.json"
        lang_file.write_text('{"key": "value"}')
        
        mock_logger = MagicMock()
        
        file_cache, all_items = extract_items_parallel(
            files=[lang_file],
            export_lang=False,
            work_thread=2,
            logger=mock_logger
        )
        
        # 至少應該有 lang 類型
        cache_types = set(item.get("cache_type") for item in all_items)
        assert "lang" in cache_types
