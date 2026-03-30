"""ftb_translator_template.py 單元測試。

用途：測試 FTB Quests 語系模板處理功能。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core.ftb_translator_template import (
    prepare_ftbquests_lang_template_only_impl,
)


class TestPrepareFtbquestsLangTemplateOnlyImpl:
    """測試 prepare_ftbquests_lang_template_only_impl 函式。"""

    def test_copy_lang_directory(self, tmp_path: Path) -> None:
        """測試複製語系目錄。"""
        # 建立來源結構
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        src_ftb = input_config / "ftbquests" / "quests" / "lang" / "zh_cn"
        src_ftb.mkdir(parents=True)
        (src_ftb / "chapter.1.json").write_text('{"key": "value"}', encoding="utf-8")
        (src_ftb / "chapter.2.json").write_text('{"key2": "value2"}', encoding="utf-8")

        result = prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="zh_cn",
        )

        assert result["mode"] == "dir"
        assert "zh_cn" in result["template_used"]

        dst_dir = output_config / "ftbquests" / "quests" / "lang" / "zh_cn"
        assert dst_dir.exists()
        assert (dst_dir / "chapter.1.json").exists()

    def test_fallback_to_en_us_directory(self, tmp_path: Path) -> None:
        """測試回退到 en_us 目錄。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 只有 en_us 目錄
        src_ftb = input_config / "ftbquests" / "quests" / "lang" / "en_us"
        src_ftb.mkdir(parents=True)
        (src_ftb / "chapter.json").write_text('{"key": "value"}', encoding="utf-8")

        result = prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="zh_cn",  # 請求不存在的語系
        )

        assert result["mode"] == "dir"
        assert "en_us" in result["template_used"]

    def test_copy_single_lang_file(self, tmp_path: Path) -> None:
        """測試複製單一語系檔案。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        src_ftb = input_config / "ftbquests" / "quests" / "lang"
        src_ftb.mkdir(parents=True)
        (src_ftb / "zh_cn.snbt").write_text("key=value", encoding="utf-8")

        result = prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="zh_cn",
        )

        assert result["mode"] == "file"
        assert "zh_cn.snbt" in result["template_used"]

        dst_file = output_config / "ftbquests" / "quests" / "lang" / "zh_cn.snbt"
        assert dst_file.exists()

    def test_fallback_to_en_us_file(self, tmp_path: Path) -> None:
        """測試回退到 en_us 檔案。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 只有 en_us.snbt
        src_ftb = input_config / "ftbquests" / "quests" / "lang"
        src_ftb.mkdir(parents=True)
        (src_ftb / "en_us.snbt").write_text("key=value", encoding="utf-8")

        result = prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="zh_cn",
        )

        assert result["mode"] == "file"
        assert "en_us.snbt" in result["template_used"]

    def test_raises_error_when_no_template_found(self, tmp_path: Path) -> None:
        """測試找不到模板時拋出異常。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 建立空的 ftbquests 目錄
        src_ftb = input_config / "ftbquests" / "quests" / "lang"
        src_ftb.mkdir(parents=True)

        with pytest.raises(FileNotFoundError) as exc_info:
            prepare_ftbquests_lang_template_only_impl(
                input_config_dir=str(input_config),
                output_config_dir=str(output_config),
                prefer_lang="zh_cn",
            )

        assert "找不到 FTB Quests 模板語系" in str(exc_info.value)

    def test_raises_error_when_ftbquests_dir_missing(self, tmp_path: Path) -> None:
        """測試缺少 ftbquests 目錄時拋出異常。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 不建立 ftbquests 目錄
        input_config.mkdir()

        with pytest.raises(FileNotFoundError) as exc_info:
            prepare_ftbquests_lang_template_only_impl(
                input_config_dir=str(input_config),
                output_config_dir=str(output_config),
                prefer_lang="zh_cn",
            )

        assert "找不到來源 ftbquests" in str(exc_info.value)

    def test_creates_output_directories(self, tmp_path: Path) -> None:
        """測試自動建立輸出目錄。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 確保 output_config 不存在
        assert not output_config.exists()

        src_ftb = input_config / "ftbquests" / "quests" / "lang" / "en_us"
        src_ftb.mkdir(parents=True)
        (src_ftb / "test.json").write_text("{}", encoding="utf-8")

        result = prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="zh_cn",
        )

        assert output_config.exists()
        assert result["template_copied_to"]

    def test_overwrites_existing_files(self, tmp_path: Path) -> None:
        """測試覆蓋已存在的檔案。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 建立來源
        src_ftb = input_config / "ftbquests" / "quests" / "lang" / "en_us"
        src_ftb.mkdir(parents=True)
        (src_ftb / "test.json").write_text('{"new": "data"}', encoding="utf-8")

        # 先建立輸出
        dst_ftb = output_config / "ftbquests" / "quests" / "lang" / "en_us"
        dst_ftb.mkdir(parents=True)
        (dst_ftb / "test.json").write_text('{"old": "data"}', encoding="utf-8")

        prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="en_us",
        )

        # 檔案應該被覆蓋
        content = json.loads((dst_ftb / "test.json").read_text(encoding="utf-8"))
        assert content == {"new": "data"}

    def test_prefer_lang_parameter_respected(self, tmp_path: Path) -> None:
        """測試 prefer_lang 參數正確運作。"""
        input_config = tmp_path / "input"
        output_config = tmp_path / "output"

        # 建立多個語系
        for lang in ["en_us", "zh_cn", "zh_tw"]:
            src_ftb = input_config / "ftbquests" / "quests" / "lang" / lang
            src_ftb.mkdir(parents=True)
            (src_ftb / "test.json").write_text(f'{{"lang": "{lang}"}}', encoding="utf-8")

        result = prepare_ftbquests_lang_template_only_impl(
            input_config_dir=str(input_config),
            output_config_dir=str(output_config),
            prefer_lang="zh_tw",
        )

        assert "zh_tw" in result["template_used"]
        content = json.loads((Path(result["template_copied_to"]) / "test.json").read_text(encoding="utf-8"))
        assert content["lang"] == "zh_tw"
