"""官方語系完整性驗證：容忍換行轉換，內容被改動或損壞時仍然失敗，且警告文字準確。

Windows 的 ``core.autocrlf=true`` 會把工作區的 JSON 轉成 CRLF；位元組雜湊因此與 manifest 不符，
整個官方術語索引被停用（官方精確翻譯與官方術語都不再注入）。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from translation_tool.core import official_locale
from translation_tool.core.official_locale import _OFFICIAL_RELATIVE_ROOT, _load_index

REAL_ROOT = Path(official_locale.__file__).resolve().parents[2]
FILES = ("manifest.json", "en_us.json", "zh_cn.json", "zh_tw.json")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    target = tmp_path / _OFFICIAL_RELATIVE_ROOT
    target.mkdir(parents=True)
    for name in FILES:
        shutil.copyfile(REAL_ROOT / _OFFICIAL_RELATIVE_ROOT / name, target / name)
    return tmp_path


def _locale_dir(root: Path) -> Path:
    return root / _OFFICIAL_RELATIVE_ROOT


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_lf_files_load(root, caplog):
    with caplog.at_level(logging.WARNING, logger=official_locale.logger.name):
        index = _load_index(root)

    assert index.available
    assert _warnings(caplog) == []


def test_crlf_converted_files_still_load(root, caplog):
    """工作區被 autocrlf 轉成 CRLF：位元組不同，但內容相同，索引必須照常載入。"""
    for name in FILES[1:]:
        path = _locale_dir(root) / name
        data = path.read_bytes()
        assert b"\r\n" not in data
        path.write_bytes(data.replace(b"\n", b"\r\n"))

    with caplog.at_level(logging.WARNING, logger=official_locale.logger.name):
        index = _load_index(root)

    assert index.available
    assert _warnings(caplog) == []


def test_changed_content_still_fails_with_an_accurate_message(root, caplog):
    path = _locale_dir(root) / "en_us.json"
    path.write_bytes(path.read_bytes().replace(b"Stone", b"Rock", 1))

    with caplog.at_level(logging.WARNING, logger=official_locale.logger.name):
        index = _load_index(root)

    assert not index.available
    (message,) = _warnings(caplog)
    assert "官方語系完整性驗證失敗：en_us.json" in message
    assert "SHA-256" in message and "manifest" in message
    assert "找不到" not in message  # 檔案存在，不能說找不到


def test_missing_file_reports_not_found(root, caplog):
    (_locale_dir(root) / "zh_tw.json").unlink()

    with caplog.at_level(logging.WARNING, logger=official_locale.logger.name):
        index = _load_index(root)

    assert not index.available
    (message,) = _warnings(caplog)
    assert message.startswith("找不到或無法讀取 Minecraft 官方語系")


def test_invalid_json_reports_a_format_error(root, caplog):
    (_locale_dir(root) / "zh_cn.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=official_locale.logger.name):
        index = _load_index(root)

    assert not index.available
    (message,) = _warnings(caplog)
    assert "格式無效" in message
