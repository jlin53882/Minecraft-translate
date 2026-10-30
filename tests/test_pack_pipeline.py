"""KubeJS／FTB Quests 共用流水線的來源選擇、manifest 與 fail-closed 測試。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services_impl import pack_pipeline_service
from app.tasks.task_session import TaskSession
from tests.test_pack_sources import _write_fixture
from translation_tool.translation_db.pack_pipeline import (
    PackPipelineError,
    PackPipelineOptions,
    discover_pack_candidates,
    resolve_pack_root,
    run_pack_pipeline,
)
from translation_tool.translation_db.repository import TranslationDB


def test_service_preview_opens_a_sqlite_readonly_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """預覽服務不能只依賴 create=False，必須在 SQLite 層明確唯讀。"""
    _write_fixture(tmp_path)
    db_path = tmp_path / "translation.db"
    seed = TranslationDB(db_path)
    seed.close()
    calls: list[tuple[bool, bool]] = []

    def open_isolated_database(*, create: bool, readonly: bool):
        calls.append((create, readonly))
        return TranslationDB(db_path, create=create, readonly=readonly)

    monkeypatch.setattr(pack_pipeline_service, "open_database", open_isolated_database)
    session = TaskSession(max_logs=100)

    report = pack_pipeline_service.run_kubejs_ftb_pipeline_service(
        str(tmp_path),
        "1.21.1",
        session,
        dry_run=True,
        publish=False,
        pack_root=tmp_path,
    )

    assert report is not None
    assert calls == [(False, True)]
    assert session.status == "DONE"


def test_discovery_keeps_multiple_pack_roots_separate(tmp_path: Path):
    wrapper = tmp_path / "extracted"
    first = wrapper / "pack-a"
    second = wrapper / "pack-b" / "overrides"
    (first / "kubejs" / "client_scripts").mkdir(parents=True)
    (second / "config" / "ftbquests" / "quests").mkdir(parents=True)

    candidates = [Path(value) for value in discover_pack_candidates(wrapper)]

    assert candidates == [first.resolve(), second.resolve()]
    with pytest.raises(PackPipelineError, match="多個模組包根目錄"):
        resolve_pack_root(wrapper)


def test_discovery_does_not_treat_kubejs_content_as_pack_root(tmp_path: Path):
    pack = tmp_path / "pack"
    (pack / "kubejs" / "assets" / "kubejs" / "lang").mkdir(parents=True)
    (pack / "kubejs" / "client_scripts").mkdir(parents=True)
    (pack / "kubejs" / "client_scripts" / "main.js").write_text(
        "ClientEvents.lang('en_us', event => event.add('demo.key', 'Demo'))",
        encoding="utf-8",
    )

    candidates = [Path(value) for value in discover_pack_candidates(pack)]

    assert candidates == [pack.resolve()]


def test_pipeline_preview_writes_reproducible_manifest_without_db_mutation(
    tmp_path: Path,
):
    _write_fixture(tmp_path)
    db_path = tmp_path / "translation.db"
    run_workspace = tmp_path / "runs"
    db = TranslationDB(db_path)
    try:
        before = db.count_entries()
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                dry_run=True,
                run_workspace=str(run_workspace),
            ),
            pack_root=tmp_path,
        )
        assert report.state == "done"
        assert report.items_found > 0
        assert report.files_read > 0
        assert report.timing_sec["scan"] >= 0
        assert report.timing_sec["total"] >= report.timing_sec["scan"]
        assert db.count_entries() == before

        manifest = json.loads(Path(report.manifest_path).read_text(encoding="utf-8"))
        assert manifest["pack_scope"] == report.pack_scope
        assert set(manifest["artifacts"]) == {
            "source_mapping",
            "translation_plan",
            "scan_report",
            "coverage",
            "execution",
            "format_issues",
            "writeback_diff",
        }
        for artifact in manifest["artifacts"].values():
            assert Path(artifact).is_file()
        coverage = json.loads(
            Path(manifest["artifacts"]["coverage"]).read_text(encoding="utf-8")
        )
        mapping = json.loads(
            Path(manifest["artifacts"]["source_mapping"]).read_text(encoding="utf-8")
        )
        assert coverage["source_breakdown"]["kubejs"]["items_found"] > 0
        assert coverage["source_breakdown"]["ftb_quests"]["items_found"] > 0
        assert all("target_path" in item for item in mapping["items"])
        assert any(
            item["target_path"].startswith("kubejs/")
            for item in mapping["items"]
            if item["target_path"]
        )
        assert manifest["program"]["version"]
        assert manifest["database"]["path"] == str(db_path.resolve())
        assert manifest["steps"]["scan"]["state"] == "done"
    finally:
        db.close()


def test_pipeline_publish_blocks_parse_failures_before_output(tmp_path: Path):
    _write_fixture(tmp_path)
    broken = tmp_path / "kubejs" / "assets" / "demo" / "lang" / "en_us.json"
    broken.write_text('{"broken":', encoding="utf-8")
    output = tmp_path.parent / "blocked-overlay"
    run_workspace = tmp_path / "runs"
    db = TranslationDB(tmp_path / "translation.db")
    try:
        with pytest.raises(PackPipelineError, match="發布不完整 overlay 已停止"):
            run_pack_pipeline(
                db,
                tmp_path,
                PackPipelineOptions(
                    version="1.21.1",
                    dry_run=False,
                    publish=True,
                    output_root=str(output),
                    run_workspace=str(run_workspace),
                ),
                pack_root=tmp_path,
            )
        assert not output.exists()
        manifests = list(run_workspace.glob("*/manifest.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["state"] == "blocked"
        assert any(
            issue["reason"] == "json_parse_failed"
            for issue in manifest["issues_detail"]
        )
    finally:
        db.close()


def test_pipeline_does_not_report_disabled_source_failures(tmp_path: Path):
    _write_fixture(tmp_path)
    broken = tmp_path / "config" / "ftbquests" / "quests" / "broken.snbt"
    broken.write_text("{broken:", encoding="utf-8")
    db = TranslationDB(tmp_path / "translation.db")
    try:
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                include_kubejs=True,
                include_ftb=False,
                dry_run=True,
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
        )
        assert report.issues == []
        assert set(report.kind_counts) <= {"kubejs_lang", "kubejs_script"}
        assert report.source_breakdown["ftb_quests"]["items_found"] == 0
    finally:
        db.close()


def test_incremental_sync_uses_source_identity_without_deleting_translations(
    tmp_path: Path,
):
    """增量只略過相同來源，不另建快取，也不刪除有效資料。"""
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        full = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                dry_run=False,
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
        )
        assert full.state == "done"
        count_before = db.count_entries()

        incremental = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                scan_mode="incremental",
                dry_run=True,
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
        )
        assert incremental.items_unchanged == incremental.items_found
        assert incremental.items_to_sync == 0
        assert incremental.ingest.new_entries == 0
        assert db.count_entries() == count_before
    finally:
        db.close()
