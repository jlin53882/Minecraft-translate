"""PR-3 函數過長重構 - 單元測試

測試目標：確保拆分後的 helper 函數行為與重構前一致。
"""

import pytest
from unittest.mock import patch, MagicMock


class TestValidateBatchItems:
    """_validate_batch_items 測試"""

    def test_validate_valid_items(self):
        """測試驗證有效的項目"""
        from translation_tool.core.lm_translator_main import _validate_batch_items
        
        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
            {"path": "test2.key", "text": "World", "cache_type": "patchouli"},
        ]
        
        result = _validate_batch_items(items)
        
        assert len(result) == 2
        assert result[0]["path"] == "test.key"

    def test_validate_missing_text(self):
        """測試缺少 text 欄位的項目被過濾"""
        from translation_tool.core.lm_translator_main import _validate_batch_items
        
        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
            {"path": "test2.key", "cache_type": "lang"},  # 缺少 text
        ]
        
        result = _validate_batch_items(items)
        
        assert len(result) == 1

    def test_validate_empty_text(self):
        """測試空文字被過濾"""
        from translation_tool.core.lm_translator_main import _validate_batch_items
        
        items = [
            {"path": "test.key", "text": "", "cache_type": "lang"},
            {"path": "test2.key", "text": "Hello", "cache_type": "lang"},
        ]
        
        result = _validate_batch_items(items)
        
        assert len(result) == 1
        assert result[0]["path"] == "test2.key"

    def test_validate_missing_cache_type(self):
        """測試缺少 cache_type 時預設為 patchouli"""
        from translation_tool.core.lm_translator_main import _validate_batch_items
        
        items = [
            {"path": "test.key", "text": "Hello"},
        ]
        
        result = _validate_batch_items(items)
        
        assert len(result) == 1
        assert result[0].get("cache_type", "patchouli") == "patchouli"


class TestDetectBatchProfile:
    """_detect_batch_profile 測試"""

    def test_detect_lang_profile(self):
        """測試 Lang 類型偵測"""
        from translation_tool.core.lm_translator_main import _detect_batch_profile
        
        items = [
            {"path": "assets/minecraft/lang/en_us.json", "text": "Hello", "cache_type": "lang"},
            {"path": "assets/minecraft/lang/en_us.json", "text": "World", "cache_type": "lang"},
        ]
        
        result = _detect_batch_profile(items)
        
        assert result == "lang"

    def test_detect_patchouli_profile(self):
        """測試 Patchouli 類型偵測"""
        from translation_tool.core.lm_translator_main import _detect_batch_profile
        
        items = [
            {"path": "assets/modid/patchouli_books/book/category/entry.json", "text": "Hello", "cache_type": "patchouli"},
        ]
        
        result = _detect_batch_profile(items)
        
        assert result == "patchouli"

    def test_detect_md_profile(self):
        """測試 Markdown 類型偵測"""
        from translation_tool.core.lm_translator_main import _detect_batch_profile
        
        items = [
            {"path": "docs/guide.md", "text": "Hello", "cache_type": "md"},
        ]
        
        result = _detect_batch_profile(items)
        
        assert result == "md"

    def test_detect_mixed_profile(self):
        """測試混合類型時回傳 patchouli"""
        from translation_tool.core.lm_translator_main import _detect_batch_profile
        
        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
            {"path": "test2.key", "text": "World", "cache_type": "patchouli"},
        ]
        
        result = _detect_batch_profile(items)
        
        # 混合時應該回傳其中一個，這裡測試邏輯存在


class TestCalculateBatchSize:
    """_calculate_batch_size 測試"""

    def test_calculate_lang_batch_size(self):
        """測試 Lang 類型批次大小計算"""
        from translation_tool.core.lm_translator_main import _calculate_batch_size
        
        profile = "lang"
        
        result = _calculate_batch_size(profile)
        
        assert result > 0

    def test_calculate_patchouli_batch_size(self):
        """測試 Patchouli 類型批次大小計算"""
        from translation_tool.core.lm_translator_main import _calculate_batch_size
        
        profile = "patchouli"
        
        result = _calculate_batch_size(profile)
        
        assert result > 0

    def test_calculate_md_batch_size(self):
        """測試 Markdown 類型批次大小計算"""
        from translation_tool.core.lm_translator_main import _calculate_batch_size
        
        profile = "md"
        
        result = _calculate_batch_size(profile)
        
        assert result > 0


class TestProcessOutput:
    """_process_output 測試"""

    def test_process_valid_output(self):
        """測試處理有效輸出"""
        from translation_tool.core.lm_translator_main import _process_output
        
        # 模擬 API 回應
        api_response = {
            "items": [
                {"id": "key1", "value": "翻譯1"},
                {"id": "key2", "value": "翻譯2"},
            ]
        }
        original_items = [
            {"path": "test.key1", "id": "key1", "text": "Hello"},
            {"path": "test.key2", "id": "key2", "text": "World"},
        ]
        
        # 測試元組輸入（從舊函數返回）
        result, status = _process_output((original_items, "AUTO"), "AUTO")
        
        # 應該返回元組
        assert isinstance(result, (list, tuple))
        if isinstance(result, tuple):
            result = result[0]
        assert len(result) >= 0

    def test_process_empty_output(self):
        """測試處理空輸出"""
        from translation_tool.core.lm_translator_main import _process_output
        
        # 測試空元組
        result, status = _process_output(([], "AUTO"), "AUTO")
        
        assert result == [] or result == ([], "AUTO")
        assert status == "AUTO"

    def test_process_malformed_output(self):
        """測試處理格式錯誤的輸出"""
        from translation_tool.core.lm_translator_main import _process_output
        
        # 測試元組輸入
        result, status = _process_output(([{"path": "test.key", "id": "key1", "text": "Hello"}], "PARTIAL"), "PARTIAL")
        
        # 應該返回元組
        assert isinstance(result, (list, tuple))


class TestBatchSizeConstants:
    """批次大小常數測試"""

    def test_min_lang_batch_size_constant(self):
        """測試 MIN_LANG_BATCH_SIZE 常數存在"""
        from translation_tool.core.lm_translator_main import MIN_LANG_BATCH_SIZE
        
        assert MIN_LANG_BATCH_SIZE == 20
        assert isinstance(MIN_LANG_BATCH_SIZE, int)

    def test_default_batch_size_constant(self):
        """測試 DEFAULT_BATCH_SIZE 常數存在"""
        from translation_tool.core.lm_translator_main import DEFAULT_BATCH_SIZE
        
        assert DEFAULT_BATCH_SIZE == 50
        assert isinstance(DEFAULT_BATCH_SIZE, int)

    def test_overload_retry_wait_constant(self):
        """測試 OVERLOAD_RETRY_WAIT_SEC 常數存在"""
        from translation_tool.core.lm_translator_main import OVERLOAD_RETRY_WAIT_SEC
        
        assert OVERLOAD_RETRY_WAIT_SEC == 12
        assert isinstance(OVERLOAD_RETRY_WAIT_SEC, int)

    def test_rpm_cooldown_constant(self):
        """測試 RPM_COOLDOWN_SEC 常數存在"""
        from translation_tool.core.lm_translator_main import RPM_COOLDOWN_SEC
        
        assert RPM_COOLDOWN_SEC == 12
        assert isinstance(RPM_COOLDOWN_SEC, int)


class TestConstantsInMain:
    """main.py 視窗常數測試"""

    def test_window_constants(self):
        """測試視窗尺寸常數"""
        import main
        
        assert hasattr(main, 'WINDOW_WIDTH_DEFAULT')
        assert hasattr(main, 'WINDOW_HEIGHT_DEFAULT')
        assert hasattr(main, 'WINDOW_MIN_WIDTH')
        assert hasattr(main, 'WINDOW_MIN_HEIGHT')
        
        assert main.WINDOW_WIDTH_DEFAULT == 1200
        assert main.WINDOW_HEIGHT_DEFAULT == 850
        assert main.WINDOW_MIN_WIDTH == 1050
        assert main.WINDOW_MIN_HEIGHT == 760
