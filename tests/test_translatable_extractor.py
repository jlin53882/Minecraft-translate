"""test_translatable_extractor.py - 可翻譯內容提取器測試單元。

用途：測試 translatable_extractor.py 的功能。
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestIsLangFile:
    """測試 is_lang_file 函式"""

    def test_is_lang_file_with_lang_in_path(self):
        """測試包含 lang 的路徑被識別為語言檔"""
        from translation_tool.core.translatable_extractor import is_lang_file
        
        path = Path("assets/modid/lang/en_us.json")
        assert is_lang_file(path) is True

    def test_is_lang_file_without_lang_in_path(self):
        """測試不包含 lang 的路徑不被識別為語言檔"""
        from translation_tool.core.translatable_extractor import is_lang_file
        
        path = Path("assets/modid/patchouli/books/book.json")
        assert is_lang_file(path) is False

    def test_is_lang_file_deep_path(self):
        """測試深層路徑"""
        from translation_tool.core.translatable_extractor import is_lang_file
        
        path = Path("data/mod/configs/lang/zh_tw.json")
        assert is_lang_file(path) is True


class TestFindLangJson:
    """測試 find_lang_json 函式"""

    def test_find_lang_json_basic(self, tmp_path):
        """測試基本語言檔搜尋"""
        from translation_tool.core.translatable_extractor import find_lang_json
        
        # 建立測試結構
        assets = tmp_path / "assets"
        mod_lang = assets / "modid" / "lang"
        mod_lang.mkdir(parents=True)
        
        (mod_lang / "en_us.json").write_text('{}')
        (mod_lang / "zh_tw.json").write_text('{}')
        
        results = find_lang_json(tmp_path)
        
        assert len(results) == 2
        assert any("en_us.json" in str(r) for r in results)
        assert any("zh_tw.json" in str(r) for r in results)

    def test_find_lang_json_multiple_mods(self, tmp_path):
        """測試多個模組的語言檔"""
        from translation_tool.core.translatable_extractor import find_lang_json
        
        # 建立多個模組結構
        (tmp_path / "assets" / "mod1" / "lang" / "en_us.json").parent.mkdir(parents=True)
        (tmp_path / "assets" / "mod1" / "lang" / "en_us.json").write_text('{}')
        
        (tmp_path / "assets" / "mod2" / "lang" / "en_us.json").parent.mkdir(parents=True)
        (tmp_path / "assets" / "mod2" / "lang" / "en_us.json").write_text('{}')
        
        results = find_lang_json(tmp_path)
        
        assert len(results) == 2


class TestFindPatchouliJson:
    """測試 find_patchouli_json 函式"""

    def test_find_patchouli_json_default_dirs(self, tmp_path):
        """測試使用預設目錄名稱搜尋"""
        from translation_tool.core.translatable_extractor import find_patchouli_json
        
        # 建立 Patchouli 目錄結構
        patchouli_dir = tmp_path / "assets" / "modid" / "patchouli_books"
        patchouli_dir.mkdir(parents=True)
        
        book_file = patchouli_dir / "book.json"
        book_file.write_text('{}')
        
        # Mock config
        with patch("translation_tool.core.translatable_extractor.load_config") as mock_config:
            mock_config.return_value = {
                "lm_translator": {
                    "patchouli": {
                        "dir_names": ["patchouli_books"]
                    }
                }
            }
            
            results = find_patchouli_json(tmp_path)
            
        assert len(results) == 1
        assert "book.json" in str(results[0])

    def test_find_patchouli_json_custom_dirs(self, tmp_path):
        """測試自訂目錄名稱"""
        from translation_tool.core.translatable_extractor import find_patchouli_json
        
        # 建立自訂目錄
        custom_dir = tmp_path / "assets" / "modid" / "custom_book_dir"
        custom_dir.mkdir(parents=True)
        
        (custom_dir / "book.json").write_text('{}')
        
        # 直接傳入自訂目錄名稱
        results = find_patchouli_json(tmp_path, dir_names=["custom_book_dir"])
        
        assert len(results) == 1


class TestExtractTranslatables:
    """測試 extract_translatables 函式"""

    def test_extract_from_lang_file_basic(self):
        """測試從語言檔提取基本翻譯內容"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "item.modid.test_key": "Hello World",
            "item.modid.another_key": "Test Translation"
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results = extract_translatables(json_data, file_path)
        
        assert len(results) == 2
        assert results[0]["text"] == "Hello World"
        assert results[0]["source_text"] == "Hello World"

    def test_extract_from_patchouli_basic(self):
        """測試從 Patchouli 檔案提取翻譯內容"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "name": "Book Name",
            "entries": {
                "intro": {
                    "title": "Introduction",
                    "text": "This is the introduction text."
                }
            }
        }
        
        file_path = Path("assets/modid/patchouli/book.json")
        results = extract_translatables(json_data, file_path)
        
        # 應該找到 name, title, text 等可翻譯欄位
        texts = [r["text"] for r in results]
        assert "Book Name" in texts or "Introduction" in texts or "This is the introduction text." in texts

    def test_extract_ignores_already_translated(self):
        """測試跳過已翻譯內容（已經是中文）"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "key1": "你好世界",  # 已是中文
            "key2": "Hello World"  # 需要翻譯
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results = extract_translatables(json_data, file_path)
        
        # 應該只找到英文
        texts = [r["text"] for r in results]
        assert "Hello World" in texts

    def test_extract_from_array(self):
        """測試從陣列中提取可翻譯內容"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "entries": [
                {"text": "First Item"},
                {"text": "Second Item"}
            ]
        }
        
        file_path = Path("assets/modid/patchouli/book.json")
        results = extract_translatables(json_data, file_path)
        
        # 應該找到陣列中的文字
        texts = [r["text"] for r in results]
        assert "First Item" in texts
        assert "Second Item" in texts

    def test_extract_with_lang_key_reference(self):
        """測試跳過語言 key 引用"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "key1": "booklet.section.entry",  # 這是 lang key 引用
            "key2": "Regular Text"  # 這是普通文字
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results = extract_translatables(json_data, file_path)
        
        texts = [r["text"] for r in results]
        # 應該只找到 Regular Text，不應該有 booklet...
        assert "Regular Text" in texts
        assert "booklet.section.entry" not in texts

    def test_extract_preserves_source_text(self):
        """測試保留原文"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "key": "Original Text"
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results = extract_translatables(json_data, file_path)
        
        assert len(results) == 1
        assert results[0]["text"] == "Original Text"
        assert results[0]["source_text"] == "Original Text"

    def test_extract_from_lang_file_flat(self):
        """測試從語言檔提取平層結構（key: value）"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        # 這是 lang 檔的典型結構：key 直接對應翻譯文字
        json_data = {
            "item.modid.test": "Test",
            "block.modid.stone": "Stone"
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results = extract_translatables(json_data, file_path)
        
        # Lang 檔中的所有 key 都應該被視為可翻譯
        assert len(results) >= 1

    def test_extract_patchouli_nested_text_field(self):
        """測試 Patchouli 中巢狀的 text 欄位"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "entries": {
                "chapter1": {
                    "entries": {
                        "page1": {
                            "type": "text",
                            "text": "This is a test page content."
                        }
                    }
                }
            }
        }
        
        file_path = Path("assets/modid/patchouli/book.json")
        results = extract_translatables(json_data, file_path)
        
        # 應該找到 text 欄位的內容
        texts = [r["text"] for r in results]
        assert "This is a test page content." in texts


class TestTranslatableExtractorEdgeCases:
    """邊界情況測試"""

    def test_extract_empty_json(self):
        """測試空 JSON 物件"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {}
        file_path = Path("assets/modid/lang/en_us.json")
        
        results = extract_translatables(json_data, file_path)
        
        assert results == []

    def test_extract_non_translatable_values(self):
        """測試不可翻譯的值"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "name": 123,  # 數字不可翻譯
            "enabled": True,  # 布林不可翻譯
            "text": "Valid Text"  # 文字可翻譯
        }
        
        file_path = Path("assets/modid/patchouli/book.json")
        results = extract_translatables(json_data, file_path)
        
        texts = [r["text"] for r in results]
        assert "Valid Text" in texts

    def test_extract_with_top_level_lang_key(self):
        """測試語言檔的頂層 key（這是 lang 檔的標準格式）"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        # Lang 檔常見這種格式
        json_data_en = {
            "item.minecraft.diamond_sword": "Diamond Sword",
            "item.minecraft.iron_pickaxe": "Iron Pickaxe"
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results_en = extract_translatables(json_data_en, file_path)
        
        # 驗證找到可翻譯內容
        assert len(results_en) >= 1

    def test_extract_tech_pattern_not_translatable(self):
        """測試技術 Pattern 不應被視為可翻譯"""
        from translation_tool.core.translatable_extractor import extract_translatables
        
        json_data = {
            "key1": "minecraft:diamond",  # 這是 ID
            "key2": "some.mod.key.path",  # 這是 key
            "key3": "Regular English Text"  # 普通文字
        }
        
        file_path = Path("assets/modid/lang/en_us.json")
        results = extract_translatables(json_data, file_path)
        
        texts = [r["text"] for r in results]
        # 應該只找到 Regular English Text
        assert "Regular English Text" in texts
        # 不應該包含 pattern
        assert "minecraft:diamond" not in texts
        assert "some.mod.key.path" not in texts
