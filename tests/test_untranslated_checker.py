"""untranslated_checker.py 測試。

用途：測試未翻譯條目檢查功能。
"""
import json
import pytest

from translation_tool.checkers import untranslated_checker


class TestCheckUntranslatedGenerator:
    """測試 check_untranslated_generator 函式。"""

    def test_no_en_us_files(self, tmp_path):
        """測試無 en_us 檔案時的行為。"""
        en_us_dir = tmp_path / "en_us"
        en_us_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        results = list(untranslated_checker.check_untranslated_generator(
            str(en_us_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該有 2 個結果（開始 + 結束錯誤）
        assert len(results) == 2
        assert results[1]["error"] is True
        assert "未找到任何 en_us.json 檔案" in results[1]["log"]

    def test_all_translated(self, tmp_path):
        """測試全部翻譯完成時的行為。"""
        en_us_dir = tmp_path / "en_us"
        en_us_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立 en_us 檔案
        en_data = {"key1": "Hello", "key2": "World"}
        (en_us_dir / "test.json").write_text(
            json.dumps(en_data), encoding="utf-8"
        )
        
        # 建立完全對應的 zh_tw 檔案
        tw_data = {"key1": "你好", "key2": "世界"}
        (zh_tw_dir / "test.json").write_text(
            json.dumps(tw_data), encoding="utf-8"
        )
        
        results = list(untranslated_checker.check_untranslated_generator(
            str(en_us_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該找到 0 個未翻譯條目 - 最後一個結果包含統計
        final_result = results[-2]  # 倒數第二個結果包含統計
        assert "0 個未翻譯的條目" in final_result["log"]

    def test_missing_translation_keys(self, tmp_path):
        """測試部分 key 未翻譯時的行為。"""
        en_us_dir = tmp_path / "en_us"
        en_us_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立 en_us 檔案
        en_data = {"key1": "Hello", "key2": "World", "key3": "Test"}
        (en_us_dir / "test.json").write_text(
            json.dumps(en_data), encoding="utf-8"
        )
        
        # 建立只有部分 key 的 zh_tw 檔案
        tw_data = {"key1": "你好"}  # 缺少 key2, key3
        (zh_tw_dir / "test.json").write_text(
            json.dumps(tw_data), encoding="utf-8"
        )
        
        results = list(untranslated_checker.check_untranslated_generator(
            str(en_us_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該找到 2 個未翻譯條目 - 最後一個結果包含統計
        final_result = results[-2]  # 倒數第二個結果包含統計
        assert "2 個未翻譯的條目" in final_result["log"]
        
        # 檢查輸出檔案
        output_file = output_dir / "test.json"
        assert output_file.exists()
        
        with open(output_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "key2" in saved_data
        assert "key3" in saved_data

    def test_missing_zh_tw_file(self, tmp_path):
        """測試對應的 zh_tw 檔案不存在時的行為。"""
        en_us_dir = tmp_path / "en_us"
        en_us_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立 en_us 檔案
        en_data = {"key1": "Hello", "key2": "World"}
        (en_us_dir / "missing.json").write_text(
            json.dumps(en_data), encoding="utf-8"
        )
        
        # 不建立 zh_tw 檔案
        
        results = list(untranslated_checker.check_untranslated_generator(
            str(en_us_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該有警告訊息
        warning_result = [r for r in results if "警告" in r.get("log", "")][0]
        assert "找不到對應的繁中檔案" in warning_result["log"]
        
        # 檢查輸出 - 應該包含整個檔案
        output_file = output_dir / "missing.json"
        assert output_file.exists()

    def test_multiple_files(self, tmp_path):
        """測試多個檔案處理。"""
        en_us_dir = tmp_path / "en_us"
        en_us_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立多個 en_us 檔案
        en_data1 = {"key1": "Hello"}
        en_data2 = {"key2": "World"}
        (en_us_dir / "file1.json").write_text(json.dumps(en_data1), encoding="utf-8")
        (en_us_dir / "file2.json").write_text(json.dumps(en_data2), encoding="utf-8")
        
        # 建立對應的 zh_tw 檔案（部分翻譯）
        tw_data1 = {"key1": "你好"}
        (zh_tw_dir / "file1.json").write_text(json.dumps(tw_data1), encoding="utf-8")
        # file2.json 不建立 -> 整個檔案視為未翻譯
        
        results = list(untranslated_checker.check_untranslated_generator(
            str(en_us_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 應該處理 2 個檔案，1 個檔案有未翻譯（file2 整個未翻譯）
        final_result = results[-2]
        assert "1 個未翻譯的條目" in final_result["log"]

    def test_preserves_directory_structure(self, tmp_path):
        """測試保留目錄結構。"""
        en_us_dir = tmp_path / "en_us"
        en_us_dir.mkdir()
        zh_tw_dir = tmp_path / "zh_tw"
        zh_tw_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 在子目錄建立檔案
        subdir = en_us_dir / "subfolder"
        subdir.mkdir()
        
        en_data = {"key1": "Hello"}
        (subdir / "nested.json").write_text(json.dumps(en_data), encoding="utf-8")
        
        results = list(untranslated_checker.check_untranslated_generator(
            str(en_us_dir), str(zh_tw_dir), str(output_dir)
        ))
        
        # 檢查輸出結構
        output_file = output_dir / "subfolder" / "nested.json"
        assert output_file.exists()
