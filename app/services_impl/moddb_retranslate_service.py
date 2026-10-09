"""Preview and repair existing source translations using direct AI output."""

from __future__ import annotations

import logging
import sqlite3
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.moddb_service import (
    database_problem,
    open_database,
    warm_stats_quietly,
)
from app.services_impl.moddb_source_service import source_label
from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    build_items,
    plan_batches,
)
from app.services_impl.pipelines._pipeline_logging import (
    ensure_pipeline_logging,
    mirror_session_log,
)
from app.tasks.task_session import add_log_unmirrored
from app.views.moddb.formatting import token_issues
from translation_tool.core.lm_batch_budget import (
    profile_for_cache_type,
    select_batch_size,
)
from translation_tool.core.lm_translator_main import translate_batch_smart
from translation_tool.core.lm_translator_shared_cache import get_default_cache_rules
from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.models import SameSourceAIEntry
from translation_tool.translation_db.run_progress import RunProgress
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.utils.cache_manager import (
    add_to_cache,
    initialize_translation_cache,
    save_translation_cache,
)
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    interruptible_sleep,
    is_cancelled,
)
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)

_OPERATION = "retranslate_same_source_ai"
_QUALITY_REPAIR_OPERATION = "repair_special_character_mismatch"
_LOG_SAMPLE_LIMIT = 20
PREVIEW_SQL_TIMEOUT_SEC = 60.0
PREVIEW_SQL_PROGRESS_OPCODES = 1000
_PROFILE_LABELS = {
    "lang": "Lang",
    "patch": "Patchouli",
    "ftb": "FTB",
    "kubejs": "KubeJS",
    "md": "Markdown",
}


def cache_profile_label(cache_type: str | None) -> str:
    """Display the translator profile selected for a runtime cache type."""
    profile = profile_for_cache_type(cache_type)
    return _PROFILE_LABELS.get(profile, profile.upper())


@dataclass(frozen=True)
class SameSourceAIRepairPreview:
    """The exact, limited candidate snapshot shown before confirmation."""

    total_candidates: int
    entries: tuple[SameSourceAIEntry, ...]
    profile_counts: tuple[tuple[str, int], ...]
    entry_cache_types: tuple[str, ...]
    estimated_batches: int
    source: int = SRC_AI
    mode: str = "same_source_ai"
    sources: tuple[int, ...] = ()

    @property
    def selected_count(self) -> int:
        return len(self.entries)


@dataclass
class SameSourceAIRepairReport:
    candidates: int = 0
    translated: int = 0
    updated: int = 0
    unchanged: int = 0
    flagged: int = 0
    skipped_changed: int = 0
    failed: int = 0
    cache_failed: int = 0
    remaining: int | None = None
    batches: int = 0
    elapsed_sec: float = 0.0
    status: str = "DONE"
    last_error: str | None = None
    mode: str = "same_source_ai"

    def as_dict(self) -> dict[str, Any]:
        operation = (
            _QUALITY_REPAIR_OPERATION if self.mode == "quality_mismatch" else _OPERATION
        )
        return {"operation": operation, **self.__dict__}


def _raise_preview_timeout(deadline: float | None) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError(
            f"譯文修復預覽查詢超過 {PREVIEW_SQL_TIMEOUT_SEC:g} 秒時間預算"
        )


def preview_same_source_ai_retranslation(
    db,
    options: TranslateOptions,
    *,
    deadline: float | None = None,
    mode: str = "same_source_ai",
):
    """Return the exact candidate snapshot; optional monotonic deadline covers SQL and transforms."""
    _raise_preview_timeout(deadline)
    if mode == "quality_mismatch":
        total, entries = db.mismatched_translation_entries(
            options.version,
            list(options.mod_ids),
            limit=options.limit or None,
            check=lambda: _raise_preview_timeout(deadline),
        )
    elif mode == "same_source_ai":
        total = db.count_same_as_source_ai(options.version, list(options.mod_ids))
        _raise_preview_timeout(deadline)
        entries = db.same_as_source_ai_entries(
            options.version,
            list(options.mod_ids),
            limit=options.limit or None,
        )
    else:
        raise ValueError(f"未知既有譯文修復條件：{mode}")
    _raise_preview_timeout(deadline)
    items = _build_retranslation_items(entries, deadline=deadline)
    entry_cache_types = []
    for item in items:
        _raise_preview_timeout(deadline)
        entry_cache_types.append(_retranslation_cache_type(item))
    items = _group_retranslation_items_by_cache_type(items, deadline=deadline)
    _raise_preview_timeout(deadline)
    profile_counts = _cache_type_counts(items, deadline=deadline)
    estimated_batches = plan_batches(
        items, check=lambda: _raise_preview_timeout(deadline)
    )
    _raise_preview_timeout(deadline)
    preview_entries = []
    for entry in entries:
        _raise_preview_timeout(deadline)
        preview_entries.append(entry)
    return SameSourceAIRepairPreview(
        total,
        tuple(preview_entries),
        tuple(profile_counts.items()),
        tuple(entry_cache_types),
        estimated_batches,
        mode=mode,
        sources=tuple(sorted({int(entry.source_id or SRC_AI) for entry in entries})),
    )


def preview_same_source_ai_retranslation_from_path(
    database_path: str | Path,
    priority: tuple[int, ...],
    options: TranslateOptions,
    *,
    mode: str = "same_source_ai",
) -> SameSourceAIRepairPreview:
    """Use a worker-owned read-only connection with bounded SQLite VM execution."""
    preview_db = TranslationDB(
        database_path,
        priority=priority,
        readonly=True,
        create=False,
    )
    deadline = monotonic() + PREVIEW_SQL_TIMEOUT_SEC
    try:
        preview_db._conn.set_progress_handler(
            lambda: int(monotonic() >= deadline), PREVIEW_SQL_PROGRESS_OPCODES
        )
        try:
            return preview_same_source_ai_retranslation(
                preview_db, options, deadline=deadline, mode=mode
            )
        except sqlite3.OperationalError as exc:
            if monotonic() >= deadline and "interrupt" in str(exc).casefold():
                raise TimeoutError(
                    f"譯文修復預覽查詢超過 {PREVIEW_SQL_TIMEOUT_SEC:g} 秒時間預算"
                ) from exc
            raise
    finally:
        preview_db.close()


def _build_retranslation_items(
    entries: Sequence[SameSourceAIEntry], *, deadline: float | None = None
) -> list[dict[str, Any]]:
    rows = []
    for row in entries:
        _raise_preview_timeout(deadline)
        rows.append((row.entry_id, row.kind, row.mod_id, row.key, row.en_us))
    items = build_items(rows, check=lambda: _raise_preview_timeout(deadline))
    for item, row in zip(items, entries, strict=True):
        _raise_preview_timeout(deadline)
        item["_expected_old_zh_tw"] = row.current_ai_translation
        item["_expected_version"] = row.mc_version
        item["_source_id"] = row.source_id if row.source_id is not None else SRC_AI
        item["_expected_revision"] = row.revision
        item["_review_status"] = row.review_status
    return items


def _retranslation_cache_type(item: dict[str, Any]) -> str:
    return str(item.get("cache_type") or "lang")


def _group_retranslation_items_by_cache_type(
    items: list[dict[str, Any]], *, deadline: float | None = None
) -> list[dict[str, Any]]:
    """Stable-group engine items by their actual cache/profile key."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        _raise_preview_timeout(deadline)
        groups.setdefault(_retranslation_cache_type(item), []).append(item)
    grouped = []
    for group in groups.values():
        for item in group:
            _raise_preview_timeout(deadline)
            grouped.append(item)
    return grouped


def _cache_type_counts(
    items: Sequence[dict[str, Any]], *, deadline: float | None = None
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        _raise_preview_timeout(deadline)
        cache_type = _retranslation_cache_type(item)
        counts[cache_type] = counts.get(cache_type, 0) + 1
    return counts


def _format_profile_breakdown(counts: dict[str, int]) -> str:
    return (
        "、".join(
            f"{cache_profile_label(cache_type)} {count:,} 筆"
            for cache_type, count in counts.items()
        )
        or "無"
    )


def _same_batch_prefix(items: list[dict[str, Any]], lm_cfg: dict[str, Any]):
    """Choose a homogeneous, token-budgeted prefix without touching the cache."""
    cache_type = _retranslation_cache_type(items[0])
    homogeneous = 1
    while (
        homogeneous < len(items)
        and _retranslation_cache_type(items[homogeneous]) == cache_type
    ):
        homogeneous += 1
    preferred = _get_default_batch_size(cache_type, None)
    if preferred <= 0:
        preferred = 50
    fit_count = select_batch_size(
        items[:homogeneous],
        profile_for_cache_type(cache_type),
        preferred,
        lm_cfg,
    )
    return items[: max(1, fit_count)], cache_type


def _valid_translation(result: Any, original: dict[str, Any]) -> bool:
    return (
        isinstance(result, dict)
        and result.get("_entry_id") == original.get("_entry_id")
        and result.get("path") == original.get("path")
        and result.get("source_text") == original.get("source_text")
        and isinstance(result.get("text"), str)
        and bool(result["text"].strip())
        and not result.get("_untranslated")
    )


def _cache_finalized_translation(item: dict[str, Any], text: str) -> bool:
    """Update only this finalized result after its database CAS has succeeded."""
    try:
        cache_type = str(item.get("cache_type") or "lang")
        rule = get_default_cache_rules().get(cache_type)
        if rule is None:
            return False
        initialize_translation_cache()
        key = rule.make_key(item)
        return add_to_cache(
            cache_type,
            key,
            item["source_text"],
            text,
            mod=item.get("_mod_id"),
            path=item.get("path"),
        )
    except Exception as exc:  # noqa: BLE001 - cache is best-effort after DB commit
        logger.warning("Mod DB 舊 AI 重翻快取更新失敗：%r", exc)
        return False


def _log(session, text: str, level: str = "info") -> None:
    mirror_session_log(session, logger, text, level, prefix="[Mod DB 譯文修復] ")


def _finalize_batch_results(
    db,
    options: TranslateOptions,
    session,
    batch: list[dict[str, Any]],
    results: list[dict[str, Any]],
    cache_type: str,
    report: SameSourceAIRepairReport,
    cache_types_to_save: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Validate, guard, and persist each result from one engine batch."""
    stop_after_batch = False
    if len(results) > len(batch):
        results = results[: len(batch)]
        report.last_error = "翻譯引擎回傳超過本批筆數的結果"
        stop_after_batch = True

    for original, result in zip(batch, results, strict=False):
        stop_after_batch |= _finalize_candidate(
            db,
            options,
            session,
            original,
            result,
            cache_type,
            report,
            cache_types_to_save,
        )
    return results, stop_after_batch


def _finalize_candidate(
    db, options, session, original, result, cache_type, report, cache_types_to_save
) -> bool:
    """Validate and persist one candidate; return whether the batch must stop."""
    if not _valid_translation(result, original):
        report.failed += 1
        report.last_error = report.last_error or "翻譯結果格式無效，舊譯文已保留"
        return True
    report.translated += 1
    issues = token_issues(original["source_text"], result["text"])
    if issues:
        report.flagged += 1
        source_text = (
            "原來源譯文" if report.mode == "quality_mismatch" else "舊 AI 譯文"
        )
        _log(
            session,
            f"⚠️ 特殊字元不一致，保留{source_text}："
            f"{original['_mod_id']} / {original['path']}：{'、'.join(issues)}",
            "warning",
        )
        return False

    replace = _replace_candidate_translation(db, original, result["text"], report)
    if replace.status == "updated":
        report.updated += 1
        if report.updated <= _LOG_SAMPLE_LIMIT:
            _log(
                session,
                f"✅ {original['_mod_id']} / {original['path']}："
                f"{original['source_text'][:50]} → {result['text'][:50]}",
            )
    elif replace.status == "unchanged":
        report.unchanged += 1
    else:
        report.skipped_changed += 1
        _log(
            session,
            f"⚠️ 資料已變動，跳過覆寫：{original['_mod_id']} / {original['path']}",
            "warning",
        )
        return False

    if options.write_cache and replace.status == "updated":
        if _cache_finalized_translation(original, result["text"]):
            cache_types_to_save.add(cache_type)
        else:
            report.cache_failed += 1
            _log(
                session,
                f"⚠️ 資料庫已更新，但快取寫入未成功："
                f"{original['_mod_id']} / {original['path']}",
                "warning",
            )
    return False


def _replace_candidate_translation(db, original, text, report):
    """Use the mode-specific compare-and-set for this preview snapshot."""
    identity = {
        "expected_version": original["_expected_version"],
        "expected_kind": original["_kind"],
        "expected_mod_id": original["_mod_id"],
        "expected_key": original["path"],
        "expected_en_us": original["source_text"],
    }
    old_text = original["_expected_old_zh_tw"]
    entry_id = original["_entry_id"]
    if report.mode == "quality_mismatch":
        return db.replace_translation_quality_mismatch(
            entry_id,
            original["_source_id"],
            old_text,
            text,
            **identity,
            expected_revision=original["_expected_revision"],
        )
    return db.replace_ai_translation(entry_id, old_text, text, **identity)


def _finish_snapshot(db, options, session, report, tracker, processed, total) -> None:
    report.remaining = _remaining_candidate_count(db, options, report.mode)
    if report.failed or report.last_error:
        report.status = "FAILED"
        session.set_error()
    else:
        report.status = "DONE"
    report.elapsed_sec = tracker.elapsed()
    session.set_progress(processed / max(total, 1))
    level = "warning" if report.failed or report.flagged else "info"
    _log(
        session,
        f"完成：候選 {report.candidates}，更新 {report.updated}，"
        f"仍相同 {report.unchanged}，格式檢查未通過 {report.flagged}，"
        f"資料已變動跳過 {report.skipped_changed}，失敗 {report.failed}，"
        f"範圍內仍符合修復條件 {report.remaining}",
        level,
    )
    if report.last_error:
        _log(session, f"最後一次錯誤：{report.last_error}", "warning")
    session.set_summary(report.as_dict())


def _remaining_candidate_count(db, options, mode: str) -> int:
    if mode == "quality_mismatch":
        count, _ = db.mismatched_translation_entries(
            options.version, list(options.mod_ids), limit=1
        )
        return count
    return db.count_same_as_source_ai(options.version, list(options.mod_ids))


def _handle_batch_completion(
    options,
    session,
    report,
    batch,
    results,
    status,
    cache_types_to_save,
) -> bool:
    """Flush finalized cache writes and decide whether the run must stop."""
    if options.write_cache:
        for cache_type in cache_types_to_save:
            try:
                saved = save_translation_cache(cache_type)
                failure_reason = "快取落盤失敗"
            except Exception as exc:  # noqa: BLE001 - cache failure must not stop DB work
                saved = False
                failure_reason = f"快取落盤拋出例外：{exc!r}"
            if not saved:
                report.cache_failed += 1
                _log(session, f"⚠️ {cache_type} {failure_reason}", "warning")
        cache_types_to_save.clear()

    stop_after_batch = False
    if len(results) < len(batch):
        report.failed += len(batch) - len(results)
        report.last_error = (
            report.last_error or "翻譯未回傳完整本批結果，未回傳項目保留舊譯文"
        )
        stop_after_batch = True
    if str(status or "").upper() not in {"DONE", "AUTO"}:
        report.last_error = report.last_error or f"翻譯引擎回傳狀態：{status}"
        stop_after_batch = True
    return stop_after_batch


def _translate_snapshot(
    db,
    options: TranslateOptions,
    session,
    entries: Sequence[SameSourceAIEntry],
    report: SameSourceAIRepairReport,
    cancelled,
) -> None:
    items, tracker, lm_cfg, sleep_seconds = _initialize_snapshot_run(
        entries, session, report
    )
    total = len(items)
    processed = 0
    cache_types_to_save: set[str] = set()

    while items:
        if cancelled() or is_cancelled():
            raise TaskCancelled()
        batch, cache_type = _same_batch_prefix(items, lm_cfg)
        report.batches += 1
        translated, status = translate_batch_smart(batch, total)
        results, finalize_stop = _finalize_batch_results(
            db,
            options,
            session,
            batch,
            list(translated or []),
            cache_type,
            report,
            cache_types_to_save,
        )

        processed += len(results)
        items = items[len(batch) :]
        completion_stop = _handle_batch_completion(
            options,
            session,
            report,
            batch,
            results,
            status,
            cache_types_to_save,
        )
        stop_after_batch = finalize_stop or completion_stop
        if stop_after_batch:
            report.failed += len(items)
            items.clear()

        if tracker.update(processed):
            live = tracker.live()
            session.set_progress(processed / max(total, 1))
            session.set_summary({**report.as_dict(), "live": live})
            _log(session, f"⏱ {tracker.line()}")

        if stop_after_batch:
            break
        if items and sleep_seconds > 0:
            interruptible_sleep(sleep_seconds)

    _finish_snapshot(db, options, session, report, tracker, processed, total)


def _initialize_snapshot_run(entries, session, report):
    """Group candidates and prepare the rate-limited translator run."""
    items = _group_retranslation_items_by_cache_type(
        _build_retranslation_items(entries)
    )
    total = len(items)
    tracker = RunProgress(total=total, planned_batches=plan_batches(items))
    config = load_config()
    lm_cfg = config.get("lm_translator", {}) if isinstance(config, dict) else {}
    rate_cfg = lm_cfg.get("rate_limit", {}) if isinstance(lm_cfg, dict) else {}
    sleep_seconds = float(rate_cfg.get("sleep_seconds_between_batches", 0.0) or 0.0)
    _log(
        session,
        (
            f"🔎 各來源特殊字元不一致候選 {total:,} 筆"
            if report.mode == "quality_mismatch"
            else f"🔎 舊 AI 機翻修復候選 {total:,} 筆；來源：{source_label(SRC_AI)}"
        )
        + f"；{_format_profile_breakdown(_cache_type_counts(items))}；"
        "略過舊快取直接重新翻譯",
    )
    _log(
        session,
        "🔁 開始修復"
        + (
            "各來源特殊字元不一致譯文"
            if report.mode == "quality_mismatch"
            else "舊 AI 同原文譯文"
        )
        + f"：{total:,} 筆",
    )
    _log(session, tracker.start_line())
    session.set_summary({**report.as_dict(), "live": tracker.live()})
    return items, tracker, lm_cfg, sleep_seconds


def run_moddb_retranslate_service(
    options: TranslateOptions,
    session,
    entries: Sequence[SameSourceAIEntry],
    *,
    mode: str = "same_source_ai",
    manage_session: bool = True,
) -> None:
    """Direct-AI repair for the exact entries shown by the user's preview."""
    ensure_pipeline_logging()
    db = None
    report = SameSourceAIRepairReport(candidates=len(entries), mode=mode)
    operation_label = "特殊字元修復" if mode == "quality_mismatch" else "舊 AI 重翻"
    started_at = monotonic()

    def cancelled() -> bool:
        return bool(getattr(session, "cancel_requested", False))

    try:
        if manage_session:
            session.start()
        UI_LOG_HANDLER.set_session(session)
        db = open_database(create=False)
        if db is None:
            _log(
                session,
                "[錯誤] 無法開啟 Mod 資料庫："
                + (database_problem() or "資料庫尚未建立，請先到「掃描匯入」建立"),
                "error",
            )
            report.status = "FAILED"
            report.last_error = "無法開啟 Mod 資料庫"
            session.set_error()
            return
        _run_repair_candidates(db, options, session, entries, report, mode, cancelled)
    except TaskCancelled:
        report.status = "CANCELLED"
        report.elapsed_sec = monotonic() - started_at
        _log(session, "⏹ 已取消；已完成並提交的項目保留，其他舊譯文未更動", "warning")
        if db is not None:
            report.remaining = _remaining_candidate_count(db, options, mode)
        session.set_summary(report.as_dict())
    except Exception as exc:  # noqa: BLE001 - service boundary reports failure in TaskSession
        logger.error(
            "Mod DB %s 失敗: %s\n%s",
            operation_label,
            exc,
            traceback.format_exc(),
            extra={"ui_mirrored": True},
        )
        report.status = "FAILED"
        report.failed += max(0, report.candidates - report.translated - report.failed)
        report.last_error = f"{type(exc).__name__}: {exc}"
        session.set_error()
        add_log_unmirrored(session, f"[致命錯誤] {operation_label}失敗：{exc}", "error")
        session.set_summary(report.as_dict())
    finally:
        report.elapsed_sec = max(report.elapsed_sec, monotonic() - started_at)
        if db is not None:
            try:
                warm_stats_quietly(db)
            finally:
                db.close()
        UI_LOG_HANDLER.set_session(None)
        if manage_session:
            session.finish()


def _run_repair_candidates(db, options, session, entries, report, mode, cancelled):
    """Run the exact preview snapshot, including the no-candidate completion path."""
    if not entries:
        report.remaining = _remaining_candidate_count(db, options, mode)
        message = (
            "沒有符合條件的來源譯文特殊字元不一致項目"
            if mode == "quality_mismatch"
            else "沒有符合條件的舊 AI 同原文譯文"
        )
        _log(session, message)
        session.set_progress(1.0)
        session.set_summary(report.as_dict())
        return

    _log(session, f"🔎 找到 {len(entries):,} 筆預覽候選，準備直接送 AI（略過舊快取）")
    with cancel_scope(cancelled):
        _translate_snapshot(db, options, session, entries, report, cancelled)


__all__ = [
    "SameSourceAIRepairPreview",
    "SameSourceAIRepairReport",
    "cache_profile_label",
    "preview_same_source_ai_retranslation",
    "run_moddb_retranslate_service",
]
