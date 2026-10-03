"""UI smoke 情境矩陣與截圖設定的契約測試。"""

from __future__ import annotations

import asyncio

import pytest

from tools.ui_smoke import (
    DIALOG_SMOKE_KEYS,
    SMOKE_SCENARIOS,
    VIEW_KEYS,
    _ack_capture,
    _expected_case_keys,
    parse_viewports,
)
from tools.ui_smoke_app import _wait_for_capture_ack


def test_view_scenario_covers_first_build_revisit_and_shared_dialogs() -> None:
    """每個 theme / viewport 都要有首次頁面、回訪與共用 Dialog 案例。"""
    keys = _expected_case_keys("views", ("dark",), ((1360, 900),))

    assert len(VIEW_KEYS) == 13
    assert len(keys) == 2 * len(VIEW_KEYS) + 3
    assert ("view_revisit", "dark", "1360x900", "dashboard") in keys
    assert ("dialog", "dark", "1360x900", "command_palette_reopen") in keys


def test_dialog_scenario_covers_every_primary_entry_point() -> None:
    """Dialog gallery 的期望清單要涵蓋所有登錄的正式入口。"""
    keys = _expected_case_keys("dialogs", ("light",), ((720, 900),))

    assert len(keys) == len(DIALOG_SMOKE_KEYS)
    assert ("dialog_gallery", "light", "720x900", "pipeline_extract_reopen") in keys


def test_each_state_scenario_has_a_case_for_every_viewport_and_theme() -> None:
    """各種工作台狀態均須在所有指定尺寸與主題留下案例。"""
    themes = ("dark", "light")
    viewports = ((900, 700), (720, 900))

    for scenario in SMOKE_SCENARIOS[2:]:
        keys = _expected_case_keys(scenario, themes, viewports)
        assert len(keys) == len(themes) * len(viewports)
        assert all(key[0] == "state" for key in keys)


def test_viewport_parser_accepts_narrow_and_portrait_cases() -> None:
    """viewport parser 接受窄版與直向尺寸，維持精確輸入值。"""
    assert parse_viewports(["900x700", "720x900"]) == ((900, 700), (720, 900))


def test_capture_ack_releases_the_next_smoke_step(tmp_path) -> None:
    """Playwright 完成截圖後，app 端可消費一次性同步標記。"""
    _ack_capture(tmp_path, "dark-1100x720-view-0")

    asyncio.run(
        _wait_for_capture_ack(
            tmp_path,
            "dark-1100x720-view-0",
            timeout_seconds=0.1,
            poll_interval=0.001,
        )
    )

    assert not (tmp_path / ".smoke-acks" / "dark-1100x720-view-0.done").exists()


def test_capture_ack_times_out_without_busy_spin(tmp_path) -> None:
    """缺少截圖確認時在期限內失敗，不無限輪詢。"""
    with pytest.raises(TimeoutError, match="截圖確認逾時"):
        asyncio.run(
            _wait_for_capture_ack(
                tmp_path,
                "missing-case",
                timeout_seconds=0.01,
                poll_interval=0.001,
            )
        )
