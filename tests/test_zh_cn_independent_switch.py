"""Markdown 與 KubeJS／FTB 流水線能獨立控制是否使用 zh_cn（issue #247）。

涵蓋：服務層傳遞、設定預設值、UI 開關與流水線精靈。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services_impl import md_pipeline_service as md_service
from app.services_impl import pack_pipeline_service
from app.tasks.task_session import TaskSession
from app.views import kubejs_ftb_pipeline_view as kfv
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db.pack_pipeline import (
    PackPipelineOptions,
    _scan_selected_sources,
)

EN = "# Title\n\nFirst.\n\nSecond.\n"
CN = "# 标题\n\n第一段。\n\nSecond.\n"


# ---------------------------------------------------------------- 服務層與設定


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, True),
        ({"translator": {}}, True),
        ({"translator": {"md_use_zh_cn_base": False}}, False),
    ],
)
def test_md_default_comes_from_config(monkeypatch, cfg, expected):
    monkeypatch.setattr(md_service, "load_config", lambda: cfg)

    assert md_service.get_md_use_zh_cn_default() is expected


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, True),
        ({"translator": {"pack_read_zh_cn": False}}, False),
        ({"translator": {"pack_read_zh_cn": True}}, True),
    ],
)
def test_pack_default_comes_from_config(monkeypatch, cfg, expected):
    monkeypatch.setattr(pack_pipeline_service, "load_config", lambda: cfg)

    assert pack_pipeline_service.get_pack_read_zh_cn_default() is expected


def test_both_settings_are_registered_with_default_on():
    from translation_tool.utils.config_manager import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["translator"]["md_use_zh_cn_base"] is True
    assert DEFAULT_CONFIG["translator"]["pack_read_zh_cn"] is True
    example_path = Path(__file__).resolve().parents[1] / "config.example.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    assert example["translator"]["md_use_zh_cn_base"] is True
    assert example["translator"]["pack_read_zh_cn"] is True


# ---------------------------------------------------------------- KubeJS／FTB 流水線


def _write_kubejs_pack(root: Path) -> None:
    script = root / "kubejs" / "client_scripts" / "lang.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ClientEvents.lang('zh_cn', event => {\n"
        "  event.add('demo', 'item.demo.machine', '机器');\n"
        "});\n"
        "ClientEvents.lang('en_us', event => {\n"
        "  event.add('demo', 'item.demo.machine', 'Machine');\n"
        "});\n",
        encoding="utf-8",
    )


def test_pack_pipeline_scan_reads_zh_cn_by_default(tmp_path):
    _write_kubejs_pack(tmp_path)

    result = _scan_selected_sources(tmp_path, PackPipelineOptions(version="1.21.1"))

    assert any(it.zh_cn for it in result.items if it.kind == "kubejs_lang")


def test_pack_pipeline_scan_skips_zh_cn_when_switched_off(tmp_path):
    _write_kubejs_pack(tmp_path)

    result = _scan_selected_sources(
        tmp_path, PackPipelineOptions(version="1.21.1", read_zh_cn=False)
    )

    items = [it for it in result.items if it.kind == "kubejs_lang"]
    assert items and not any(it.zh_cn for it in items)
    assert all(it.source_locale == "en_us" for it in items)


def test_pack_service_passes_read_zh_cn_into_options(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_run(db, source, options, **_kwargs):
        seen["options"] = options
        return SimpleNamespace(as_dict=dict)

    monkeypatch.setattr(pack_pipeline_service, "run_pack_pipeline", fake_run)
    monkeypatch.setattr(pack_pipeline_service, "open_database", lambda **_k: None)
    session = TaskSession(max_logs=10)

    pack_pipeline_service.run_kubejs_ftb_pipeline_service(
        str(tmp_path), "1.21.1", session, dry_run=True, read_zh_cn=False
    )

    assert seen["options"].read_zh_cn is False


# ---------------------------------------------------------------- UI


def test_pipeline_view_checkbox_follows_config_and_wizard_shows_it(monkeypatch):
    monkeypatch.setattr(kfv, "get_pack_read_zh_cn_default", lambda: False)
    view = kfv.KubejsFtbPipelineView(mock_page(), mock_filepicker())

    assert view.read_zh_cn.value is False
    wizard = view._wizard
    wizard._step = 2
    column = wizard._content_translate()
    assert view.read_zh_cn in column.controls

    view.read_zh_cn.value = True
    summary = wizard._content_summary()
    texts = [c.value for c in summary.controls if hasattr(c, "value")]
    assert "讀取 zh_cn：開啟" in texts
    view.read_zh_cn.value = False
    texts = [c.value for c in wizard._content_summary().controls if hasattr(c, "value")]
    assert "讀取 zh_cn：關閉" in texts


def test_settings_page_lists_both_switches():
    from app.views.config.settings_schema import LAYOUT

    text = repr(LAYOUT)
    assert "translator.md_use_zh_cn_base" in text
    assert "translator.pack_read_zh_cn" in text


def test_pipeline_view_start_forwards_read_zh_cn_to_the_service(monkeypatch, tmp_path):
    import asyncio

    seen: dict = {}
    monkeypatch.setattr(kfv, "get_pack_read_zh_cn_default", lambda: True)
    view = kfv.KubejsFtbPipelineView(mock_page(), mock_filepicker())
    view.read_zh_cn.value = False
    view.source_field.value = str(tmp_path)
    view.lm_db_options.version_field.value = "1.21.1"
    view._candidates = [str(tmp_path)]
    monkeypatch.setattr(view, "_run_blocker", lambda publish: None)

    def fake_launch(_page, fn, **_kwargs):
        monkeypatch.setattr(
            kfv,
            "run_kubejs_ftb_pipeline_service",
            lambda *a, **k: seen.update(k),
        )
        fn()
        return True

    monkeypatch.setattr(kfv, "launch_page_operation", fake_launch)

    asyncio.run(view._start(dry_run=True, publish=False))

    assert seen["read_zh_cn"] is False
