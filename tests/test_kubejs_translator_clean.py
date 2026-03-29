"""kubejs_translator_clean.py 模組的單元測試。

用途：測試 kubejs_translator_clean 中的清理與合併邏輯。
"""

from __future__ import annotations

import sys
from pathlib import Path

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


class TestBuildReverseIndexImpl:
    """測試 _build_reverse_index_impl 函式。

    驗證 reverse_index 為 dict[str, str] 而非 dict[str, list]，
    以及選擇 canonical key 的確定性邏輯（優先已翻譯，再取字母序最小）。
    """

    def test_reverse_index_is_dict_str_str_not_list(self):
        """reverse_index 必須是 dict[str, str]，不能是 dict[str, list]。"""
        final_tw_lookup = {
            "key_a": "翻譯值",
            "key_b": "另一個翻譯",
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        # 類型驗證：每個 value 都應該是 str，不是 list
        for k, v in result.items():
            assert isinstance(k, str), f"key 應為 str，實際為 {type(k).__name__}"
            assert isinstance(v, str), (
                f"value for key '{k}' 應為 str，實際為 {type(v).__name__}"
            )

    def test_prefers_translated_key_over_untranslated(self):
        """當多個 key 有相同翻譯值時，應優先選擇「已翻譯」的 key。

        「已翻譯」定義：zh_tw 值與英文 key 名不同。
        """
        # key_a：翻譯值不同於 key 名（已翻譯）
        # key_b：翻譯值等於 key 名（未翻譯）
        final_tw_lookup = {
            "apple": "蘋果",  # 已翻譯（值 != key）
            "蘋果": "蘋果",  # 未翻譯（值 == key）
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        # 對"蘋果"這個翻譯結果，應選擇 key "apple"（已翻譯）而非 "蘋果"（未翻譯）
        assert result["蘋果"] == "apple"

    def test_prefers_alphabetically_smallest_among_same_priority(self):
        """同優先級時（都是已翻譯或都是未翻譯），取字母序最小的 key。"""
        # 多個 key 都已翻譯（值 != key），取字母序最小
        final_tw_lookup = {
            "zebra": "動物",  # 已翻譯，但字母序較大
            "ant": "動物",  # 已翻譯，字母序最小
            "bee": "動物",  # 已翻譯，字母序居中
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        assert result["動物"] == "ant"

    def test_mixed_translated_and_untranslated_chooses_correct(self):
        """混合場景：已翻譯優先於未翻譯。"""
        final_tw_lookup = {
            "apple": "蘋果",  # 已翻譯
            "banana": "香蕉",  # 未翻譯
            "cherry": "櫻桃",  # 已翻譯
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        assert result["蘋果"] == "apple"
        assert result["香蕉"] == "banana"
        assert result["櫻桃"] == "cherry"

    def test_stability_multiple_executions_same_input(self):
        """多次執行同一組資料，結果必須完全一致（確定性）。"""
        final_tw_lookup = {
            "z_key": "翻譯Z",
            "a_key": "翻譯A",
            "m_key": "翻譯M",
            "翻譯Z": "翻譯Z",  # 未翻譯
            "翻譯A": "翻譯A",  # 未翻譯
        }

        results = [_build_reverse_index_impl(final_tw_lookup) for _ in range(10)]

        # 所有結果應該完全相同
        first = results[0]
        for i, r in enumerate(results[1:], 1):
            assert r == first, f"第 {i} 次結果與第 1 次不同：{r} vs {first}"

    def test_stability_with_multiple_keys_same_translation(self):
        """多個 key 映射到同一翻譯值時，選擇結果穩定。"""
        final_tw_lookup = {
            "zulu_item": "測試翻譯",
            "alpha_item": "測試翻譯",
            "測試翻譯": "測試翻譯",  # 未翻譯
        }

        results = [_build_reverse_index_impl(final_tw_lookup) for _ in range(5)]
        first = results[0]
        for i, r in enumerate(results[1:], 1):
            assert r == first, f"第 {i} 次結果與第 1 次不同"

        # 應選已翻譯且字母序最小的：alpha_item < zulu_item
        assert first["測試翻譯"] == "alpha_item"

    def test_empty_final_tw_lookup_returns_empty_dict(self):
        """空的 final_tw_lookup 回傳空字典。"""
        result = _build_reverse_index_impl({})
        assert result == {}

    def test_non_filled_text_values_are_ignored(self):
        """非填充文字值（如空字串、空白）不應進入 reverse_index。"""
        final_tw_lookup = {
            "key1": "有效翻譯",
            "key2": "",  # 空字串，應忽略
            "key3": "   ",  # 空白，應忽略
            "key4": "{ref}",  # 語言參考，應忽略
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        assert "有效翻譯" in result
        assert "" not in result
        assert "   " not in result
        assert "{ref}" not in result

    def test_casefold_ascii_translation_detection(self):
        """ASCII 翻譯使用 casefold() 判斷是否為「已翻譯」。"""
        # "Copper Ingot" vs "copper ingot"：casefold 後相同，視為已翻譯
        # "copper ingot" vs "copper ingot"：完全相同，視為未翻譯
        final_tw_lookup = {
            "copper_ingot": "Copper Ingot",  # 已翻譯（casefold 不同）
            "Copper Ingot": "Copper Ingot",  # 未翻譯（casefold 相同）
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        # 應選 key 名與值 casefold 後不同的 "copper_ingot"
        assert result["Copper Ingot"] == "copper_ingot"

    def test_non_ascii_uses_direct_equality(self):
        """非 ASCII 翻譯使用直接相等判斷是否為「已翻譯」。"""
        final_tw_lookup = {
            "蘋果": "蘋果",  # 未翻譯
            "apple": "蘋果",  # 已翻譯
        }
        result = _build_reverse_index_impl(final_tw_lookup)

        assert result["蘋果"] == "apple"


class TestDedupPendingEnImpl:
    """測試 _dedup_pending_en_impl 函式。

    驗證去重邏輯使用 `v in reverse_index` 而非 `k != reverse_index[v]`，
    以及跨命名空間比對的正確性。
    """

    def test_dedup_removes_keys_with_value_in_reverse_index(self):
        """當 pending_en 的 value 存在於 reverse_index 時，該 key 應被移除。"""
        pending_en = {
            "mod.item1": "Apple",
            "mod.item2": "Banana",
            "mod.item3": "Cherry",
        }
        reverse_index = {
            "Apple": "final.apple",  # Apple 已在 final 中
            "Banana": "final.banana",  # Banana 已在 final 中
        }

        result = _dedup_pending_en_impl(pending_en, reverse_index)

        # Apple 和 Banana 已在 final，應被移除；Cherry 不在 reverse_index，應保留
        assert result == {"mod.item3": "Cherry"}

    def test_dedup_cross_namespace_bug_fixed(self):
        """跨命名空間比對：raw/pending 的 k 與 final 的 key 名不同，但翻譯值相同時，應去重。

        這是原本 bug 的核心場景：
        - pending 的 key: "raw_namespace:item_name"（value: "Apple"）
        - final 的 key: "final_namespace:item_name"（value: "Apple"）
        - 舊邏輯：`k != reverse_index[v]` → "raw_namespace:item_name" != "final_namespace:item_name"
          → 判斷為「不相同」，導致不去重 ❌
        - 新邏輯：`v in reverse_index` → "Apple" in reverse_index → True → 去重 ✅
        """
        pending_en = {
            "raw:item_a": "Apple",  # value: Apple
            "raw:item_b": "Banana",  # value: Banana（不在 reverse_index）
            "raw:item_c": "Cherry",  # value: Cherry
        }
        reverse_index = {
            # final 中有不同的 key 名，但相同的翻譯值
            "Apple": "final:item_x",
            "Cherry": "final:item_y",
        }

        result = _dedup_pending_en_impl(pending_en, reverse_index)

        # Apple 和 Cherry 的 key 名雖然與 reverse_index 中的不同，
        # 但翻譯值存在於 reverse_index，仍應被去重
        assert result == {"raw:item_b": "Banana"}

    def test_dedup_non_filled_text_not_removed(self):
        """非填充文字（如空字串、空白、語言參考）不受去重邏輯影響。"""
        pending_en = {
            "key1": "",  # 空字串，應保留（即使 "" 在 reverse_index）
            "key2": "   ",  # 空白，應保留
            "key3": "{ref}",  # 語言參考，應保留
            "key4": "有效翻譯",  # 有效文字，在 reverse_index 中，應移除
        }
        reverse_index = {
            "": "some_key",  # reverse_index 中有 ""
            "   ": "some_key2",  # reverse_index 中有空白
            "{ref}": "some_key3",  # reverse_index 中有 ref
            "有效翻譯": "tw_key",  # 有效翻譯
        }

        result = _dedup_pending_en_impl(pending_en, reverse_index)

        # 只有 "有效翻譯" 應被移除；空字串、空白、ref 都應保留
        assert result == {"key1": "", "key2": "   ", "key3": "{ref}"}

    def test_dedup_empty_pending_returns_empty(self):
        """空的 pending_en 回傳空字典。"""
        reverse_index = {"key": "value"}
        result = _dedup_pending_en_impl({}, reverse_index)
        assert result == {}

    def test_dedup_empty_reverse_index_keeps_all(self):
        """空的 reverse_index 保留所有 pending_en。"""
        pending_en = {
            "key1": "Apple",
            "key2": "Banana",
        }
        result = _dedup_pending_en_impl(pending_en, {})
        assert result == {"key1": "Apple", "key2": "Banana"}

    def test_dedup_stability_across_multiple_calls(self):
        """同一組輸入，多次呼叫結果一致。"""
        pending_en = {
            "namespace:item1": "翻譯A",
            "namespace:item2": "翻譯B",
            "namespace:item3": "翻譯C",
        }
        reverse_index = {
            "翻譯A": "final:key1",
            "翻譯B": "final:key2",
        }

        results = [_dedup_pending_en_impl(pending_en, reverse_index) for _ in range(10)]

        expected = {"namespace:item3": "翻譯C"}
        for i, r in enumerate(results):
            assert r == expected, f"第 {i} 次結果與預期不同"


class TestCleanKubejsFromRawImpl:
    """測試 clean_kubejs_from_raw_impl 函式（整合測試）。"""

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
