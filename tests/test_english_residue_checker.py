"""english_residue_checker.py 測試。

用途：測試翻譯檔案中英文殘留檢查功能。
"""
import json

from translation_tool.checkers import english_residue_checker


class TestFindJsonFiles:
    """測試 find_json_files 輔助函式。"""

    def test_find_json_files_in_empty_dir(self, tmp_path):
        """測試空目錄應回傳空列表。"""
        result = list(english_residue_checker.find_json_files(str(tmp_path)))
        assert result == []

    def test_find_json_files_with_json_files(self, tmp_path):
        """測試找出目錄中的 json 檔案。"""
        # 建立測試檔案
        (tmp_path / "file1.json").write_text("{}", encoding="utf-8")
        (tmp_path / "file2.txt").write_text("test", encoding="utf-8")
        (tmp_path / "file3.json").write_text("{}", encoding="utf-8")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.json").write_text("{}", encoding="utf-8")

        result = list(english_residue_checker.find_json_files(str(tmp_path)))
        
        # 應找到 3 個 json 檔案
        assert len(result) == 3
        assert all(f.endswith(".json") for f in result)


class TestCheckEnglishResidueGenerator:
    """測試 check_english_residue_generator 函式。"""

    def test_no_json_files(self, tmp_path):
        """測試無 json 檔案時的行為。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        
        results = list(english_residue_checker.check_english_residue_generator(
            str(input_dir), str(output_dir)
        ))
        
        # 應該有 2 個結果（開始 + 結束錯誤）
        assert len(results) == 2
        assert results[1]["error"] is True
        assert "未找到任何 json 檔案" in results[1]["log"]

    def test_no_english_residue(self, tmp_path):
        """測試無英文殘留時的行為。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立不含英文的翻譯檔案
        test_data = {"key1": "測試", "key2": "你好世界"}
        (input_dir / "test.json").write_text(
            json.dumps(test_data, ensure_ascii=False), encoding="utf-8"
        )
        
        results = list(english_residue_checker.check_english_residue_generator(
            str(input_dir), str(output_dir)
        ))
        
        # 應該完成並找到 0 個可疑條目 - 最後一個結果包含統計
        final_result = results[-2]  # 倒數第二個結果包含統計
        assert "0 個可疑殘留英文條目" in final_result["log"]

    def test_with_english_residue(self, tmp_path):
        """測試有英文殘留時的行為。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立含英文的翻譯檔案 - key 有英文，value 也有英文
        test_data = {
            "item.iron_sword": "Iron Sword 鐵劍",
            "item.diamond_pickaxe": "Diamond Pickaxe 鑽石鎬",
            "pure_chinese": "純中文"
        }
        (input_dir / "items.json").write_text(
            json.dumps(test_data, ensure_ascii=False), encoding="utf-8"
        )
        
        results = list(english_residue_checker.check_english_residue_generator(
            str(input_dir), str(output_dir)
        ))
        
        # 應該找到英文殘留 - 最後一個結果包含統計
        final_result = results[-2]  # 倒數第二個結果包含統計
        assert "2 個可疑殘留英文條目" in final_result["log"]
        
        # 檢查輸出檔案
        output_file = output_dir / "items.json"
        assert output_file.exists()
        
        with open(output_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "item.iron_sword" in saved_data
        assert "item.diamond_pickaxe" in saved_data

    def test_preserves_directory_structure(self, tmp_path):
        """測試保留目錄結構。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        subdir = input_dir / "subfolder"
        subdir.mkdir()
        
        output_dir = tmp_path / "output"
        
        # 在子目錄建立檔案
        test_data = {"key": "Hello"}
        (subdir / "nested.json").write_text(
            json.dumps(test_data, ensure_ascii=False), encoding="utf-8"
        )
        
        results = list(english_residue_checker.check_english_residue_generator(
            str(input_dir), str(output_dir)
        ))
        
        # 檢查輸出結構
        output_file = output_dir / "subfolder" / "nested.json"
        assert output_file.exists()

    def test_handles_invalid_json(self, tmp_path):
        """測試處理無效 JSON 的行為。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # 建立無效 JSON 檔案
        (input_dir / "invalid.json").write_text("{invalid", encoding="utf-8")
        
        results = list(english_residue_checker.check_english_residue_generator(
            str(input_dir), str(output_dir)
        ))
        
        # 應該有錯誤訊息但繼續處理
        error_results = [r for r in results if r.get("error")]
        assert len(error_results) > 0
