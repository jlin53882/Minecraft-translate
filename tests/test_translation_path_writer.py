"""test_translation_path_writer.py - 翻譯路徑寫入器測試單元。

用途：測試 translation_path_writer.py 的功能。
"""

import pytest
from pathlib import Path


class TestMapLangOutputPath:
    """測試 map_lang_output_path 函式"""

    def test_map_en_us_to_zh_tw(self):
        """測試 en_us.json 轉換為 zh_tw.json"""
        from translation_tool.core.translation_path_writer import map_lang_output_path
        
        src = Path("assets/modid/lang/en_us.json")
        result = map_lang_output_path(src)
        
        assert result.name == "zh_tw.json"

    def test_map_non_en_us_file(self):
        """測試非 en_us.json 檔案不變"""
        from translation_tool.core.translation_path_writer import map_lang_output_path
        
        src = Path("assets/modid/lang/zh_tw.json")
        result = map_lang_output_path(src)
        
        assert result == src

    def test_map_different_case(self):
        """測試不同大小寫的 en_us"""
        from translation_tool.core.translation_path_writer import map_lang_output_path
        
        # 測試 EN_US.json（大寫）
        src = Path("assets/modid/lang/EN_US.json")
        result = map_lang_output_path(src)
        
        assert result.name == "zh_tw.json"

    def test_map_without_lang_folder(self):
        """測試不在 lang 資料夾中的檔案"""
        from translation_tool.core.translation_path_writer import map_lang_output_path
        
        src = Path("assets/modid/patchouli/book.json")
        result = map_lang_output_path(src)
        
        # 不在 lang 資料夾中，應該保持不變
        assert result == src


class TestSetByPath:
    """測試 set_by_path 函式"""

    def test_set_simple_key(self):
        """測試設定簡單 key"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {}
        set_by_path(root, "name", "測試")
        
        assert root["name"] == "測試"

    def test_set_existing_nested_key(self):
        """測試設定已存在的巢狀 key"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"parent": {}}
        set_by_path(root, "parent.child", "value")
        
        assert root["parent"]["child"] == "value"

    def test_set_array_index(self):
        """測試設定陣列索引"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"items": ["a", "b", "c"]}
        set_by_path(root, "items[1]", "modified")
        
        assert root["items"][1] == "modified"

    def test_set_nested_with_array(self):
        """測試巢狀結構中的陣列"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"parent": {"children": ["old1", "old2"]}}
        set_by_path(root, "parent.children[0]", "new1")
        
        assert root["parent"]["children"][0] == "new1"

    def test_set_overwrite_existing(self):
        """測試覆寫已存在的值"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"key": "original"}
        set_by_path(root, "key", "updated")
        
        assert root["key"] == "updated"

    def test_set_with_brackets_notation(self):
        """測試使用括號記法的陣列"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"data": [None, None, None]}
        set_by_path(root, "data[2]", "third")
        
        assert root["data"][2] == "third"

    def test_set_complex_nested_array(self):
        """測試複雜的巢狀陣列"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {
            "entries": [
                {"title": "Old Title"}
            ]
        }
        set_by_path(root, "entries[0].title", "New Title")
        
        assert root["entries"][0]["title"] == "New Title"

    def test_set_array_nested_in_dict(self):
        """測試字典中巢狀的陣列"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"recipes": [{"result": "old"}]}
        set_by_path(root, "recipes[0].result", "diamond_sword")
        
        assert root["recipes"][0]["result"] == "diamond_sword"

    def test_set_deeply_nested_with_existing_path(self):
        """測試已存在路徑的深層巢狀"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"a": {"b": {"c": {}}}}
        set_by_path(root, "a.b.c.d", "deep_value")
        
        assert root["a"]["b"]["c"]["d"] == "deep_value"


class TestSetByPathEdgeCases:
    """set_by_path 邊界情況測試"""

    def test_set_invalid_array_index(self):
        """測試無效的陣列索引"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"items": ["a", "b"]}
        
        # 索引超出範圍
        with pytest.raises((IndexError, KeyError)):
            set_by_path(root, "items[5]", "out_of_range")

    def test_set_on_non_dict(self):
        """測試嘗試在非字典上設定"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"key": "string_value"}
        
        with pytest.raises((TypeError, KeyError)):
            set_by_path(root, "key.nested", "value")

    def test_set_array_as_dict_key(self):
        """測試將陣列當作字典鍵處理"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"items": ["a", "b"]}
        
        # 嘗試用 key 訪問陣列
        with pytest.raises((TypeError, KeyError)):
            set_by_path(root, "items.invalid", "value")

    def test_set_preserves_other_data(self):
        """測試設定值時保留其他資料"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {
            "keep_this": "value",
            "nested": {
                "also_keep": "data"
            }
        }
        set_by_path(root, "new_key", "new_value")
        
        assert root["keep_this"] == "value"
        assert root["nested"]["also_keep"] == "data"
        assert root["new_key"] == "new_value"

    def test_set_array_inside_dict_key(self):
        """測試字典中的陣列處理"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {"list": [None, {"nested": "old"}]}
        set_by_path(root, "list[1].nested", "new")
        
        assert root["list"][1]["nested"] == "new"

    def test_set_multiple_keys_same_root(self):
        """測試在同一根物件設定多個鍵"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        root = {}
        set_by_path(root, "a", "1")
        set_by_path(root, "b", "2")
        
        assert root["a"] == "1"
        assert root["b"] == "2"


class TestTranslationPathWriterIntegration:
    """翻譯路徑寫入器整合測試"""

    def test_complete_translation_workflow(self):
        """測試完整翻譯工作流程"""
        from translation_tool.core.translation_path_writer import (
            map_lang_output_path,
            set_by_path,
        )
        
        # 模擬翻譯流程
        # 1. 映射輸出路徑
        input_path = Path("assets/mod/lang/en_us.json")
        output_path = map_lang_output_path(input_path)
        
        assert output_path.name == "zh_tw.json"
        
        # 2. 建立翻譯資料結構（從簡單巢狀結構開始）
        translations = {"item": {"mod": {}}}
        set_by_path(translations, "item.mod.test", "測試翻譯")
        set_by_path(translations, "item.mod.another", "另一個翻譯")
        
        # 3. 驗證結果
        assert translations["item"]["mod"]["test"] == "測試翻譯"
        assert translations["item"]["mod"]["another"] == "另一個翻譯"

    def test_patchouli_translation_workflow(self):
        """測試 Patchouli 翻譯流程"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        # 模擬 Patchouli 條目翻譯（從簡單結構開始）
        patchouli_data = {"entries": {"intro": {}, "chapter1": {}}}
        
        set_by_path(patchouli_data, "entries.intro.title", "介紹")
        set_by_path(patchouli_data, "entries.intro.text", "這是介紹頁面的內容")
        set_by_path(patchouli_data, "entries.chapter1.title", "第一章")
        
        assert patchouli_data["entries"]["intro"]["title"] == "介紹"
        assert patchouli_data["entries"]["intro"]["text"] == "這是介紹頁面的內容"
        assert patchouli_data["entries"]["chapter1"]["title"] == "第一章"

    def test_array_based_translation(self):
        """測試基於陣列的翻譯"""
        from translation_tool.core.translation_path_writer import set_by_path
        
        # 模擬翻譯帶有多個結果的配方
        data = {"recipes": [{"result": ""}, {"result": ""}, {"result": ""}]}
        
        set_by_path(data, "recipes[0].result", "鑽石劍")
        set_by_path(data, "recipes[1].result", "鐵鎬")
        set_by_path(data, "recipes[2].result", "弓箭")
        
        assert data["recipes"][0]["result"] == "鑽石劍"
        assert data["recipes"][1]["result"] == "鐵鎬"
        assert data["recipes"][2]["result"] == "弓箭"

    def test_lang_file_output_path_mapping(self):
        """測試語言檔輸出路徑映射"""
        from translation_tool.core.translation_path_writer import map_lang_output_path
        
        # 標準 Minecraft mod 結構
        test_cases = [
            ("assets/modid/lang/en_us.json", "zh_tw.json"),
            ("assets/other_mod/lang/en_US.json", "zh_tw.json"),
            # ZH_TW.json 不應被改變因為不是 en_us
            ("resources/mod/lang/en_us.json", "zh_tw.json"),
        ]
        
        for input_path, expected_name in test_cases:
            src = Path(input_path)
            result = map_lang_output_path(src)
            assert result.name == expected_name, f"Failed for {input_path}"

    def test_non_lang_file_no_change(self):
        """測試非語言檔路徑不變"""
        from translation_tool.core.translation_path_writer import map_lang_output_path
        
        test_cases = [
            "assets/modid/patchouli/book.json",
            "assets/modid/models/item/diamond_sword.json",
            "pack.mcmeta",
        ]
        
        for input_path in test_cases:
            src = Path(input_path)
            result = map_lang_output_path(src)
            assert result == src, f"Should not change for {input_path}"
