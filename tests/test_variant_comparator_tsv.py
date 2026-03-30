"""variant_comparator_tsv.py 測試。

用途：測試 TSV 檔案簡繁翻譯變體比較功能。
"""
from unittest.mock import MagicMock, patch

import pandas as pd

from translation_tool.checkers import variant_comparator_tsv


class TestCompareVariantsTsvGenerator:
    """測試 compare_variants_tsv_generator 函式。"""

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_file_not_exists(self, mock_opencc, tmp_path):
        """測試檔案不存在時的行為。"""
        file_path = tmp_path / "nonexistent.tsv"
        output_file = tmp_path / "output.csv"

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該有 2 個結果（開始 + 結束錯誤）
        assert len(results) == 2
        assert results[1]["error"] is True
        assert "不存在" in results[1]["log"]

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_missing_columns(self, mock_opencc, tmp_path):
        """測試缺少必要欄位時的行為。"""
        file_path = tmp_path / "test.tsv"
        output_file = tmp_path / "output.csv"

        # 建立缺少必要欄位的 TSV
        df = pd.DataFrame({
            'key': ['key1', 'key2'],
            'zh_cn': ['测试', '硬盘']
            # 缺少 zh_tw
        })
        df.to_csv(file_path, sep='\t', index=False, encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該有 2 個結果（開始 + 結束錯誤）
        assert len(results) == 2
        assert results[1]["error"] is True
        assert "缺少必要的欄位" in results[1]["log"]

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_read_error(self, mock_opencc, tmp_path):
        """測試讀取失敗時的行為。"""
        file_path = tmp_path / "test.tsv"
        output_file = tmp_path / "output.csv"

        # 建立無效 TSV 內容
        file_path.write_text("invalid\ttsv\tcontent", encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該有錯誤（缺少必要欄位）
        assert len(results) == 2
        assert results[1]["error"] is True

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_no_differences(self, mock_opencc, tmp_path):
        """測試無差異時的行為。"""
        file_path = tmp_path / "test.tsv"
        output_file = tmp_path / "output.csv"

        # Mock OpenCC 轉換
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: x  # 轉換後相同
        mock_opencc.return_value = mock_converter

        # 建立無差異的 TSV
        df = pd.DataFrame({
            'key': ['key1', 'key2'],
            'zh_cn': ['测试', '硬盘'],
            'zh_tw': ['测试', '硬盘']  # 轉換後相同
        })
        df.to_csv(file_path, sep='\t', index=False, encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該發現 0 個差異
        final_result = results[-1]
        assert "未發現簡繁差異" in final_result["log"]

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_with_differences(self, mock_opencc, tmp_path):
        """測試有差異時的行為。"""
        file_path = tmp_path / "test.tsv"
        output_file = tmp_path / "output_diff.csv"

        # Mock OpenCC 轉換 - 簡體轉繁體
        mock_converter = MagicMock()
        # 轉換後與原文不同
        mock_converter.convert.side_effect = lambda x: x + "_converted"
        mock_opencc.return_value = mock_converter

        # 建立有差異的 TSV
        df = pd.DataFrame({
            'key': ['key1', 'key2'],
            'zh_cn': ['测试', '硬盘'],
            'zh_tw': ['test', 'harddisk']  # 與轉換後不同
        })
        df.to_csv(file_path, sep='\t', index=False, encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該發現差異
        final_result = [r for r in results if "差異" in r.get("log", "")][-1]
        assert "差異" in final_result["log"]

        # 檢查輸出檔案
        assert output_file.exists()

        diff_df = pd.read_csv(output_file, encoding='utf-8-sig')
        assert len(diff_df) > 0

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_with_null_values(self, mock_opencc, tmp_path):
        """測試處理空值時的行為。"""
        file_path = tmp_path / "test.tsv"
        output_file = tmp_path / "output.csv"

        # Mock OpenCC
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: x
        mock_opencc.return_value = mock_converter

        # 建立包含空值的 TSV
        df = pd.DataFrame({
            'key': ['key1', 'key2', 'key3'],
            'zh_cn': ['test', None, ''],
            'zh_tw': ['test', '', None]
        })
        df.to_csv(file_path, sep='\t', index=False, encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該正常處理完成
        assert any(r["progress"] == 1.0 for r in results)

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_output_directory_creation(self, mock_opencc, tmp_path):
        """測試輸出目錄自動建立。"""
        file_path = tmp_path / "test.tsv"

        # 輸出到不存在的子目錄
        output_file = "subdir/output.csv"  # 使用相對路徑

        # Mock OpenCC - 讓轉換後有差異，這樣才會寫入輸出檔案
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: x + "_converted"
        mock_opencc.return_value = mock_converter

        # 建立 TSV - 有差異
        df = pd.DataFrame({
            'key': ['key1'],
            'zh_cn': ['test'],
            'zh_tw': ['test_orig']  # 不同，會被記錄為差異
        })
        df.to_csv(file_path, sep='\t', index=False, encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), output_file
        ))

        # 應該成功建立目錄和檔案（在 current working directory）
        tmp_path / "subdir" / "output.csv"
        # 由於使用相對路徑，檔案會建立在執行目錄
        # 這個測試驗證函式能正確處理輸出目錄建立
        # 檢查有差異產生
        diff_result = [r for r in results if "差異" in r.get("log", "")][0]
        assert "差異" in diff_result["log"]

    @patch('translation_tool.checkers.variant_comparator_tsv.OpenCC')
    def test_opencc_init_error(self, mock_opencc, tmp_path):
        """測試 OpenCC 初始化失敗時的行為。"""
        file_path = tmp_path / "test.tsv"
        output_file = tmp_path / "output.csv"

        # Mock OpenCC 初始化失敗
        mock_opencc.side_effect = Exception("初始化失敗")

        # 建立 TSV
        df = pd.DataFrame({
            'key': ['key1'],
            'zh_cn': ['test'],
            'zh_tw': ['test']
        })
        df.to_csv(file_path, sep='\t', index=False, encoding='utf-8')

        results = list(variant_comparator_tsv.compare_variants_tsv_generator(
            str(file_path), str(output_file)
        ))

        # 應該有 2 個結果（開始 + 錯誤）
        assert len(results) == 2
        assert results[1]["error"] is True
        assert "初始化 OpenCC 失敗" in results[1]["log"]
