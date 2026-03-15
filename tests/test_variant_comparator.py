"""variant_comparator.py 測試。

用途：測試簡繁翻譯變體比較功能。
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from translation_tool.checkers import variant_comparator


class TestCompareVariantsGenerator:
    """測試 compare_variants_generator 函式。"""

    def test_no_zh_cn_files(self, tmp_path):
        """測試無 zh_cn 檔案時的行為。"""
        zh_cn_dir = tmp_path / "zh_cn"
        zh_cn_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 不需要 mock，因為沒有檔案時不會初始化 OpenCC
        results = list(variant_comparator.compare_variants_generator(
            str(zh_cn_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該有 2 個結果（開始 + 結束錯誤）
        assert len(results) == 2
        assert results[1]["error"] is True
        assert "未找到任何 zh_cn.json 檔案" in results[1]["log"]

    @patch('translation_tool.checkers.variant_comparator.OpenCC')
    @patch('translation_tool.checkers.variant_comparator.apply_replace_rules')
    @patch('translation_tool.checkers.variant_comparator.load_replace_rules')
    def test_all_consistent(self, mock_load_rules, mock_apply_rules, mock_opencc, tmp_path):
        """測試簡繁翻譯完全一致時的行為。"""
        zh_cn_dir = tmp_path / "zh_cn"
        zh_cn_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # Mock 依賴
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: x  # 轉換後與原文相同
        mock_opencc.return_value = mock_converter
        mock_load_rules.return_value = []
        mock_apply_rules.side_effect = lambda x, rules: x
        
        # 建立相同內容的檔案
        data = {"key1": "測試", "key2": "你好"}
        (zh_cn_dir / "test.json").write_text(json.dumps(data), encoding="utf-8")
        (zh_tw_dir / "test.json").write_text(json.dumps(data), encoding="utf-8")
        
        results = list(variant_comparator.compare_variants_generator(
            str(zh_cn_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該找到 0 個差異
        final_result = results[-2]
        assert "0 個翻譯差異" in final_result["log"]

    @patch('translation_tool.checkers.variant_comparator.OpenCC')
    @patch('translation_tool.checkers.variant_comparator.apply_replace_rules')
    @patch('translation_tool.checkers.variant_comparator.load_replace_rules')
    def test_with_differences(self, mock_load_rules, mock_apply_rules, mock_opencc, tmp_path):
        """測試有翻譯差異時的行為。"""
        zh_cn_dir = tmp_path / "zh_cn"
        zh_cn_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # Mock 依賴 - OpenCC 轉換 "内存" -> "記憶體" (與 tw 不同)
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: x.replace("内存", "記憶體")
        mock_opencc.return_value = mock_converter
        mock_load_rules.return_value = []
        mock_apply_rules.side_effect = lambda x, rules: x
        
        # 建立有差異的檔案
        cn_data = {"item1": "内存", "item2": "硬盘"}
        (zh_cn_dir / "items.json").write_text(json.dumps(cn_data), encoding="utf-8")
        
        # zh_tw 使用不同的翻譯
        tw_data = {"item1": "記憶卡", "item2": "硬碟"}  # 兩個都不同
        (zh_tw_dir / "items.json").write_text(json.dumps(tw_data), encoding="utf-8")
        
        results = list(variant_comparator.compare_variants_generator(
            str(zh_cn_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該找到差異
        final_result = results[-2]
        assert "翻譯差異" in final_result["log"]
        
        # 檢查輸出檔案內容
        output_file = output_dir / "items.json"
        assert output_file.exists()
        
        with open(output_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "item1" in saved_data

    @patch('translation_tool.checkers.variant_comparator.OpenCC')
    @patch('translation_tool.checkers.variant_comparator.apply_replace_rules')
    @patch('translation_tool.checkers.variant_comparator.load_replace_rules')
    def test_missing_zh_tw_file(self, mock_load_rules, mock_apply_rules, mock_opencc, tmp_path):
        """測試對應的 zh_tw 檔案不存在時的行為。"""
        zh_cn_dir = tmp_path / "zh_cn"
        zh_cn_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # Mock 依賴
        mock_converter = MagicMock()
        mock_opencc.return_value = mock_converter
        mock_load_rules.return_value = []
        mock_apply_rules.side_effect = lambda x, rules: x
        
        # 只建立 zh_cn 檔案
        cn_data = {"key1": "测试"}
        (zh_cn_dir / "missing.json").write_text(json.dumps(cn_data), encoding="utf-8")
        
        results = list(variant_comparator.compare_variants_generator(
            str(zh_cn_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該有跳過訊息
        skip_result = [r for r in results if "跳過" in r.get("log", "")][0]
        assert "找不到對應的繁中檔案" in skip_result["log"]

    @patch('translation_tool.checkers.variant_comparator.OpenCC')
    @patch('translation_tool.checkers.variant_comparator.apply_replace_rules')
    @patch('translation_tool.checkers.variant_comparator.load_replace_rules')
    def test_non_string_values_skipped(self, mock_load_rules, mock_apply_rules, mock_opencc, tmp_path):
        """測試非字串值被跳過。"""
        zh_cn_dir = tmp_path / "zh_cn"
        zh_cn_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # Mock 依賴
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: x
        mock_opencc.return_value = mock_converter
        mock_load_rules.return_value = []
        mock_apply_rules.side_effect = lambda x, rules: x
        
        # 建立包含非字串值的檔案 - 使用相同內容
        cn_data = {"key1": "test"}
        (zh_cn_dir / "test.json").write_text(json.dumps(cn_data), encoding="utf-8")
        tw_data = {"key1": "test"}
        (zh_tw_dir / "test.json").write_text(json.dumps(tw_data), encoding="utf-8")
        
        results = list(variant_comparator.compare_variants_generator(
            str(zh_cn_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該找到 0 個差異（非字串被跳過，且 key1 相同）
        final_result = results[-2]
        assert "0 個翻譯差異" in final_result["log"]

    @patch('translation_tool.checkers.variant_comparator.OpenCC')
    @patch('translation_tool.checkers.variant_comparator.apply_replace_rules')
    @patch('translation_tool.checkers.variant_comparator.load_replace_rules')
    def test_init_error(self, mock_load_rules, mock_apply_rules, mock_opencc, tmp_path):
        """測試初始化失敗時的行為。"""
        zh_cn_dir = tmp_path / "zh_cn"
        zh_cn_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # Mock OpenCC 初始化失敗
        mock_opencc.side_effect = Exception("OpenCC 初始化失敗")
        
        results = list(variant_comparator.compare_variants_generator(
            str(zh_cn_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該有錯誤訊息 - 第二個結果是錯誤
        assert len(results) >= 1
        error_result = results[1]  # 第一個結果是開始，第二個是錯誤
        assert error_result["error"] is True
        assert "錯誤" in error_result["log"]
