"""啟動時「上次被中斷的任務」確認對話框的行為（#151、#164）。

重點契約：使用者按下「續跑」之前不會啟動任何翻譯；放棄會清除標記；多個任務共用一個對話框、
各自續跑或放棄、一次只續跑一個；指紋不符時只能放棄或稍後，並且說明原因。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import flet as ft
import pytest

from app.shell import resume_prompt as rp
from app.shell.resume_prompt import ResumePrompt, describe_task


def _task(kind: str = "lm_directory", **overrides):
    base = {
        "kind": kind,
        "label": {"lm_directory": "機器翻譯", "md": "Markdown 翻譯"}.get(kind, kind),
        "input_dir": "C:/mods/assets",
        "output_dir": "C:/out",
        "export_lang": False,
        "write_new_cache": True,
        "options": {},
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


def _walk(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from _walk(child)
    inner = getattr(control, "content", None)
    if inner is not None and not isinstance(inner, str):
        yield from _walk(inner)
    for action in getattr(control, "actions", None) or []:
        yield from _walk(action)


class Harness:
    """把背景檢查延後到 ``finish_checks()``，讓測試能觀察「檢查中」的狀態。"""

    def __init__(self, *, tasks=None, checks=None, page=None) -> None:
        self.page = page or FakePage()
        self.tasks = tasks
        self.checks = checks or {}
        self.checked: list = []
        self.discarded: list = []
        self.resumed: list = []
        self.pending: list = []
        self.prompt = ResumePrompt(
            self.page,
            find_tasks=lambda: self.tasks,
            check_resume=self._check,
            discard=self.discarded.append,
            on_resume=self.resumed.append,
            run_background=lambda work, done: self.pending.append((work, done)),
        )

    def _check(self, task):
        self.checked.append(task)
        return self.checks.get(task.kind, SimpleNamespace(ok=True, reason=""))

    def finish_checks(self) -> None:
        pending, self.pending = self.pending, []
        for work, done in pending:
            done(work())

    @property
    def dialog(self) -> ft.AlertDialog:
        return self.page.dialogs[-1]

    def buttons(self, label: str) -> list[ft.TextButton]:
        return [
            c
            for c in _walk(self.dialog)
            if isinstance(c, ft.TextButton) and c.content == label
        ]

    def texts(self) -> list[str]:
        return [
            c.value for c in _walk(self.dialog) if isinstance(c, ft.Text) and c.value
        ]


def _click(button: ft.TextButton) -> None:
    button.on_click(None)


def test_no_interrupted_task_shows_nothing():
    for empty in (None, []):
        h = Harness(tasks=empty)

        assert h.prompt.show_if_needed() is False
        assert h.page.dialogs == [] and h.pending == []


def test_page_without_dialog_support_is_skipped_quietly():
    h = Harness(tasks=[_task()], page=FakePage(with_dialog=False))

    assert h.prompt.show_if_needed() is False
    assert h.resumed == []


def test_detection_failure_never_breaks_startup():
    prompt = ResumePrompt(
        FakePage(),
        find_tasks=lambda: (_ for _ in ()).throw(OSError("unreadable")),
        check_resume=lambda t: None,
        discard=lambda t: None,
        on_resume=lambda t: None,
    )

    assert prompt.show_if_needed() is False


def test_dialog_describes_the_task_and_waits_for_the_check():
    task = _task()
    h = Harness(tasks=[task])

    assert h.prompt.show_if_needed() is True

    text = describe_task(task)
    assert "機器翻譯" in text and "C:/mods/assets" in text and "C:/out" in text
    assert "40 / 100" in text
    assert "已保存的進度" in text, "顯示的是可恢復的進度，不是本次行程已處理的數量"
    assert h.dialog.modal is True
    assert any("檢查" in t for t in h.texts())
    (resume,) = h.buttons("續跑")
    assert resume.disabled is True, "檢查完成前不可續跑"
    assert h.resumed == [] and h.discarded == []


def test_resume_is_enabled_only_after_the_check_passes_and_requires_a_click():
    h = Harness(tasks=[_task()])
    h.prompt.show_if_needed()
    h.finish_checks()

    assert h.checked == h.tasks
    (resume,) = h.buttons("續跑")
    assert resume.disabled is False
    assert h.resumed == [], "檢查通過也不得自動續跑，必須由使用者確認"

    _click(resume)

    assert h.resumed == h.tasks
    assert h.page.dialogs == []
    assert h.discarded == []


def test_resume_click_during_the_check_does_nothing():
    h = Harness(tasks=[_task()])
    h.prompt.show_if_needed()

    _click(h.buttons("續跑")[0])  # 按鈕仍是停用狀態

    assert h.resumed == []


def test_changed_input_shows_the_reason_and_blocks_resume():
    h = Harness(
        tasks=[_task()],
        checks={
            "lm_directory": SimpleNamespace(
                ok=False, reason="輸入內容與上次不同（檔案或文字已變動）"
            )
        },
    )
    h.prompt.show_if_needed()
    h.finish_checks()

    assert any("無法續跑" in t and "輸入內容與上次不同" in t for t in h.texts())
    (resume,) = h.buttons("續跑")
    assert resume.disabled is True
    _click(resume)
    assert h.resumed == []
    _click(h.buttons("放棄")[0])  # 仍可放棄
    assert len(h.discarded) == 1


def test_discard_clears_the_marker_without_resuming(monkeypatch):
    snacks: list[str] = []
    monkeypatch.setattr(rp, "show_snack", lambda page, msg, *a, **k: snacks.append(msg))
    task = _task()
    h = Harness(tasks=[task])
    h.prompt.show_if_needed()
    h.finish_checks()

    _click(h.buttons("放棄")[0])

    assert h.discarded == [task]
    assert h.resumed == []
    assert h.page.dialogs == [], "最後一個任務放棄後對話框關閉"
    assert any("放棄" in m for m in snacks)


def test_discard_failure_is_reported_not_swallowed(monkeypatch):
    snacks: list[str] = []
    monkeypatch.setattr(rp, "show_snack", lambda page, msg, *a, **k: snacks.append(msg))
    h = Harness(tasks=[_task()])
    h.prompt._discard = lambda _t: (_ for _ in ()).throw(OSError("locked"))
    h.prompt.show_if_needed()

    _click(h.buttons("放棄")[0])

    assert any("無法清除" in m for m in snacks)
    assert h.page.dialogs, "清除失敗時保留對話框，使用者可以重試"


def test_later_keeps_every_marker_and_starts_nothing():
    h = Harness(tasks=[_task(), _task("md")])
    h.prompt.show_if_needed()
    h.finish_checks()

    _click(h.buttons("稍後再說")[0])

    assert h.resumed == [] and h.discarded == []
    assert h.page.dialogs == []


def test_check_finishing_after_the_dialog_was_closed_is_ignored():
    h = Harness(tasks=[_task()])
    h.prompt.show_if_needed()
    _click(h.buttons("稍後再說")[0])

    h.finish_checks()  # 不應拋出例外，也不應更新已關閉的對話框
    assert h.resumed == []


# -- #164：多個任務 -------------------------------------------------------------


def test_several_tasks_share_one_dialog_and_are_checked_independently():
    lm, md = _task("lm_directory"), _task("md", input_dir="D:/docs")
    h = Harness(
        tasks=[lm, md],
        checks={"md": SimpleNamespace(ok=False, reason="輸入內容與上次不同")},
    )

    assert h.prompt.show_if_needed() is True

    assert len(h.page.dialogs) == 1
    assert len(h.buttons("續跑")) == 2 and len(h.buttons("放棄")) == 2
    assert "2 個未完成" in h.dialog.title.value
    assert any("Markdown 翻譯" in t for t in h.texts())
    h.finish_checks()
    lm_resume, md_resume = h.buttons("續跑")
    assert lm_resume.disabled is False, "機器翻譯可續跑"
    assert md_resume.disabled is True, "MD 輸入已變動，只能放棄或稍後"
    assert h.checked == [lm, md]


def test_resuming_one_task_starts_only_that_task_and_keeps_the_others_markers():
    lm, md = _task("lm_directory"), _task("md")
    h = Harness(tasks=[lm, md])
    h.prompt.show_if_needed()
    h.finish_checks()

    _click(h.buttons("續跑")[1])

    assert h.resumed == [md], "一次只續跑被選的那個"
    assert h.discarded == [], "其餘任務的標記保留，下次啟動再詢問"
    assert h.page.dialogs == []


def test_discarding_one_task_keeps_the_dialog_for_the_remaining_ones(monkeypatch):
    monkeypatch.setattr(rp, "show_snack", lambda *a, **k: None)
    lm, md = _task("lm_directory"), _task("md")
    h = Harness(tasks=[lm, md])
    h.prompt.show_if_needed()
    h.finish_checks()

    _click(h.buttons("放棄")[0])  # 放棄機器翻譯

    assert h.discarded == [lm]
    assert h.page.dialogs, "還有任務時對話框保留"
    assert len(h.buttons("續跑")) == 1
    assert any("Markdown 翻譯" in t for t in h.texts())
    assert not any("機器翻譯" in t for t in h.texts() if "任務：" in t)
    _click(h.buttons("放棄")[0])  # 再放棄剩下的
    assert h.discarded == [lm, md]
    assert h.page.dialogs == []


def test_a_late_check_for_a_discarded_task_is_ignored(monkeypatch):
    monkeypatch.setattr(rp, "show_snack", lambda *a, **k: None)
    lm, md = _task("lm_directory"), _task("md")
    h = Harness(tasks=[lm, md])
    h.prompt.show_if_needed()
    _click(h.buttons("放棄")[0])  # 檢查還沒完成就放棄第一個

    h.finish_checks()  # 不得拋出例外或復活被放棄的任務

    assert len(h.buttons("續跑")) == 1


# -- 預設的背景執行 ---------------------------------------------------------------


def _wait_for(page: FakePage) -> None:
    for _ in range(200):
        if page.tasks:
            return
        time.sleep(0.01)


def test_default_background_runner_delivers_the_result_via_page_run_task():
    page = FakePage()
    results: list = []
    prompt = ResumePrompt(
        page,
        find_tasks=lambda: [_task()],
        check_resume=lambda t: SimpleNamespace(ok=True, reason=""),
        discard=lambda t: None,
        on_resume=lambda t: None,
    )
    prompt._run_background(lambda: "checked", results.append)

    _wait_for(page)
    assert page.tasks, "結果要經由 page.run_task 回到 UI 執行緒"
    assert results == []  # 在 UI 執行緒執行 handler 之前不得套用
    asyncio.run(page.tasks[0]())
    assert results == ["checked"]


def test_default_background_runner_turns_exceptions_into_a_failed_check():
    page = FakePage()
    results: list = []
    prompt = ResumePrompt(
        page,
        find_tasks=lambda: None,
        check_resume=lambda t: None,
        discard=lambda t: None,
        on_resume=lambda t: None,
    )

    def boom():
        raise RuntimeError("scan crashed")

    prompt._run_background(boom, results.append)
    _wait_for(page)
    asyncio.run(page.tasks[0]())

    assert results[0].ok is False and "scan crashed" in results[0].reason


def test_a_legacy_plugin_marker_is_shown_as_not_resumable_and_can_be_discarded(
    tmp_path, monkeypatch
):
    """#164：舊版標記不得靜默忽略——啟動時看到原因，只能放棄或稍後；放棄後標記被清除。"""
    import json

    from translation_tool.core import lm_resume, plugin_resume

    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rp, "show_snack", lambda *a, **k: None)
    marker = plugin_resume.marker_path("ftbquests")
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"plugin": "ftbquests", "processed": 2, "total": 7}),
        encoding="utf-8",
    )
    page = FakePage()
    prompt = ResumePrompt(
        page,
        find_tasks=lm_resume.peek_interrupted_tasks,
        check_resume=lm_resume.check_resume_feasibility,
        discard=lm_resume.discard_interrupted_task,
        on_resume=lambda t: pytest.fail("不可續跑"),
        run_background=lambda work, done: done(work()),
    )

    assert prompt.show_if_needed() is True

    texts = [c.value for c in _walk(page.dialogs[-1]) if isinstance(c, ft.Text)]
    assert any("FTB 任務翻譯" in t for t in texts)
    assert any("無法續跑" in t and "舊版" in t for t in texts)
    buttons = {
        c.content: c for c in _walk(page.dialogs[-1]) if isinstance(c, ft.TextButton)
    }
    assert buttons["續跑"].disabled is True
    buttons["放棄"].on_click(None)
    assert not marker.exists() and page.dialogs == []
