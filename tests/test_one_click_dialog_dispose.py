"""一鍵對話框關閉：先 open=False 推到前端、移除 overlay 後再推一次，提示才不會被殘留遮罩蓋住。"""

from types import SimpleNamespace

from app.ui.dialogs import close_page_dialog, dispose_dialogs, present_dialog


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

    dispose_dialogs(ctx)

    assert page.events == [("update", False, True), ("update", False, False)]
    assert page.overlay == [] and ctx.dialogs == []


def test_dispose_without_dialogs_does_not_extra_update():
    page = _Page(SimpleNamespace(open=False))
    page.overlay.clear()
    ctx = SimpleNamespace(page=page, dialogs=[])

    dispose_dialogs(ctx)

    assert len(page.events) == 0


class _DialogApiPage(_Page):
    """有 show_dialog / pop_dialog 的頁面（Flet 0.85+ Web）。"""

    def __init__(self):
        self.overlay = []
        self.events = []
        self.shown = []
        self.popped = 0
        self._dialogs = SimpleNamespace(
            controls=[], update=lambda: self.events.append("stack-update")
        )

    def update(self):
        self.events.append("update")

    def show_dialog(self, dialog):
        dialog.open = True
        self.shown.append(dialog)
        self._dialogs.controls.append(dialog)

    def pop_dialog(self):
        self.popped += 1
        dialog = next(
            (item for item in reversed(self._dialogs.controls) if item.open), None
        )
        if dialog is not None:
            dialog.open = False
            return dialog
        return None


def test_present_uses_show_dialog_when_page_supports_it():
    dlg = SimpleNamespace(open=False)
    page = _DialogApiPage()
    ctx = SimpleNamespace(page=page, dialogs=[], uses_dialog_api=True)

    present_dialog(ctx, dlg)

    assert page.shown == [dlg] and page.overlay == [] and ctx.dialogs == [dlg]


def test_present_falls_back_to_overlay_without_dialog_api():
    dlg = SimpleNamespace(open=False)
    page = _Page(dlg)
    page.overlay.clear()
    ctx = SimpleNamespace(page=page, dialogs=[], uses_dialog_api=False)

    present_dialog(ctx, dlg)

    assert page.overlay == [dlg] and dlg.open is True


def test_dispose_closes_owned_native_dialog_without_pop_dialog():
    dlg = SimpleNamespace(open=True, update=lambda: None)
    page = _DialogApiPage()
    ctx = SimpleNamespace(page=page, dialogs=[dlg], uses_dialog_api=True)

    dispose_dialogs(ctx)

    assert dlg.open is False and page.popped == 0 and ctx.dialogs == []
    assert page.events == []


def test_close_targets_owned_dialog_without_popping_unrelated_topmost():
    owned = SimpleNamespace(open=True, update=lambda: None)
    unrelated = SimpleNamespace(open=True)
    page = _DialogApiPage()
    page._dialogs.controls.append(owned)
    page.show_dialog(unrelated)

    assert close_page_dialog(page, owned)

    assert owned.open is False
    assert unrelated.open is True
    assert page.popped == 0
    # Keep the entry mounted until Flet reports the post-animation dismiss.
    assert page._dialogs.controls == [owned, unrelated]


def test_close_pops_owned_native_dialog_only_when_it_is_topmost():
    owned = SimpleNamespace(open=True, update=lambda: None)
    page = _DialogApiPage()
    page.show_dialog(owned)

    assert close_page_dialog(page, owned)

    assert owned.open is False
    assert page.popped == 1


def test_close_is_idempotent_for_already_closed_dialog():
    dlg = SimpleNamespace(open=False)
    page = _DialogApiPage()

    assert not close_page_dialog(page, dlg)
    assert page.popped == 0 and page.events == []
