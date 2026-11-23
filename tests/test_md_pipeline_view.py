"""Markdown 流水線頁：來源探索勾選、版本、執行參數傳遞與服務層。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from app.services_impl import md_pipeline_service as svc
from app.tasks.task_session import TaskSession
from app.views import md_pipeline_view as mv
from tests.conftest import mock_filepicker, mock_page


def _view(monkeypatch, *, zh_cn_default: bool = True):
    monkeypatch.setattr(mv, "get_md_use_zh_cn_default", lambda: zh_cn_default)
    return mv.MarkdownPipelineView(mock_page(), mock_filepicker())


def test_read_zh_cn_checkbox_follows_config_default(monkeypatch):
    assert _view(monkeypatch, zh_cn_default=True).read_zh_cn.value is True
    assert _view(monkeypatch, zh_cn_default=False).read_zh_cn.value is False


def test_selected_sources_falls_back_to_typed_path_before_discovery(monkeypatch):
    view = _view(monkeypatch)
    view.source_field.value = "C:/guides"

    assert view.selected_sources() == ["C:/guides"]


def test_discovery_lists_candidates_as_checked_boxes(monkeypatch, tmp_path):
    (tmp_path / "kubejs" / "assets").mkdir(parents=True)
    (tmp_path / "resourcepacks" / "a" / "assets").mkdir(parents=True)
    view = _view(monkeypatch)
    monkeypatch.setattr(mv, "detect_minecraft_version", lambda _s: "1.21.1")
    view.source_field.value = str(tmp_path)

    asyncio.run(view._discover())

    boxes = view.candidate_column.controls
    assert [Path(str(b.label)).name for b in boxes] == ["kubejs", "a"]
    assert all(b.value for b in boxes)
    assert view.version_field.value == "1.21.1"
    boxes[0].value = False
    assert [Path(p).name for p in view.selected_sources()] == ["a"]


def test_unchecking_every_candidate_blocks_the_run(monkeypatch):
    view = _view(monkeypatch)
    view.source_field.value = "C:/pack"
    view.version_field.value = "1.21.1"
    view._candidates = ["C:/pack/kubejs"]
    view.candidate_column.controls = [ft.Checkbox(label="C:/pack/kubejs", value=False)]

    blocker = view._run_blocker(False)

    assert blocker is not None and "勾選" in blocker[0]


def test_publish_requires_an_output_folder(monkeypatch):
    view = _view(monkeypatch)
    view.source_field.value = "C:/guides"
    view.version_field.value = "1.21.1"

    assert view._run_blocker(False) is None
    blocker = view._run_blocker(True)
    assert blocker is not None and "輸出" in blocker[0]


def test_start_forwards_options_to_the_service(monkeypatch, tmp_path):
    view = _view(monkeypatch)
    view.source_field.value = str(tmp_path)
    view.version_field.value = "1.21.1"
    view.output_field.value = "D:/out"
    view.read_zh_cn.value = False
    view.translate_missing.value = True
    seen: dict = {}
    monkeypatch.setattr(
        mv, "run_md_pipeline_service", lambda *a, **k: seen.update(args=a, kwargs=k)
    )
    monkeypatch.setattr(
        mv, "launch_page_operation", lambda _p, fn, **_k: (fn(), True)[1]
    )
    monkeypatch.setattr(view._poller, "start", lambda *a, **k: None)

    asyncio.run(view._start(dry_run=False, publish=True))

    assert seen["args"][0] == [str(tmp_path)] and seen["args"][1] == "1.21.1"
    kwargs = seen["kwargs"]
    assert kwargs["read_zh_cn"] is False
    assert kwargs["translate_missing"] is True
    assert kwargs["publish"] is True and kwargs["dry_run"] is False
    assert kwargs["output_root"] == "D:/out"


def test_service_runs_each_source_and_merges_summaries(monkeypatch):
    calls: list[str] = []

    class _Report:
        def __init__(self, n):
            self.n = n

        def as_dict(self):
            return {"status": "DONE", "items_found": self.n, "issues": [{"p": self.n}]}

    def fake_run(db, folder, options, **_kw):
        calls.append(folder)
        return _Report(len(calls))

    monkeypatch.setattr(svc, "run_md_pipeline", fake_run)
    monkeypatch.setattr(svc, "open_database", lambda **_k: None)
    session = TaskSession(max_logs=10)

    svc.run_md_pipeline_service(["a", "b"], "1.21.1", session, dry_run=True)

    assert calls == ["a", "b"]
    summary = session.snapshot()["summary"]
    assert summary["items_found"] == 3 and len(summary["issues"]) == 2


def test_service_reports_error_instead_of_raising(monkeypatch):
    monkeypatch.setattr(svc, "open_database", lambda **_k: None)
    session = TaskSession(max_logs=10)

    result = svc.run_md_pipeline_service([], "1.21.1", session, dry_run=False)

    assert result is None
    assert str(session.snapshot().get("status")).upper() == "ERROR"


def test_translation_runner_translates_only_guides_of_scanned_mods(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        svc,
        "run_moddb_translate_service",
        lambda options, session, **kw: (
            seen.update(options=options) or {"status": "DONE"}
        ),
    )
    runner = svc._translation_runner("1.21.1", True)
    scan = SimpleNamespace(mod_ids=("demo",))

    runner(object(), scan, SimpleNamespace(dry_run=False), object())

    assert seen["options"].kind == "guide" and seen["options"].mod_ids == ("demo",)
    off = svc._translation_runner("1.21.1", False)(
        object(), scan, SimpleNamespace(dry_run=False), object()
    )
    assert off["candidates"] == 0


def _non_controls(node, path="view"):
    """控制項樹中不是 ft.Control 的子項（Flet 序列化時會失敗）。"""
    bad = []
    for name in ("controls", "content"):
        child = getattr(node, name, None)
        children = child if isinstance(child, list) else [child]
        for item in children:
            if item is None:
                continue
            if isinstance(item, str):
                continue
            if not isinstance(item, ft.Control):
                bad.append(f"{path}.{name}: {type(item).__name__}")
            else:
                bad += _non_controls(item, f"{path}.{name}")
    return bad


def test_control_tree_contains_only_flet_controls(monkeypatch):
    view = mv.MarkdownPipelineView(mock_page(), mock_filepicker())

    assert _non_controls(view) == []


def test_service_expands_a_pack_root_without_a_prior_analysis(monkeypatch, tmp_path):
    (tmp_path / "kubejs" / "assets").mkdir(parents=True)
    (tmp_path / "resourcepacks" / "rp" / "assets").mkdir(parents=True)
    calls: list[str] = []

    class _Report:
        def as_dict(self):
            return {"status": "DONE"}

    monkeypatch.setattr(
        svc,
        "run_md_pipeline",
        lambda db, folder, *_a, **_k: calls.append(folder) or _Report(),
    )
    monkeypatch.setattr(svc, "open_database", lambda **_k: None)

    svc.run_md_pipeline_service(
        str(tmp_path), "1.21.1", TaskSession(max_logs=10), dry_run=True
    )

    assert [Path(c).relative_to(tmp_path.resolve()).as_posix() for c in calls] == [
        "kubejs",
        "resourcepacks/rp",
    ]


def test_service_never_scans_the_output_folder(monkeypatch, tmp_path):
    (tmp_path / "kubejs" / "assets").mkdir(parents=True)
    out = tmp_path / "resourcepacks" / "out"
    (out / "assets").mkdir(parents=True)
    calls: list[str] = []

    class _Report:
        def as_dict(self):
            return {"status": "DONE"}

    monkeypatch.setattr(
        svc,
        "run_md_pipeline",
        lambda db, folder, *_a, **_k: calls.append(folder) or _Report(),
    )
    monkeypatch.setattr(svc, "open_database", lambda **_k: None)

    svc.run_md_pipeline_service(
        [str(tmp_path / "kubejs"), str(out)],
        "1.21.1",
        TaskSession(max_logs=10),
        output_root=str(out),
        dry_run=True,
    )

    assert [Path(c).name for c in calls] == ["kubejs"]


def test_dry_run_opens_the_database_read_only(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_open(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(svc, "open_database", fake_open)
    monkeypatch.setattr(
        svc,
        "run_md_pipeline",
        lambda *_a, **_k: SimpleNamespace(as_dict=lambda: {"status": "DONE"}),
    )

    svc.run_md_pipeline_service(
        str(tmp_path), "1.21.1", TaskSession(max_logs=10), dry_run=True
    )

    assert seen == {"create": False, "readonly": True}


def test_merge_keeps_the_worst_translation_status():
    merged = svc._merge_summaries(
        [
            {"status": "DONE", "translation": {"status": "DONE", "written": 2}},
            {"status": "DONE", "translation": {"status": "FAILED", "written": 1}},
        ]
    )

    assert merged["translation"] == {"status": "FAILED", "written": 3}
