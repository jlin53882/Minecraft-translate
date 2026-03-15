"""kubejs_translator_clean.py 模組的單元測試。

用途：測試 kubejs_translator_clean 中的清理與合併邏輯。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

import orjson
import pytest

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.core.kubejs_translator_clean import (
    is_filled_text_impl,
    deep_merge_3way_flat_impl,
    prune_en_by_tw_flat_impl,
    clean_kubejs_from_raw_impl,
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
        result = deep_merge_3way_flat_impl({}, {}, {}, safe_convert_text_fn=self._safe_convert)
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
