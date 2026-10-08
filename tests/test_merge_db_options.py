"""語系合併頁的資料庫補譯選項：預設取自設定，頁面動過之後以頁面為準，並傳給合併服務。"""

from __future__ import annotations

import json

from app.services_impl.pipelines import merge_service
from app.tasks.task_session import TaskSession
from app.views.merge import merge_db_options
from app.views.moddb import version_picker
from app.views.pipeline import pipeline_config
from app.views.pipeline.pipeline_actions import PipelineActions
from translation_tool.core import lang_merge_db
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)


def _patch(monkeypatch, **kw):
    state = {"settings": DbSettings(**kw)}
    monkeypatch.setattr(merge_db_options, "load_db_settings", lambda: state["settings"])
    monkeypatch.setattr(merge_db_options, "summarize_database", lambda: None)
    monkeypatch.setattr(
        version_picker, "target_version_choices", lambda: ["1.21.1", "1.20.1"]
    )
    return state


def test_defaults_come_from_config(monkeypatch):
    _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert opts.use_db is True and opts.version == "1.21.1"
    _patch(monkeypatch, merge_enabled=False, version="")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert opts.use_db is False and opts.version is None


def test_untouched_page_follows_config_changes(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    state["settings"] = DbSettings(merge_enabled=False, version="1.20.1")
    opts.sync_from_config()
    assert opts.use_db is False and opts.version == "1.20.1"


def test_page_choice_wins_after_user_changes_it(monkeypatch):
    state = _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    opts.switch.value = False
    opts._on_changed()  # 使用者在頁面上切換
    state["settings"] = DbSettings(merge_enabled=True, version="1.19.2")
    opts.sync_from_config()
    assert opts.use_db is False and opts.version == "1.21.1"


def test_target_version_can_be_selected_or_typed(monkeypatch):
    _patch(monkeypatch, merge_enabled=True, version="1.21.1")
    opts = merge_db_options.MergeDbOptions(lambda: None)

    assert opts.version_field.editable is True
    assert [option.key for option in opts.version_field.options] == ["1.21.1", "1.20.1"]
    opts.version_field.text = "26.2"
    opts.version_field.value = None
    assert opts.version == "26.2"

    opts.version_field.value = "1.20.1"
    assert opts.version == "1.20.1"


def test_focusing_target_version_suggests_creating_missing_database(monkeypatch):
    _patch(monkeypatch, merge_enabled=True, version="")
    prompts = []
    opts = merge_db_options.MergeDbOptions(lambda: None, lambda: prompts.append(True))

    opts.version_field.on_focus(type("Event", (), {"control": opts.version_field})())

    assert len(prompts) == 1


def test_info_warns_when_database_missing_or_no_version(monkeypatch):
    _patch(monkeypatch, merge_enabled=True, version="")
    opts = merge_db_options.MergeDbOptions(lambda: None)
    assert "尚未建立資料庫" in opts.info.value
    assert "尚未指定目標版本" in opts.info.value


def test_service_passes_page_choice_to_core(monkeypatch, tmp_path):
    """服務層把頁面的選擇原樣交給合併核心（None＝用設定）。"""
    seen: dict = {}

    def fake_folder(*args, **kwargs):
        seen["stage1"] = (
            kwargs.get("use_translation_db"),
            kwargs.get("translation_db_version"),
        )
        yield {"progress": 1.0, "log": None}

    def fake_stage2(*args, **kwargs):
        seen["stage2"] = (
            kwargs.get("use_translation_db"),
            kwargs.get("translation_db_version"),
        )
        yield {"progress": 1.0, "log": None}

    monkeypatch.setattr(merge_service, "merge_zhcn_to_zhtw_from_folder", fake_folder)
    monkeypatch.setattr(merge_service, "merge_extracted_to_assets", fake_stage2)

    session = TaskSession()

    inp = tmp_path / "in"
    inp.mkdir()
    list(
        merge_service.run_merge_folder_batch_service(
            str(inp),
            str(tmp_path / "out"),
            session,
            only_process_lang=True,
            use_translation_db=False,
            translation_db_version="1.21.1",
        )
    )
    assert seen["stage1"] == (False, "1.21.1")
    assert seen["stage2"] == (False, "1.21.1")


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
