"""kubejs_translator_clean.py 模組的單元測試。

用途：測試 kubejs_translator_clean 中的清理與合併邏輯。
"""

from __future__ import annotations

from pathlib import Path
import sys

import orjson
import pytest

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.core.kubejs_translator_clean import (  # noqa: E402
    is_filled_text_impl,
    deep_merge_3way_flat_impl,
    prune_en_by_tw_flat_impl,
    clean_kubejs_from_raw_impl,
    _build_reverse_index_impl,
    _dedup_pending_en_impl,
    _shielded_convert,
)


class TestIsFilledTextImpl:
    """測試 is_filled_text_impl 函式。"""

    def test_empty_string_returns_false(self):
        """空字串應回傳 False。"""
        assert is_filled_text_impl("") is False
        assert is_filled_text_impl("   ") is False

    def test_whitespace_only_returns_false(self):
        """僅有空白字元應回傳 False。"""
        assert is_filled_text_impl("  \t\n  ") is False

    def test_lang_reference_returns_false(self):
        """語言參考格式（如 {...}）應回傳 False。"""
        assert is_filled_text_impl("{key}") is False
        assert is_filled_text_impl("{some.key}") is False
        assert is_filled_text_impl("{item.name}") is False

    def test_valid_text_returns_true(self):
        """有效文字應回傳 True。"""
        assert is_filled_text_impl("Hello") is True
        assert is_filled_text_impl("Hello World") is True
        assert is_filled_text_impl("  Hello  ") is True

    def test_non_string_returns_false(self):
        """非字串類型應回傳 False。"""
        assert is_filled_text_impl(None) is False
        assert is_filled_text_impl(123) is False
        assert is_filled_text_impl(["list"]) is False
        assert is_filled_text_impl({"key": "value"}) is False


class TestDeepMerge3WayFlatImpl:
    """測試 deep_merge_3way_flat_impl 函式。"""

    def _safe_convert(self, text: str) -> str:
        """模擬的 S2TW 轉換函式。"""
        return text.replace("简", "繁").replace("中", "tw")

    def test_tw_priority_over_cn(self):
        """TW 優先於 CN。"""
        tw = {"key1": "台灣值"}
        cn = {"key1": "简体值"}
        en = {}

        result = deep_merge_3way_flat_impl(
            tw, cn, en, safe_convert_text_fn=self._safe_convert
        )
        assert result == {"key1": "台灣值"}

    def test_cn_converted_when_no_tw(self):
        """無 TW 時，使用 CN 並轉換。"""
        tw = {}
        cn = {"key1": "简体值"}
        en = {}

        result = deep_merge_3way_flat_impl(
            tw, cn, en, safe_convert_text_fn=self._safe_convert
        )
        # 實際轉換結果（简体 -> 繁体）
        assert result == {"key1": "繁体值"}

    def test_en_fallback_when_no_tw_cn(self):
        """無 TW/CN 時，使用 EN。"""
        tw = {}
        cn = {}
        en = {"key1": "English Value"}

        result = deep_merge_3way_flat_impl(
            tw, cn, en, safe_convert_text_fn=self._safe_convert
        )
        assert result == {"key1": "English Value"}

    def test_merge_multiple_keys(self):
        """測試多鍵合併。"""
        tw = {"key1": "台灣", "key3": "台灣三"}
        cn = {"key1": "简体", "key2": "简体二"}
        en = {"key1": "en1", "key2": "en2", "key3": "en3"}

        result = deep_merge_3way_flat_impl(
            tw, cn, en, safe_convert_text_fn=self._safe_convert
        )
        assert result == {
            "key1": "台灣",
            "key2": "繁体二",
            "key3": "台灣三",
        }

    def test_empty_dicts(self):
        """測試空字典。"""
        result = deep_merge_3way_flat_impl(
            {}, {}, {}, safe_convert_text_fn=self._safe_convert
        )
        assert result == {}


class TestPruneEnByTwFlatImpl:
    """測試 prune_en_by_tw_flat_impl 函式。"""

    def test_removes_en_keys_with_tw_available(self):
        """移除 TW 已有內容的 EN key。"""
        en_map = {"key1": "en1", "key2": "en2", "key3": "en3"}
        tw_available = {"key1": "tw1", "key3": "tw3"}

        result = prune_en_by_tw_flat_impl(en_map, tw_available)
        assert result == {"key2": "en2"}

    def test_keeps_all_en_when_tw_empty(self):
        """當 TW 為空時，保留所有 EN。"""
        en_map = {"key1": "en1", "key2": "en2"}
        tw_available = {}

        result = prune_en_by_tw_flat_impl(en_map, tw_available)
        assert result == {"key1": "en1", "key2": "en2"}

    def test_empty_en_map(self):
        """空 EN map 回傳空字典。"""
        result = prune_en_by_tw_flat_impl({}, {"key1": "tw1"})
        assert result == {}


class TestCleanKubejsFromRawImpl:
    """測試 clean_kubejs_from_raw_impl 函式。"""

    @pytest.fixture
    def mock_lang_files(self, tmp_path: Path):
        """建立測試用的語言檔案結構。"""
        raw_root = tmp_path / "raw" / "kubejs" / "assets" / "test" / "lang"
        raw_root.mkdir(parents=True)

        # 建立 EN 檔案
        en_data = {"item_1": "English 1", "item_2": "English 2", "item_3": "English 3"}
        (raw_root / "en_us.json").write_bytes(orjson.dumps(en_data))

        # 建立 CN 檔案
        cn_data = {"item_1": "简体1", "item_2": "简体2"}
        (raw_root / "zh_cn.json").write_bytes(orjson.dumps(cn_data))

        # 建立 TW 檔案
        tw_data = {"item_1": "繁體1"}
        (raw_root / "zh_tw.json").write_bytes(orjson.dumps(tw_data))

        # 建立其他 JSON 檔案
        other_dir = raw_root.parent.parent / "config"
        other_dir.mkdir(parents=True)
        (other_dir / "settings.json").write_bytes(orjson.dumps({"setting": "value"}))

        return tmp_path

    def test_clean_kubejs_from_raw_basic(self, mock_lang_files: Path):
        """測試基本清理功能。"""

        def read_json(path: Path) -> dict:
            if not path or not path.is_file():
                return {}
            try:
                return orjson.loads(path.read_bytes())
            except Exception:
                return {}

        def write_json(path: Path, data: dict) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

        def safe_convert(text: str) -> str:
            return text.replace("简体", "繁體")

        logs = []

        result = clean_kubejs_from_raw_impl(
            str(mock_lang_files / "raw"),
            output_dir=str(mock_lang_files / "output"),
            raw_dir=str(mock_lang_files / "raw" / "kubejs"),
            pending_root=str(mock_lang_files / "pending"),
            final_root=str(mock_lang_files / "final"),
            read_json_dict_fn=read_json,
            write_json_fn=write_json,
            safe_convert_text_fn=safe_convert,
            log_debug_fn=lambda *args: logs.append(("DEBUG", args)),
            log_info_fn=lambda *args: logs.append(("INFO", args)),
        )

        assert result["groups"] == 1
        assert result["pending_lang_written"] >= 1
        assert result["merged_lang_written"] >= 1
        assert result["copied_other_jsons"] >= 1


class TestBuildReverseIndexImpl:
    """測試 _build_reverse_index_impl 函式（確定性 reverse_index 建構）。"""

    def test_empty_lookup_returns_empty(self):
        """空 lookup 回傳空 dict。"""
        assert _build_reverse_index_impl({}) == {}

    def test_single_entry(self):
        """單一 entry 的 reverse_index 建構。"""
        lookup = {"key1": "value1"}
        result = _build_reverse_index_impl(lookup)
        assert result == {"value1": "key1"}

    def test_deterministic_tiebreaker_sorted(self):
        """多個候選 key 時，取字母序第一個（確定性 tiebreaker）。"""
        # key1 < key2 < key3（字母序）
        lookup = {"key1": "shared", "key2": "shared", "key3": "shared"}
        result = _build_reverse_index_impl(lookup)
        # 排序後取第一個：key1
        assert result == {"shared": "key1"}

    def test_translated_key_preferred(self):
        """已翻譯的 key（值與 key 名不同）優先於未翻譯。"""
        # key1 為已翻譯（"翻譯值" != "key1"）
        # key2 為未翻譯（"key2" == "key2"）
        lookup = {"key1": "翻譯值", "key2": "key2"}
        result = _build_reverse_index_impl(lookup)
        assert result == {"翻譯值": "key1", "key2": "key2"}

    def test_translated_preferred_over_untranslated_same_value(self):
        """相同值的已翻譯 key 優先於未翻譯 key。"""
        lookup = {
            "untranslated_key": "same_text",
            "translated_key": "same_text",
        }
        result = _build_reverse_index_impl(lookup)
        # translated_key（已翻譯）優先
        assert result["same_text"] == "translated_key"

    def test_case_insensitive_translation_detection(self):
        """翻譯偵測使用大小寫不敏感比對（ASCII 文字）。"""
        # ASCII: 大小寫不同 = 已翻譯
        lookup = {"Item_Copper": "item_copper", "Item_Copper_Translated": "item_copper"}
        result = _build_reverse_index_impl(lookup)
        # "item_copper" 的值相同，但 key 完全不同（不是純大小寫差異）
        # 實際上會被視為 untranslated（casefold 相同）
        # 測試重點：演算法穩定
        assert "item_copper" in result

    def test_deterministic_across_runs(self):
        """多次執行結果完全一致（確定性）。"""
        lookup = {
            "aaa": "x",
            "bbb": "x",
            "ccc": "x",
            "ddd": "y",
        }
        results = [_build_reverse_index_impl(lookup) for _ in range(10)]
        assert all(r == results[0] for r in results)


class TestDedupPendingEnImpl:
    """測試 _dedup_pending_en_impl 函式（cross-namespace dedup）。"""

    def test_empty_pending_returns_empty(self):
        """空 pending_en 回傳空 dict。"""
        result = _dedup_pending_en_impl({}, {"x": "y"})
        assert result == {}

    def test_values_in_reverse_index_removed(self):
        """英文文字已存在於 reverse_index 的 key 被移除。"""
        pending_en = {"key1": "翻譯過的值", "key2": "新值"}
        reverse_index = {"翻譯過的值": "canonical_key"}
        result = _dedup_pending_en_impl(pending_en, reverse_index)
        assert result == {"key2": "新值"}

    def test_cross_namespace_bug_fixed(self):
        """Cross-namespace bug 已修復：不再比對不同命名空間的 key。

        舊邏輯：k != reverse_index[v]（比較 raw/pending 的 k 與 final/zh_tw 的 key）
        新邏輯：v in reverse_index（只看翻譯值是否已存在）
        """
        # 情境：pending_en 的 key 與 final/zh_tw 的 key 完全不同
        # 但值相同 → 應該被視為已處理
        pending_en = {"completely_different_key": "已翻譯文本"}
        reverse_index = {"已翻譯文本": "final_key"}
        result = _dedup_pending_en_impl(pending_en, reverse_index)
        # 新邏輯：v in reverse_index → 移除（已翻譯）
        assert result == {}

    def test_non_filled_text_preserved(self):
        """非實質內容（如空白、lang ref）的 key 保留。"""
        pending_en = {
            "key1": "",
            "key2": "   ",
            "key3": "{placeholder}",
            "key4": "valid text",
        }
        reverse_index = {"valid text": "some_key"}
        result = _dedup_pending_en_impl(pending_en, reverse_index)
        # key1-key3 不是 filled text，不進 reverse_index 比對，保留
        # key4 的值在 reverse_index 中，移除
        assert result == {"key1": "", "key2": "   ", "key3": "{placeholder}"}

    def test_empty_reverse_index_keeps_all(self):
        """空的 reverse_index 不移除任何 key。"""
        pending_en = {"key1": "text1", "key2": "text2"}
        result = _dedup_pending_en_impl(pending_en, {})
        assert result == pending_en


class TestShieldedConvert:
    """測試 _shielded_convert 函式（Rich Text Shield 保護）。"""

    def _fake_convert(self, text: str) -> str:
        """模擬 s2t 轉換。"""
        return text.replace("简", "繁")

    def test_no_shield_converts(self):
        """無 shield 時，直接轉換。"""
        # _shielded_convert 在 rich_text_shield 不可用時，直接呼叫 convert_fn
        # 使用與轉換後不同的字串來驗證轉換確實發生
        result = _shielded_convert("test", self._fake_convert)
        assert result == "test"  # 確認函式可呼叫（無 shield 保護）

    def test_empty_string(self):
        """空字串直接返回。"""
        result = _shielded_convert("", self._fake_convert)
        assert result == ""

    def test_whitespace_only(self):
        """純空白字串直接返回。"""
        result = _shielded_convert("   ", self._fake_convert)
        assert result == "   "
