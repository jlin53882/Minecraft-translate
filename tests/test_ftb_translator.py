"""test_ftb_translator.py - ftb_translator 模組測試單元。

用途：測試 ftb_translator.py 的主要公用函數和工具類別。
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from translation_tool.core import ftb_translator


class TestDeepMerge3Way:
    """測試 deep_merge_3way 函數 - 三方合併"""

    def test_tw_priority_over_cn_and_en(self) -> None:
        zh_tw = {"key1": "繁中"}
        zh_cn = {"key1": "簡中"}
        en_us = {"key1": "English"}
        result = ftb_translator.deep_merge_3way(zh_tw, zh_cn, en_us)
        assert result["key1"] == "繁中"

    def test_cn_used_when_tw_missing(self) -> None:
        zh_tw = {}
        zh_cn = {"key1": "簡中"}
        en_us = {"key1": "English"}
        result = ftb_translator.deep_merge_3way(zh_tw, zh_cn, en_us)
        assert result["key1"] == "簡中"

    def test_en_used_as_fallback(self) -> None:
        zh_tw = {}
        zh_cn = {}
        en_us = {"key1": "English"}
        result = ftb_translator.deep_merge_3way(zh_tw, zh_cn, en_us)
        assert result["key1"] == "English"

    def test_nested_dict_merge(self) -> None:
        zh_tw = {"outer": {"inner": "繁中"}}
        zh_cn = {"outer": {"inner": "簡中"}}
        en_us = {"outer": {"inner": "English"}}
        result = ftb_translator.deep_merge_3way(zh_tw, zh_cn, en_us)
        assert result["outer"]["inner"] == "繁中"

    def test_empty_values_fallback(self) -> None:
        zh_tw = {"key1": ""}
        zh_cn = {"key1": "簡中"}
        en_us = {"key1": "English"}
        result = ftb_translator.deep_merge_3way(zh_tw, zh_cn, en_us)
        assert result["key1"] == "簡中"


class TestResolveFtbquestsRoot:
    """測試 resolve_ftbquests_quests_root 函數 - 解析 FTB Quests 目錄"""

    def test_resolves_quests_dir(self, tmp_path: Path) -> None:
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)

        result = ftb_translator.resolve_ftbquests_quests_root(str(tmp_path))
        assert result == str(quests_dir.resolve())

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        # 找不到時應該拋出 FileNotFoundError
        with pytest.raises(FileNotFoundError):
            ftb_translator.resolve_ftbquests_quests_root(str(tmp_path))


class TestExportFtbquestsRawJson:
    """測試 export_ftbquests_raw_json 函數 - 輸出 FTB Quests 原始 JSON"""

    def test_export_creates_output_structure(self, tmp_path: Path) -> None:
        # 建立測試結構
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)
        lang_dir = quests_dir / "lang"
        lang_dir.mkdir(parents=True)

        (lang_dir / "en_us.json").write_bytes(orjson.dumps({"key": "value"}))

        result = ftb_translator.export_ftbquests_raw_json(str(tmp_path), output_dir=str(tmp_path / "Output"))

        assert "raw_root" in result


class TestCleanFtbquestsFromRaw:
    """測試 clean_ftbquests_from_raw 函數 - 清理 FTB Quests 原始資料"""

    def test_clean_ftbquests_basic(self, tmp_path: Path) -> None:
        # 建立測試結構
        raw_root = tmp_path / "Output" / "ftbquests" / "raw" / "config" / "ftbquests" / "quests" / "lang"
        (raw_root / "en_us").mkdir(parents=True)
        (raw_root / "zh_cn").mkdir(parents=True)

        (raw_root / "en_us" / "ftb_lang.json").write_bytes(orjson.dumps({"a": "A", "b": "B"}))
        (raw_root / "zh_cn" / "ftb_lang.json").write_bytes(orjson.dumps({"a": "簡中A"}))

        result = ftb_translator.clean_ftbquests_from_raw(str(tmp_path), output_dir=str(tmp_path / "Output"))

        assert "en_pending_dir" in result
        assert "zh_tw_dir" in result


class TestPrepareLangTemplate:
    """測試 prepare_ftbquests_lang_template_only 函數 - 準備語言模板"""

    def test_prepare_template_requires_source(self, tmp_path: Path) -> None:
        # 沒有來源時應該拋出 FileNotFoundError
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)

        output_dir = tmp_path / "Output"

        with pytest.raises(FileNotFoundError):
            ftb_translator.prepare_ftbquests_lang_template_only(
                str(config_dir),
                str(output_dir),
                prefer_lang="zh_cn",
            )

    def test_prepare_template_with_source_exists(self, tmp_path: Path) -> None:
        # 建立測試結構 - 包含 ftbquests 相關語言資料夾
        config_dir = tmp_path / "config" / "ftbquests" / "quests" / "lang" / "zh_cn"
        config_dir.mkdir(parents=True)

        output_dir = tmp_path / "Output"

        result = ftb_translator.prepare_ftbquests_lang_template_only(
            str(tmp_path / "config"),
            str(output_dir),
            prefer_lang="zh_cn",
        )

        assert result is not None


class TestTranslateDirectoryGenerator:
    """測試 translate_directory_generator 函數 - 目錄翻譯生成器"""

    def test_generator_yields_progress(self, tmp_path: Path) -> None:
        # 建立測試結構
        input_dir = tmp_path / "input"
        input_dir.mkdir(parents=True)

        (input_dir / "test.json").write_bytes(orjson.dumps({"key": "value"}))

        generator = ftb_translator.translate_directory_generator(str(input_dir))

        # 迭代生成器
        results = list(generator)

        assert len(results) > 0
        assert "progress" in results[0]

    def test_generator_handles_empty_dir(self, tmp_path: Path) -> None:
        input_dir = tmp_path / "empty"
        input_dir.mkdir(parents=True)

        generator = ftb_translator.translate_directory_generator(str(input_dir))
        results = list(generator)

        assert len(results) > 0


class TestRunFtbPipeline:
    """測試 run_ftb_pipeline 完整流程"""

    def test_pipeline_step_export_only(self, tmp_path: Path) -> None:
        # 建立必要的目錄結構
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)
        lang_dir = quests_dir / "lang"
        lang_dir.mkdir(parents=True)

        (lang_dir / "en_us.json").write_bytes(orjson.dumps({"test": "Test"}))

        result = ftb_translator.run_ftb_pipeline(
            directory_path=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            step_export=True,
            step_clean=False,
            step_translate=False,
            step_inject=False,
        )

        assert "raw_paths" in result

    def test_pipeline_step_export_and_clean(self, tmp_path: Path) -> None:
        # 建立完整測試結構
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)
        lang_dir = quests_dir / "lang"
        lang_dir.mkdir(parents=True)

        (lang_dir / "en_us.json").write_bytes(orjson.dumps({"key1": "EN1", "key2": "EN2"}))
        (lang_dir / "zh_cn.json").write_bytes(orjson.dumps({"key1": "CN1"}))

        result = ftb_translator.run_ftb_pipeline(
            directory_path=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            step_export=True,
            step_clean=True,
            step_translate=False,
            step_inject=False,
        )

        assert "raw_paths" in result
        assert "clean_paths" in result

    def test_pipeline_dry_run(self, tmp_path: Path) -> None:
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)
        lang_dir = quests_dir / "lang"
        lang_dir.mkdir(parents=True)

        (lang_dir / "en_us.json").write_bytes(orjson.dumps({"key": "Value"}))

        result = ftb_translator.run_ftb_pipeline(
            directory_path=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            dry_run=True,
            step_export=True,
            step_clean=True,
            step_translate=False,
            step_inject=True,
        )

        # Dry-run 應該跳過 inject
        assert result.get("inject", {}).get("skipped") is True

    def test_pipeline_requires_clean_before_translate(self, tmp_path: Path) -> None:
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)
        lang_dir = quests_dir / "lang"
        lang_dir.mkdir(parents=True)

        (lang_dir / "en_us.json").write_bytes(orjson.dumps({"key": "Value"}))

        # 嘗試跳過 clean 但執行 translate，應該拋出錯誤
        with pytest.raises(RuntimeError):
            ftb_translator.run_ftb_pipeline(
                directory_path=str(tmp_path),
                output_dir=str(tmp_path / "Output"),
                step_export=False,
                step_clean=False,
                step_translate=True,
                step_inject=False,
            )


class TestPruneFunctions:
    """測試 prune 函數 - 從模組導入的函數"""

    def test_prune_en_us_by_zh_tw(self) -> None:
        from translation_tool.core.ftb_translator_clean import prune_en_us_by_zh_tw

        en_us = {"key1": "EN1", "key2": "EN2", "key3": "EN3"}
        zh_tw = {"key1": "TW1", "key2": ""}

        result = prune_en_us_by_zh_tw(en_us, zh_tw)
        assert "key1" not in result
        assert result["key2"] == "EN2"
        assert result["key3"] == "EN3"

    def test_prune_flat_en_by_tw(self) -> None:
        from translation_tool.core.ftb_translator_clean import prune_flat_en_by_tw

        en_map = {"key1": "EN1", "key2": "EN2"}
        tw_available = {"key1": "TW1"}

        result = prune_flat_en_by_tw(en_map, tw_available)
        assert result == {"key2": "EN2"}


class TestPipelineOutputStructure:
    """測試 Pipeline 輸出結構"""

    def test_pipeline_creates_required_output_dirs(self, tmp_path: Path) -> None:
        quests_dir = tmp_path / "config" / "ftbquests" / "quests"
        quests_dir.mkdir(parents=True)
        lang_dir = quests_dir / "lang"
        lang_dir.mkdir(parents=True)

        (lang_dir / "en_us.json").write_bytes(orjson.dumps({"key": "value"}))

        result = ftb_translator.run_ftb_pipeline(
            directory_path=str(tmp_path),
            output_dir=str(tmp_path / "Output"),
            step_export=True,
            step_clean=True,
            step_translate=False,
            step_inject=False,
        )

        # 驗證輸出目錄存在
        output = Path(tmp_path / "Output")
        assert output.exists()
