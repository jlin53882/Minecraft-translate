"""open_extractor_dialog 的行為層測試（#114）。

原本這個對話框只有「原始碼文字樣式」測試保護；這裡直接操作對話框的按鈕與背景執行緒，
驗證實際行為：驗證輸入、modal 鎖定、取消旗標、完成後的 UI 切換與統計、錯誤處理、DUAL 分區。

背景執行緒以同步 stub 取代（start() 不真的開執行緒，測試手動呼叫 target），
``page.run_task`` 排入的 coroutine 由 ``_drain`` 執行。
"""

from __future__ import annotations

import flet as ft
import pytest

from app.views.extractor import extractor_dialog as mod
from tests.conftest import _make_page, mock_filepicker


def _walk(control):
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)


def _texts(dialog) -> list[str]:
    out = []
    for c in _walk(dialog):
        value = getattr(c, "value", None)
        if isinstance(value, str):
            out.append(value)
    return out


def _button(dialog, label: str):
    for a in dialog.actions:
        if getattr(a, "content", None) == label:
            return a
    raise AssertionError(f"找不到按鈕 {label}")


def _drain(page) -> None:
    for _ in range(5):
        tasks, page._tasks = page._tasks, []
        if not tasks:
            return
        for coro, args in tasks:
            result = coro(*args)
            if result is not None:
                try:
                    result.send(None)
                except StopIteration:
                    pass


class _Env:
    def __init__(self, monkeypatch, tmp_path):
        self.page = _make_page(width=1200, height=800)
        self.mods = tmp_path / "mods"
        self.mods.mkdir()
        self.out = tmp_path / "out"
        self.threads: list = []
        self.loop_calls: list[dict] = []
        self.generators: list[tuple[str, dict]] = []
        self.opened: list[str] = []
        self.result = {"success": 3, "warnings": 1, "failures": 0}
        self.raise_in_loop: Exception | None = None
        self.completed: list = []

        env = self

        class _Thread:
            def __init__(self, target=None, daemon=None, **_):
                self.target = target
                env.threads.append(self)

            def start(self):
                pass

        monkeypatch.setattr(mod.threading, "Thread", _Thread)
        monkeypatch.setattr(
            mod, "prepare_extraction_paths", lambda mods, mode, out: str(self.out)
        )
        monkeypatch.setattr(mod, "get_lang_codes", lambda skip_zh_cn=False: ["zh_tw"])

        def make_gen(name):
            def gen(*args, **kwargs):
                env.generators.append((name, kwargs))
                return iter(())

            return gen

        monkeypatch.setattr(mod, "extract_lang_files_generator", make_gen("lang"))
        monkeypatch.setattr(mod, "extract_book_files_generator", make_gen("book"))
        monkeypatch.setattr(mod, "extract_dual_files_generator", make_gen("dual"))

        def fake_loop(gen, cancelled_flag, on_update):
            env.loop_calls.append({"flag": cancelled_flag, "on_update": on_update})
            if env.raise_in_loop is not None:
                raise env.raise_in_loop
            on_update({"progress": 0.5, "current": 1, "total": 2, "log": "進度訊息"})
            return dict(env.result)

        monkeypatch.setattr(mod, "run_extraction_loop", fake_loop)
        monkeypatch.setattr(mod, "open_output_folder", lambda p: env.opened.append(p))

    def open(self, **kwargs):
        kwargs.setdefault("input_path", str(self.mods))
        kwargs.setdefault("output_path", str(self.out))
        kwargs.setdefault(
            "on_complete", lambda done, stats: self.completed.append((done, stats))
        )
        return mod.open_extractor_dialog(self.page, mock_filepicker(), **kwargs)

    def run_thread(self):
        assert len(self.threads) == 1, "應該只啟動一條提取執行緒"
        self.threads[0].target()
        _drain(self.page)


@pytest.fixture
def env(monkeypatch, tmp_path):
    return _Env(monkeypatch, tmp_path)


def test_open_shows_dialog_with_only_start_button(env):
    dialog = env.open()
    assert dialog.open is True and dialog in env.page.overlay
    assert dialog.modal is False
    assert _button(dialog, "開始提取").visible is True
    for label in ("取消", "關閉", "開啟輸出資料夾"):
        assert _button(dialog, label).visible is False
    assert "等待任務啟動..." in _texts(dialog)


@pytest.mark.parametrize(
    ("input_path", "snack_text", "status"),
    [
        ("", "⚠️ 請先選擇 Mods 資料夾", "⚠️ 請先設定 Mod 來源"),
        ("__missing__", "⚠️ Mods 資料夾不存在", "⚠️ Mod 來源資料夾不存在"),
    ],
)
def test_start_is_rejected_for_missing_mods_dir(env, input_path, snack_text, status):
    path = str(env.mods / "no_such_dir") if input_path == "__missing__" else input_path
    dialog = env.open(input_path=path)
    _button(dialog, "開始提取").on_click(None)

    snacks = [c for c in env.page.overlay if isinstance(c, ft.SnackBar)]
    assert snacks and snacks[-1].content.value == snack_text
    assert status in _texts(dialog)
    assert env.threads == []
    assert dialog.modal is False


def test_start_locks_modal_switches_buttons_and_starts_one_thread(env):
    dialog = env.open()
    start = _button(dialog, "開始提取")
    start.on_click(None)
    start.on_click(None)  # 連點不得重複啟動

    assert dialog.modal is True
    assert start.visible is False
    assert _button(dialog, "取消").visible is True
    assert len(env.threads) == 1


@pytest.mark.parametrize(
    ("mode", "expected_generator"),
    [("lang", "lang"), ("book", "book"), ("dual", "dual")],
)
def test_run_completes_unlocks_dialog_and_reports_stats(env, mode, expected_generator):
    dialog = env.open(mode=mode, skip_zh_cn=True)
    _button(dialog, "開始提取").on_click(None)
    env.run_thread()

    name, kwargs = env.generators[0]
    assert name == expected_generator
    assert kwargs.get("skip_zh_cn") is True
    if mode != "book":
        assert kwargs.get("lang_codes") == ["zh_tw"]

    assert dialog.modal is False
    assert _button(dialog, "取消").visible is False
    assert _button(dialog, "關閉").visible is True
    assert _button(dialog, "開啟輸出資料夾").visible is True
    texts = _texts(dialog)
    assert "3" in texts and "1" in texts and "0" in texts  # 成功／跳過／失敗
    assert any("[完成] 成功 3 / 跳過 1 / 失敗 0" in t for t in texts)
    assert "100%" in texts
    assert len(env.completed) == 1 and env.completed[0][0] is True
    assert env.out.exists()


def test_dual_mode_fills_lang_and_book_rows(env):
    env.result = {
        "success": 5,
        "warnings": 2,
        "failures": 0,
        "lang": {"success": 3, "warnings": 1},
        "book": {"success": 2, "warnings": 1},
    }
    dialog = env.open(mode="dual")
    _button(dialog, "開始提取").on_click(None)
    env.run_thread()

    keyed = {
        c.key: c
        for c in _walk(dialog)
        if getattr(c, "key", None)
        in ("lang_success", "lang_warnings", "book_success", "book_warnings")
    }
    assert keyed["lang_success"].value == "3"
    assert keyed["lang_warnings"].value == "1"
    assert keyed["book_success"].value == "2"
    assert keyed["book_warnings"].value == "1"


def test_cancel_updates_status_text(env):
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)
    _button(dialog, "取消").on_click(None)
    assert "正在取消..." in _texts(dialog)


def test_cancel_during_service_run_marks_task_cancelled(env, monkeypatch):
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)

    def loop_that_gets_cancelled(gen, cancelled_flag, on_update):
        _button(dialog, "取消").on_click(None)
        assert cancelled_flag[0] is True
        return {"success": 0, "warnings": 0, "failures": 0}

    monkeypatch.setattr(mod, "run_extraction_loop", loop_that_gets_cancelled)
    env.run_thread()

    assert any("[系統] 任務已取消" in t for t in _texts(dialog))
    assert dialog.modal is False


def test_service_error_is_logged_and_dialog_is_unlocked(env):
    env.raise_in_loop = RuntimeError("壞掉了")
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)
    env.run_thread()

    texts = _texts(dialog)
    assert any("[ERROR] 壞掉了" in t for t in texts)
    assert any("[TRACEBACK]" in t for t in texts)
    assert dialog.modal is False
    assert _button(dialog, "關閉").visible is True
    assert len(env.completed) == 1


def test_dismiss_while_running_sets_the_service_cancel_flag(env, monkeypatch):
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)
    seen = {}

    def loop_that_gets_dismissed(gen, cancelled_flag, on_update):
        assert cancelled_flag[0] is False  # 新任務開始時旗標已重設
        dialog.on_dismiss(None)
        seen["flag"] = cancelled_flag[0]
        return {"success": 0, "warnings": 0, "failures": 0}

    monkeypatch.setattr(mod, "run_extraction_loop", loop_that_gets_dismissed)
    env.run_thread()
    assert seen["flag"] is True


def test_dismiss_when_idle_does_not_touch_the_flag(env, monkeypatch):
    dialog = env.open()
    dialog.on_dismiss(None)  # 沒有任務時 dismiss：不丟例外、不啟動任何執行緒
    assert env.threads == []


def test_close_pops_dialog_and_browse_opens_output_folder(env):
    dialog = env.open()
    _button(dialog, "關閉").on_click(None)
    assert dialog.open is False

    _button(dialog, "開啟輸出資料夾").on_click(None)
    assert env.opened == [str(env.out)]


def test_auto_start_runs_without_a_click(env):
    dialog = env.open(auto_start=True)
    assert dialog.modal is True
    assert len(env.threads) == 1


def test_stats_reach_on_complete(env):
    """成功時 on_complete 收到 service 回報的統計（原本恆為 0/0/0）。"""
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)
    env.run_thread()
    done, stats = env.completed[0]
    assert done is True
    assert (stats["success"], stats["warnings"], stats["failures"]) == (3, 1, 0)


def test_exception_is_reported_as_not_done_with_one_failure(env):
    env.raise_in_loop = RuntimeError("壞掉了")
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)
    env.run_thread()
    done, stats = env.completed[0]
    assert done is False
    assert stats["failures"] == 1


def test_cancelled_run_is_not_reported_as_complete(env, monkeypatch):
    """取消後不得出現「[完成]」與 100%「任務完成」，on_complete 也不是 done。"""
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)

    def loop_that_gets_cancelled(gen, cancelled_flag, on_update):
        on_update({"progress": 0.4, "current": 2, "total": 5, "log": "處理中"})
        _button(dialog, "取消").on_click(None)
        return {"success": 2, "warnings": 0, "failures": 0}

    monkeypatch.setattr(mod, "run_extraction_loop", loop_that_gets_cancelled)
    env.run_thread()

    texts = _texts(dialog)
    assert not any("[完成]" in t for t in texts)
    assert "任務完成" not in texts and "100%" not in texts
    assert any("[取消] 已處理部分：成功 2 / 跳過 0 / 失敗 0" in t for t in texts)
    assert "已取消" in texts
    assert env.completed[0][0] is False
    assert dialog.modal is False


def test_failure_before_the_try_block_still_unlocks_the_dialog(env, monkeypatch):
    """輸出目錄建不起來（權限／路徑是檔案）時，不得讓對話框永遠停在 modal + 取消。"""

    def boom(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(mod.os, "makedirs", boom)
    dialog = env.open()
    _button(dialog, "開始提取").on_click(None)
    env.run_thread()

    assert any("[ERROR] denied" in t for t in _texts(dialog))
    assert dialog.modal is False
    assert _button(dialog, "取消").visible is False
    assert _button(dialog, "關閉").visible is True
