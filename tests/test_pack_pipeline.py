"""KubeJS／FTB Quests 共用流水線的來源選擇、manifest 與 fail-closed 測試。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services_impl import moddb_translate_service, pack_pipeline_service
from app.services_impl.moddb_translate_service import TranslateOptions
from app.tasks.task_session import TaskSession
from tests.test_pack_sources import _write_fixture
from translation_tool.translation_db import pack_pipeline as pack_pipeline_module
from translation_tool.translation_db.pack_output import PackOutputReport
from translation_tool.translation_db.pack_pipeline import (
    PackPipelineError,
    PackPipelineOptions,
    discover_pack_candidates,
    resolve_pack_root,
    run_pack_pipeline,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator


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


def test_shared_translation_service_reuses_pipeline_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db = TranslationDB(tmp_path / "translation.db")
    session = TaskSession(max_logs=100)
    monkeypatch.setattr(
        moddb_translate_service,
        "open_database",
        lambda **_kwargs: pytest.fail("pipeline must pass its existing DB connection"),
    )
    try:
        result = moddb_translate_service.run_moddb_translate_service(
            TranslateOptions(version="1.21.1", dry_run=True),
            session,
            manage_session=False,
            db_override=db,
        )
    finally:
        db.close()

    assert result is not None
    assert result["status"] == "DONE"
    assert result["planned_batches"] == 0


def test_service_wires_shared_translation_runner_with_scoped_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """流水線 LM phase 必須沿用同一個 DB 與來源範圍，不另開第二套流程。"""
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    calls: list[tuple[tuple[str, ...], str]] = []

    monkeypatch.setattr(
        pack_pipeline_service,
        "open_database",
        lambda **_kwargs: db,
    )

    def fake_shared_translation(options, session, **kwargs):
        assert kwargs["db_override"] is db
        calls.append((tuple(sorted(options.mod_ids)), options.version))
        return {
            "status": "DONE",
            "candidates": 0,
            "written": 0,
            "planned_batches": 0,
        }

    monkeypatch.setattr(
        pack_pipeline_service,
        "run_moddb_translate_service",
        fake_shared_translation,
    )
    session = TaskSession(max_logs=100)

    try:
        report = pack_pipeline_service.run_kubejs_ftb_pipeline_service(
            str(tmp_path),
            "1.21.1",
            session,
            dry_run=True,
            publish=False,
            pack_root=tmp_path,
            translate_missing=True,
        )
    finally:
        db.close()

    assert report is not None
    assert calls
    assert calls[0][1] == "1.21.1"
    assert calls[0][0]
    # KubeJS lang JSON 以 namespace 作為 mod_id；所有來源仍必須帶本次
    # pack scope，避免把同資料庫其他模組包的條目交給 LM。
    assert all(value.endswith(f"@{report.pack_scope}") for value in calls[0][0])
    assert report.translation["status"] == "DONE"


def test_shared_translation_failure_is_reported_to_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """共用翻譯服務失敗時不得以預設 DONE 回報。"""
    db = TranslationDB(tmp_path / "translation.db")
    session = TaskSession(max_logs=100)

    def fail_selection(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(moddb_translate_service, "_select_rows", fail_selection)
    try:
        result = moddb_translate_service.run_moddb_translate_service(
            TranslateOptions(version="1.21.1", dry_run=True),
            session,
            manage_session=False,
            db_override=db,
        )
    finally:
        db.close()

    assert result is not None
    assert result["status"] == "ERROR"
    assert "provider unavailable" in result["metrics"]["error"]


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


@pytest.mark.parametrize(
    ("include_kubejs", "include_ftb", "expected_prefixes", "excluded_prefix"),
    [
        (True, False, ("kubejs/",), "config/"),
        (False, True, ("config/",), "kubejs/"),
        (True, True, ("kubejs/", "config/"), None),
    ],
)
def test_pipeline_output_respects_selected_source_kinds(
    tmp_path: Path,
    include_kubejs: bool,
    include_ftb: bool,
    expected_prefixes: tuple[str, ...],
    excluded_prefix: str | None,
):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        list(
            scan_folder_generator(
                db,
                tmp_path,
                ScanOptions("1.21.1", include_pack_content=True),
                workers=1,
            )
        )
        for kind in ("kubejs_lang", "ftb_quests"):
            entry_id = db._one("SELECT id FROM entry WHERE kind=? LIMIT 1", (kind,))[0]
            db.save_manual(entry_id, f"{kind} 翻譯", propagate=False)

        output = tmp_path.parent / f"selected-{int(include_kubejs)}-{int(include_ftb)}"
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                include_kubejs=include_kubejs,
                include_ftb=include_ftb,
                dry_run=False,
                publish=True,
                output_root=str(output),
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
        )

        assert report.output_published
        files = [
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file() and not path.name.startswith(".minecraft-translate")
        ]
        assert all(
            any(path.startswith(prefix) for path in files)
            for prefix in expected_prefixes
        )
        if excluded_prefix is not None:
            assert not any(path.startswith(excluded_prefix) for path in files)
    finally:
        db.close()


def test_pipeline_can_run_shared_translation_phase(tmp_path: Path):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    calls: list[tuple[int, str]] = []

    def translation_runner(current_db, result, options, session):
        calls.append((len(result.items), options.version))
        assert current_db is db
        return {
            "status": "DONE",
            "candidates": len(result.items),
            "written": 3,
            "planned_batches": 1,
        }

    try:
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                dry_run=True,
                translate_missing=True,
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
            translation_runner=translation_runner,
        )

        assert calls and calls[0][0] == report.items_found
        assert report.translation == {
            "status": "DONE",
            "candidates": report.items_found,
            "written": 3,
            "planned_batches": 1,
        }
        plan = json.loads(
            (
                Path(report.manifest_path).parent / "metadata" / "translation_plan.json"
            ).read_text(encoding="utf-8")
        )
        assert plan["translation_phase"] == "shared_moddb_lm"
        assert plan["lm_delegated_to_existing_task"] is False
    finally:
        db.close()


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("FAILED", "translation_failed"),
        ("ABORTED", "translation_failed"),
        ("ALL_KEYS_EXHAUSTED", "translation_failed"),
        ("UNKNOWN", "translation_status_unknown"),
        ("DONE_WITH_WARNINGS", "translation_status_unknown"),
    ],
)
def test_pipeline_blocks_publish_for_non_done_translation_status(
    tmp_path: Path, status: str, reason: str
):
    """不完整或未知的 LM 終態不得發布部分 overlay。"""
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    output = tmp_path.parent / f"blocked-{status.lower()}"
    run_workspace = tmp_path / "runs"

    def translation_runner(*_args, **_kwargs):
        return {
            "status": status,
            "candidates": 1,
            "written": 1,
            "planned_batches": 1,
        }

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
                    translate_missing=True,
                ),
                pack_root=tmp_path,
                translation_runner=translation_runner,
            )

        assert not output.exists()
        manifests = list(run_workspace.glob("*/manifest.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["state"] == "blocked"
        assert manifest["steps"]["translation"]["state"] == "failed"
        assert any(issue["reason"] == reason for issue in manifest["issues_detail"])
    finally:
        db.close()


def test_pipeline_preview_dry_run_translation_is_not_reported_as_failure(
    tmp_path: Path,
):
    """唯讀 LM 預覽成功時不應產生失敗 issue，manifest 應標記 preview。"""
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")

    def translation_runner(*_args, **_kwargs):
        return {
            "status": "DONE",
            "dry_run": True,
            "candidates": 1,
            "written": 0,
            "planned_batches": 1,
        }

    try:
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                dry_run=True,
                translate_missing=True,
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
            translation_runner=translation_runner,
        )

        assert report.state == "done"
        assert report.issues == []
        manifest = json.loads(Path(report.manifest_path).read_text(encoding="utf-8"))
        assert manifest["steps"]["translation"]["state"] == "preview"
    finally:
        db.close()


def test_pipeline_cancel_after_publish_keeps_committed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    session = TaskSession(max_logs=100)

    def publish_then_cancel(*_args, **_kwargs):
        session.request_cancel()
        return PackOutputReport("scope", "output", False)

    monkeypatch.setattr(pack_pipeline_module, "_export_output", publish_then_cancel)
    try:
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                dry_run=False,
                publish=True,
                output_root=str(tmp_path / "output"),
                run_workspace=str(tmp_path / "runs"),
            ),
            session=session,
            pack_root=tmp_path,
        )
    finally:
        db.close()

    assert report.output_published is True
    assert report.state == "done"


def test_pipeline_manifest_failure_after_publish_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_fixture(tmp_path)
    db = TranslationDB(tmp_path / "translation.db")
    monkeypatch.setattr(
        pack_pipeline_module,
        "_export_output",
        lambda *_args, **_kwargs: PackOutputReport("scope", "output", False),
    )

    def fail_manifest(*_args, **_kwargs):
        raise OSError("manifest disk full")

    monkeypatch.setattr(pack_pipeline_module, "_write_run_manifest", fail_manifest)
    try:
        report = run_pack_pipeline(
            db,
            tmp_path,
            PackPipelineOptions(
                version="1.21.1",
                dry_run=False,
                publish=True,
                output_root=str(tmp_path / "output"),
                run_workspace=str(tmp_path / "runs"),
            ),
            pack_root=tmp_path,
        )
    finally:
        db.close()

    assert report.output_published is True
    assert report.state == "published_with_manifest_error"
    assert "manifest disk full" in report.manifest_error


def test_pipeline_rejects_stale_selected_pack_root(tmp_path: Path):
    _write_fixture(tmp_path)
    other = tmp_path.parent / f"{tmp_path.name}-other-pack"
    _write_fixture(other)
    db = TranslationDB(tmp_path / "translation.db")
    try:
        with pytest.raises(PackPipelineError, match="不再屬於目前來源"):
            run_pack_pipeline(
                db,
                tmp_path,
                PackPipelineOptions(version="1.21.1", dry_run=True),
                pack_root=other,
            )
    finally:
        db.close()
