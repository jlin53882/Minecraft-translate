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
