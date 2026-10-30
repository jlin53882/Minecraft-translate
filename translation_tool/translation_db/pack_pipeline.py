"""KubeJS／FTB Quests 與 ModDB 共用的模組包執行契約。

這個 orchestrator 不建立第二個資料庫，也不執行 JavaScript。它把既有的
``pack_sources`` 掃描器、``TranslationDB`` ingest、共用翻譯入口與
``pack_output`` writer 串成可預覽、可重試、可記錄的單次 run。
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from translation_tool.core.translation_status import translation_stop_reason
from translation_tool.translation_db.models import IngestStats, ScanItem
from translation_tool.translation_db.pack_output import (
    PackOutputReport,
    export_pack_overlay,
    overlay_target_path,
)
from translation_tool.translation_db.pack_sources import (
    PackScanResult,
    discover_pack_roots,
    scan_pack_sources,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scan_ingest import preview_ingest_many
from translation_tool.translation_db.schema import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_LANG,
    KIND_KUBEJS_SCRIPT,
)
from translation_tool.utils.cancellation import TaskCancelled, raise_if_cancelled

PACK_KINDS = frozenset(
    {KIND_KUBEJS_LANG, KIND_KUBEJS_SCRIPT, KIND_FTB_QUEST_LANG, KIND_FTB_QUESTS}
)
KUBEJS_KINDS = frozenset({KIND_KUBEJS_LANG, KIND_KUBEJS_SCRIPT})
FTB_KINDS = frozenset({KIND_FTB_QUEST_LANG, KIND_FTB_QUESTS})


class PackPipelineError(RuntimeError):
    """來源候選不明確、設定不安全或執行無法完成。"""


@dataclass(frozen=True)
class PackPipelineOptions:
    """一次執行的不可變設定快照。"""

    version: str
    scan_mode: str = "full"
    include_kubejs: bool = True
    include_ftb: bool = True
    dry_run: bool = True
    publish: bool = False
    output_root: str | None = None
    run_workspace: str | None = None
    translate_missing: bool = False

    def __post_init__(self) -> None:
        if self.scan_mode not in {"full", "incremental"}:
            raise ValueError("scan_mode 必須是 'full' 或 'incremental'")
        if self.publish and self.dry_run:
            raise ValueError("publish=True 不可同時使用 dry_run=True")


@dataclass
class PackPipelineReport:
    """預覽、DB 匯入與 overlay 輸出的完整處置結果。"""

    run_id: str
    version: str
    source_root: str
    pack_scope: str
    scan_mode: str = "full"
    state: str = "pending"
    elapsed_sec: float = 0.0
    timing_sec: dict[str, float] = field(default_factory=dict)
    files_seen: int = 0
    files_read: int = 0
    items_found: int = 0
    references_found: int = 0
    items_to_sync: int = 0
    items_unchanged: int = 0
    issues: list[dict[str, str]] = field(default_factory=list)
    kind_counts: dict[str, int] = field(default_factory=dict)
    source_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    ingest: IngestStats = field(default_factory=IngestStats)
    output: PackOutputReport | None = None
    manifest_path: str = ""
    translation: dict[str, Any] | None = None
    output_published: bool = False
    manifest_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "minecraft_translate.pack_pipeline_run.v1",
            "run_id": self.run_id,
            "version": self.version,
            "source_root": self.source_root,
            "pack_scope": self.pack_scope,
            "scan_mode": self.scan_mode,
            "state": self.state,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "timing_sec": {
                key: round(value, 3) for key, value in sorted(self.timing_sec.items())
            },
            "coverage": {
                "files_seen": self.files_seen,
                "files_read": self.files_read,
                "items_found": self.items_found,
                "references_found": self.references_found,
                "kind_counts": dict(sorted(self.kind_counts.items())),
                "source_breakdown": {
                    source: dict(sorted(values.items()))
                    for source, values in sorted(self.source_breakdown.items())
                },
                "issues": len(self.issues),
            },
            "sync": {
                "items_to_sync": self.items_to_sync,
                "items_unchanged": self.items_unchanged,
            },
            "ingest": self.ingest.__dict__.copy(),
            "output": self.output.as_dict() if self.output else None,
            "translation": dict(self.translation) if self.translation else None,
            "publish": {
                "committed": self.output_published,
                "manifest_error": self.manifest_error,
            },
            "issues_detail": list(self.issues),
            "manifest_path": self.manifest_path,
        }


def discover_pack_candidates(path: str | Path) -> list[str]:
    """供 Web／Flet 預覽使用；多個候選不會被自動合併。"""
    return [str(root) for root in discover_pack_roots(path)]


def resolve_pack_root(path: str | Path) -> tuple[Path, list[Path]]:
    """解析來源並在多個 pack root 時要求明確選擇。"""
    roots = discover_pack_roots(path)
    if not roots:
        raise PackPipelineError(f"找不到 KubeJS 或 FTB Quests 來源：{path}")
    requested = Path(path).expanduser().resolve()
    for root in roots:
        if root == requested:
            return root, roots
    if len(roots) > 1:
        choices = "、".join(str(root) for root in roots[:8])
        raise PackPipelineError(f"來源包含多個模組包根目錄，請明確選擇：{choices}")
    return roots[0], roots


def run_pack_pipeline(
    db: TranslationDB | None,
    source: str | Path,
    options: PackPipelineOptions,
    *,
    session=None,
    pack_root: str | Path | None = None,
    translation_runner: Callable[..., Any] | None = None,
) -> PackPipelineReport:
    """執行來源分析、DB 同步與可選 overlay 輸出。

    ``dry_run`` 永遠只讀來源；``publish`` 只有在 dry-run 驗證成功後才會發布。
    ``db`` 為 None 時可做純來源預覽，但不能發布 overlay。
    """
    _validate_run_options(db, options)
    started = time.perf_counter()
    _raise_if_cancelled(session)
    run_id = uuid.uuid4().hex[:16]
    root = _resolve_selected_pack_root(source, pack_root)
    scan_started = time.perf_counter()
    result = _scan_selected_sources(root, options)
    scan_elapsed = time.perf_counter() - scan_started
    _raise_if_cancelled(session)
    report = _make_report(
        run_id, options, root, result, timing_sec={"scan": scan_elapsed}
    )
    _log(session, f"📦 [{run_id}] 來源分析完成：{root}")
    _log(session, _coverage_log(report))
    _set_progress(session, 0.35)

    sync_items, unchanged = _items_for_sync(
        db, result.items, result.pack_scope, options
    )
    report.items_to_sync = len(sync_items)
    report.items_unchanged = unchanged
    _log(
        session,
        f"🔄 [{run_id}] 同步模式={options.scan_mode}，"
        f"待同步 {len(sync_items):,}，來源未變更略過 {unchanged:,}",
    )
    db_started = time.perf_counter()
    if db is not None:
        report.ingest = _sync_database(db, result, sync_items, options, run_id, session)
    elif not options.dry_run:
        raise PackPipelineError("正式同步需要資料庫；預覽模式可不提供資料庫")
    report.timing_sec["db_sync"] = time.perf_counter() - db_started
    _set_progress(session, 0.65)
    _raise_if_cancelled(session)

    _run_translation_phase(
        db,
        result,
        options,
        report,
        session,
        run_id,
        translation_runner,
    )
    _raise_if_cancelled(session)
    _block_publish_with_issues(db, options, report, result, session, started, run_id)
    _export_requested_output(db, root, options, result.pack_scope, report, session)
    return _complete_pipeline(report, options, result, db, session, started, run_id)


def _validate_run_options(
    db: TranslationDB | None, options: PackPipelineOptions
) -> None:
    if not options.publish:
        return
    if options.dry_run:
        raise PackPipelineError("正式發布不可使用預覽模式")
    if db is None:
        raise PackPipelineError("發布 overlay 需要資料庫")
    if not options.output_root:
        raise PackPipelineError("發布 overlay 需要輸出路徑")


def _run_translation_phase(
    db: TranslationDB | None,
    result: PackScanResult,
    options: PackPipelineOptions,
    report: PackPipelineReport,
    session,
    run_id: str,
    translation_runner: Callable[..., Any] | None,
) -> None:
    if not options.translate_missing:
        return
    if db is None:
        raise PackPipelineError("LM 翻譯需要資料庫")
    if report.issues:
        _log(session, f"⏭️ [{run_id}] 來源有問題，跳過 LM 翻譯；請先處理掃描問題")
        return
    if translation_runner is None:
        raise PackPipelineError("LM 翻譯已啟用，但未提供共用翻譯服務入口")

    translation_started = time.perf_counter()
    _log(session, f"🧠 [{run_id}] 啟動共用 ModDB LM 翻譯 phase")
    translation_result = translation_runner(db, result, options, session)
    report.translation = _translation_summary(translation_result)
    report.timing_sec["translation"] = time.perf_counter() - translation_started
    status = str(report.translation.get("status") or "").upper()
    stop_reason = translation_stop_reason(report.translation)
    if stop_reason is not None:
        report.issues.append(
            {
                "status": "translation_failed",
                "reason": stop_reason,
                "detail": (
                    f"共用 ModDB LM 翻譯 phase 未完成，狀態={status or 'UNKNOWN'}"
                ),
            }
        )
    _log(
        session,
        f"🧠 [{run_id}] LM 翻譯 phase 完成："
        f"狀態={status or 'UNKNOWN'}，"
        f"候選={report.translation.get('candidates', 0):,}，"
        f"寫入={report.translation.get('written', 0):,}，"
        f"預估批次={report.translation.get('planned_batches', 0):,}",
    )


def _block_publish_with_issues(
    db: TranslationDB | None,
    options: PackPipelineOptions,
    report: PackPipelineReport,
    result: PackScanResult,
    session,
    started: float,
    run_id: str,
) -> None:
    if not options.publish or not report.issues:
        return
    report.state = "blocked"
    report.elapsed_sec = time.perf_counter() - started
    report.timing_sec["total"] = report.elapsed_sec
    report.manifest_path = _write_run_manifest(report, options, result, db=db)
    _log(
        session,
        f"⛔ [{run_id}] 發布停止：來源有 {len(report.issues):,} 筆未解決問題；"
        f"詳見 {report.manifest_path}",
    )
    if session is not None:
        session.set_summary(report.as_dict())
    raise PackPipelineError(
        f"來源掃描有 {len(report.issues):,} 筆問題，為避免發布不完整 overlay 已停止；"
        f"報告：{report.manifest_path}"
    )


def _export_requested_output(
    db: TranslationDB | None,
    root: Path,
    options: PackPipelineOptions,
    scope: str,
    report: PackPipelineReport,
    session,
) -> None:
    output_started = time.perf_counter()
    if options.output_root and db is not None:
        report.output = _export_output(db, root, options, scope, session)
    elif options.publish:
        raise PackPipelineError("發布 overlay 需要輸出路徑與資料庫")
    report.timing_sec["output"] = time.perf_counter() - output_started
    report.output_published = bool(
        options.publish and report.output is not None and report.output.ok
    )
    _set_progress(session, 0.95)
    if (
        report.output_published
        and session is not None
        and getattr(session, "cancel_requested", False)
    ):
        _log(session, "ℹ️ 發布提交點已完成；忽略之後才到達的取消請求")


def _complete_pipeline(
    report: PackPipelineReport,
    options: PackPipelineOptions,
    result: PackScanResult,
    db: TranslationDB | None,
    session,
    started: float,
    run_id: str,
) -> PackPipelineReport:
    report.state = "done" if not report.issues else "done_with_warnings"
    report.elapsed_sec = time.perf_counter() - started
    report.timing_sec["total"] = report.elapsed_sec
    try:
        report.manifest_path = _write_run_manifest(report, options, result, db=db)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        report.manifest_error = str(exc)
        if not report.output_published:
            raise PackPipelineError(f"執行 manifest 寫入失敗：{exc}") from exc
        report.state = "published_with_manifest_error"
        _log(
            session,
            f"⚠️ [{run_id}] overlay 已發布，但 manifest 寫入失敗：{exc}",
        )
    _log(session, f"✅ [{run_id}] 執行完成：{report.state}")
    _set_progress(session, 1.0)
    return report


def _resolve_selected_pack_root(
    source: str | Path, pack_root: str | Path | None
) -> Path:
    """重新驗證 UI 選定的根目錄仍屬於目前來源快照。"""
    if pack_root is None:
        return resolve_pack_root(source)[0]
    selected = Path(pack_root).expanduser().resolve()
    current_candidates = discover_pack_roots(source)
    if selected not in current_candidates:
        raise PackPipelineError(
            "來源在分析後已變更，選定的模組包根目錄不再屬於目前來源；請重新分析"
        )
    return selected


def _scan_selected_sources(root: Path, options: PackPipelineOptions) -> PackScanResult:
    result = scan_pack_sources(
        root,
        options.version,
        include_kubejs=options.include_kubejs,
        include_ftb=options.include_ftb,
    )
    enabled = set()
    if options.include_kubejs:
        enabled.update(KUBEJS_KINDS)
    if options.include_ftb:
        enabled.update(FTB_KINDS)
    enabled_sources = set()
    if options.include_kubejs:
        enabled_sources.add("kubejs")
    if options.include_ftb:
        enabled_sources.add("ftb_quests")
    return replace(
        result,
        items=[item for item in result.items if item.kind in enabled],
        references=[ref for ref in result.references if ref.source_kind in enabled],
        files_seen=sum(
            result.files_seen_by_source.get(source, 0) for source in enabled_sources
        ),
        sources_found_by_source={
            source: count
            for source, count in result.sources_found_by_source.items()
            if source in enabled_sources
        },
        files_seen_by_source={
            source: count
            for source, count in result.files_seen_by_source.items()
            if source in enabled_sources
        },
        files_read_by_source={
            source: count
            for source, count in result.files_read_by_source.items()
            if source in enabled_sources
        },
        source_directories={
            source: present
            for source, present in result.source_directories.items()
            if source in enabled_sources
        },
    )


def _make_report(
    run_id: str,
    options: PackPipelineOptions,
    root: Path,
    result: PackScanResult,
    *,
    timing_sec: dict[str, float] | None = None,
) -> PackPipelineReport:
    enabled_sources = set()
    if options.include_kubejs:
        enabled_sources.add("kubejs")
    if options.include_ftb:
        enabled_sources.add("ftb_quests")
    files_seen = sum(
        result.files_seen_by_source.get(source, 0) for source in enabled_sources
    )
    files_read = sum(
        result.files_read_by_source.get(source, 0) for source in enabled_sources
    )
    counts = {
        kind: sum(item.kind == kind for item in result.items) for kind in PACK_KINDS
    }
    counts = {kind: count for kind, count in counts.items() if count}
    return PackPipelineReport(
        run_id=run_id,
        version=options.version,
        source_root=str(root),
        pack_scope=result.pack_scope,
        scan_mode=options.scan_mode,
        timing_sec=dict(timing_sec or {}),
        files_seen=files_seen,
        files_read=files_read,
        items_found=len(result.items),
        references_found=len(result.references),
        issues=list(result.issues),
        kind_counts=counts,
        source_breakdown=result.source_breakdown(),
    )


def _sync_database(
    db: TranslationDB,
    result: PackScanResult,
    items: list[ScanItem],
    options: PackPipelineOptions,
    run_id: str,
    session,
) -> IngestStats:
    if options.dry_run:
        preview = db.scan_preview_target(options.version)
        stats = preview_ingest_many(preview, [items], None)[0]
        _log(
            session,
            f"🔎 [{run_id}] DB 預覽（{options.scan_mode}）："
            f"{stats.new_entries:,} 新條目，{len(items):,} 筆待同步",
        )
        return stats
    stats = db.ingest(options.version, items)
    db.record_source_references(
        options.version, result.references, pack_scope=result.pack_scope
    )
    if result.issues:
        db.record_content_issues(
            options.version, run_id, result.issues, origin="pipeline"
        )
    _log(
        session,
        f"📥 [{run_id}] DB 同步：新增 {stats.new_entries:,}，既有 {stats.existing:,}",
    )
    return stats


def _items_for_sync(
    db: TranslationDB | None,
    items: list[ScanItem],
    pack_scope: str,
    options: PackPipelineOptions,
) -> tuple[list[ScanItem], int]:
    """套用非破壞增量策略；完整模式仍使用共用 ingest 全量重掃。"""
    if options.scan_mode == "full" or db is None:
        return items, 0
    existing = db.pack_origin_hashes(options.version, pack_scope)
    pending: list[ScanItem] = []
    unchanged = 0
    for item in items:
        key = (item.kind, item.mod_id, item.source_path, item.source_selector)
        hashes = existing.get(key)
        if item.source_hash and hashes and item.source_hash in hashes:
            unchanged += 1
            continue
        pending.append(item)
    return pending, unchanged


def _export_output(
    db: TranslationDB,
    root: Path,
    options: PackPipelineOptions,
    scope: str,
    session,
) -> PackOutputReport:
    output = Path(options.output_root).expanduser().resolve()
    preview = export_pack_overlay(
        db,
        root,
        options.version,
        output,
        pack_scope=scope,
        dry_run=True,
        allowed_kinds=_selected_pack_kinds(options),
    )
    _log(session, f"📋 overlay 預覽：{preview.entries_considered:,} 筆候選")
    if not options.publish:
        return preview
    return export_pack_overlay(
        db,
        root,
        options.version,
        output,
        pack_scope=scope,
        allowed_kinds=_selected_pack_kinds(options),
    )


def _selected_pack_kinds(options: PackPipelineOptions) -> frozenset[str]:
    kinds: set[str] = set()
    if options.include_kubejs:
        kinds.update(KUBEJS_KINDS)
    if options.include_ftb:
        kinds.update(FTB_KINDS)
    return frozenset(kinds)


def _translation_summary(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    if isinstance(value, dict):
        return dict(value)
    raise PackPipelineError("共用翻譯服務沒有回傳可序列化的報告")


def _coverage_log(report: PackPipelineReport) -> str:
    return (
        f"📊 [{report.run_id}] 檔案 {report.files_read:,}/{report.files_seen:,}，"
        f"候選 {report.items_found:,}，引用 {report.references_found:,}，"
        f"問題 {len(report.issues):,}"
    )


def _write_run_manifest(
    report: PackPipelineReport,
    options: PackPipelineOptions,
    result: PackScanResult,
    *,
    db: TranslationDB | None,
) -> str:
    workspace = _run_workspace(report, options)
    metadata, reports = _prepare_manifest_directories(workspace)
    source_mapping = _source_mapping_report(report, result)
    scan_report = _scan_manifest_report(result)
    translation_plan = _translation_plan_report(report, result, options)
    output = report.output
    execution_report = _execution_manifest_report(report)
    format_issues = _format_issue_report(report, output)
    writeback_diff = _writeback_diff_report(output)
    steps = _manifest_steps(report, output, db)
    retryable = _retryable_issues(report)
    coverage_report = _coverage_manifest_report(report)
    _write_json(metadata / "source_mapping.json", source_mapping)
    _write_json(metadata / "translation_plan.json", translation_plan)
    _write_json(reports / "scan_report.json", scan_report)
    _write_json(reports / "coverage.json", coverage_report)
    _write_json(reports / "execution.json", execution_report)
    _write_json(reports / "format_issues.json", format_issues)
    _write_json(reports / "writeback_diff.json", writeback_diff)
    path = workspace / "manifest.json"
    report.manifest_path = str(path)
    manifest = report.as_dict()
    manifest["options"] = options.__dict__.copy()
    manifest["database"] = _database_identity(db)
    manifest["program"] = _program_identity()
    manifest["steps"] = steps
    manifest["retryable_issues"] = retryable
    manifest["output_ownership"] = {
        "schema": "minecraft_translate.pack_overlay.v1",
        "pack_scope": report.pack_scope,
        "marker": ".minecraft-translate-overlay.json",
        "published": writeback_diff["published"],
    }
    manifest["artifacts"] = {
        "source_mapping": str(metadata / "source_mapping.json"),
        "translation_plan": str(metadata / "translation_plan.json"),
        "scan_report": str(reports / "scan_report.json"),
        "coverage": str(reports / "coverage.json"),
        "execution": str(reports / "execution.json"),
        "format_issues": str(reports / "format_issues.json"),
        "writeback_diff": str(reports / "writeback_diff.json"),
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(path)


def _prepare_manifest_directories(workspace: Path) -> tuple[Path, Path]:
    workspace.mkdir(parents=True, exist_ok=True)
    metadata = workspace / "metadata"
    reports = workspace / "reports"
    metadata.mkdir(exist_ok=True)
    reports.mkdir(exist_ok=True)
    return metadata, reports


def _source_mapping_report(
    report: PackPipelineReport, result: PackScanResult
) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_source_mapping.v1",
        "root": report.source_root,
        "pack_scope": report.pack_scope,
        "sources": list(result.sources_found),
        "source_directories": dict(result.source_directories),
        "items": [
            {
                "kind": item.kind,
                "mod_id": item.mod_id,
                "key": item.key,
                "source_path": item.source_path,
                "selector": item.source_selector,
                "source_hash": item.source_hash,
                "pack_scope": item.pack_scope,
                "target_path": overlay_target_path(
                    item.kind, item.source_path, format_profile=item.source_format
                ),
            }
            for item in result.items
        ],
        "references": [ref.__dict__.copy() for ref in result.references],
    }


def _scan_manifest_report(result: PackScanResult) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_scan_report.v1",
        "files_seen": result.files_seen,
        "files_seen_by_source": dict(result.files_seen_by_source),
        "files_read_by_source": dict(result.files_read_by_source),
        "sources_found_by_source": dict(result.sources_found_by_source),
        "locale_fallbacks": dict(result.locale_fallbacks),
        "issues": list(result.issues),
        "source_breakdown": result.source_breakdown(),
    }


def _translation_plan_report(
    report: PackPipelineReport,
    result: PackScanResult,
    options: PackPipelineOptions,
) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_translation_plan.v1",
        "existing_translation_only": not options.translate_missing,
        "lm_delegated_to_existing_task": not options.translate_missing,
        "translation_phase": (
            "shared_moddb_lm" if options.translate_missing else "external_task"
        ),
        "items_planned": len(result.items),
        "items_to_sync": report.items_to_sync,
        "items_unchanged": report.items_unchanged,
        "scan_mode": options.scan_mode,
        "kind_counts": dict(report.kind_counts),
        "publish": options.publish,
        "output_contract": {
            "kubejs_lang": "kubejs/assets/<namespace>/lang/zh_tw.json",
            "kubejs_script": "kubejs/<source-relative-path>",
            "ftb_quests": "config/ftbquests/quests/<source-relative-path>",
            "ftb_quest_lang": (
                "config/ftbquests/quests/lang/<target-locale>/<relative-file>.snbt"
            ),
        },
    }


def _execution_manifest_report(report: PackPipelineReport) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_execution_report.v1",
        "run_id": report.run_id,
        "state": report.state,
        "timing_sec": dict(report.timing_sec),
        "ingest": report.ingest.__dict__.copy(),
        "output": report.output.as_dict() if report.output else None,
        "translation": dict(report.translation) if report.translation else None,
        "publish": {
            "committed": report.output_published,
            "manifest_error": report.manifest_error,
        },
        "issues": list(report.issues),
    }


def _format_issue_report(
    report: PackPipelineReport, output: PackOutputReport | None
) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_format_issues.v1",
        "scan": list(report.issues),
        "output": {
            "unsupported": list(output.unsupported) if output else [],
            "conflicts": list(output.conflicts) if output else [],
            "stale_sources": list(output.stale_sources) if output else [],
            "unapplied": list(output.unapplied) if output else [],
        },
    }


def _writeback_diff_report(output: PackOutputReport | None) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_writeback_diff.v1",
        "planned": output.entries_considered if output else 0,
        "applied": output.entries_applied if output else 0,
        "verified": output.entries_verified if output else 0,
        "reparsed": output.entries_reparsed if output else 0,
        "rescan_verified": output.entries_rescan_verified if output else 0,
        "files_written": output.files_written if output else 0,
        "published": bool(output and not output.dry_run and output.ok),
    }


def _manifest_steps(
    report: PackPipelineReport,
    output: PackOutputReport | None,
    db: TranslationDB | None,
) -> dict[str, dict[str, Any]]:
    return {
        "scan": {
            "state": "done",
            "elapsed_sec": report.timing_sec.get("scan", 0.0),
        },
        "db_sync": {
            "state": "done" if db is not None else "skipped",
            "elapsed_sec": report.timing_sec.get("db_sync", 0.0),
        },
        "translation": {
            "state": "done" if report.translation else "skipped",
            "elapsed_sec": report.timing_sec.get("translation", 0.0),
        },
        "output": {
            "state": _output_step_state(output),
            "elapsed_sec": report.timing_sec.get("output", 0.0),
        },
    }


def _output_step_state(output: PackOutputReport | None) -> str:
    if output is None:
        return "skipped"
    if output.dry_run:
        return "preview"
    return "published" if output.ok else "failed"


def _retryable_issues(report: PackPipelineReport) -> list[dict[str, str]]:
    return [
        issue
        for issue in report.issues
        if issue.get("status") in {"pending", "parse_failed"}
    ]


def _coverage_manifest_report(report: PackPipelineReport) -> dict[str, Any]:
    return {
        "schema": "minecraft_translate.pack_coverage.v1",
        **report.as_dict()["coverage"],
        "source_breakdown": dict(report.source_breakdown),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _database_identity(db: TranslationDB | None) -> dict[str, Any]:
    """記錄 DB 的可比對身份，不複製或雜湊大型正式資料庫。"""
    if db is None:
        return {"path": None, "size": None, "mtime_ns": None}
    path = Path(db.path).expanduser().resolve()
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "size": None, "mtime_ns": None}
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _program_identity() -> dict[str, str | None]:
    """保留版本與執行當下 Git HEAD；打包版沒有 Git 時仍可讀版本。"""
    try:
        version = importlib.metadata.version("minecraft-translator-flet")
    except importlib.metadata.PackageNotFoundError:
        version = "0.8.0"
    repo = Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    return {
        "package": "minecraft-translator-flet",
        "version": version,
        "git_head": head,
    }


def _run_workspace(report: PackPipelineReport, options: PackPipelineOptions) -> Path:
    if options.run_workspace:
        return Path(options.run_workspace).expanduser().resolve() / report.run_id
    if options.output_root:
        return (
            Path(options.output_root).expanduser().resolve().parent
            / ".minecraft-translate-runs"
            / report.run_id
        )
    return Path(report.source_root).parent / ".minecraft-translate-runs" / report.run_id


def _log(session, message: str) -> None:
    if session is not None:
        session.add_log(message)


def _set_progress(session, value: float) -> None:
    if session is not None:
        session.set_progress(value)


def _raise_if_cancelled(session) -> None:
    """在昂貴階段之間檢查操作取消，避免取消後仍發布 overlay。"""
    raise_if_cancelled()
    if session is not None and getattr(session, "cancel_requested", False):
        raise TaskCancelled()


__all__ = [
    "PackPipelineError",
    "PackPipelineOptions",
    "PackPipelineReport",
    "discover_pack_candidates",
    "resolve_pack_root",
    "run_pack_pipeline",
]
