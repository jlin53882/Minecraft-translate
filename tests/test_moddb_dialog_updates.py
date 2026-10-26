"""Mod 資料庫對話框的更新範圍契約：只重畫自己的視窗，不走訪整個頁面。

對話框內的進度、日誌與預覽會頻繁更新；整頁 ``page.update()`` 要走訪整棵控制樹
（外殼、側欄、Mod 資料庫頁…），單次就要數十到上百毫秒。每個視窗只更新自己的 ``AlertDialog``。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb import (
    bulk_review_dialog,
    cleanup_dialog,
    repair_reviewer,
    smart_review_context,
    species_dialog,
)


class _NoWholePage:
    """任何整頁更新都視為失敗。"""

    def update(self, *_controls) -> None:
        raise AssertionError("對話框不應使用整頁 page.update()")


@pytest.mark.parametrize(
    ("module", "cls", "method"),
    [
        (bulk_review_dialog, bulk_review_dialog.BulkReviewDialog, "_safe_update"),
        (cleanup_dialog, cleanup_dialog.CleanupDialog, "_safe_update"),
    ],
)
def test_simple_dialogs_update_only_their_alert_dialog(
    monkeypatch, module, cls, method
):
    calls: list[tuple] = []
    monkeypatch.setattr(module, "update_regions", lambda *c: calls.append(c))
    dialog = object()
    fake = SimpleNamespace(page=_NoWholePage(), dialog=dialog)

    getattr(cls, method)(fake)

    assert calls == [(dialog,)]


def test_species_dialog_flush_updates_only_its_alert_dialog(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(species_dialog, "update_regions", lambda *c: calls.append(c))
    dialog = object()
    fake = SimpleNamespace(page=_NoWholePage(), dialog=dialog, _last_flush=0.0)

    species_dialog.SpeciesCandidateDialog._flush(fake, force=True)

    assert calls == [(dialog,)]


def test_smart_review_context_updates_the_dialog_when_it_is_known(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        smart_review_context, "update_regions", lambda *c: calls.append(c)
    )
    ctx = smart_review_context.SmartReviewContext(_NoWholePage(), lambda: None, None)
    dialog = object()
    ctx.dialog = dialog

    ctx.update()

    assert calls == [(dialog,)]


def test_smart_review_context_without_dialog_falls_back_to_the_page():
    updated: list[int] = []
    page = SimpleNamespace(update=lambda: updated.append(1))
    ctx = smart_review_context.SmartReviewContext(page, lambda: None, None)

    ctx.update()

    assert updated == [1]


def test_repair_reviewer_updates_both_of_its_dialogs(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(repair_reviewer, "update_regions", lambda *c: calls.append(c))
    main, confirm = object(), object()
    fake = SimpleNamespace(
        page=_NoWholePage(), dialog=main, apply_confirmation_dialog=confirm
    )

    repair_reviewer.RepairReviewer._update_dialogs(fake)

    assert calls == [(main, confirm)]
