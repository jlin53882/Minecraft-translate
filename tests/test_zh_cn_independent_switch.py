"""Markdown 與 KubeJS／FTB 流水線能獨立控制是否使用 zh_cn（issue #247）。

涵蓋：核心流程參數、服務層傳遞、設定預設值、UI 開關與流水線精靈。
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
from translation_tool.core.md_translation_assembly import run_md_pipeline
from translation_tool.plugins.md import md_lmtranslator
from translation_tool.translation_db.pack_pipeline import (
    PackPipelineOptions,
    _scan_selected_sources,
)

EN = "# Title\n\nFirst.\n\nSecond.\n"
CN = "# 标题\n\n第一段。\n\nSecond.\n"


@pytest.fixture
def fake_lm(monkeypatch):
    """LM 換成固定回覆，記錄實際送出的文字。"""
    sent: list[str] = []

    def fake_skeleton(items, *, hooks, **_kwargs):
        for it in items:
            sent.append(it["text"])
            hooks.on_translated_item({**it, "text": "譯:" + it["text"]})
        return SimpleNamespace(
            status="DONE", elapsed_sec=0.0, completed_calls=1, processed=len(items)
        )

    monkeypatch.setattr(md_lmtranslator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(
        md_lmtranslator,
        "prepare_translator_items",
        lambda items, **_: ([], list(items)),
    )
    monkeypatch.setattr(md_lmtranslator, "run_translator_skeleton", fake_skeleton)
    return sent


def _md_layout(tmp_path: Path):
    src, out = tmp_path / "in", tmp_path / "out"
    for rel, text in (("pack/en_us/guide.md", EN), ("pack/zh_cn/guide.md", CN)):
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src, out


def _final(out: Path) -> str:
    return (out / "md" / "完成" / "pack" / "zh_tw" / "guide.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------- Markdown 核心


def test_markdown_uses_zh_cn_base_by_default(tmp_path, fake_lm):
    src, out = _md_layout(tmp_path)

    run_md_pipeline(str(src), output_dir=str(out), lang_mode="non_cjk_only")

    assert fake_lm == ["Second."]  # 只送殘留英文
    assert "第一段。" in _final(out)


def test_markdown_without_zh_cn_translates_everything_from_english(tmp_path, fake_lm):
    src, out = _md_layout(tmp_path)

    result = run_md_pipeline(
        str(src), output_dir=str(out), lang_mode="non_cjk_only", use_zh_cn=False
    )

    assert sorted(fake_lm) == ["# Title", "First.", "Second."]
    final = _final(out)
    assert "譯:First." in final and "第一段" not in final
    assert result["step1"]["zh_cn_based_docs"] == []
    assert result["step3"]["written_from_zh_cn"] == 0


def test_switching_zh_cn_off_after_a_zh_cn_run_rebuilds_from_english(tmp_path, fake_lm):
    """先以 zh_cn 底稿跑過，再關閉開關重跑：舊的 zh_cn pending 被清掉，輸出改由英文產生。"""
    src, out = _md_layout(tmp_path)
    run_md_pipeline(str(src), output_dir=str(out), lang_mode="non_cjk_only")
    fake_lm.clear()

    run_md_pipeline(
        str(src), output_dir=str(out), lang_mode="non_cjk_only", use_zh_cn=False
    )

    assert "譯:First." in _final(out)
    pending = out / "md" / "待翻譯" / "pack"
    assert not (pending / "zh_cn" / "guide.md.json").exists()


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
    view.version_field.value = "1.21.1"
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


def test_zh_cn_translated_files_left_after_switching_off_are_superseded(
    tmp_path, fake_lm
):
    """先以 zh_cn 底稿跑過、再關閉開關：舊的 zh_cn 譯後檔是預期的略過，不警告「沒有更新」。"""
    from translation_tool.core import md_translation_steps

    src, out = _md_layout(tmp_path)
    run_md_pipeline(str(src), output_dir=str(out), lang_mode="non_cjk_only")
    warnings: list[str] = []
    real = md_translation_steps.log_warning
    md_translation_steps.log_warning = lambda msg, *a: warnings.append(str(msg))
    try:
        for _ in range(2):
            result = run_md_pipeline(
                str(src), output_dir=str(out), lang_mode="non_cjk_only", use_zh_cn=False
            )
            assert result["step3"]["skipped_pending_mismatch"] == 0
            assert result["step3"]["skipped_superseded"] >= 1
    finally:
        md_translation_steps.log_warning = real
    assert not any("沒有更新" in w for w in warnings)


def test_corrupted_zh_cn_pending_is_still_reported_not_hidden_as_superseded(
    tmp_path, fake_lm
):
    """zh_cn 的 pending 毀損（不是使用者關掉開關）：維持「不一致」略過，不當成被取代。"""
    src, out = _md_layout(tmp_path)
    run_md_pipeline(str(src), output_dir=str(out), lang_mode="non_cjk_only")
    pending = out / "md" / "待翻譯" / "pack" / "zh_cn" / "guide.md.json"
    assert pending.exists()
    pending.write_text("{壞掉", encoding="utf-8")

    result = run_md_pipeline(
        str(src),
        output_dir=str(out),
        lang_mode="non_cjk_only",
        step_extract=False,
        step_translate=False,
    )

    assert result["step3"]["skipped_pending_mismatch"] == 1
    assert result["step3"]["skipped_superseded"] == 0
