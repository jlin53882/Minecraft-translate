"""scan_jars 的 jar_files 參數與提取預掃描清單一致性（issue #111）。

find_jar_files 用 os.walk（遞迴），scan_jars 預設只掃頂層。提取流程必須把
find_jar_files 的清單傳給 scan_jars，預掃描與實際處理的 JAR 才會一致。
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import pytest

from translation_tool.core import jar_processor
from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor_extract import run_extraction_process_impl
from translation_tool.utils import jar_browser
from translation_tool.utils.zip_safety import ZipReadBudget

_PATTERN = r"assets/[^/]+/lang/.*\.json$"


def _jar(path: Path, modid: str, n: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(n):
            zf.writestr(f"assets/{modid}/lang/en_us{i}.json", f'{{"k{i}": "v{i}"}}')
    return path


@pytest.fixture
def nested_mods(tmp_path) -> dict:
    mods = tmp_path / "mods"
    top = _jar(mods / "top.jar", "top")
    nested = _jar(mods / "sub" / "deep" / "nested.jar", "nested")
    return {"mods": mods, "top": top, "nested": nested}


# ---------------------------------------------------------------------------
# scan_jars 本身
# ---------------------------------------------------------------------------


def test_default_scan_still_only_scans_top_level(nested_mods):
    """未傳 jar_files 時維持既有行為（icon_preview_view 等呼叫端不受影響）。"""
    result = jar_browser.scan_jars(nested_mods["mods"], [_PATTERN], max_workers=2)

    assert set(result) == {nested_mods["top"]}
    assert set(result.budgets) == {nested_mods["top"]}


def test_explicit_jar_files_include_nested_jars(nested_mods):
    jars = [nested_mods["top"], nested_mods["nested"]]

    result = jar_browser.scan_jars(
        nested_mods["mods"], [_PATTERN], max_workers=2, jar_files=jars
    )

    assert set(result) == set(jars)
    # 每個被掃描的 JAR 各有一份預算，供提取階段沿用
    assert set(result.budgets) == set(jars)
    assert len({id(b) for b in result.budgets.values()}) == 2


def test_explicit_jar_files_accept_str_and_dedupe(nested_mods):
    top = nested_mods["top"]

    result = jar_browser.scan_jars(
        nested_mods["mods"], [_PATTERN], jar_files=[str(top), top, str(top)]
    )

    assert list(result.budgets) == [top]
    assert set(result) == {top}


def test_empty_jar_files_list_does_not_fall_back_to_glob(nested_mods):
    """空清單代表「沒有要掃的 JAR」，不可退回 glob 掃頂層。"""
    result = jar_browser.scan_jars(nested_mods["mods"], [_PATTERN], jar_files=[])

    assert dict(result) == {}
    assert result.budgets == {}


def test_progress_callback_total_matches_explicit_list(nested_mods):
    calls: list[tuple[int, int]] = []
    jars = [nested_mods["top"], nested_mods["nested"]]

    jar_browser.scan_jars(
        nested_mods["mods"],
        [_PATTERN],
        max_workers=1,
        processed_callback=lambda done, total: calls.append((done, total)),
        jar_files=jars,
    )

    assert [t for _, t in calls] == [2, 2]
    assert calls[-1][0] == 2


# ---------------------------------------------------------------------------
# 提取流程
# ---------------------------------------------------------------------------


def _run(mods: Path, out: Path) -> list[dict]:
    return list(
        run_extraction_process_impl(
            str(mods),
            str(out),
            re.compile(_PATTERN),
            "Lang",
            find_jar_files_fn=find_jar_files,
            extract_from_jar_fn=jar_processor._extract_from_jar,
        )
    )


def test_extraction_prescans_same_jars_as_find_jar_files(
    nested_mods, tmp_path, monkeypatch
):
    seen: dict = {}
    real_scan = jar_browser.scan_jars

    def spy(*args, **kwargs):
        seen["jar_files"] = kwargs.get("jar_files")
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(jar_browser, "scan_jars", spy)

    updates = _run(nested_mods["mods"], tmp_path / "out")

    expected = {Path(p) for p in find_jar_files(str(nested_mods["mods"]))}
    assert {Path(p) for p in seen["jar_files"]} == expected
    assert len(expected) == 2
    # stats.success 是提取出的檔案數：2 個 JAR × 2 個成員
    assert updates[-1]["stats"]["success"] == 4
    assert updates[-1]["stats"]["failures"] == 0


def test_prescan_log_numbers_match_actual_jar_list(nested_mods, tmp_path, caplog):
    with caplog.at_level(
        logging.INFO, logger="translation_tool.core.jar_processor_extract"
    ):
        _run(nested_mods["mods"], tmp_path / "out")

    msgs = [
        r.getMessage() for r in caplog.records if "[scan_jars] 完成" in r.getMessage()
    ]
    assert msgs, "找不到預掃描完成的日誌"
    assert "共預掃描 2 / 2 個 JAR" in msgs[0]
    assert "其中 2 個含可提取內容" in msgs[0]


def test_nested_jar_uses_single_budget_and_is_not_rescanned(
    nested_mods, tmp_path, monkeypatch
):
    """巢狀 JAR 與頂層 JAR 一樣：預掃描建立一份預算，提取階段沿用，不再自建。"""
    created: list[ZipReadBudget] = []

    def factory(label: str = "") -> ZipReadBudget:
        b = ZipReadBudget(max_bytes=10**9, max_members=10_000, label=label)
        created.append(b)
        return b

    monkeypatch.setattr("translation_tool.utils.jar_browser.ZipReadBudget", factory)
    monkeypatch.setattr(
        "translation_tool.core.jar_processor_extract.ZipReadBudget", factory
    )

    updates = _run(nested_mods["mods"], tmp_path / "out")

    assert updates[-1]["stats"]["success"] == 4
    assert len(created) == 2  # 每個 JAR（含巢狀）一份，提取階段沒有另建
    assert sorted(b.label for b in created) == ["nested.jar", "top.jar"]
    # 兩個 JAR 各 2 個文字成員，只在預掃描讀取一次
    assert [b.used_members for b in created] == [2, 2]
