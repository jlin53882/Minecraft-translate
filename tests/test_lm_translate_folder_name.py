"""LM 翻譯輸出資料夾名稱的邊界：只接受單一資料夾名稱，不能讓輸出寫到輸出資料夾以外。"""

from __future__ import annotations

import json
import logging
import os
from unittest.mock import patch

import pytest

from app.views.pipeline.pipeline_config import PipelineConfig
from translation_tool.utils import config_manager as cm
from translation_tool.utils.config_schema import get_schema_default

DEFAULT = cm.DEFAULT_LM_TRANSLATE_FOLDER_NAME


def test_lm_folder_default_comes_from_schema():
    assert DEFAULT == get_schema_default("lm_translator.lm_translate_folder_name")


def _cfg(name):
    return {"lm_translator": {"lm_translate_folder_name": name}}


@pytest.mark.parametrize("name", ["LM翻譯後", "自訂輸出", "out.v2", "_翻譯輸出"])
def test_single_folder_names_are_accepted(name):
    assert cm.is_single_folder_name(name)
    cm.validate_lm_translate_folder_name(_cfg(name))
    assert cm.get_lm_translate_folder_name(_cfg(name)) == name


@pytest.mark.parametrize(
    "name",
    [
        ".",
        "..",
        "..\\..\\other",
        "../other",
        "sub/dir",
        "D:\\other",
        "D:other",
        "/abs",
        " 前置空白",
        "結尾空白 ",
        "結尾句點.",
        "a\tb",
        "CON",
        "con.txt",
        "LPT1",
        "a?b",
        123,
    ],
)
def test_paths_and_illegal_names_are_rejected_on_save_and_ignored_on_use(name, caplog):
    assert not cm.is_single_folder_name(name)
    with pytest.raises(cm.ConfigValidationError, match="LM 翻譯輸出資料夾"):
        cm.validate_lm_translate_folder_name(_cfg(name))
    with caplog.at_level(logging.WARNING, logger=cm.log.name):
        assert cm.get_lm_translate_folder_name(_cfg(name)) == DEFAULT
    assert "LM 翻譯輸出資料夾名稱無效" in caplog.text


@pytest.mark.parametrize("name", [None, "", "   ", "\t"])
def test_missing_or_blank_name_uses_the_default(name):
    cm.validate_lm_translate_folder_name(_cfg(name))
    assert cm.get_lm_translate_folder_name(_cfg(name)) == DEFAULT
    assert cm.get_lm_translate_folder_name({}) == DEFAULT


def test_existing_config_json_with_a_path_still_loads_and_stays_inside_output(
    tmp_path,
):
    """手動編輯過的 config.json 不會讓啟動失敗，流水線也不會寫到輸出資料夾以外。"""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_cfg("..\\..\\other")), encoding="utf-8")
    example = tmp_path / "config.example.json"
    example.write_text("{}", encoding="utf-8")
    with (
        patch.object(cm, "CONFIG_PATH", cfg_path),
        patch.object(cm, "EXAMPLE_PATH", example),
    ):
        loaded = cm.load_config()

    out = tmp_path / "out"
    pipeline = PipelineConfig("in", str(out), config=loaded)
    assert pipeline.translate_output_subfolder == DEFAULT
    assert os.path.commonpath(
        [os.path.abspath(pipeline.translate_output_dir), str(out)]
    ) == str(out)
