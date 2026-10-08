"""Explicit repair of existing AI translations that currently equal their source text."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.moddb_service import (
    database_problem,
    open_database,
    warm_stats_quietly,
)
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
from app.views.moddb.formatting import (
    source_label,
    token_issues,
)
from translation_tool.core.lm_batch_budget import (
    profile_for_cache_type,
    select_batch_size,
)
from translation_tool.core.lm_translator_main import translate_batch_smart
from translation_tool.core.lm_translator_shared_cache import get_default_cache_rules
from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size
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
_LOG_SAMPLE_LIMIT = 20
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
    remaining: int = 0
    batches: int = 0
    elapsed_sec: float = 0.0
    status: str = "DONE"
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"operation": _OPERATION, **self.__dict__}


def preview_same_source_ai_retranslation(db, options: TranslateOptions):
    """Return total matches plus the exact version/mod/limit snapshot to confirm."""
    total = db.count_same_as_source_ai(options.version, list(options.mod_ids))
    entries = db.same_as_source_ai_entries(
        options.version,
        list(options.mod_ids),
        limit=options.limit or None,
    )
    items = _build_retranslation_items(entries)
    entry_cache_types = tuple(_retranslation_cache_type(item) for item in items)
    items = _group_retranslation_items_by_cache_type(items)
    return SameSourceAIRepairPreview(
        total,
        tuple(entries),
        tuple(_cache_type_counts(items).items()),
        entry_cache_types,
        plan_batches(items),
    )


def _build_retranslation_items(
    entries: Sequence[SameSourceAIEntry],
) -> list[dict[str, Any]]:
    rows = [(row.entry_id, row.kind, row.mod_id, row.key, row.en_us) for row in entries]
    items = build_items(rows)
    for item, row in zip(items, entries, strict=True):
        item["_expected_old_zh_tw"] = row.current_ai_translation
        item["_expected_version"] = row.mc_version
    return items


def _retranslation_cache_type(item: dict[str, Any]) -> str:
    return str(item.get("cache_type") or "lang")


def _group_retranslation_items_by_cache_type(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stable-group engine items by their actual cache/profile key."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(_retranslation_cache_type(item), []).append(item)
    return [item for group in groups.values() for item in group]


def _cache_type_counts(items: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
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
    mirror_session_log(session, logger, text, level, prefix="[Mod DB 舊 AI 重翻] ")


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
        if not _valid_translation(result, original):
            report.failed += 1
            report.last_error = report.last_error or "翻譯結果格式無效，舊譯文已保留"
            stop_after_batch = True
            continue

        report.translated += 1
        issues = token_issues(original["source_text"], result["text"])
        if issues:
            report.flagged += 1
            _log(
                session,
                f"⚠️ 特殊字元不一致，保留舊 AI 譯文："
                f"{original['_mod_id']} / {original['path']}：{'、'.join(issues)}",
                "warning",
            )
            continue

        replace = db.replace_ai_translation(
            original["_entry_id"],
            original["_expected_old_zh_tw"],
            result["text"],
            expected_version=original["_expected_version"],
            expected_kind=original["_kind"],
            expected_mod_id=original["_mod_id"],
            expected_key=original["path"],
            expected_en_us=original["source_text"],
        )
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
            continue

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
    return results, stop_after_batch


def _finish_snapshot(db, options, session, report, tracker, processed, total) -> None:
    report.remaining = db.count_same_as_source_ai(
        options.version, list(options.mod_ids)
    )
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
        f"範圍內仍符合條件 {report.remaining}",
        level,
    )
    if report.last_error:
        _log(session, f"最後一次錯誤：{report.last_error}", "warning")
    session.set_summary(report.as_dict())


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
    items = _group_retranslation_items_by_cache_type(
        _build_retranslation_items(entries)
    )
    total = len(items)
    tracker = RunProgress(total=total, planned_batches=plan_batches(items))
    config = load_config()
    lm_cfg = config.get("lm_translator", {}) if isinstance(config, dict) else {}
    rate_cfg = lm_cfg.get("rate_limit", {}) if isinstance(lm_cfg, dict) else {}
    sleep_seconds = float(rate_cfg.get("sleep_seconds_between_batches", 0.0) or 0.0)
    processed = 0
    cache_types_to_save: set[str] = set()

    _log(
        session,
        f"🔎 舊 AI 機翻修復候選 {total:,} 筆；來源：{source_label(SRC_AI)}；"
        f"{_format_profile_breakdown(_cache_type_counts(items))}；"
        "略過舊快取直接重新翻譯",
    )
    _log(session, f"🔁 開始重新翻譯舊 AI 同原文譯文：{total:,} 筆")
    _log(session, tracker.start_line())
    session.set_summary({**report.as_dict(), "live": tracker.live()})

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


def run_moddb_retranslate_service(
    options: TranslateOptions,
    session,
    entries: Sequence[SameSourceAIEntry],
    *,
    manage_session: bool = True,
) -> None:
    """Direct-AI repair for the exact entries shown by the user's preview."""
    ensure_pipeline_logging()
    db = None
    report = SameSourceAIRepairReport(candidates=len(entries))
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
        if not entries:
            report.remaining = db.count_same_as_source_ai(
                options.version, list(options.mod_ids)
            )
            _log(session, "沒有符合條件的舊 AI 同原文譯文")
            session.set_progress(1.0)
            session.set_summary(report.as_dict())
            return
        _log(
            session, f"🔎 找到 {len(entries):,} 筆預覽候選，準備直接送 AI（略過舊快取）"
        )
        with cancel_scope(cancelled):
            _translate_snapshot(db, options, session, entries, report, cancelled)
    except TaskCancelled:
        report.status = "CANCELLED"
        report.elapsed_sec = monotonic() - started_at
        _log(session, "⏹ 已取消；已完成並提交的項目保留，其他舊譯文未更動", "warning")
        if db is not None:
            report.remaining = db.count_same_as_source_ai(
                options.version, list(options.mod_ids)
            )
        session.set_summary(report.as_dict())
    except Exception as exc:  # noqa: BLE001 - service boundary reports failure in TaskSession
        logger.error(
            "Mod DB 舊 AI 重翻失敗: %s\n%s",
            exc,
            traceback.format_exc(),
            extra={"ui_mirrored": True},
        )
        report.status = "FAILED"
        report.failed += max(0, report.candidates - report.translated - report.failed)
        report.last_error = f"{type(exc).__name__}: {exc}"
        session.set_error()
        add_log_unmirrored(session, f"[致命錯誤] 舊 AI 重翻失敗：{exc}", "error")
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


__all__ = [
    "SameSourceAIRepairPreview",
    "SameSourceAIRepairReport",
    "cache_profile_label",
    "preview_same_source_ai_retranslation",
    "run_moddb_retranslate_service",
]
