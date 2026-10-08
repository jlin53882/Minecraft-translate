"""open_preview_dialog 的行為層測試（#114）。

直接操作預覽對話框：開始掃描、背景 worker 與 ``ui_poller``（用同步 stub 與手動 drain 取代真實執行緒／event loop）、
結果畫面、確認執行／取消、錯誤、中途 dismiss、重新掃描。
"""

from __future__ import annotations

import asyncio

import flet as ft
import pytest

from app.tasks import operation_registry
from app.views.extractor import extractor_dialog
from app.views.extractor import extractor_preview_dialog as mod
from tests.conftest import _make_page, mock_filepicker


def _walk(control):
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    title = getattr(control, "title", None)
    if title is not None and not isinstance(title, str):
        yield from _walk(title)


def _texts(dialog) -> list[str]:
    out = []
    for c in _walk(dialog):
        value = getattr(c, "value", None)
        if isinstance(value, str):
            out.append(value)
    return out


def _action(dialog, label: str):
    for a in dialog.actions:
        if getattr(a, "content", None) == label or getattr(a, "text", None) == label:
            return a
    raise AssertionError(
        f"找不到按鈕 {label}：{[getattr(a, 'content', a) for a in dialog.actions]}"
    )


class _Env:
    def __init__(self, monkeypatch, tmp_path):
        self.page = _make_page(width=1200, height=800)
        self.mods = tmp_path / "mods"
        self.mods.mkdir()
        self.threads: list = []
        self.updates: list[dict] = []
        self.opened_extractor: list[dict] = []
        env = self

        class _Thread:
            def __init__(self, target=None, daemon=None, **_):
                self.target = target
                env.threads.append(self)

            def start(self):
                pass

        monkeypatch.setattr(operation_registry.threading, "Thread", _Thread)
        monkeypatch.setattr(mod, "_UI_FLUSH_INTERVAL_SEC", 0)
        monkeypatch.setattr(
            mod,
            "prepare_extraction_paths",
            lambda mods, mode, out: str(tmp_path / "resolved_out"),
        )
        monkeypatch.setattr(
            mod, "preview_extraction_generator", lambda *a, **k: iter(env.updates)
        )
        monkeypatch.setattr(
            extractor_dialog,
            "open_extractor_dialog",
            lambda *a, **k: env.opened_extractor.append(k),
        )

    def open(self, **kwargs):
        kwargs.setdefault("input_path", str(self.mods))
        kwargs.setdefault("output_path", str(self.mods / "out"))
        return mod.open_preview_dialog(self.page, mock_filepicker(), **kwargs)

    def scan(self, dialog):
        """按下「開始預覽」並同步跑完 worker 與 ui_poller。"""
        _action(dialog, "開始預覽").on_click(None)
        self.threads[-1].target()
        tasks, self.page._tasks = self.page._tasks, []
        for coro, args in tasks:
            asyncio.run(coro(*args))


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


def _result(count=2, size=1.5):
    return {
        "preview_results": [
            {"jar": "a.jar", "count": count, "size_mb": size},
            {"jar": "empty.jar", "count": 0, "size_mb": 0},
        ],
        "total_files": count,
        "total_size_mb": size,
    }


def test_open_shows_unlocked_dialog_with_start_button_only(env):
    dialog = env.open(mode="lang")
    assert dialog in env.page.overlay and dialog.open is True
    assert dialog.modal is False
    assert [getattr(a, "content", None) for a in dialog.actions] == ["開始預覽"]
    texts = _texts(dialog)
    assert "預覽 - LANG" in texts and "等待開始預覽..." in texts


def test_start_locks_modal_and_starts_worker_and_poller(env):
    dialog = env.open()
    start = _action(dialog, "開始預覽")
    start.on_click(None)
    start.on_click(None)  # 掃描中連點不得重複啟動

    assert dialog.modal is True
    assert start.disabled is True
    assert "正在掃描..." in _texts(dialog)
    assert len(env.threads) == 1
    assert len(env.page._tasks) == 1  # ui_poller


def test_scan_result_replaces_dialog_content_in_place(env):
    env.updates = [
        {"progress": 0.5, "current": 1, "total": 2, "log": "掃描中 1/2"},
        {"result": _result()},
    ]
    dialog = env.open(mode="lang")
    env.scan(dialog)

    assert dialog.modal is False
    texts = _texts(dialog)
    assert "提取預覽 - LANG" in texts
    assert "共找到 2 個檔案" in texts
    assert "總大小：1.50 MB" in texts
    assert any("a.jar" in t and "2 個檔案" in t for t in texts)
    assert not any("empty.jar" in t for t in texts)  # 沒有可提取檔案的 JAR 不列出
    assert any("另有 1 個 JAR 沒有可提取的檔案" in t for t in texts)
    labels = [
        getattr(a, "content", None) or getattr(a, "text", None) for a in dialog.actions
    ]
    assert labels == ["取消", "確認執行"]


def test_dual_mode_result_shows_lang_and_book_counts(env):
    env.updates = [
        {
            "result": {
                "preview_results": [
                    {"jar": "m.jar", "lang_count": 3, "book_count": 4, "size_mb": 1}
                ],
                "total_files": 7,
                "total_size_mb": 1,
            }
        }
    ]
    dialog = env.open(mode="dual")
    env.scan(dialog)
    texts = _texts(dialog)
    assert "Lang：3 個" in texts and "Book：4 個" in texts
    assert any("Lang 3 個 / Book 4 個" in t for t in texts)


def test_scan_error_is_reported_in_status(env):
    env.updates = [{"error": "讀取失敗"}]
    dialog = env.open()
    env.scan(dialog)
    assert "預覽失敗：讀取失敗" in _texts(dialog)
    assert _action(dialog, "開始預覽").disabled is False  # 可重新掃描


def test_confirm_closes_preview_and_opens_extractor_with_auto_start(env):
    env.updates = [{"result": _result()}]
    dialog = env.open(mode="book", skip_zh_cn=True)
    env.scan(dialog)
    _action(dialog, "確認執行").on_click(None)

    assert dialog.open is False
    assert len(env.opened_extractor) == 1
    kwargs = env.opened_extractor[0]
    assert kwargs["auto_start"] is True
    assert kwargs["mode"] == "book"
    assert kwargs["input_path"] == str(env.mods)
    assert kwargs["skip_zh_cn"] is True  # 預覽時勾選的「跳過 zh_cn」要帶到實際提取


def test_cancel_in_result_view_only_pops_the_dialog(env):
    env.updates = [{"result": _result()}]
    dialog = env.open()
    env.scan(dialog)
    _action(dialog, "取消").on_click(None)
    assert dialog.open is False
    assert env.opened_extractor == []


def test_dismiss_while_scanning_stops_the_worker_early(env, monkeypatch):
    seen = []

    def gen(*a, **k):
        yield {"progress": 0.1, "current": 1, "total": 3, "log": "一"}
        dialog.on_dismiss(None)
        yield {"progress": 0.2, "current": 2, "total": 3, "log": "二"}
        seen.append("不應該跑到這裡")
        yield {"result": _result()}

    monkeypatch.setattr(mod, "preview_extraction_generator", gen)
    dialog = env.open()
    _action(dialog, "開始預覽").on_click(None)
    env.threads[-1].target()
    assert seen == []


def test_cancelled_scan_does_not_report_100_percent(env, monkeypatch):
    dialog_ref = {}

    def gen(*_args, **_kwargs):
        yield {"progress": 0.4, "current": 2, "total": 5, "log": "掃描中"}
        dialog_ref["dialog"].on_dismiss(None)
        yield {"result": _result()}

    monkeypatch.setattr(mod, "preview_extraction_generator", gen)
    dialog = env.open()
    dialog_ref["dialog"] = dialog
    _action(dialog, "開始預覽").on_click(None)
    env.threads[-1].target()

    tasks, env.page._tasks = env.page._tasks, []
    for coro, args in tasks:
        asyncio.run(coro(*args))

    texts = _texts(dialog)
    assert "已取消" in texts
    assert "100%" not in texts
    assert not any("預覽完成" == text for text in texts)


def test_rescan_after_a_finished_scan_works_again(env):
    env.updates = [{"result": _result()}]
    dialog = env.open()
    env.scan(dialog)
    # 結果畫面把按鈕換成「取消／確認執行」；用新的對話框重新掃描同一資料夾也要正常
    env.updates = [{"result": _result(count=5)}]
    dialog2 = env.open()
    env.scan(dialog2)
    assert "共找到 5 個檔案" in _texts(dialog2)


def test_start_without_output_path_shows_resolved_output(env):
    env.updates = [{"result": _result()}]
    dialog = env.open(output_path="")
    _action(dialog, "開始預覽").on_click(None)
    assert any(
        "輸出（確認執行後）：" in t and "resolved_out" in t for t in _texts(dialog)
    )


def test_confirmation_uses_the_output_path_shown_by_preview(env):
    env.updates = [{"result": _result()}]
    dialog = env.open(output_path="")
    env.scan(dialog)

    _action(dialog, "確認執行").on_click(None)

    assert env.opened_extractor[-1]["output_path"] == str(
        env.mods.parent / "resolved_out"
    )


def test_dialog_dismiss_when_idle_is_harmless(env):
    dialog = env.open()
    dialog.on_dismiss(None)
    assert env.threads == []
    assert isinstance(dialog, ft.AlertDialog)


def test_rescan_on_the_same_dialog_after_an_error_resets_the_state(env):
    """問題 6：上一次掃描的 done=True 不得讓這次的 ui_poller 立刻結束。"""
    env.updates = [{"error": "第一次失敗"}]
    dialog = env.open()
    env.scan(dialog)
    assert "預覽失敗：第一次失敗" in _texts(dialog)

    env.updates = [{"result": _result(count=7)}]
    env.scan(dialog)
    assert "共找到 7 個檔案" in _texts(dialog)


def test_error_unlocks_modal_so_the_dialog_can_be_closed(env):
    """預覽失敗後必須解除 modal（原本鎖死、沒有任何關閉方式）並重設進度。"""
    env.updates = [
        {"progress": 0.6, "current": 3, "total": 5, "log": "掃描中"},
        {"error": "壞掉了"},
    ]
    dialog = env.open()
    env.scan(dialog)
    assert "預覽失敗：壞掉了" in _texts(dialog)
    assert dialog.modal is False
    assert "100%" not in _texts(dialog)


def test_empty_result_unlocks_modal(env):
    env.updates = []
    dialog = env.open()
    env.scan(dialog)
    assert dialog.modal is False
