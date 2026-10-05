"""啟動時「上次被中斷的任務」確認對話框的行為（#151）。

重點契約：使用者按下「續跑」之前不會啟動任何翻譯；放棄會清除標記；
指紋不符時只能放棄或稍後，並且說明原因。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import flet as ft

from app.shell import resume_prompt as rp
from app.shell.resume_prompt import ResumePrompt, describe_task


def _task(**overrides):
    base = {
        "input_dir": "C:/mods/assets",
        "output_dir": "C:/out",
        "export_lang": False,
        "write_new_cache": True,
        "completed": 40,
        "total": 100,
        "updated_at": "2026-10-05T12:00:00+0800",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakePage:
    def __init__(self, *, with_dialog: bool = True) -> None:
        self.dialogs: list = []
        self.updates = 0
        self.tasks: list = []
        if with_dialog:
            self.show_dialog = self.dialogs.append

    def pop_dialog(self, *_a) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def update(self, *_a, **_k) -> None:
        self.updates += 1

    def run_task(self, handler, *args):
        self.tasks.append(handler)


class Harness:
    """把背景檢查延後到 ``finish_check()``，讓測試能觀察「檢查中」的狀態。"""

    def __init__(self, *, task=None, check=None, page=None) -> None:
        self.page = page or FakePage()
        self.task = task
        self.check_result = check or SimpleNamespace(ok=True, reason="")
        self.checked: list = []
        self.discarded = 0
        self.resumed: list = []
        self.pending: list = []
        self.prompt = ResumePrompt(
            self.page,
            find_task=lambda: self.task,
            check_resume=self._check,
            discard=self._discard,
            on_resume=self.resumed.append,
            run_background=lambda work, done: self.pending.append((work, done)),
        )

    def _check(self, task):
        self.checked.append(task)
        return self.check_result

    def _discard(self):
        self.discarded += 1

    def finish_check(self) -> None:
        work, done = self.pending.pop(0)
        done(work())

    @property
    def dialog(self) -> ft.AlertDialog:
        return self.page.dialogs[-1]

    def button(self, label: str) -> ft.TextButton:
        return next(b for b in self.dialog.actions if b.content == label)

    def status_text(self) -> str:
        return self.dialog.content.controls[1].value


def _click(button: ft.TextButton) -> None:
    button.on_click(None)


def test_no_interrupted_task_shows_nothing():
    h = Harness(task=None)

    assert h.prompt.show_if_needed() is False
    assert h.page.dialogs == []
    assert h.pending == []


def test_page_without_dialog_support_is_skipped_quietly():
    h = Harness(task=_task(), page=FakePage(with_dialog=False))

    assert h.prompt.show_if_needed() is False
    assert h.resumed == []


def test_detection_failure_never_breaks_startup():
    page = FakePage()
    prompt = ResumePrompt(
        page,
        find_task=lambda: (_ for _ in ()).throw(OSError("unreadable")),
        check_resume=lambda t: None,
        discard=lambda: None,
        on_resume=lambda t: None,
    )

    assert prompt.show_if_needed() is False


def test_dialog_describes_the_interrupted_task_and_waits_for_the_check():
    h = Harness(task=_task())

    assert h.prompt.show_if_needed() is True

    text = describe_task(_task())
    assert "C:/mods/assets" in text and "C:/out" in text and "40 / 100" in text
    assert h.dialog.modal is True
    assert "檢查" in h.status_text()
    assert h.button("續跑").disabled is True, "檢查完成前不可續跑"
    assert h.resumed == [] and h.discarded == 0


def test_resume_is_enabled_only_after_the_check_passes_and_requires_a_click():
    h = Harness(task=_task())
    h.prompt.show_if_needed()
    h.finish_check()

    assert h.checked == [h.task]
    assert h.button("續跑").disabled is False
    assert h.resumed == [], "檢查通過也不得自動續跑，必須由使用者確認"

    _click(h.button("續跑"))

    assert h.resumed == [h.task]
    assert h.page.dialogs == []
    assert h.discarded == 0


def test_resume_click_during_the_check_does_nothing():
    h = Harness(task=_task())
    h.prompt.show_if_needed()

    _click(h.button("續跑"))  # 按鈕仍是停用狀態

    assert h.resumed == []


def test_changed_input_shows_the_reason_and_blocks_resume():
    h = Harness(
        task=_task(),
        check=SimpleNamespace(
            ok=False, reason="輸入內容與上次不同（檔案或文字已變動）"
        ),
    )
    h.prompt.show_if_needed()
    h.finish_check()

    assert "無法續跑" in h.status_text()
    assert "輸入內容與上次不同" in h.status_text()
    assert h.button("續跑").disabled is True
    _click(h.button("續跑"))
    assert h.resumed == []
    # 仍可放棄
    _click(h.button("放棄"))
    assert h.discarded == 1


def test_discard_clears_the_marker_without_resuming(monkeypatch):
    snacks: list[str] = []
    monkeypatch.setattr(rp, "show_snack", lambda page, msg, *a, **k: snacks.append(msg))
    h = Harness(task=_task())
    h.prompt.show_if_needed()
    h.finish_check()

    _click(h.button("放棄"))

    assert h.discarded == 1
    assert h.resumed == []
    assert h.page.dialogs == []
    assert any("放棄" in m for m in snacks)


def test_discard_failure_is_reported_not_swallowed(monkeypatch):
    snacks: list[str] = []
    monkeypatch.setattr(rp, "show_snack", lambda page, msg, *a, **k: snacks.append(msg))
    h = Harness(task=_task())
    h.prompt._discard = lambda: (_ for _ in ()).throw(OSError("locked"))
    h.prompt.show_if_needed()

    _click(h.button("放棄"))

    assert any("無法清除" in m for m in snacks)


def test_later_keeps_the_marker_and_starts_nothing():
    h = Harness(task=_task())
    h.prompt.show_if_needed()
    h.finish_check()

    _click(h.button("稍後再說"))

    assert h.resumed == [] and h.discarded == 0
    assert h.page.dialogs == []


def test_check_finishing_after_the_dialog_was_closed_is_ignored():
    h = Harness(task=_task())
    h.prompt.show_if_needed()
    _click(h.button("稍後再說"))

    h.finish_check()  # 不應拋出例外，也不應更新已關閉的對話框
    assert h.resumed == []


def test_default_background_runner_delivers_the_result_via_page_run_task():
    page = FakePage()
    results: list = []
    prompt = ResumePrompt(
        page,
        find_task=lambda: _task(),
        check_resume=lambda t: SimpleNamespace(ok=True, reason=""),
        discard=lambda: None,
        on_resume=lambda t: None,
    )
    prompt._run_background(lambda: "checked", results.append)

    # 背景執行緒結束後，結果必須交給 page.run_task（UI 執行緒）才會套用
    for _ in range(200):
        if page.tasks:
            break
        time.sleep(0.01)
    assert page.tasks, "結果要經由 page.run_task 回到 UI 執行緒"
    assert results == []  # 在 UI 執行緒執行 handler 之前不得套用
    asyncio.run(page.tasks[0]())
    assert results == ["checked"]


def test_default_background_runner_turns_exceptions_into_a_failed_check():
    page = FakePage()
    results: list = []
    prompt = ResumePrompt(
        page,
        find_task=lambda: None,
        check_resume=lambda t: None,
        discard=lambda: None,
        on_resume=lambda t: None,
    )

    def boom():
        raise RuntimeError("scan crashed")

    prompt._run_background(boom, results.append)

    for _ in range(200):
        if page.tasks:
            break
        time.sleep(0.01)
    asyncio.run(page.tasks[0]())

    assert results[0].ok is False and "scan crashed" in results[0].reason
