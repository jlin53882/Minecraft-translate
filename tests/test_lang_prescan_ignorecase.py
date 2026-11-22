"""lang 預掃描必須沿用 target_regex 的 IGNORECASE 旗標（issue #185 補充第 2 項）。

預掃描若只傳 pattern 字串，重新編譯後旗標遺失，`zh_TW.json`、`en_US.lang`
這類大小寫變體會在預掃描階段被丟掉，提取階段也就拿不到。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from translation_tool.core import jar_processor
from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor_extract import run_extraction_process_impl
from translation_tool.utils import jar_browser

_MEMBERS = [
    "assets/x/lang/zh_TW.json",
    "assets/x/lang/en_US.lang",
    "assets/x/lang/zh_CN.lang",
]


def _make_jar(mods: Path) -> Path:
    mods.mkdir(parents=True, exist_ok=True)
    jar = mods / "oritech.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        for name in _MEMBERS:
            zf.writestr(name, '{"k": "v"}')
    return jar


def _regex() -> re.Pattern:
    return jar_processor.build_lang_file_regex(codes=["en_us", "zh_tw", "zh_cn"])


def test_scan_jars_keeps_ignorecase_flag_of_compiled_pattern(tmp_path):
    jar = _make_jar(tmp_path / "mods")

    result = jar_browser.scan_jars(tmp_path / "mods", [_regex()], jar_files=[jar])

    assert set(result[jar]) == set(_MEMBERS)


def test_extraction_prescan_hits_case_variants(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    _make_jar(mods)
    seen: dict = {}
    real_scan = jar_browser.scan_jars

    def spy(*args, **kwargs):
        result = real_scan(*args, **kwargs)
        seen["members"] = {n for members in result.values() for n in members}
        return result

    monkeypatch.setattr(jar_browser, "scan_jars", spy)

    updates = list(
        run_extraction_process_impl(
            str(mods),
            str(tmp_path / "out"),
            _regex(),
            "Lang",
            find_jar_files_fn=find_jar_files,
            extract_from_jar_fn=jar_processor._extract_from_jar,
        )
    )

    assert seen["members"] == set(_MEMBERS)
    assert updates[-1]["stats"]["success"] == 3
