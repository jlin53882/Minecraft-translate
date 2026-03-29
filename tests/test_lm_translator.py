"""lm_translator.py 單元測試

測試目標：翻譯目錄生成器。
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestTranslateDirectoryGenerator:
    """translate_directory_generator 測試"""

    @patch('translation_tool.core.lm_translator.save_translation_cache')
    @patch('translation_tool.core.lm_translator.add_to_cache')
    @patch('translation_tool.core.lm_translator.get_cache_dict_ref')
    @patch('translation_tool.core.lm_translator.extract_items_parallel')
    @patch('translation_tool.core.lm_translator.scan_translatable_files')
    @patch('translation_tool.core.lm_translator.reload_translation_cache')
    @patch('translation_tool.core.lm_translator.validate_api_keys')
    @patch('translation_tool.core.lm_translator.translate_batch_smart')
    def test_export_lang_format(
        self, mock_translate, mock_validate, mock_reload,
        mock_scan, mock_extract, mock_cache_ref, mock_add, mock_save, tmp_path
    ):
        """測試 .lang 格式導出"""
        from translation_tool.core.lm_translator import translate_directory_generator

        mock_validate.return_value = None

        # 建立測試目錄結構
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        
        lang_dir = input_dir / "lang"
        lang_dir.mkdir()
        lang_file = lang_dir / "en_us.json"
        lang_file.write_text('{"test.key": "Hello"}')

        mock_scan.return_value = ([], [lang_file], [lang_file])
        mock_cache_ref.return_value = {}
        mock_extract.return_value = (
            {str(lang_file): {"test.key": "Hello"}},
            [{"path": "test.key", "text": "Hello", "source_text": "Hello", "cache_type": "lang", "file": str(lang_file)}]
        )
        
        # Mock translate_batch_smart 避免真實 API 呼叫
        mock_translate.return_value = (
            [{"path": "test.key", "text": "你好", "file": str(lang_file), "source_text": "Hello", "cache_type": "lang"}],
            "AUTO"
        )

        gen = translate_directory_generator(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            export_lang=True,
            write_new_cache=True
        )

        results = list(gen)

        assert len(results) > 0
        assert results[-1]["progress"] == 1.0

    @patch('translation_tool.core.lm_translator.scan_translatable_files')
    def test_scan_returns_correct_structure(self, mock_scan, tmp_path):
        """測試掃描返回正確結構"""
        from translation_tool.core.lm_translator import scan_translatable_files

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        
        lang_dir = input_dir / "lang"
        lang_dir.mkdir()
        lang_file = lang_dir / "en_us.json"
        lang_file.write_text("{}")

        mock_scan.return_value = ([], [lang_file], [lang_file])
        
        result = scan_translatable_files(str(input_dir))

        assert len(result) == 3  # (books, lang_files, patchouli)


class TestFormatDuration:
    """時間格式化測試"""

    def test_format_duration_seconds_basic(self):
        """測試基本秒數格式化"""
        from translation_tool.core.lm_translator import format_duration_seconds
        
        result = format_duration_seconds(75)
        assert "1 分" in result
        assert "15 秒" in result

    def test_format_duration_seconds_hours(self):
        """測試小時格式化"""
        from translation_tool.core.lm_translator import format_duration_seconds
        
        result = format_duration_seconds(3661)
        assert "1 小時" in result
