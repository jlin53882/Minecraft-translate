"""重複鍵清理視窗的資料庫身分與非同步預覽契約。"""

from __future__ import annotations

from pathlib import Path

from app.views.moddb import duplicate_cleanup_dialog as module
from tests.conftest import mock_page
from translation_tool.translation_db import TranslationDB

VERSION = "1.21.1"
ALIASES = (
    "lang/bestiary/_en_us/alchemy_0.txt#0",
    "lang/bestiary/alchemy_0.txt#0",
)
ENGLISH = "After harvesting dragon blood by using a glass."


def _db(path: Path) -> TranslationDB:
    db = TranslationDB(path)
    with db._tx() as conn:
        for key in ALIASES:
            conn.execute(
                "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
                "VALUES ('guide', ?, 'bestiary', ?, ?)",
                (VERSION, key, ENGLISH),
            )
    return db


def _dialog(monkeypatch, current_db):
    workers = []
    shown = []
    monkeypatch.setattr(
        module,
        "launch_page_operation",
        lambda _page, target, **_kwargs: (workers.append(target), True)[1],
    )
    monkeypatch.setattr(
        module,
        "show_snack",
        lambda _page, message, *_args, **_kwargs: shown.append(message),
    )
    dialog = module.DuplicateCleanupDialog(
        mock_page(), lambda: current_db["value"], VERSION, None
    )
    monkeypatch.setattr(dialog, "_post", lambda callback, *args: callback(*args))
    return dialog, workers, shown


def test_preview_discards_result_when_database_changes_while_worker_runs(
    tmp_path, monkeypatch
):
    first = _db(tmp_path / "first.db")
    second = _db(tmp_path / "second.db")
    current_db = {"value": first}
    try:
        dialog, workers, shown = _dialog(monkeypatch, current_db)

        dialog._preview()
        assert len(workers) == 1
        current_db["value"] = second
        workers[0]()

        assert dialog._plan is None
        assert dialog.apply_btn.disabled is True
        assert shown == ["資料庫已變動，請重新預覽。"]
    finally:
        first.close()
        second.close()


def test_open_starts_read_only_preview_and_renders_candidates(tmp_path, monkeypatch):
    db = _db(tmp_path / "open.db")
    try:
        dialog, workers, _shown = _dialog(monkeypatch, {"value": db})
        monkeypatch.setattr(module, "isolate", lambda _dialog: None)
        monkeypatch.setattr(module, "present_page_dialog", lambda _page, _dialog: None)

        dialog.open()

        assert len(workers) == 1
        assert dialog._busy is True
        workers[0]()

        assert dialog._busy is False
        assert dialog._plan is not None
        assert dialog.summary.value.startswith("共找到 1 組候選")
        assert dialog.details.controls
        assert dialog.apply_btn.disabled is False
    finally:
        db.close()


def test_apply_rejects_a_completed_plan_after_database_switch(tmp_path, monkeypatch):
    first = _db(tmp_path / "first.db")
    second = _db(tmp_path / "second.db")
    current_db = {"value": first}
    try:
        dialog, workers, shown = _dialog(monkeypatch, current_db)

        dialog._preview()
        workers[0]()
        assert dialog._plan is not None

        current_db["value"] = second
        before = second._one("SELECT COUNT(*) FROM entry")[0]
        dialog._apply()

        assert second._one("SELECT COUNT(*) FROM entry")[0] == before
        assert dialog._plan is None
        assert dialog.apply_btn.disabled is True
        assert shown == ["資料庫已變動，請重新預覽。"]
    finally:
        first.close()
        second.close()
