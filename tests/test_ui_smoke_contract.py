"""UI smoke 情境矩陣與截圖設定的契約測試。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools.ui_smoke import (
    DIALOG_SMOKE_KEYS,
    SMOKE_SCENARIOS,
    VIEW_KEYS,
    _ack_capture,
    _capture_case,
    _expected_case_keys,
    _title_matches,
    parse_viewports,
    wait_for_title,
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


class _TitleHandle:
    """提供 smoke waiter 所需的最小 title handle。"""

    def __init__(self, title: str) -> None:
        """保存瀏覽器目前符合條件的標題。"""
        self._title = title

    def json_value(self) -> str:
        """回傳模擬 Playwright handle 的標題值。"""
        return self._title


class _ScriptedTitlePage:
    """依標題序列模擬 Playwright 的 exact/prefix 等待條件。"""

    def __init__(self, titles: tuple[str, ...]) -> None:
        """依序提供 smoke app 的 lifecycle 標題。"""
        self._titles = titles
        self.predicate: str | None = None
        self.arg: str | dict[str, str | bool] | None = None

    def wait_for_function(
        self,
        _predicate: str,
        *,
        arg: str | dict[str, str | bool],
        timeout: int,
    ) -> _TitleHandle:
        """回傳第一個符合 waiter 契約的標題。"""
        del timeout
        self.predicate = _predicate
        self.arg = arg
        if isinstance(arg, str):
            expected, exact = arg, False
        else:
            expected, exact = str(arg["expected"]), bool(arg.get("exact", False))
        for title in self._titles:
            matched = title == expected if exact else title.startswith(expected)
            if title.startswith("SMOKE:ERROR:") or matched:
                return _TitleHandle(title)
        raise AssertionError("模擬標題序列沒有符合 waiter 的狀態")

    def title(self) -> str:
        """回傳序列最後一個標題，供錯誤訊息使用。"""
        return self._titles[-1]


def test_wait_for_title_does_not_capture_opening_prefix() -> None:
    """Dialog waiter 必須略過 OPENING，等到完整 OPEN 狀態才繼續。"""
    expected = "SMOKE:DIALOG:pipeline_merge:OPEN"
    page = _ScriptedTitlePage(
        (
            "SMOKE:DIALOG:pipeline_merge:OPENING",
            expected,
        )
    )

    assert wait_for_title(page, expected, 100) == expected
    assert page.arg == {"expected": expected, "exact": True}
    assert page.predicate is not None and "title === expected" in page.predicate


@pytest.mark.parametrize("sibling_state", ("OPENING", "OPENED", "OPEN_ERROR"))
def test_exact_title_match_rejects_sibling_lifecycle_states(
    sibling_state: str,
) -> None:
    """共同 prefix 的 sibling state 不得被視為預期的完整標題。"""
    expected = "SMOKE:DIALOG:pipeline_merge:OPEN"
    sibling = f"SMOKE:DIALOG:pipeline_merge:{sibling_state}"

    assert sibling.startswith(expected)
    assert not _title_matches(sibling, expected, exact=True)


def test_wait_for_title_keeps_explicit_prefix_matching() -> None:
    """帶建置耗時後綴的 smoke 標題仍可使用明確 prefix 模式。"""
    expected = "SMOKE:VIEW:0:dashboard:"
    current = f"{expected}18.25"
    page = _ScriptedTitlePage((current,))

    assert wait_for_title(page, expected, 100, exact=False) == current


class _ScreenshotPage:
    """以檔案寫入模擬 Playwright screenshot 的同步完成語意。"""

    def __init__(self, payload: bytes) -> None:
        """設定 screenshot 寫入的測試位元組。"""
        self._payload = payload

    def screenshot(self, *, path: str, full_page: bool) -> None:
        """將測試內容寫入要求的截圖路徑。"""
        del full_page
        Path(path).write_bytes(self._payload)


def test_capture_case_returns_after_nonempty_screenshot_is_written(
    tmp_path: Path,
) -> None:
    """截圖檔案必須先寫入且非空，呼叫端才能發送 ACK。"""
    filename = "dialog.png"

    assert _capture_case(_ScreenshotPage(b"png-bytes"), tmp_path, filename) == filename
    assert (tmp_path / filename).read_bytes() == b"png-bytes"


def test_capture_case_rejects_empty_screenshot(tmp_path: Path) -> None:
    """空截圖不得被當成已完成案例而放行 ACK。"""
    with pytest.raises(RuntimeError, match="截圖未完整寫入"):
        _capture_case(_ScreenshotPage(b""), tmp_path, "empty.png")
