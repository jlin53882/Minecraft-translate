"""Pipeline dialogs close through their owned control without popping foreign modals."""

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
    """Page surface exposing the public dialog API, without modeling private state."""

    def __init__(self):
        self.overlay = []
        self.events = []
        self.shown = []
        self.popped = 0

    def update(self):
        self.events.append("update")

    def show_dialog(self, dialog):
        dialog.open = True
        self.shown.append(dialog)

    def pop_dialog(self):
        self.popped += 1


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


def test_dispose_closes_owned_native_dialog_by_targeted_update():
    dlg = SimpleNamespace(open=True, updates=0)
    dlg.update = lambda: setattr(dlg, "updates", dlg.updates + 1)
    page = _DialogApiPage()
    ctx = SimpleNamespace(page=page, dialogs=[dlg], uses_dialog_api=True)

    dispose_dialogs(ctx)

    assert dlg.open is False and dlg.updates == 1
    assert page.popped == 0 and ctx.dialogs == []
    assert page.events == []


def test_close_targets_owned_dialog_without_popping_unrelated_modal():
    owned = SimpleNamespace(open=True, updates=0)
    owned.update = lambda: setattr(owned, "updates", owned.updates + 1)
    unrelated = SimpleNamespace(open=True)
    page = _DialogApiPage()
    page.show_dialog(unrelated)

    assert close_page_dialog(page, owned)

    assert owned.open is False
    assert unrelated.open is True
    assert page.popped == 0
    assert owned.updates == 1


def test_close_native_dialog_never_uses_stack_pop():
    owned = SimpleNamespace(open=True, updates=0)
    owned.update = lambda: setattr(owned, "updates", owned.updates + 1)
    page = _DialogApiPage()
    page.show_dialog(owned)

    assert close_page_dialog(page, owned)

    assert owned.open is False
    assert owned.updates == 1
    assert page.popped == 0


def test_close_is_idempotent_for_already_closed_dialog():
    dlg = SimpleNamespace(open=False)
    page = _DialogApiPage()

    assert not close_page_dialog(page, dlg)
    assert page.popped == 0 and page.events == []
