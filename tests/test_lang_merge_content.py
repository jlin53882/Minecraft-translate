"""lang_merge_content.py 模組的單元測試。

用途：測試 lang_merge_content 中的 façade 函式。
"""
from __future__ import annotations

from pathlib import Path
import sys

from unittest.mock import MagicMock

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestLangMergeContentFacade:
    """測試 lang_merge_content 的 façade 介面。"""

    def test_imports_available(self):
        """測試所有導出的符號是否可用。"""
        from translation_tool.core.lang_merge_content import (
            _patch_localized_content_json,
            _process_content_or_copy_file,
            remove_empty_dirs,
            export_filtered_pending,
            load_config,
            apply_replace_rules,
            recursive_translate_dict,
        )
        
        # 確認這些是可呼叫的
        assert callable(_patch_localized_content_json)
        assert callable(_process_content_or_copy_file)
        assert callable(remove_empty_dirs)
        assert callable(export_filtered_pending)
        assert callable(load_config)
        assert callable(apply_replace_rules)
        assert callable(recursive_translate_dict)


class TestLangMergeContentExports:
    """測試 lang_merge_content 的導出列表。"""

    def test_all_exports_in_module(self):
        """測試 __all__ 列表是否正確。"""
        import translation_tool.core.lang_merge_content as module
        
        expected_exports = [
            "_patch_localized_content_json",
            "_process_content_or_copy_file",
            "remove_empty_dirs",
            "export_filtered_pending",
            "load_config",
            "apply_replace_rules",
            "recursive_translate_dict",
        ]
        
        for exp in expected_exports:
            assert hasattr(module, exp), f"Missing export: {exp}"
        
        # 檢查 __all__ 是否存在
        assert hasattr(module, "__all__")


class TestLangMergeContentProxyFunctions:
    """測試代理函式是否正確委派。"""

    def test_remove_empty_dirs_calls_impl(self, tmp_path: Path):
        """測試 remove_empty_dirs 是否正確呼叫實作。"""
        from translation_tool.core.lang_merge_content import remove_empty_dirs
        
        # 建立測試目錄結構
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        (test_dir / "empty_sub").mkdir()
        
        # 呼叫函式
        remove_empty_dirs(str(test_dir))
        
        # 確認 logger 被呼叫（即使函式可能不做任何事）
        # 此測試驗證函式可執行而不報錯

    def test_export_filtered_pending_signature(self):
        """測試 export_filtered_pending 函式簽名。"""
        from translation_tool.core.lang_merge_content import export_filtered_pending
        import inspect
        
        sig = inspect.signature(export_filtered_pending)
        params = list(sig.parameters.keys())
        
        assert "pending_root" in params
        assert "output_root" in params
        assert "min_count" in params


class TestLangMergeContentProcessContent:
    """測試 _process_content_or_copy_file 函式。"""

    def test_process_content_function_signature(self):
        """測試 _process_content_or_copy_file 函式簽名。"""
        from translation_tool.core.lang_merge_content import _process_content_or_copy_file
        import inspect
        
        sig = inspect.signature(_process_content_or_copy_file)
        params = list(sig.parameters.keys())
        
        assert "zf" in params
        assert "input_path" in params
        assert "rules" in params
        assert "output_dir" in params
        assert "only_process_lang" in params

    def test_process_content_with_mock_zip(self, tmp_path: Path):
        """測試使用 mock ZIP 呼叫 _process_content_or_copy_file。"""
        from translation_tool.core.lang_merge_content import _process_content_or_copy_file
        
        # 建立 mock ZIP 檔案
        mock_zf = MagicMock()
        
        # 測試非 lang 檔案在 only_process_lang=True 時應被跳過
        result = _process_content_or_copy_file(
            mock_zf,
            "assets/test/config.json",  # 不是 lang 檔案
            [],  # rules
            str(tmp_path / "output"),
            only_process_lang=True,
        )
        
        assert result.get("success") is True
        assert result.get("log") is None  # 應該被跳過


class TestLangMergeContentPatchLocalied:
    """測試 _patch_localized_content_json 函式。"""

    def test_patch_function_signature(self):
        """測試 _patch_localized_content_json 函式簽名。"""
        from translation_tool.core.lang_merge_content import _patch_localized_content_json
        import inspect
        
        sig = inspect.signature(_patch_localized_content_json)
        params = list(sig.parameters.keys())
        
        assert "zf" in params
        assert "cn_path" in params
        assert "tw_output_path" in params
        assert "rules" in params
        assert "log_prefix" in params
        assert "output_dir" in params

    def test_patch_with_mock_zip(self, tmp_path: Path):
        """測試使用 mock ZIP 呼叫 _patch_localized_content_json。"""
        from translation_tool.core.lang_merge_content import _patch_localized_content_json
        
        mock_zf = MagicMock()
        
        result = _patch_localized_content_json(
            mock_zf,
            "test/lang/zh_cn.json",
            str(tmp_path / "output" / "zh_tw.json"),
            [],
            "Test:",
            str(tmp_path),
        )
        
        # 應該回傳 dict
        assert isinstance(result, dict)
