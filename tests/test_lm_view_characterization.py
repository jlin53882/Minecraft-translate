import pytest

from app.tasks import LogEntry, operation_registry
from app.views import lm_view
from app.views.moddb import lm_db_options, version_picker
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db import DbSettings


class _Session:
    def __init__(self):
        self.started = 0
        self.logs = []

    def start(self):
        self.started += 1

    def add_log(self, text):
        self.logs.append(text)

    def snapshot(self):
        return {
            "status": "DONE",
            "progress": 1.0,
            "logs": [LogEntry(seq=0, level="info", text="done", source="test")],
        }


def test_lm_view_initializes_primary_controls(monkeypatch):
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    view = lm_view.LMView(mock_page(), mock_filepicker())

    assert view.start_button.content == "開始翻譯"
    assert view.status_chip.label.value == "尚未開始"


def test_start_clicked_without_input_sets_error_status(monkeypatch):
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    page = mock_page()
    view = lm_view.LMView(page, mock_filepicker())

    view.start_clicked(None)

    assert view.status_chip.label.value == "請先選擇輸入資料夾"


def test_start_clicked_launches_service_with_current_flags(monkeypatch):
    page = mock_page()
    calls = {}
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    monkeypatch.setattr(
        operation_registry.threading,
        "Thread",
        lambda target=None, args=(), daemon=None: type(
            "T", (), {"start": lambda self: target(*args)}
        )(),
    )
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)

    def fake_service(
        input_dir, output_dir, session, dry_run, export_lang, write_new_cache, **db
    ):
        calls.update(
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "session": session,
                "dry_run": dry_run,
                "export_lang": export_lang,
                "write_new_cache": write_new_cache,
                "db": db,
            }
        )

    monkeypatch.setattr(lm_view, "run_lm_translation_service", fake_service)

    view = lm_view.LMView(page, mock_filepicker())
    view.input_path.value = "C:/Assets"
    view.output_path.value = "C:/Out"
    view.dry_run_switch.value = True
    view.export_lang_checkbox.value = True
    view.write_new_cache_switch.value = True

    view.start_clicked(None)

    assert calls["input_dir"] == "C:/Assets"
    assert calls["output_dir"] == "C:/Out"
    assert calls["dry_run"] is True
    assert calls["export_lang"] is True
    assert calls["write_new_cache"] is True
    # Mod 資料庫選項（預設值取自設定；版本留空則傳 None 交給設定）
    assert set(calls["db"]) == {
        "use_translation_db",
        "translation_db_version",
        "translation_db_settings_snapshot",
    }
    assert calls["db"]["translation_db_settings_snapshot"].path


def test_db_options_follow_the_page_controls(monkeypatch, tmp_path):
    """機器翻譯頁上的「使用 Mod 資料庫」與目標版本會原樣傳給 service。"""
    calls = {}
    settings = DbSettings(
        enabled=True,
        path=str(tmp_path / "mod_translation.db"),
        version="1.21.1",
    )
    monkeypatch.setattr(
        lm_db_options.moddb_service, "load_db_settings", lambda: settings
    )
    monkeypatch.setattr(
        lm_db_options, "database_version_choices", lambda current: [current.version]
    )
    monkeypatch.setattr(
        lm_db_options,
        "summarize_database",
        lambda current: {"entries": 1, "progress": 0, "versions": [current.version]},
    )
    monkeypatch.setattr(lm_view, "TaskSession", _Session)

    def launch_immediately(_page, target, **_options):
        target()
        return True

    monkeypatch.setattr(lm_view, "launch_page_operation", launch_immediately)
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)
    monkeypatch.setattr(
        lm_view,
        "run_lm_translation_service",
        lambda *a, **db: calls.update(db=db),
    )
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.input_path.value = "C:/Assets"
    view.use_db_switch.value = False
    view.use_db_switch.on_change(None)
    view.lm_db_options.inherit_version_switch.value = False
    view.lm_db_options.inherit_version_switch.on_change(None)
    view.db_version_field.text = " 26.2 "
    view.db_version_field.value = None
    view.lm_db_options._on_version_changed(
        type("Event", (), {"control": view.db_version_field})()
    )
    view.start_clicked(None)
    assert calls["db"] == {
        "use_translation_db": False,
        "translation_db_version": "26.2",
        "translation_db_settings_snapshot": calls["db"][
            "translation_db_settings_snapshot"
        ],
    }
    assert calls["db"]["translation_db_settings_snapshot"].version == "26.2"
    assert calls["db"]["translation_db_settings_snapshot"].enabled is False

    # Use a fresh view for the enabled run: this test checks option snapshot
    # forwarding, not whether a completed view/session can be started twice.
    enabled_view = lm_view.LMView(mock_page(), mock_filepicker())
    enabled_view.input_path.value = "C:/Assets"
    enabled_view.use_db_switch.value = True
    enabled_view.use_db_switch.on_change(None)
    enabled_view.lm_db_options.inherit_version_switch.value = True
    enabled_view.lm_db_options.inherit_version_switch.on_change(None)
    snapshot = enabled_view._db_snapshot_for_run()
    assert snapshot.use_db is True
    assert snapshot.version == "1.21.1"
    enabled_view.start_clicked(None)
    assert calls["db"]["use_translation_db"] is True
    assert calls["db"]["translation_db_version"]
    assert (
        calls["db"]["translation_db_settings_snapshot"].version
        == calls["db"]["translation_db_version"]
    )


def test_lm_snapshot_preserves_switch_value_before_change_callback(
    monkeypatch, tmp_path
):
    """提交邊界需保留已更新的開關值，即使 on_change 尚未送達。"""
    settings = DbSettings(
        enabled=False,
        path=str(tmp_path / "mod_translation.db"),
        version="1.21.1",
    )
    monkeypatch.setattr(
        lm_db_options, "database_version_choices", lambda current: [current.version]
    )
    monkeypatch.setattr(
        lm_db_options,
        "summarize_database",
        lambda current: {"entries": 1, "progress": 0, "versions": [current.version]},
    )
    options = lm_db_options.LmDbOptions(mock_page().update, settings=settings)

    # Flet may have applied the control value before dispatching its change event.
    options.use_db_switch.value = True
    snapshot = options.snapshot_for_run(settings)

    assert snapshot.use_db is True
    assert snapshot.database_settings.enabled is True


def test_lm_run_snapshot_refreshes_untouched_global_identity(monkeypatch, tmp_path):
    settings_a = DbSettings(path=str(tmp_path / "a.db"), version="1.20.1")
    settings_b = DbSettings(path=str(tmp_path / "b.db"), version="1.21.1")
    monkeypatch.setattr(
        lm_db_options, "database_version_choices", lambda s: [s.version]
    )
    monkeypatch.setattr(
        lm_db_options,
        "summarize_database",
        lambda s: {"entries": 3, "progress": 33, "versions": [s.version]},
    )
    monkeypatch.setattr(
        lm_db_options.moddb_service, "load_db_settings", lambda: settings_a
    )

    options = lm_db_options.LmDbOptions(mock_page().update)
    assert "1.20.1" in options.info.value

    snapshot = options.snapshot_for_run(settings_b)

    assert snapshot.version == "1.21.1"
    assert snapshot.source == "global"
    assert snapshot.database_settings.resolved_path() == settings_b.resolved_path()
    assert "1.21.1" in options.info.value
    assert (options.version_field.value or options.version_field.text) == "1.21.1"


def test_target_version_dropdown_suggests_creating_database_on_focus(monkeypatch):
    shown = []
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    monkeypatch.setattr(lm_db_options, "summarize_database", lambda *_a, **_k: None)
    monkeypatch.setattr(version_picker, "target_version_choices", lambda: ["1.21.1"])
    monkeypatch.setattr(
        lm_view, "show_snack", lambda page, message, *a: shown.append(message)
    )

    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.db_version_field.on_focus(
        type("Event", (), {"control": view.db_version_field})()
    )

    assert shown and "先到「Mod 資料庫」頁" in shown[0]


def test_start_clicked_leaves_session_start_and_finish_to_the_service(monkeypatch):
    """LMView 不可自己 start() session：service 才是單一 lifecycle owner（否則會重複 start、
    清掉剛寫入的日誌，且 service 不 finish 時會在 TaskManager 留下 phantom active）。"""
    from app.shell.task_manager import TaskManager
    from app.tasks.task_session import TaskSession

    manager = TaskManager()
    manager.attach()
    try:
        monkeypatch.setattr(
            operation_registry.threading,
            "Thread",
            lambda target=None, args=(), daemon=None: type(
                "T", (), {"start": lambda self: target(*args)}
            )(),
        )
        monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)
        seen = {}

        def fake_service(
            input_dir, output_dir, session, dry_run, export_lang, write_new_cache, **db
        ):
            seen["active_before_service_start"] = manager.active()
            session.start()  # 真正的 service 會自己 start()／finish()
            session.finish()

        monkeypatch.setattr(lm_view, "run_lm_translation_service", fake_service)
        view = lm_view.LMView(mock_page(), mock_filepicker())
        view.input_path.value = "C:/Assets"

        view.start_clicked(None)

        assert isinstance(view.session, TaskSession)
        assert seen["active_before_service_start"] == []  # view 沒有先 start
        assert manager.active() == []
        assert len(manager.recent()) == 1  # 只有一筆 terminal record
    finally:
        manager.detach()


def _rendered_logs(view):
    return " ".join(
        str(getattr(c, "spans", "")) + str(getattr(c, "value", ""))
        for c in view.log_view._list_view.controls
    )


def test_default_output_notice_survives_service_start_and_poller_tail_sync(
    monkeypatch,
):
    """未指定輸出的預設路徑提示屬於 session 日誌：service 的 start() 清空日誌後仍在，
    而且 poller 的第一次同步（tail mode 會重建控制項）之後、重複同步之後都還在。"""
    monkeypatch.setattr(
        operation_registry.threading,
        "Thread",
        lambda target=None, args=(), daemon=None: type(
            "T", (), {"start": lambda self: target(*args)}
        )(),
    )
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)

    def fake_service(
        input_dir, output_dir, session, dry_run, export_lang, write_new_cache, **db
    ):
        session.start()  # 真正的 service 會 start()，清空 session 日誌
        session.add_log("translating…")

    monkeypatch.setattr(lm_view, "run_lm_translation_service", fake_service)
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.input_path.value = "C:/Assets"
    view.output_path.value = ""

    view.start_clicked(None)
    view._sync_from_session()  # 模擬 poller 首次同步（tail mode 重建控制項）
    view._sync_from_session()  # 再同步一次

    assert "未指定輸出，將使用預設" in _rendered_logs(view)
    texts = [e.text for e in view.session.snapshot()["logs"]]
    assert any("未指定輸出" in t for t in texts)  # 是 session snapshot 的一部分
    assert texts.index(next(t for t in texts if "未指定輸出" in t)) < texts.index(
        "translating…"
    )


def test_explicit_output_has_no_default_notice(monkeypatch):
    monkeypatch.setattr(
        operation_registry.threading,
        "Thread",
        lambda target=None, args=(), daemon=None: type(
            "T", (), {"start": lambda self: None}
        )(),
    )
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.input_path.value = "C:/Assets"
    view.output_path.value = "C:/Out"
    view.start_clicked(None)
    assert not [e for e in view.session.snapshot()["logs"] if "未指定輸出" in e.text]


def _launch_spy(monkeypatch):
    calls = {}
    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    monkeypatch.setattr(
        operation_registry.threading,
        "Thread",
        lambda target=None, args=(), daemon=None: type(
            "T", (), {"start": lambda self: target(*args)}
        )(),
    )
    monkeypatch.setattr(lm_view.LMView, "start_ui_timer", lambda self: None)
    monkeypatch.setattr(
        lm_view,
        "run_lm_translation_service",
        lambda input_dir, output_dir, session, dry_run, export_lang, write_new_cache, **db: (
            calls.update(
                input_dir=input_dir,
                output_dir=output_dir,
                dry_run=dry_run,
                export_lang=export_lang,
                write_new_cache=write_new_cache,
                db=db,
            )
        ),
    )
    return calls


def test_resume_interrupted_restores_inputs_and_options_then_starts(monkeypatch):
    """#151：續跑帶入上次的輸入與選項（dry-run 一律關閉），之後與按下「開始翻譯」相同。"""
    calls = _launch_spy(monkeypatch)
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.dry_run_switch.value = True  # 使用者目前的設定不應影響續跑
    task = type(
        "Task",
        (),
        {
            "input_dir": "C:/mods/assets",
            "output_dir": "C:/out",
            "export_lang": True,
            "write_new_cache": False,
            "use_translation_db": False,  # 沒有記錄資料庫選項的舊 checkpoint
            "translation_db_version": "",
        },
    )()

    view.resume_interrupted(task)

    assert calls == {
        "input_dir": "C:/mods/assets",
        "output_dir": "C:/out",
        "dry_run": False,
        "export_lang": True,
        "write_new_cache": False,
        "db": {
            "use_translation_db": False,
            "translation_db_version": "",
            "translation_db_settings_snapshot": calls["db"][
                "translation_db_settings_snapshot"
            ],
        },
    }
    assert view.input_path.value == "C:/mods/assets"
    assert view.output_path.value == "C:/out"


def test_resume_interrupted_does_not_start_a_second_run(monkeypatch):
    calls = _launch_spy(monkeypatch)
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view._ui_timer_running = True  # 已有任務在跑
    task = type(
        "Task",
        (),
        {
            "input_dir": "C:/assets",
            "output_dir": "",
            "export_lang": False,
            "write_new_cache": True,
            "use_translation_db": False,
            "translation_db_version": "",
        },
    )()

    view.resume_interrupted(task)

    assert calls == {}


def _task(use_db, version):
    effective_db = use_db and bool(version)
    return type(
        "Task",
        (),
        {
            "input_dir": "C:/assets",
            "output_dir": "C:/out",
            "export_lang": False,
            "write_new_cache": True,
            "use_translation_db": effective_db,
            "translation_db_version": version,
            "translation_db_settings": {
                "enabled": effective_db,
                "merge_enabled": True,
                "path": "C:/saved/mod.db",
                "version": version if effective_db else "",
                "cross_version": True,
                "write_back": True,
                "sync_manual": True,
                "priority": [5, 1, 4, 2, 0, 3],
                "priority_lines": [],
                "zip_source": 5,
            },
        },
    )()


@pytest.mark.parametrize(
    ("current_on", "current_version", "saved_on", "saved_version", "expected"),
    [
        # 原任務 DB=True、1.21.1；現在頁面是別的版本 → 仍用 1.21.1
        (True, "1.20.1", True, "1.21.1", (True, "1.21.1")),
        # 原任務 DB=False；現在頁面是開的 → 仍然關閉
        (True, "1.20.1", False, "", (False, None)),
        # 原任務沒有指定版本（其實沒用資料庫）→ 不能退回目前設定的版本
        (True, "1.20.1", True, "", (False, None)),
        # 原任務版本覆寫了全域設定 → 恢復覆寫值，而不是頁面目前的值
        (False, "9.9.9", True, "1.19.2", (True, "1.19.2")),
    ],
)
def test_resume_interrupted_restores_the_original_database_choice(
    monkeypatch, current_on, current_version, saved_on, saved_version, expected
):
    calls = _launch_spy(monkeypatch)
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.use_db_switch.value = current_on
    view.use_db_switch.on_change(None)
    view.db_version_field.value = current_version

    view.resume_interrupted(_task(saved_on, saved_version))

    assert calls["db"] == {
        "use_translation_db": expected[0],
        "translation_db_version": expected[1] or "",
        "translation_db_settings_snapshot": calls["db"][
            "translation_db_settings_snapshot"
        ],
    }
    assert calls["db"]["translation_db_settings_snapshot"].path == "C:/saved/mod.db"


def test_start_clicked_logs_the_paths_the_backend_received(monkeypatch, caplog):
    """畫面有值而後端是空的（Web 同步問題）時，後台 log 要能看出來。"""
    import logging

    monkeypatch.setattr(lm_view, "TaskSession", _Session)
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.input_path.value = ""
    view.output_path.value = "C:/out"

    with caplog.at_level(logging.INFO):
        view.start_clicked(None)

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "[LM翻譯] 開始按鈕" in m and "input=''" in m and "C:/out" in m for m in messages
    )
