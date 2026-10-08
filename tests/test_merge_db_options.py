"""語系合併頁的資料庫補譯選項：預設取自設定，頁面動過之後以頁面為準，並傳給合併服務。"""

from __future__ import annotations

import json

import pytest

from app.services_impl.pipelines import merge_service
from app.tasks.task_session import TaskSession
from app.views import merge_view
from app.views.merge import merge_db_options, merge_widgets
from app.views.moddb import version_picker
from app.views.pipeline import pipeline_config
from app.views.pipeline.pipeline_actions import PipelineActions
from tests.conftest import mock_filepicker, mock_page
from translation_tool.core import lang_merge_db
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)


def _patch(monkeypatch, **kw):
    state = {
        "settings": DbSettings(**kw),
        "versions": ["1.21.1", "1.20.1"],
        "summary": {
            "entries": 20,
            "progress": 50,
            "versions": ["1.21.1", "1.20.1"],
        },
    }
    monkeypatch.setattr(merge_db_options, "load_db_settings", lambda: state["settings"])
    monkeypatch.setattr(
        merge_db_options, "summarize_database", lambda *_args: state["summary"]
    )
    monkeypatch.setattr(
        version_picker,
        "merge_target_version_choices",
        lambda *_args: list(state["versions"]),
    )
    return state


def test_defaults_come_from_config(monkeypatch):
    _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert opts.use_db is True and opts.version is None
    assert opts.version_field.value == version_picker.MERGE_INHERIT_VERSION
    assert "沿用全域設定（目前：1.21.1）" in [
        option.text for option in opts.version_field.options
    ]
    _patch(monkeypatch, merge_enabled=False, version="")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert opts.use_db is False and opts.version is None


def test_untouched_page_follows_config_changes(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    state["settings"] = DbSettings(merge_enabled=False, version="1.20.1")
    opts.sync_from_config()
    assert opts.use_db is False and opts.version is None
    assert opts.version_field.value == version_picker.MERGE_INHERIT_VERSION
    assert "沿用全域設定（目前：1.20.1）" in [
        option.text for option in opts.version_field.options
    ]


def test_page_choice_wins_after_user_changes_it(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    opts.switch.value = False
    opts._on_changed()  # 使用者在頁面上切換
    state["settings"] = DbSettings(merge_enabled=True, version="1.19.2")
    opts.sync_from_config()
    assert opts.use_db is False and opts.version is None


def test_target_version_can_select_only_existing_database_versions(monkeypatch):
    _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)

    assert opts.version_field.editable is False
    assert [option.key for option in opts.version_field.options] == [
        version_picker.MERGE_INHERIT_VERSION,
        "1.21.1",
        "1.20.1",
    ]

    opts.version_field.value = "1.20.1"
    opts.version_field.on_select(type("Event", (), {"control": opts.version_field})())
    assert opts.version == "1.20.1"


def test_focusing_target_version_suggests_creating_missing_database(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="")
    state["summary"] = None
    state["versions"] = []
    prompts = []
    opts = merge_db_options.MergeDbOptions(lambda: None, lambda: prompts.append(True))

    opts.version_field.on_focus(type("Event", (), {"control": opts.version_field})())

    assert len(prompts) == 1


def test_info_warns_when_database_missing_or_no_version(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="")
    state["summary"] = None
    state["versions"] = []
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert "尚未建立資料庫" in opts.info.value
    assert "本次略過資料庫補譯" in opts.info.value


def test_inherit_shows_global_version_and_unset_global_skips(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert "本次實際使用：1.21.1（沿用全域設定）" in opts.info.value
    assert opts.snapshot_for_run().version == "1.21.1"

    state["settings"] = DbSettings(merge_enabled=True, version="")
    opts.sync_from_config()
    assert opts.version_field.value == version_picker.MERGE_INHERIT_VERSION
    assert "沿用全域設定（目前：未指定）" in [
        option.text for option in opts.version_field.options
    ]
    assert "全域未指定版本，本次略過資料庫補譯" in opts.info.value
    assert opts.snapshot_for_run().version == ""


def test_invalid_global_version_is_reported_and_never_falls_back(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.19.4")
    opts = merge_db_options.MergeDbOptions(lambda: None)

    assert "全域設定版本 1.19.4 不存在於目前資料庫" in opts.info.value
    snapshot = opts.snapshot_for_run()
    assert snapshot.use_db is True
    assert snapshot.version == ""
    assert "1.19.4" in snapshot.warning
    assert state["versions"] == ["1.21.1", "1.20.1"]


def test_page_override_wins_and_survives_global_settings_refresh(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    opts.version_field.value = "1.20.1"
    opts.version_field.on_select(type("Event", (), {"control": opts.version_field})())
    state["settings"] = DbSettings(merge_enabled=True, version="1.19.4")

    opts.sync_from_config()

    assert opts.use_db is True
    assert opts.version == "1.20.1"
    assert "沿用全域設定（目前：1.19.4）" in [
        option.text for option in opts.version_field.options
    ]
    assert "本次實際使用：1.20.1（頁面指定）" in opts.info.value
    assert opts.snapshot_for_run().version == "1.20.1"


def test_switch_only_touch_does_not_freeze_global_version_hint(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    opts.switch.value = False
    opts._on_changed()
    state["settings"] = DbSettings(merge_enabled=True, version="1.20.1")

    opts.sync_from_config()

    assert opts.use_db is False
    assert "沿用全域設定（目前：1.20.1）" in [
        option.text for option in opts.version_field.options
    ]


def test_returning_to_inherit_refreshes_global_hint(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    opts.version_field.value = "1.20.1"
    opts.version_field.on_select(type("Event", (), {"control": opts.version_field})())
    opts.version_field.value = version_picker.MERGE_INHERIT_VERSION
    opts.version_field.on_select(type("Event", (), {"control": opts.version_field})())
    state["settings"] = DbSettings(merge_enabled=True, version="1.20.1")

    opts.sync_from_config()

    assert opts.version is None
    assert opts.version_field.value == version_picker.MERGE_INHERIT_VERSION
    assert "本次實際使用：1.20.1（沿用全域設定）" in opts.info.value


def test_start_snapshot_uses_latest_global_setting_without_manual_page_sync(
    monkeypatch,
):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    state["settings"] = DbSettings(merge_enabled=True, version="1.20.1")

    snapshot = opts.snapshot_for_run()

    assert snapshot.version == "1.20.1"
    assert "沿用全域設定（目前：1.20.1）" in [
        option.text for option in opts.version_field.options
    ]
    assert "本次實際使用：1.20.1（沿用全域設定）" in opts.info.value


def test_stale_page_selection_is_not_reintroduced_or_silently_inherited(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    opts.version_field.value = "1.20.1"
    opts.version_field.on_select(type("Event", (), {"control": opts.version_field})())
    state["versions"] = ["1.21.1"]
    state["summary"] = {"entries": 10, "progress": 50, "versions": ["1.21.1"]}

    opts.sync_from_config()

    assert [option.key for option in opts.version_field.options] == [
        version_picker.MERGE_INHERIT_VERSION,
        "1.21.1",
    ]
    assert opts.version == "1.20.1"
    assert "頁面指定版本 1.20.1 不存在於目前資料庫" in opts.info.value
    assert opts.version_warning.visible is True
    assert "頁面原指定版本 1.20.1 已不存在" in opts.version_warning.value
    snapshot = opts.snapshot_for_run()
    assert snapshot.version == ""
    assert "1.20.1" in snapshot.warning

    opts.version_field.value = "1.21.1"
    opts.version_field.on_select(type("Event", (), {"control": opts.version_field})())
    assert opts.version == "1.21.1"
    assert opts.version_warning.visible is False


@pytest.mark.parametrize("input_mode", ["folder", "zip"])
def test_service_passes_page_choice_to_both_stages(monkeypatch, tmp_path, input_mode):
    """Folder／ZIP 的 Stage 1 與 Stage 2 都使用同一固定版本。"""
    seen: dict = {}
    settings_snapshot = DbSettings(
        path=str(tmp_path / "database-a.db"),
        merge_enabled=True,
        version="1.21.1",
        cross_version=False,
        priority=(4, 2, 1),
    )

    def fake_folder(*args, **kwargs):
        seen["stage1"] = (
            kwargs.get("use_translation_db"),
            kwargs.get("translation_db_version"),
            kwargs.get("translation_db_settings_snapshot"),
        )
        yield {"progress": 1.0, "log": None}

    def fake_stage2(*args, **kwargs):
        seen["stage2"] = (
            kwargs.get("use_translation_db"),
            kwargs.get("translation_db_version"),
            kwargs.get("translation_db_settings_snapshot"),
        )
        yield {"progress": 1.0, "log": None}

    monkeypatch.setattr(merge_service, "merge_zhcn_to_zhtw_from_folder", fake_folder)
    monkeypatch.setattr(merge_service, "merge_zhcn_to_zhtw_from_zip", fake_folder)
    monkeypatch.setattr(merge_service, "merge_extracted_to_assets", fake_stage2)
    monkeypatch.setattr(
        merge_service,
        "load_config",
        lambda: {"lang_merger": {"enable_extracted_to_assets_merge": True}},
    )

    session = TaskSession()

    if input_mode == "folder":
        inp = tmp_path / "in"
        inp.mkdir()
        list(
            merge_service.run_merge_folder_batch_service(
                str(inp),
                str(tmp_path / "out"),
                session,
                only_process_lang=True,
                use_translation_db=True,
                translation_db_version="1.21.1",
                translation_db_settings_snapshot=settings_snapshot,
            )
        )
    else:
        list(
            merge_service.run_merge_zip_batch_service(
                ["input.zip"],
                str(tmp_path / "out"),
                session,
                only_process_lang=True,
                use_translation_db=True,
                translation_db_version="1.21.1",
                translation_db_settings_snapshot=settings_snapshot,
            )
        )
    assert seen["stage1"] == (True, "1.21.1", settings_snapshot)
    if input_mode == "folder":
        assert seen["stage2"] == (True, "1.21.1", settings_snapshot)
    else:
        # ZIP merge currently has no extracted-assets Stage 2 entry point.
        assert "stage2" not in seen


def test_one_click_uses_selected_version_for_database_supplement(monkeypatch, tmp_path):
    """完整走過一鍵步驟與合併服務，確認補譯採用頁面版本而非設定預設值。"""
    db_path = tmp_path / "mod.db"
    db = TranslationDB(db_path)
    for version, translation in (
        ("1.21.1", "設定預設版本"),
        ("1.20.1", "頁面選取版本"),
    ):
        db.ingest(
            version,
            [ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", translation)],
        )
    db.close()

    monkeypatch.setattr(
        lang_merge_db,
        "load_db_settings",
        lambda: DbSettings(path=str(db_path), version="1.21.1", merge_enabled=True),
    )
    monkeypatch.setattr(
        lang_merge_db, "value_fully_translated", lambda value: bool(value)
    )
    monkeypatch.setattr(pipeline_config, "load_config", dict)
    monkeypatch.setattr(
        merge_service,
        "load_config",
        lambda: {"lang_merger": {"enable_extracted_to_assets_merge": False}},
    )

    input_dir, output_dir = tmp_path / "mods", tmp_path / "output"
    input_dir.mkdir()
    cfg = pipeline_config.PipelineConfig(str(input_dir), str(output_dir))
    lang_dir = (
        tmp_path
        / "output"
        / "jar_mod_extract"
        / "_提取lang_輸出"
        / "assets"
        / "foo"
        / "lang"
    )
    lang_dir.mkdir(parents=True)
    (lang_dir / "en_us.json").write_text(
        json.dumps({"item.foo.a": "Steel Casing"}), encoding="utf-8"
    )

    steps = PipelineActions().one_click_steps(
        {"only_lang": True},
        cfg,
        "lang",
        ["zh_tw"],
        {
            "output_dir": cfg.merge_output_dir,
            "process_zh_cn": False,
            "patchouli_skip": True,
            "patchouli_threshold": 0.5,
            "zh_en_threshold": 2,
            "use_translation_db": True,
            "translation_db_version": "1.20.1",
        },
    )

    list(steps[1][2](TaskSession()))

    result = (
        tmp_path
        / "output"
        / "locale_sort"
        / "_整理輸出"
        / "lang_output"
        / "assets"
        / "foo"
        / "lang"
        / "zh_tw.json"
    )
    assert json.loads(result.read_text(encoding="utf-8")) == {
        "item.foo.a": "頁面選取版本"
    }


@pytest.mark.parametrize("input_mode", ["folder", "zip"])
def test_merge_start_freezes_db_version_for_worker_and_both_stages(
    monkeypatch, tmp_path, input_mode
):
    """開始後更改頁面／全域版本，不得影響已提交的 Folder 或 ZIP 任務。"""
    database_a = tmp_path / "database-a.db"
    state = _patch(
        monkeypatch,
        path=str(database_a),
        merge_enabled=True,
        version="1.21.1",
        cross_version=False,
        priority=(4, 2, 1),
    )
    monkeypatch.setattr(merge_view, "TaskSession", TaskSession)
    monkeypatch.setattr(merge_widgets, "TaskSession", TaskSession)
    monkeypatch.setattr(merge_view, "load_config", lambda: {"lang_merger": {}})
    monkeypatch.setattr(merge_widgets, "load_config", lambda: {"lang_merger": {}})
    view = merge_view.MergeView(mock_page(), mock_filepicker())
    # 選擇一個有效的明確覆寫，作為這次工作的 immutable snapshot。
    view.db_options.version_field.value = "1.20.1"
    view.db_options.version_field.on_select(
        type("Event", (), {"control": view.db_options.version_field})()
    )

    captured = {}

    class _Operation:
        admitted = True

        def launch(self, worker):
            captured["worker"] = worker

    monkeypatch.setattr(
        merge_view, "reserve_page_operation", lambda *a, **k: _Operation()
    )
    monkeypatch.setattr(view, "_start_ui_poller", lambda: None)
    monkeypatch.setattr(view.log_view, "clear", lambda: None)

    def fake_folder(**kwargs):
        captured["folder"] = kwargs
        return iter(())

    def fake_zip(**kwargs):
        captured["zip"] = kwargs
        return iter(())

    monkeypatch.setattr(merge_view, "run_merge_folder_batch_service", fake_folder)
    monkeypatch.setattr(merge_view, "run_merge_zip_batch_service", fake_zip)

    if input_mode == "folder":
        source = tmp_path / "source"
        source.mkdir()
        view.folder_path_field.value = str(source)
    else:
        archive = tmp_path / "source.zip"
        archive.write_bytes(b"zip")
        view.input_mode_group.value = "zip"
        view.selected_zips = [str(archive)]
    output = tmp_path / "output"
    view.output_dir_field.value = str(output)

    view.start_merge(None)

    # 工作者尚未開始時 UI 和全域設定都發生變動。
    state["settings"] = DbSettings(
        path=str(tmp_path / "database-b.db"),
        merge_enabled=True,
        version="1.21.1",
        cross_version=True,
        priority=(1, 2, 4),
    )
    view.db_options.version_field.value = "1.21.1"
    view.db_options.version_field.on_select(
        type("Event", (), {"control": view.db_options.version_field})()
    )
    captured["worker"]()

    service_kwargs = captured[input_mode]
    assert service_kwargs["use_translation_db"] is True
    assert service_kwargs["translation_db_version"] == "1.20.1"
    snapshot = service_kwargs["translation_db_settings_snapshot"]
    assert snapshot.path == str(database_a.resolve())
    assert snapshot.version == "1.20.1"
    assert snapshot.cross_version is False
    assert snapshot.priority == (4, 2, 1)
