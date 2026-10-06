"""一鍵對話框關閉：先 open=False 推到前端、移除 overlay 後再推一次，提示才不會被殘留遮罩蓋住。"""

from types import SimpleNamespace

from app.views.pipeline.pipeline_one_click_dialog import _one_click_dispose_dialogs


class _Page:
    def __init__(self, dialog):
        self.overlay = [dialog]
        self.events = []
        self._dialog = dialog

    def update(self):
        self.events.append(("update", self._dialog.open, self._dialog in self.overlay))


def test_dispose_flushes_close_then_flushes_removal():
    dlg = SimpleNamespace(open=True)
    page = _Page(dlg)
    ctx = SimpleNamespace(page=page, dialogs=[dlg])

    _one_click_dispose_dialogs(ctx)

    assert page.events == [("update", False, True), ("update", False, False)]
    assert page.overlay == [] and ctx.dialogs == []


def test_dispose_without_dialogs_does_not_extra_update():
    page = _Page(SimpleNamespace(open=False))
    page.overlay.clear()
    ctx = SimpleNamespace(page=page, dialogs=[])

    _one_click_dispose_dialogs(ctx)

    assert len(page.events) == 1


class _DialogApiPage(_Page):
    """有 show_dialog / pop_dialog 的頁面（Flet 0.85+ Web）。"""

    def __init__(self):
        self.overlay = []
        self.events = []
        self.shown = []
        self.popped = 0

    def update(self):
        self.events.append("update")

    def show_dialog(self, dialog):
        self.shown.append(dialog)

    def pop_dialog(self):
        self.popped += 1


def test_present_uses_show_dialog_when_page_supports_it():
    from app.views.pipeline.pipeline_one_click_dialog import (
        _one_click_present_dialog,
    )

    dlg = SimpleNamespace(open=False)
    page = _DialogApiPage()
    ctx = SimpleNamespace(page=page, dialogs=[], uses_dialog_api=True)

    _one_click_present_dialog(ctx, dlg)

    assert page.shown == [dlg] and page.overlay == [] and ctx.dialogs == [dlg]


def test_present_falls_back_to_overlay_without_dialog_api():
    from app.views.pipeline.pipeline_one_click_dialog import (
        _one_click_present_dialog,
    )

    dlg = SimpleNamespace(open=False)
    page = _Page(dlg)
    page.overlay.clear()
    ctx = SimpleNamespace(page=page, dialogs=[], uses_dialog_api=False)

    _one_click_present_dialog(ctx, dlg)

    assert page.overlay == [dlg] and dlg.open is True


def test_dispose_pops_dialog_and_flushes_again_with_dialog_api():
    dlg = SimpleNamespace(open=True)
    page = _DialogApiPage()
    ctx = SimpleNamespace(page=page, dialogs=[dlg], uses_dialog_api=True)

    _one_click_dispose_dialogs(ctx)

    assert dlg.open is False and page.popped == 1 and ctx.dialogs == []
    assert page.events == ["update", "update"]  # open=False 先送出、pop 後再送一次
