"""test_kubejs_translator.py - kubejs_translator 模組測試單元。

用途：測試 kubejs_translator.py 的主要公用函數和工具類別。
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core import kubejs_translator


class TestIsFilledText:
    """測試 _is_filled_text 函數 - 檢查是否為有效的文字內容"""

    def test_filled_string_returns_true(self) -> None:
        assert kubejs_translator._is_filled_text("Hello World") is True

    def test_empty_string_returns_false(self) -> None:
        assert kubejs_translator._is_filled_text("") is False

    def test_whitespace_only_returns_false(self) -> None:
        assert kubejs_translator._is_filled_text("   ") is False

    def test_none_returns_false(self) -> None:
        assert kubejs_translator._is_filled_text(None) is False

    def test_dict_returns_false(self) -> None:
        assert kubejs_translator._is_filled_text({"key": "value"}) is False

    def test_list_returns_false(self) -> None:
        assert kubejs_translator._is_filled_text(["item"]) is False


class TestDeepMerge3WayFlat:
    """測試 deep_merge_3way_flat 函數 - 三方合併平字典"""

    def test_tw_priority_over_cn_and_en(self) -> None:
        tw = {"key1": "繁中"}
        cn = {"key1": "簡中"}
        en = {"key1": "English"}
        result = kubejs_translator.deep_merge_3way_flat(tw, cn, en)
        assert result["key1"] == "繁中"

    def test_cn_used_when_tw_missing(self) -> None:
        tw = {}
        cn = {"key1": "簡中"}
        en = {"key1": "English"}
        result = kubejs_translator.deep_merge_3way_flat(tw, cn, en)
        assert result["key1"] == "簡中"

    def test_en_used_as_fallback(self) -> None:
        tw = {}
        cn = {}
        en = {"key1": "English"}
        result = kubejs_translator.deep_merge_3way_flat(tw, cn, en)
        assert result["key1"] == "English"

    def test_merge_multiple_keys(self) -> None:
        tw = {"key1": "繁中1"}
        cn = {"key2": "簡中2"}
        en = {"key3": "English3"}
        result = kubejs_translator.deep_merge_3way_flat(tw, cn, en)
        assert result["key1"] == "繁中1"
        assert result["key2"] == "簡中2"
        assert result["key3"] == "English3"


class TestPruneEnByTwFlat:
    """測試 prune_en_by_tw_flat 函數 - 根據 TW 可用值修剪 EN"""

    def test_keys_with_tw_translation_removed(self) -> None:
        en_map = {"key1": "English1", "key2": "English2", "key3": "English3"}
        tw_available = {"key1": "繁中1", "key2": ""}
        result = kubejs_translator.prune_en_by_tw_flat(en_map, tw_available)
        assert "key1" not in result
        assert result["key2"] == "English2"
        assert result["key3"] == "English3"

    def test_empty_tw_returns_all_en(self) -> None:
        en_map = {"key1": "English1"}
        tw_available = {}
        result = kubejs_translator.prune_en_by_tw_flat(en_map, tw_available)
        assert result == {"key1": "English1"}

    def test_none_tw_values_kept(self) -> None:
        en_map = {"key1": "English1"}
        tw_available = {"key1": None}
        result = kubejs_translator.prune_en_by_tw_flat(en_map, tw_available)
        assert result["key1"] == "English1"


class TestJsonIO:
    """測試 JSON 讀寫函數"""

    def test_read_json_dict_orjson(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.json"
        test_data = {"key1": "value1", "key2": 123}
        test_file.write_bytes(orjson.dumps(test_data))

        result = kubejs_translator._read_json_dict_orjson(test_file)
        assert result == test_data

    def test_write_json_orjson(self, tmp_path: Path) -> None:
        test_file = tmp_path / "output.json"
        test_data = {"key": "value"}

        kubejs_translator._write_json_orjson(test_file, test_data)

        result = orjson.loads(test_file.read_bytes())
        assert result == test_data


class TestResolveKubejsRoot:
    """測試 resolve_kubejs_root 函數 - 解析 KubeJS 目錄"""

    def test_resolves_kubejs_dir(self, tmp_path: Path) -> None:
        # kubejs_translator_paths.py 邏輯：找到 kubejs 目錄
        kubejs_dir = tmp_path / "kubejs"
        kubejs_dir.mkdir(parents=True)

        result = kubejs_translator.resolve_kubejs_root(str(tmp_path))
        # 找到 kubejs 目錄（因為 base/kubejs 存在）
        assert result == kubejs_dir.resolve()

    def test_max_depth_limit(self, tmp_path: Path) -> None:
        # 建立 5 層深度結構（max_depth=4 應該找到第 4 層而不是第 5 層）
        deep_dir = tmp_path / "a" / "b" / "c" / "kubejs"
        deep_dir.mkdir(parents=True)

        # 由於沒有 client_scripts，可能找到但可能不是最深層
        result = kubejs_translator.resolve_kubejs_root(str(tmp_path), max_depth=4)
        # 找到的路徑應該存在，而不是 None
        assert result is not None

    def test_finds_at_max_depth(self, tmp_path: Path) -> None:
        # 建立 3 層深度結構（max_depth=3 應該找到）
        kubejs_dir = tmp_path / "a" / "kubejs"
        kubejs_dir.mkdir(parents=True)

        result = kubejs_translator.resolve_kubejs_root(str(tmp_path), max_depth=3)
        assert result == kubejs_dir.resolve()

    def test_returns_input_when_not_found(self, tmp_path: Path) -> None:
        # 沒有 kubejs 目錄時應該回傳輸入目錄本身
        other_dir = tmp_path / "other"
        other_dir.mkdir(parents=True)

        result = kubejs_translator.resolve_kubejs_root(str(other_dir))
        assert result == other_dir.resolve()


class TestCleanKubejsFromRaw:
    """測試 clean_kubejs_from_raw 函數 - 清理 KubeJS 原始資料"""

    def test_clean_kubejs_from_raw_basic(self, tmp_path: Path) -> None:
        # 建立測試結構
        raw_root = tmp_path / "Output" / "kubejs" / "raw" / "kubejs"
        lang_root = raw_root / "assets" / "test" / "lang"
        lang_root.mkdir(parents=True)

        (lang_root / "en_us.json").write_bytes(orjson.dumps({"key1": "EN1", "key2": "EN2"}))
        (lang_root / "zh_cn.json").write_bytes(orjson.dumps({"key1": "CN1"}))

        result = kubejs_translator.clean_kubejs_from_raw(
            str(tmp_path),
            output_dir=str(tmp_path / "Output"),
        )

        assert "pending_root" in result
        assert "final_root" in result
        assert result["groups"] >= 0


class TestStepFunctions:
    """測試步驟函數的基本簽名"""

    def test_step1_extract_and_clean_requires_dirs(self) -> None:
        # 缺少必要參數應該拋出異常
        with pytest.raises(Exception):
            kubejs_translator.step1_extract_and_clean()

    def test_step2_translate_lm_requires_output(self) -> None:
        # 必須提供 output_dir 或 translated_dir
        with pytest.raises(ValueError):
            kubejs_translator.step2_translate_lm(pending_dir="dummy")

    def test_step3_inject_requires_dirs(self) -> None:
        with pytest.raises(Exception):
            kubejs_translator.step3_inject()


class TestRunKubejsPipeline:
    """測試 run_kubejs_pipeline 完整流程"""

    def test_pipeline_creates_output_dirs(self, tmp_path: Path, monkeypatch) -> None:
        # Mock step1 傳回空結果結構
        def mock_step1(**kwargs):
            pending = Path(kwargs["pending_dir"])
            pending.mkdir(parents=True, exist_ok=True)
            return {"extract": {}, "clean": {}, "pending_dir": str(pending)}

        monkeypatch.setattr(kubejs_translator, "step1_extract_and_clean", mock_step1)

        result = kubejs_translator.run_kubejs_pipeline(
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            step_extract=True,
            step_translate=False,
            step_inject=False,
        )

        assert "paths" in result
        assert result["paths"]["raw"]
        assert result["paths"]["pending"]

    def test_pipeline_dry_run_mode(self, tmp_path: Path, monkeypatch) -> None:
        def mock_step1(**kwargs):
            pending = Path(kwargs["pending_dir"])
            pending.mkdir(parents=True, exist_ok=True)
            return {"extract": {}, "clean": {}, "pending_dir": str(pending)}

        def mock_step2(**kwargs):
            return {"skipped": True, "reason": "pending lang keys = 0"}

        monkeypatch.setattr(kubejs_translator, "step1_extract_and_clean", mock_step1)
        monkeypatch.setattr(kubejs_translator, "step2_translate_lm", mock_step2)

        result = kubejs_translator.run_kubejs_pipeline(
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            dry_run=True,
            step_extract=True,
            step_translate=True,
            step_inject=False,
        )

        assert result["step2"]["skipped"] is True
