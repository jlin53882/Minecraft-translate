"""md_translation_assembly.py 單元測試。

用途：測試 Markdown 翻譯組裝相關功能。
"""
import pytest
from unittest.mock import patch, Mock, MagicMock, mock_open
from pathlib import Path
import json
import tempfile
import shutil


class TestStep1Extract:
    """測試 step1_extract 函數。"""

    @patch('translation_tool.core.md_translation_assembly.step1_extract_impl')
    def test_basic_extraction(self, mock_impl):
        """測試基本擷取功能。"""
        from translation_tool.core.md_translation_assembly import step1_extract
        
        mock_impl.return_value = {
            "extracted_count": 10,
            "files": ["test1.md", "test2.md"]
        }
        
        result = step1_extract(
            input_dir="input",
            pending_dir="pending",
            lang_mode="non_cjk_only",
        )
        
        assert result["extracted_count"] == 10

    @patch('translation_tool.core.md_translation_assembly.step1_extract_impl')
    def test_with_session(self, mock_impl):
        """測試帶 session 的擷取。"""
        from translation_tool.core.md_translation_assembly import step1_extract
        
        mock_impl.return_value = {}
        
        session = Mock()
        result = step1_extract(
            input_dir="input",
            pending_dir="pending",
            session=session,
        )
        
        mock_impl.assert_called_once()


class TestStep2Translate:
    """測試 step2_translate 函數。"""

    @patch('translation_tool.core.md_translation_assembly.step2_translate_impl')
    def test_basic_translation(self, mock_impl):
        """測試基本翻譯功能。"""
        from translation_tool.core.md_translation_assembly import step2_translate
        
        mock_impl.return_value = {
            "translated_blocks": 5,
            "cache_hit": 10,
            "cache_miss": 5,
        }
        
        result = step2_translate(
            pending_dir="pending",
            translated_dir="translated",
        )
        
        assert result["translated_blocks"] == 5

    @patch('translation_tool.core.md_translation_assembly.step2_translate_impl')
    def test_dry_run_mode(self, mock_impl):
        """測試 dry-run 模式。"""
        from translation_tool.core.md_translation_assembly import step2_translate
        
        mock_impl.return_value = {}
        
        result = step2_translate(
            pending_dir="pending",
            translated_dir="translated",
            dry_run=True,
        )
        
        # 驗證 dry_run 參數被傳遞
        call_kwargs = mock_impl.call_args.kwargs
        assert call_kwargs.get('dry_run') is True


class TestStep3Inject:
    """測試 step3_inject 函數。"""

    @patch('translation_tool.core.md_translation_assembly.step3_inject_impl')
    def test_basic_injection(self, mock_impl):
        """測試基本注入功能。"""
        from translation_tool.core.md_translation_assembly import step3_inject
        
        mock_impl.return_value = {
            "written_files": 3,
        }
        
        result = step3_inject(
            input_dir="input",
            json_dir="json",
            final_dir="final",
        )
        
        assert result["written_files"] == 3


class TestRunMdPipeline:
    """測試 run_md_pipeline 函數。"""

    def test_full_pipeline(self, tmp_path):
        """測試完整流程。"""
        from translation_tool.core.md_translation_assembly import run_md_pipeline
        from translation_tool.core.md_translation_assembly import step1_extract, step2_translate, step3_inject
        
        # 建立臨時輸入目錄
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 使用 patch 來 mock 內部函數
        with patch('translation_tool.core.md_translation_assembly.step1_extract', return_value={"extracted_count": 10}):
            with patch('translation_tool.core.md_translation_assembly.step2_translate', return_value={"translated_blocks": 5, "cache_hit": 3, "cache_miss": 2, "files": 2, "total_blocks": 5}):
                with patch('translation_tool.core.md_translation_assembly.step3_inject', return_value={"written_files": 2}):
                    with patch('translation_tool.core.md_translation_assembly._count_md_pending_docs', return_value=5):
                        result = run_md_pipeline(
                            input_dir=str(input_dir),
                            output_dir=str(output_dir),
                        )
        
        assert "paths" in result
        assert "step1" in result
        assert "step2" in result

    def test_skip_extract_step(self, tmp_path):
        """測試略過擷取步驟。"""
        from translation_tool.core.md_translation_assembly import run_md_pipeline
        
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        
        with patch('translation_tool.core.md_translation_assembly._count_md_pending_docs', return_value=0):
            result = run_md_pipeline(
                input_dir=str(input_dir),
                step_extract=False,
            )
        
        assert result["step1"]["skipped"] is True

    def test_skip_translate_step(self, tmp_path):
        """測試略過翻譯步驟。"""
        from translation_tool.core.md_translation_assembly import run_md_pipeline
        
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        
        with patch('translation_tool.core.md_translation_assembly._count_md_pending_docs', return_value=0):
            result = run_md_pipeline(
                input_dir=str(input_dir),
                step_translate=False,
            )
        
        assert result["step2"]["skipped"] is True

    def test_dry_run_mode(self, tmp_path):
        """測試 dry-run 模式。"""
        from translation_tool.core.md_translation_assembly import run_md_pipeline
        
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        
        # Mock 所有可能調用的函數
        with patch('translation_tool.core.md_translation_assembly._count_md_pending_docs', return_value=5):
            with patch('translation_tool.core.md_translation_assembly.step1_extract', return_value={"extracted_count": 10}):
                with patch('translation_tool.core.md_translation_assembly.step2_translate', return_value={"skipped": True, "reason": "dry_run"}):
                    with patch('translation_tool.core.md_translation_assembly.step3_inject', return_value={"skipped": True, "reason": "dry_run"}):
                        result = run_md_pipeline(
                            input_dir=str(input_dir),
                            dry_run=True,
                        )
        
        assert result["step3"]["skipped"] is True
        assert result["step3"]["reason"] == "dry_run"

    def test_invalid_input_dir(self):
        """測試無效輸入目錄。"""
        from translation_tool.core.md_translation_assembly import run_md_pipeline
        
        with pytest.raises(FileNotFoundError):
            run_md_pipeline(input_dir="nonexistent_directory")


class TestModuleExports:
    """測試模組導出。"""

    def test_exports(self):
        """測試導出的函數和類別。"""
        from translation_tool.core.md_translation_assembly import (
            _ProgressProxy,
            step1_extract,
            step2_translate,
            step3_inject,
            run_md_pipeline,
        )
        assert callable(step1_extract)
        assert callable(step2_translate)
        assert callable(step3_inject)
        assert callable(run_md_pipeline)
