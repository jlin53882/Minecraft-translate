"""Preview and repair existing source translations using direct AI output."""

from __future__ import annotations

import logging
import sqlite3
import time
import traceback
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any

from app.services_impl import moddb_repair_review_store
from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.moddb_service import (
    database_problem,
    open_database,
    warm_stats_quietly,
)
from app.services_impl.moddb_source_service import source_catalog_for, source_label
from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    build_items,
)
from app.services_impl.pipelines._pipeline_logging import (
    ensure_pipeline_logging,
    mirror_session_log,
)
from app.tasks.task_session import add_log_unmirrored
from translation_tool.core.format_diagnostics import analyze_format_diagnostics
from translation_tool.core.lm_batch_budget import (
    BudgetConfig,
    get_tracker,
    profile_for_cache_type,
    select_batch_size,
)
from translation_tool.core.lm_batch_planner import (
    count_planned_batches,
    group_items_by_batch_key,
    next_batch,
)
from translation_tool.core.lm_moddb_context import attach_moddb_context
from translation_tool.core.lm_session import TranslationSessionState
from translation_tool.core.lm_translator_main import (
    estimate_batch_fixed_input_tokens,
    translate_batch_smart,
)
from translation_tool.core.lm_translator_shared_cache import get_default_cache_rules
from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.models import SameSourceAIEntry
from translation_tool.translation_db.quality import (
    repair_input_issues_for_kind,
    translation_issues,
    repair_output_issues_for_kind,
)
from translation_tool.translation_db.run_progress import format_repair_live
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.utils.cache_manager import (
    add_to_cache_with_receipt,
    initialize_translation_cache,
    save_translation_cache_keys,
)
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    is_cancelled,
)
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)

_OPERATION = "retranslate_same_source_ai"
_QUALITY_REPAIR_OPERATION = "repair_special_character_mismatch"
_LOG_SAMPLE_LIMIT = 20
_FLAGGED_DETAIL_LIMIT = 200
PREVIEW_SQL_TIMEOUT_SEC = 60.0
PREVIEW_SQL_PROGRESS_OPCODES = 1000
_PROFILE_LABELS = {
    "lang": "Lang",
    "patch": "Patchouli",
    "ftb": "FTB",
    "kubejs": "KubeJS",
    "md": "Markdown",
}


class _SequenceSuffixView(Sequence[dict[str, Any]]):
    """Read-only suffix view that avoids copying the full remaining item list."""

    def __init__(self, items: Sequence[dict[str, Any]], start: int) -> None:
        self._items = items
        self._start = max(0, int(start))

    def __len__(self) -> int:
        return max(0, len(self._items) - self._start)

    def __getitem__(self, index: int):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return [
                self._items[self._start + offset] for offset in range(start, stop, step)
            ]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._items[self._start + index]


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
    input_newline_mismatch_candidates: int = 0
    input_newline_mismatch_with_other_hard_issues: int = 0
    skipped_newline_only_candidates: int = 0
    newline_only_included: bool = False

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
    flagged_entries: list[dict[str, Any]] = field(default_factory=list)
    flagged_entries_omitted: int = 0
    input_newline_mismatch_candidates: int = 0
    input_newline_mismatch_with_other_hard_issues: int = 0
    skipped_newline_only_candidates: int = 0
    strict_newline: bool = False
    sync_other_versions: bool = False
    synced_other_versions: int = 0
    shadowed: int = 0
    unchanged_shadowed: int = 0
    review_run_id: str = ""
    review_store_path: str = ""
    reviewable_results: int = 0
    skipped_changed: int = 0
    failed: int = 0
    cache_failed: int = 0
    cache_add_failed: int = 0
    cache_save_failed: int = 0
    cache_keys_changed: int | None = None
    cache_keys_saved: int | None = None
    cache_keys_superseded: int = 0
    cache_stats_note: str = ""
    ai_submitted_items: int = 0
    ai_validated_items: int = 0
    ai_submission_unit: str = "items_passed_to_translate_batch_smart"
    candidate_profile_counts: dict[str, int] = field(default_factory=dict)
    processed_candidates: int = 0
    attempted_candidates: int = 0
    not_submitted_candidates: int = 0
    unprocessed_candidates: int = 0
    log_details_omitted: int = 0
    _log_details_emitted: int = field(default=0, repr=False)
    _log_details_omitted_by_category: dict[str, int] = field(
        default_factory=dict, repr=False
    )
    _cache_changed_keys: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _cache_saved_keys: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _cache_save_failed_keys: set[tuple[str, str]] = field(
        default_factory=set, repr=False
    )
    _cache_superseded_keys: set[tuple[str, str]] = field(
        default_factory=set, repr=False
    )
    _cache_saving_disabled: bool = field(default=False, repr=False)
    remaining: int | None = None
    batches: int = 0
    planned_batches: int = 0
    elapsed_sec: float = 0.0
    status: str = "DONE"
    last_error: str | None = None
    mode: str = "same_source_ai"

    def as_dict(self) -> dict[str, Any]:
        operation = (
            _QUALITY_REPAIR_OPERATION if self.mode == "quality_mismatch" else _OPERATION
        )
        return {
            "operation": operation,
            **{
                key: value
                for key, value in self.__dict__.items()
                if not key.startswith("_")
            },
        }


class _RepairRunProgress:
    """Track source-row progress and profile-specific ETA samples."""

    def __init__(
        self,
        items: Sequence[dict[str, Any]],
        *,
        total_candidates: int,
        lm_cfg: dict[str, Any],
        started_mono: float | None = None,
        started_wall: float | None = None,
    ) -> None:
        self.total_candidates = total_candidates
        self.lm_cfg = lm_cfg
        self.started_mono = monotonic() if started_mono is None else started_mono
        self.started_wall = time.time() if started_wall is None else started_wall
        self.batches_done = 0
        self.profile_batches_done: dict[str, int] = {}
        self.profile_candidate_totals: dict[str, int] = {}
        self.profile_candidate_processed: dict[str, int] = {}
        self.profile_items_submitted: dict[str, int] = {}
        self.profile_sample_items: dict[str, int] = {}
        self.profile_sample_seconds: dict[str, float] = {}
        self._items = items
        self._profile_ranges: dict[str, list[tuple[int, int]]] = {}
        for index, item in enumerate(items):
            cache_type = _retranslation_cache_type(item)
            ranges = self._profile_ranges.setdefault(cache_type, [])
            if ranges and ranges[-1][1] == index:
                ranges[-1] = (ranges[-1][0], index + 1)
            else:
                ranges.append((index, index + 1))
            self.profile_candidate_totals[cache_type] = (
                self.profile_candidate_totals.get(cache_type, 0) + 1
            )
        self._planned_batches_by_profile: dict[str, int] = {}
        self._planned_first_sizes: dict[str, int] = {}
        self._planned_signatures: dict[str, tuple[Any, ...]] = {}
        self._last_batch_sizes: dict[str, int] = {}
        self._planned_canonical_batches = 0
        self._planned_canonical_first_size = 0
        self._planned_canonical_submitted = 0
        self._planned_canonical_hard_signature: tuple[Any, ...] = ()
        self._planned_canonical_session_signature: tuple[Any, ...] = ()
        self._canonical_item_cache_types = tuple(
            str(item.get("cache_type") or "lang") for item in items
        )
        self._canonical_cache_type_order = tuple(
            dict.fromkeys(self._canonical_item_cache_types)
        )
        self._canonical_item_sessions = tuple(
            item.get("_translation_session")
            if isinstance(item.get("_translation_session"), TranslationSessionState)
            else None
            for item in items
        )
        self._canonical_remaining_cache_types = Counter(
            self._canonical_item_cache_types
        )
        self._canonical_remaining_sessions = Counter(
            id(session)
            for session in self._canonical_item_sessions
            if session is not None
        )
        self._canonical_sessions = {
            id(session): session
            for session in self._canonical_item_sessions
            if session is not None
        }
        self._canonical_metadata_submitted = 0
        for cache_type, ranges in self._profile_ranges.items():
            batches, first_size = _plan_profile_ranges(
                self._items, cache_type, ranges, self.lm_cfg
            )
            self._planned_batches_by_profile[cache_type] = batches
            self._planned_first_sizes[cache_type] = first_size
            self._planned_signatures[cache_type] = _batch_plan_signature(
                cache_type, self.lm_cfg
            )
        self._replan_canonical(None)

    def update(
        self,
        cache_type: str,
        *,
        count: int,
        elapsed: float,
    ) -> None:
        self.batches_done += 1
        self.profile_batches_done[cache_type] = (
            self.profile_batches_done.get(cache_type, 0) + 1
        )
        self.profile_candidate_processed[cache_type] = (
            self.profile_candidate_processed.get(cache_type, 0) + count
        )
        self.profile_items_submitted[cache_type] = (
            self.profile_items_submitted.get(cache_type, 0) + count
        )
        self.profile_sample_items[cache_type] = (
            self.profile_sample_items.get(cache_type, 0) + count
        )
        self.profile_sample_seconds[cache_type] = self.profile_sample_seconds.get(
            cache_type, 0.0
        ) + max(0.0, elapsed)
        self._last_batch_sizes[cache_type] = count
        self._advance_canonical_plan(count)

    def live(
        self,
        *,
        processed: int = 0,
        now_mono: float | None = None,
        now_wall: float | None = None,
        check=None,
    ) -> dict[str, Any]:
        now_mono = monotonic() if now_mono is None else now_mono
        now_wall = time.time() if now_wall is None else now_wall
        remaining, remaining_candidates, remaining_batches_by_profile = (
            self._remaining_profile_state(check)
        )
        remaining_batches = self._remaining_canonical_batches(check)
        eta = 0.0
        unknown_profiles = []
        for cache_type, count in remaining.items():
            sample_items = self.profile_sample_items.get(cache_type, 0)
            if count and sample_items <= 0:
                unknown_profiles.append(cache_profile_label(cache_type))
                continue
            if count:
                eta += (self.profile_sample_seconds[cache_type] / sample_items) * count
        if unknown_profiles:
            eta_value = None
            eta_note = f"{', '.join(unknown_profiles)} 尚無耗時樣本"
        else:
            eta_value = eta
            eta_note = ""

        profile_progress = {
            cache_type: {
                "label": cache_profile_label(cache_type),
                "processed_candidates": self.profile_candidate_processed.get(
                    cache_type, 0
                ),
                "total_candidates": self.profile_candidate_totals[cache_type],
                "remaining_candidates": remaining_candidates.get(cache_type, 0),
                "ai_submitted_items": self.profile_items_submitted.get(cache_type, 0),
                "completed_batches": self.profile_batches_done.get(cache_type, 0),
                "planned_remaining_batches": remaining_batches_by_profile.get(
                    cache_type, 0
                ),
            }
            for cache_type in self.profile_candidate_totals
        }
        return {
            "batch_done": self.batches_done,
            "batch_est": self.batches_done + remaining_batches,
            "planned_batches_done": self.batches_done,
            "planned_batches_total": self.batches_done + remaining_batches,
            "actual_requests": self.batches_done,
            "ai_completed": sum(self.profile_items_submitted.values()),
            "ai_total": self.total_candidates,
            "source_processed": max(0, min(processed, self.total_candidates)),
            "source_total": self.total_candidates,
            "processed": max(0, min(processed, self.total_candidates)),
            "total": self.total_candidates,
            "elapsed_sec": max(0.0, now_mono - self.started_mono),
            "eta_sec": eta_value,
            "eta_note": eta_note,
            "finish_ts": None if eta_value is None else now_wall + eta_value,
            "started_ts": self.started_wall,
            "updated_ts": now_wall,
            "started_mono": self.started_mono,
            "updated_mono": now_mono,
            "profile_progress": profile_progress,
        }

    def _remaining_profile_state(self, check):
        remaining: dict[str, int] = {}
        remaining_candidates: dict[str, int] = {}
        remaining_batches_by_profile = {}
        for cache_type, total_items in self.profile_candidate_totals.items():
            if check is not None:
                check()
            submitted = self.profile_items_submitted.get(cache_type, 0)
            remaining_count = max(0, total_items - submitted)
            if remaining_count:
                remaining[cache_type] = remaining_count
                remaining_candidates[cache_type] = max(
                    0,
                    self.profile_candidate_totals[cache_type]
                    - self.profile_candidate_processed.get(cache_type, 0),
                )
            remaining_batches_by_profile[cache_type] = self._remaining_batch_plan(
                cache_type, submitted, remaining_count, check
            )
        return remaining, remaining_candidates, remaining_batches_by_profile

    def start_line(self, items: Sequence[dict[str, Any]]) -> str:
        counts = _cache_type_counts(items)
        planned = self._remaining_canonical_batches(None)
        return (
            f"📦 批次規劃｜開始時預估下界 {planned:,} 批，共 {len(items):,} 筆需由 AI 處理"
            f"（{_format_profile_breakdown(counts)}）"
        )

    def line(self, live: dict[str, Any]) -> str:
        return format_repair_live(live)

    def elapsed(self) -> float:
        return max(0.0, monotonic() - self.started_mono)

    def _remaining_canonical_batches(self, check) -> int:
        """Return the cached canonical plan, refreshing only when its inputs change."""
        submitted = sum(self.profile_items_submitted.values())
        if submitted != self._planned_canonical_submitted:
            self._replan_canonical(check)
            return self._planned_canonical_batches

        hard_signature, session_signature = self._canonical_plan_signatures(submitted)
        if hard_signature != self._planned_canonical_hard_signature:
            self._replan_canonical(check)
        elif session_signature != self._planned_canonical_session_signature:
            first_size = self._first_canonical_batch_size(submitted, check=check)
            if first_size != self._planned_canonical_first_size:
                self._replan_canonical(check)
            else:
                self._planned_canonical_session_signature = session_signature
        return self._planned_canonical_batches

    def _canonical_fixed_input_tokens(self):
        return (
            partial(estimate_batch_fixed_input_tokens, lm_cfg=self.lm_cfg)
            if self.lm_cfg.get("token_budget_enabled", True)
            else None
        )

    def _canonical_plan_signatures(self, submitted: int):
        self._sync_canonical_metadata(submitted)
        cache_types = tuple(
            cache_type
            for cache_type in self._canonical_cache_type_order
            if self._canonical_remaining_cache_types.get(cache_type, 0) > 0
        )
        hard_signature = tuple(
            (cache_type, _batch_plan_signature(cache_type, self.lm_cfg))
            for cache_type in cache_types
        )
        sessions = []
        for session_id, session in self._canonical_sessions.items():
            if self._canonical_remaining_sessions.get(session_id, 0) <= 0:
                continue
            sessions.append(
                (
                    session_id,
                    getattr(session, "generation", 0),
                    len(session.history),
                    session.max_history_tokens,
                )
            )
        return hard_signature, tuple(sessions)

    def _sync_canonical_metadata(self, submitted: int) -> None:
        """Advance suffix metadata without rescanning the remaining item list."""
        target = max(0, min(int(submitted), len(self._items)))
        while self._canonical_metadata_submitted < target:
            index = self._canonical_metadata_submitted
            cache_type = self._canonical_item_cache_types[index]
            remaining = self._canonical_remaining_cache_types[cache_type] - 1
            if remaining:
                self._canonical_remaining_cache_types[cache_type] = remaining
            else:
                self._canonical_remaining_cache_types.pop(cache_type, None)
            session = self._canonical_item_sessions[index]
            if session is not None:
                session_id = id(session)
                remaining_sessions = self._canonical_remaining_sessions[session_id] - 1
                if remaining_sessions:
                    self._canonical_remaining_sessions[session_id] = remaining_sessions
                else:
                    self._canonical_remaining_sessions.pop(session_id, None)
            self._canonical_metadata_submitted += 1

    def _first_canonical_batch_size(self, submitted: int, *, check=None) -> int:
        if submitted >= len(self._items):
            return 0
        batch, _cache_type = next_batch(
            self._items,
            start=submitted,
            lm_cfg=self.lm_cfg,
            batch_size_for_type=lambda cache_type: _get_default_batch_size(
                cache_type, None
            ),
            fixed_input_tokens_for_batch=self._canonical_fixed_input_tokens(),
            select_size=select_batch_size,
            check=lambda: _check_batch_planning(None, check),
        )
        return len(batch)

    def _replan_canonical(self, check) -> None:
        submitted = sum(self.profile_items_submitted.values())
        remaining = _SequenceSuffixView(self._items, submitted)
        fixed = self._canonical_fixed_input_tokens()
        first_batch, _cache_type = next_batch(
            remaining,
            lm_cfg=self.lm_cfg,
            batch_size_for_type=lambda cache_type: _get_default_batch_size(
                cache_type, None
            ),
            fixed_input_tokens_for_batch=fixed,
            select_size=select_batch_size,
            check=lambda: _check_batch_planning(None, check),
        )
        remaining_after_first = _SequenceSuffixView(
            self._items, submitted + len(first_batch)
        )
        batches = 0
        if first_batch:
            batches = 1 + count_planned_batches(
                remaining_after_first,
                lm_cfg=self.lm_cfg,
                batch_size_for_type=lambda cache_type: _get_default_batch_size(
                    cache_type, None
                ),
                fixed_input_tokens_for_batch=fixed,
                select_size=select_batch_size,
                check=lambda: _check_batch_planning(None, check),
            )
        hard_signature, session_signature = self._canonical_plan_signatures(submitted)
        self._planned_canonical_batches = batches
        self._planned_canonical_first_size = len(first_batch)
        self._planned_canonical_submitted = submitted
        self._planned_canonical_hard_signature = hard_signature
        self._planned_canonical_session_signature = session_signature

    def _advance_canonical_plan(self, count: int) -> None:
        submitted = sum(self.profile_items_submitted.values())
        hard_signature, session_signature = self._canonical_plan_signatures(submitted)
        if (
            count != self._planned_canonical_first_size
            or hard_signature != self._planned_canonical_hard_signature
        ):
            self._replan_canonical(None)
            return
        self._planned_canonical_batches = max(0, self._planned_canonical_batches - 1)
        self._planned_canonical_submitted = submitted
        self._planned_canonical_hard_signature = hard_signature
        self._planned_canonical_session_signature = session_signature
        self._planned_canonical_first_size = self._first_canonical_batch_size(submitted)

    def _remaining_batch_plan(
        self, cache_type: str, submitted: int, remaining_count: int, check
    ) -> int:
        if not remaining_count:
            self._planned_batches_by_profile[cache_type] = 0
            self._planned_first_sizes[cache_type] = 0
            self._last_batch_sizes.pop(cache_type, None)
            return 0

        signature = _batch_plan_signature(cache_type, self.lm_cfg)
        last_batch_size = self._last_batch_sizes.pop(cache_type, None)
        signature_changed = signature != self._planned_signatures[cache_type]
        batch_size_changed = (
            last_batch_size is not None
            and last_batch_size != self._planned_first_sizes[cache_type]
        )
        if signature_changed or batch_size_changed:
            ranges = _remaining_profile_ranges(
                self._profile_ranges[cache_type], submitted
            )
            batches, first_size = _plan_profile_ranges(
                self._items, cache_type, ranges, self.lm_cfg, check=check
            )
            self._planned_batches_by_profile[cache_type] = batches
            self._planned_first_sizes[cache_type] = first_size
            self._planned_signatures[cache_type] = signature
        elif last_batch_size is not None:
            self._planned_batches_by_profile[cache_type] = max(
                0, self._planned_batches_by_profile[cache_type] - 1
            )
            ranges = _remaining_profile_ranges(
                self._profile_ranges[cache_type], submitted
            )
            self._planned_first_sizes[cache_type] = _first_profile_batch_size(
                self._items,
                cache_type,
                ranges,
                self.lm_cfg,
                check=check,
            )
        return self._planned_batches_by_profile[cache_type]


def _raise_preview_timeout(deadline: float | None) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError(
            f"譯文修復預覽查詢超過 {PREVIEW_SQL_TIMEOUT_SEC:g} 秒時間預算"
        )


def _limited_repair_snapshot(db, options, deadline):
    """Count every eligible effective row; keep only the first ``limit`` for the run."""
    limit = options.limit if options.limit and options.limit > 0 else None
    chosen: list[SameSourceAIEntry] = []
    eligible = newline_mismatch_count = newline_mismatch_with_hard_issues = (
        skipped_newline_only
    ) = 0
    for entry, newline_mismatch, mixed in db.iter_repairable_translation_entries(
        options.version,
        list(options.mod_ids),
        check=lambda: _raise_preview_timeout(deadline),
    ):
        _raise_preview_timeout(deadline)
        newline_mismatch_count += int(newline_mismatch)
        newline_mismatch_with_hard_issues += int(newline_mismatch and mixed)
        if newline_mismatch and not mixed and not options.repair_newline_only:
            skipped_newline_only += 1
            continue
        eligible += 1
        if limit is None or len(chosen) < limit:
            chosen.append(entry)
    items = _group_retranslation_items_by_cache_type(
        _build_retranslation_items(chosen, deadline=deadline), deadline=deadline
    )
    rows_by_key = {
        (entry.entry_id, int(entry.source_id or SRC_AI)): entry for entry in chosen
    }
    selected_entries = [
        rows_by_key[(int(item["_entry_id"]), int(item.get("_source_id", SRC_AI)))]
        for item in items
    ]
    return (
        eligible,
        newline_mismatch_count,
        newline_mismatch_with_hard_issues,
        skipped_newline_only,
        selected_entries,
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
    skipped_newline_only = 0
    if mode == "quality_mismatch":
        (
            total,
            newline_mismatch_count,
            newline_mismatch_with_hard_issues,
            skipped_newline_only,
            entries,
        ) = _limited_repair_snapshot(db, options, deadline)
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
    attach_moddb_context(
        items,
        operation_id=f"retranslate-preview:{id(db)}",
        mc_version=options.version,
        reviewed_glossary_loader=db.reviewed_lang_glossary,
    )
    items = _group_retranslation_items_by_cache_type(items, deadline=deadline)
    _raise_preview_timeout(deadline)
    entry_cache_types = [_retranslation_cache_type(item) for item in items]
    profile_counts = _cache_type_counts(items, deadline=deadline)
    lm_cfg = _load_lm_config()
    estimated_batches = _count_planned_batches(items, lm_cfg, deadline=deadline)
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
        input_newline_mismatch_candidates=(
            newline_mismatch_count if mode == "quality_mismatch" else 0
        ),
        input_newline_mismatch_with_other_hard_issues=(
            newline_mismatch_with_hard_issues if mode == "quality_mismatch" else 0
        ),
        skipped_newline_only_candidates=(
            skipped_newline_only if mode == "quality_mismatch" else 0
        ),
        newline_only_included=mode == "quality_mismatch"
        and options.repair_newline_only,
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
    """Stable-group engine items by cache type and module/kind boundary."""
    return group_items_by_batch_key(
        items, check=lambda: _raise_preview_timeout(deadline)
    )


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
    """Choose the same homogeneous, token-budgeted prefix as preview planning."""
    fixed = (
        partial(estimate_batch_fixed_input_tokens, lm_cfg=lm_cfg)
        if lm_cfg.get("token_budget_enabled", True)
        else None
    )
    return next_batch(
        items,
        lm_cfg=lm_cfg,
        batch_size_for_type=lambda cache_type: _get_default_batch_size(
            cache_type, None
        ),
        fixed_input_tokens_for_batch=fixed,
        select_size=select_batch_size,
    )


def _load_lm_config() -> dict[str, Any]:
    config = load_config()
    lm_cfg = config.get("lm_translator", {}) if isinstance(config, dict) else {}
    return lm_cfg if isinstance(lm_cfg, dict) else {}


def _configured_model_label(lm_cfg: dict[str, Any]) -> str:
    """Return a safe display label; the translator may still rotate models on retry."""
    provider = str(lm_cfg.get("provider") or "gemini").lower()
    if provider == "chatgpt":
        return str(lm_cfg.get("chatgpt_model") or "ChatGPT 模型未設定")
    models = lm_cfg.get("models")
    if isinstance(models, dict):
        enabled = [
            str(name)
            for name, values in models.items()
            if isinstance(values, dict) and values.get("enabled")
        ]
        if enabled:
            return "、".join(enabled)
    return f"{provider} 模型池"


def _count_planned_batches(
    items: Sequence[dict[str, Any]],
    lm_cfg: dict[str, Any],
    *,
    deadline: float | None = None,
    check=None,
) -> int:
    """Count the exact windows used by ``_same_batch_prefix``."""
    fixed = (
        partial(estimate_batch_fixed_input_tokens, lm_cfg=lm_cfg)
        if lm_cfg.get("token_budget_enabled", True)
        else None
    )
    return count_planned_batches(
        items,
        lm_cfg=lm_cfg,
        batch_size_for_type=lambda cache_type: _get_default_batch_size(
            cache_type, None
        ),
        fixed_input_tokens_for_batch=fixed,
        select_size=select_batch_size,
        check=lambda: _check_batch_planning(deadline, check),
    )


def _count_planned_batches_by_profile(
    items: Sequence[dict[str, Any]],
    lm_cfg: dict[str, Any],
    *,
    deadline: float | None = None,
    check=None,
) -> dict[str, int]:
    """Plan contiguous profile ranges with bounded slices and repeated checks."""
    counts: dict[str, int] = {}
    offset = 0
    total = len(items)
    while offset < total:
        _check_batch_planning(deadline, check)
        cache_type = _retranslation_cache_type(items[offset])
        profile_end = offset + 1
        while profile_end < total:
            if profile_end % 128 == 0:
                _check_batch_planning(deadline, check)
            if _retranslation_cache_type(items[profile_end]) != cache_type:
                break
            profile_end += 1
        profile_batches, _first_size = _plan_profile_range(
            items,
            cache_type,
            offset,
            profile_end,
            lm_cfg,
            deadline=deadline,
            check=check,
        )
        counts[cache_type] = counts.get(cache_type, 0) + profile_batches
        offset = profile_end
    _check_batch_planning(deadline, check)
    return counts


def _plan_profile_range(
    items, cache_type, start, end, lm_cfg, *, deadline=None, check=None
) -> tuple[int, int]:
    batches = 0
    first_size = 0
    offset = start
    while offset < end:
        _check_batch_planning(deadline, check)
        size = _profile_batch_size(
            items, cache_type, offset, end, lm_cfg, deadline=deadline, check=check
        )
        if not first_size:
            first_size = size
        offset += size
        batches += 1
    return batches, first_size


def _profile_batch_size(
    items, cache_type, offset, end, lm_cfg, *, deadline=None, check=None
) -> int:
    _check_batch_planning(deadline, check)
    preferred = _get_default_batch_size(cache_type, None)
    if preferred <= 0:
        preferred = 50
    window_end = min(end, offset + preferred)
    window = items[offset:window_end]
    fit_count = select_batch_size(
        window, profile_for_cache_type(cache_type), preferred, lm_cfg
    )
    return max(1, min(fit_count, len(window)))


def _first_profile_batch_size(items, cache_type, ranges, lm_cfg, *, check=None):
    if not ranges:
        return 0
    start, end = ranges[0]
    return _profile_batch_size(items, cache_type, start, end, lm_cfg, check=check)


def _plan_profile_ranges(items, cache_type, ranges, lm_cfg, *, check=None):
    batches = 0
    first_size = 0
    for start, end in ranges:
        range_batches, range_first_size = _plan_profile_range(
            items, cache_type, start, end, lm_cfg, check=check
        )
        batches += range_batches
        if not first_size:
            first_size = range_first_size
    return batches, first_size


def _remaining_profile_ranges(ranges, submitted):
    remaining_ranges = []
    skipped = submitted
    for start, end in ranges:
        length = end - start
        if skipped >= length:
            skipped -= length
            continue
        remaining_ranges.append((start + skipped, end))
        skipped = 0
    return remaining_ranges


def _batch_plan_signature(cache_type, lm_cfg):
    preferred = _get_default_batch_size(cache_type, None)
    if preferred <= 0:
        preferred = 50
    profile = profile_for_cache_type(cache_type)
    return preferred, get_tracker(profile).planning_signature(
        BudgetConfig.from_config(lm_cfg)
    )


def _check_batch_planning(deadline, check) -> None:
    _raise_preview_timeout(deadline)
    if check is not None:
        check()


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


def _cache_finalized_translation(
    item: dict[str, Any], text: str
) -> tuple[str, str, Any] | None:
    """Update the cache after DB CAS and return its mutation receipt."""
    try:
        cache_type = str(item.get("cache_type") or "lang")
        rule = get_default_cache_rules().get(cache_type)
        if rule is None:
            return None
        initialize_translation_cache()
        key = rule.make_key(item)
        receipt = add_to_cache_with_receipt(
            cache_type,
            key,
            item["source_text"],
            text,
            mod=item.get("_mod_id"),
            path=item.get("path"),
        )
        return cache_type, key, receipt
    except Exception as exc:  # noqa: BLE001 - cache is best-effort after DB commit
        logger.warning("Mod DB repair cache add failed: %r", exc)
        return None


def _log(session, text: str, level: str = "info") -> None:
    mirror_session_log(session, logger, text, level, prefix="[Mod DB 譯文修復] ")


def _candidate_identity(db, item: dict[str, Any]) -> str:
    source_id = int(item.get("_source_id", SRC_AI))
    label = source_label(source_id, source_catalog_for(db), item.get("_review_status"))
    return (
        f"[MC {item.get('_expected_version', '')}]"
        f"[mod={item.get('_mod_id', '')}]"
        f"[kind={item.get('_kind', '')}]"
        f"[source={source_id}:{label}]"
        f"[key={item.get('path', '')}]"
    )


def _log_candidate(
    db,
    session,
    report,
    item: dict[str, Any],
    category: str,
    text: str,
    level: str = "info",
) -> None:
    """Emit source-identifiable detail while respecting the run-wide sample cap."""
    if report._log_details_emitted >= _LOG_SAMPLE_LIMIT:
        report.log_details_omitted += 1
        report._log_details_omitted_by_category[category] = (
            report._log_details_omitted_by_category.get(category, 0) + 1
        )
        return
    report._log_details_emitted += 1
    _log(session, f"{_candidate_identity(db, item)} {text}", level)


def _log_omitted_candidate_details(session, report) -> None:
    if not report.log_details_omitted:
        return
    category_counts = "、".join(
        f"{name} {count:,} 筆"
        for name, count in sorted(report._log_details_omitted_by_category.items())
    )
    _log(
        session,
        f"ℹ️ 已省略 {report.log_details_omitted:,} 筆候選明細"
        + (f"（{category_counts}）" if category_counts else ""),
    )


def _finalize_batch_results(
    db,
    options: TranslateOptions,
    session,
    batch: list[dict[str, Any]],
    results: list[dict[str, Any]],
    report: SameSourceAIRepairReport,
    cache_keys_to_save: dict[str, dict[str, int | None]],
    cancelled,
) -> tuple[int, bool]:
    """Validate each result of one batch, then CAS its source row."""
    stop_after_batch = False
    if len(results) > len(batch):
        results = results[: len(batch)]
        report.last_error = "翻譯引擎回傳超過本批筆數的結果"
        stop_after_batch = True
    for index, original in enumerate(batch):
        if cancelled() or is_cancelled():
            raise TaskCancelled()
        has_result = index < len(results)
        result = results[index] if has_result else None
        stop_after_batch = (
            _finalize_result(
                db,
                options,
                session,
                original,
                result,
                has_result,
                report,
                cache_keys_to_save,
            )
            or stop_after_batch
        )
    return len(results), stop_after_batch


def _finalize_result(
    db, options, session, original, result, has_result, report, cache_keys_to_save
) -> bool:
    """Validate one AI result and CAS its source row; True when the run should stop."""
    if not has_result:
        report.last_error = (
            report.last_error or "翻譯未回傳完整本批結果，未回傳項目保留舊譯文"
        )
        _mark_failed(
            db,
            session,
            report,
            original,
            "missing_result",
            "翻譯引擎未回傳本筆結果，保留舊譯文",
        )
        return True
    if not _valid_translation(result, original):
        report.last_error = report.last_error or "翻譯結果格式無效，舊譯文已保留"
        _mark_failed(
            db,
            session,
            report,
            original,
            "invalid_result",
            "AI 結果格式無效，保留舊譯文",
        )
        return True
    translated_text = result["text"]
    kind = str(original.get("_kind") or "lang")
    issues = (
        repair_output_issues_for_kind(
            original["source_text"],
            translated_text,
            kind=kind,
            allow_actual_newlines=not report.strict_newline,
        )
        if report.mode == "quality_mismatch"
        else list(
            analyze_format_diagnostics(
                original["source_text"], translated_text, kind=kind
            ).legacy_messages
        )
    )
    if issues:
        _mark_flagged(db, session, report, original, translated_text, issues)
        return False
    report.ai_validated_items += 1
    _finalize_source_target(
        db, options, session, report, original, translated_text, cache_keys_to_save
    )
    return False


def _mark_failed(db, session, report, target, category, message):
    """Record one missing or invalid result; the old translation stays."""
    report.failed += 1
    report.processed_candidates += 1
    _log_candidate(db, session, report, target, category, message, "warning")


def _mark_flagged(db, session, report, target, translated_text, issues):
    """Keep the old translation when a validated answer changes protected tokens."""
    old_source = "原來源譯文" if report.mode == "quality_mismatch" else "舊 AI 譯文"
    message = f"特殊字元不一致，保留{old_source}：{'、'.join(issues)}"
    report.translated += 1
    report.flagged += 1
    report.processed_candidates += 1
    if report.mode == "quality_mismatch":
        review_item = {
            "entry_id": target["_entry_id"],
            "version": target["_expected_version"],
            "mod_id": target["_mod_id"],
            "kind": target["_kind"],
            "source_id": target["_source_id"],
            "key": target["path"],
            "en_us": target["source_text"],
            "old_translation": target["_expected_old_zh_tw"],
            "ai_translation": translated_text,
            "issues": tuple(issues),
            "expected_revision": target["_expected_revision"],
            "strict_newline": report.strict_newline,
            "sync_other_versions": report.sync_other_versions,
            "database_identity": str(getattr(db, "path", "")),
            "database_file_id": Path(getattr(db, "path", "")).stat().st_ino,
            "database_priority": tuple(getattr(db, "priority", ())),
        }
        if report.review_store_path and report.review_run_id:
            moddb_repair_review_store.append_item(
                report.review_store_path, report.review_run_id, review_item
            )
            report.reviewable_results += 1
            report.flagged_entries_omitted += 1
        elif len(report.flagged_entries) < _FLAGGED_DETAIL_LIMIT:
            report.flagged_entries.append(review_item)
        else:
            report.flagged_entries_omitted += 1
    _log_candidate(db, session, report, target, "token_mismatch", message, "warning")


def _finalize_source_target(
    db, options, session, report, target, text, cache_keys_to_save
):
    """CAS one source row, then report only cache mutations owned by this run."""
    replace = _replace_candidate_translation(db, target, text, report)
    report.translated += 1
    if replace.status == "updated":
        report.updated += 1
        report.synced_other_versions += replace.synced_other_versions
        if replace.shadowed:
            report.shadowed += 1
            _log_candidate(
                db,
                session,
                report,
                target,
                "updated_shadowed",
                "⚠️ 已寫入，但目前來源優先序讓別的譯文仍然生效",
                "warning",
            )
        else:
            _log_candidate(db, session, report, target, "updated", "✅ 更新成功")
    elif replace.status == "unchanged":
        report.unchanged += 1
        if replace.shadowed:
            report.unchanged_shadowed += 1
            _log_candidate(
                db,
                session,
                report,
                target,
                "unchanged_shadowed",
                "⚠️ 修復來源已有相同譯文，但目前來源優先序讓別的譯文仍然生效",
                "warning",
            )
        else:
            _log_candidate(
                db, session, report, target, "unchanged", "譯文已相同，未新增歷史"
            )
    else:
        report.skipped_changed += 1
        report.processed_candidates += 1
        _log_candidate(
            db,
            session,
            report,
            target,
            "cas_changed",
            "⚠️ 資料已變動，跳過覆寫",
            "warning",
        )
        return
    report.processed_candidates += 1
    if options.write_cache and replace.status == "updated":
        _record_repair_cache(db, session, report, target, text, cache_keys_to_save)


def _record_repair_cache(db, session, report, target, text, cache_keys_to_save):
    cached = _cache_finalized_translation(target, text)
    if cached is None or not cached[2].accepted:
        report.cache_add_failed += 1
        report.cache_failed += 1
        detail = "快取變更未能確認" if cached is None else "快取拒絕此 key"
        _log_candidate(
            db,
            session,
            report,
            target,
            "cache_add_failed",
            f"⚠️ 資料庫已更新，但{detail}",
            "warning",
        )
        return
    cache_type, key, receipt = cached
    if receipt.changed:
        # Preview order is stable. For shared keys, the last successful CAS owns dst.
        report._cache_changed_keys.add((cache_type, key))
        cache_keys_to_save.setdefault(cache_type, {})[key] = receipt.generation


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
            strict_newline=report.strict_newline,
            sync_other_versions=report.sync_other_versions,
        )
    return db.replace_ai_translation(entry_id, old_text, text, **identity)


def _open_review_db(database_path: str | Path, item: dict[str, Any]):
    """Open the repair DB for review items, or ``None`` when the DB identity changed."""
    if str(Path(database_path).resolve()) != str(
        Path(item.get("database_identity", "")).resolve()
    ):
        return None
    try:
        if Path(database_path).stat().st_ino != int(item["database_file_id"]):
            return None
    except (KeyError, OSError, TypeError, ValueError):
        return None
    priority = tuple(int(value) for value in item.get("database_priority", ()))
    db = TranslationDB(database_path, priority=priority, create=False)
    if str(Path(db.path).resolve()) != str(Path(item["database_identity"]).resolve()):
        db.close()
        return None
    return db


def _apply_review_item_on(db, item: dict[str, Any], draft: str) -> str:
    result = db.replace_translation_quality_mismatch(
        int(item["entry_id"]),
        int(item["source_id"]),
        str(item["old_translation"]),
        draft,
        expected_version=str(item["version"]),
        expected_kind=str(item["kind"]),
        expected_mod_id=str(item["mod_id"]),
        expected_key=str(item["key"]),
        expected_en_us=str(item["en_us"]),
        expected_revision=item.get("expected_revision"),
        actor="AI 機翻修復・人工確認",
        action="quality_repair_review",
        sync_other_versions=bool(item.get("sync_other_versions")),
    )
    return "applied" if result.status == "updated" else "stale"


def apply_and_record_repair_review(
    database_path: str | Path,
    store_path: str | Path,
    run_id: str,
    item: dict[str, Any],
    draft: str,
) -> str:
    """Apply a draft and matching unresolved keys on the worker thread.

    One database connection serves the item and all its matching siblings;
    opening a fresh connection per sibling made large groups noticeably slow.
    """
    db = _open_review_db(database_path, item)
    try:
        result = _apply_review_item_on(db, item, draft) if db is not None else "stale"
        moddb_repair_review_store.save_review_state(
            store_path,
            run_id,
            int(item["item_index"]),
            "applied" if result == "applied" else "stale",
            draft,
        )
        if result != "applied":
            return result

        for sibling in moddb_repair_review_store.matching_items(
            store_path, run_id, item
        ):
            if int(sibling["item_index"]) == int(item["item_index"]):
                continue
            sibling_result = _apply_review_item_on(db, sibling, draft)
            moddb_repair_review_store.save_review_state(
                store_path,
                run_id,
                int(sibling["item_index"]),
                "applied" if sibling_result == "applied" else "stale",
                draft,
            )
        return result
    finally:
        if db is not None:
            db.close()


def _finish_snapshot(db, options, session, report, tracker) -> None:
    report.remaining = _remaining_candidate_count(db, options, report.mode)
    report.unprocessed_candidates = max(
        0, report.candidates - report.processed_candidates
    )
    _set_cache_report_stats(options, report)
    if report.failed or report.last_error:
        report.status = "FAILED"
        session.set_error()
    else:
        report.status = "DONE"
    report.elapsed_sec = tracker.elapsed()
    session.set_progress(report.processed_candidates / max(report.candidates, 1))
    level = "warning" if report.failed or report.flagged else "info"
    _log(
        session,
        f"完成：來源項目 {report.candidates}，"
        f"送出來源項目 {report.attempted_candidates}，已完成來源項目 {report.processed_candidates}；"
        f"實際送給 AI {report.ai_submitted_items} 個項目（不等於實際 HTTP 請求次數），"
        f"已驗證 {report.ai_validated_items}；更新 {report.updated}"
        + (
            f"（另同步其他版本 {report.synced_other_versions}）"
            if report.sync_other_versions
            else ""
        )
        + (
            f"（其中 {report.shadowed} 筆因來源優先序仍顯示原本的譯文）"
            if report.shadowed
            else ""
        )
        + "，"
        f"仍相同 {report.unchanged}"
        + (
            f"（其中 {report.unchanged_shadowed} 筆已有修復譯文，但因來源優先序仍未生效）"
            if report.unchanged_shadowed
            else ""
        )
        + f"，格式檢查未通過 {report.flagged}，"
        f"資料已變動跳過 {report.skipped_changed}，失敗 {report.failed}，"
        f"未處理來源項目 {report.unprocessed_candidates}，未送出來源項目 {report.not_submitted_candidates}，"
        f"範圍內仍符合修復條件 {report.remaining}；快取 key 變更 "
        f"{report.cache_keys_changed if report.cache_keys_changed is not None else '未知'}，"
        f"成功落盤 "
        f"{report.cache_keys_saved if report.cache_keys_saved is not None else '未知'}"
        + (f"（{report.cache_stats_note}）" if report.cache_stats_note else ""),
        level,
    )
    if report.last_error:
        _log(session, f"最後一次錯誤：{report.last_error}", "warning")
    _log_omitted_candidate_details(session, report)
    session.set_summary(report.as_dict())


def _set_cache_report_stats(options, report) -> None:
    report.cache_save_failed = len(report._cache_save_failed_keys)
    report.cache_keys_changed = (
        len(report._cache_changed_keys) if options.write_cache else None
    )
    if not options.write_cache:
        report.cache_keys_saved = None
        report.cache_stats_note = "本次未啟用快取寫入"
    elif report._cache_save_failed_keys:
        report.cache_keys_saved = None
        report.cache_stats_note = (
            f"{len(report._cache_save_failed_keys):,} 個 key 落盤結果未確認"
        )
    elif report._cache_saving_disabled:
        report.cache_keys_saved = len(report._cache_saved_keys)
        report.cache_stats_note = "快取落盤設定停用，變更保留在記憶體 pending"
    else:
        report.cache_keys_saved = len(report._cache_saved_keys)
        if report._cache_superseded_keys:
            report.cache_stats_note = (
                f"{len(report._cache_superseded_keys):,} 個快取 key 已被較新寫入取代，"
                "未歸屬於本任務落盤"
            )


def _remaining_candidate_count(db, options, mode: str) -> int:
    if mode == "quality_mismatch":
        return sum(
            entry is not None
            and (options.repair_newline_only or not (newline_mismatch and not mixed))
            for entry, newline_mismatch, mixed in db.iter_repairable_translation_entries(
                options.version, list(options.mod_ids)
            )
        )
    return db.count_same_as_source_ai(options.version, list(options.mod_ids))


def _handle_batch_completion(
    options,
    session,
    report,
    batch,
    result_count,
    status,
    cache_keys_to_save,
) -> bool:
    """Flush this run's changed keys and decide whether the run must stop."""
    _flush_repair_cache(options, session, report, cache_keys_to_save)

    stop_after_batch = False
    if result_count < len(batch):
        report.last_error = (
            report.last_error or "翻譯未回傳完整本批結果，未回傳項目保留舊譯文"
        )
        stop_after_batch = True
    if str(status or "").upper() not in {"DONE", "AUTO"}:
        report.last_error = report.last_error or f"翻譯引擎回傳狀態：{status}"
        stop_after_batch = True
    return stop_after_batch


def _flush_repair_cache(options, session, report, cache_keys_to_save) -> None:
    if not options.write_cache:
        return
    for cache_type, key_versions in list(cache_keys_to_save.items()):
        if not key_versions:
            cache_keys_to_save.pop(cache_type, None)
            continue
        try:
            receipt = save_translation_cache_keys(
                cache_type,
                set(key_versions),
                write_new_shard=options.new_cache_shard,
                expected_versions=key_versions,
            )
        except Exception as exc:  # noqa: BLE001 - DB results survive cache failure
            logger.warning("Mod DB repair cache flush failed: %r", exc)
            receipt = None
        if receipt is None:
            failed = {(cache_type, key) for key in key_versions}
            report._cache_save_failed_keys.update(failed)
            report.cache_save_failed += len(key_versions)
            report.cache_failed += len(key_versions)
            _log(
                session,
                f"⚠️ {cache_type} 有 {len(key_versions):,} 個本次變更 key 落盤狀態未知；"
                "資料庫更新已保留",
                "warning",
            )
            continue
        if not receipt.saving_enabled:
            report._cache_saving_disabled = True
            continue
        report._cache_superseded_keys.update(
            (cache_type, key) for key in receipt.superseded_keys
        )
        report.cache_keys_superseded = len(report._cache_superseded_keys)
        if receipt.saved_keys is None:
            failed = {
                (cache_type, key)
                for key in set(key_versions) - set(receipt.superseded_keys)
            }
            report._cache_save_failed_keys.update(failed)
            report.cache_save_failed += len(failed)
            report.cache_failed += len(failed)
            _log(
                session,
                f"⚠️ {cache_type} 有 {len(failed):,} 個本次變更 key 落盤失敗；"
                "資料庫更新已保留，快取仍待重試",
                "warning",
            )
            continue
        saved = {(cache_type, key) for key in receipt.saved_keys}
        report._cache_saved_keys.update(saved)
        report._cache_save_failed_keys.difference_update(saved)
        cache_keys_to_save.pop(cache_type, None)


def _translate_snapshot(
    db,
    options: TranslateOptions,
    session,
    entries: Sequence[SameSourceAIEntry],
    report: SameSourceAIRepairReport,
    cancelled,
) -> None:
    items, tracker, lm_cfg = _initialize_snapshot_run(
        db, entries, session, report, options
    )
    cache_keys_to_save: dict[str, dict[str, int | None]] = {}
    while items:
        _raise_if_repair_cancelled(items, report, cancelled)
        batch, cache_type = _same_batch_prefix(items, lm_cfg)
        remainder, stop_after_batch = _run_repair_batch(
            db,
            options,
            session,
            batch,
            items,
            cache_type,
            tracker,
            report,
            cancelled,
            cache_keys_to_save,
        )
        if stop_after_batch:
            break
        items = remainder
    _finish_snapshot(db, options, session, report, tracker)


def _set_not_submitted_candidates(report, items):
    report.not_submitted_candidates = len(items)


def _raise_if_repair_cancelled(items, report, cancelled):
    if cancelled() or is_cancelled():
        _set_not_submitted_candidates(report, items)
        raise TaskCancelled()


def _run_repair_batch(
    db,
    options,
    session,
    batch,
    items,
    cache_type,
    tracker,
    report,
    cancelled,
    cache_keys_to_save: dict[str, dict[str, int | None]],
):
    """Submit one homogeneous batch, finalize each CAS, then publish its progress."""
    report.batches += 1
    report.ai_submitted_items += len(batch)
    report.attempted_candidates += len(batch)
    batch_started = monotonic()
    tail = items[len(batch) :]
    batch_number = tracker.batches_done + 1
    before_validated = report.ai_validated_items
    planned_live = tracker.live(processed=report.processed_candidates)
    planned_total = planned_live["planned_batches_total"]
    model_label = _configured_model_label(tracker.lm_cfg)
    in_flight_live = dict(planned_live)
    in_flight_live.update(
        {
            "current_profile": cache_profile_label(cache_type),
            "current_batch": batch_number,
            "current_batch_total": planned_total,
            "current_status": "等待模型回應",
            "model_label": model_label,
        }
    )
    session.set_summary({**report.as_dict(), "live": in_flight_live})
    _log(
        session,
        f"🚀 第 {batch_number}/{planned_total} 批｜{cache_profile_label(cache_type)} "
        f"{len(batch):,} 筆已送出｜模型：{model_label}｜狀態：等待模型回應",
    )
    try:
        translated, status = translate_batch_smart(batch, report.candidates)
        if cancelled() or is_cancelled():
            raise TaskCancelled()
        applying_live = dict(planned_live)
        applying_live.update(
            {
                "current_profile": cache_profile_label(cache_type),
                "current_batch": batch_number,
                "current_batch_total": planned_total,
                "current_status": "套用翻譯結果至對應來源項目",
                "model_label": model_label,
            }
        )
        session.set_summary({**report.as_dict(), "live": applying_live})
        _log(session, "🔄 正在套用翻譯結果至對應來源項目")
        result_count, finalize_stop = _finalize_batch_results(
            db,
            options,
            session,
            batch,
            list(translated or []),
            report,
            cache_keys_to_save,
            cancelled,
        )
        completion_stop = _handle_batch_completion(
            options, session, report, batch, result_count, status, cache_keys_to_save
        )
        stop_after_batch = finalize_stop or completion_stop
        if stop_after_batch:
            _set_not_submitted_candidates(report, tail)
        tracker.update(
            cache_type,
            count=len(batch),
            elapsed=monotonic() - batch_started,
        )

        def check_planning_cancelled():
            if cancelled() or is_cancelled():
                raise TaskCancelled()

        live = tracker.live(
            processed=report.processed_candidates, check=check_planning_cancelled
        )
        live.update(
            {
                "current_profile": cache_profile_label(cache_type),
                "current_batch": batch_number,
                "current_batch_total": live["planned_batches_total"],
                "current_status": "本批完成",
                "model_label": model_label,
            }
        )
        report.planned_batches = live["planned_batches_total"]
        session.set_progress(report.processed_candidates / max(report.candidates, 1))
        session.set_summary({**report.as_dict(), "live": live})
        _log(
            session,
            f"✅ 第 {batch_number}/{live['planned_batches_total']} 批完成｜"
            f"回應 {result_count:,}/{len(batch):,} 筆｜"
            f"通過格式檢查 {report.ai_validated_items - before_validated:,} 筆",
        )
        _log(session, f"📈 {tracker.line(live)}")
    except TaskCancelled:
        _set_not_submitted_candidates(report, tail)
        report.unprocessed_candidates = max(
            0, report.candidates - report.processed_candidates
        )
        _flush_after_interrupted_batch(options, session, report, cache_keys_to_save)
        raise
    except Exception:
        _set_not_submitted_candidates(report, tail)
        report.unprocessed_candidates = max(
            0, report.candidates - report.processed_candidates
        )
        _flush_after_interrupted_batch(options, session, report, cache_keys_to_save)
        raise
    return tail, stop_after_batch


def _flush_after_interrupted_batch(options, session, report, cache_keys_to_save):
    try:
        _flush_repair_cache(options, session, report, cache_keys_to_save)
    except Exception:
        logger.exception("Mod DB repair cache flush failed while stopping a batch")


def _initialize_snapshot_run(db, entries, session, report, options):
    """Group candidates and prepare the rate-limited translator run."""
    if report.mode == "quality_mismatch" and not options.repair_newline_only:
        entries, skipped_newline_only = _filter_newline_only_repair_entries(entries)
        report.skipped_newline_only_candidates += skipped_newline_only
        report.candidates = len(entries)
    candidates = _build_retranslation_items(entries)
    attach_moddb_context(
        candidates,
        operation_id=f"retranslate:{getattr(session, 'id', '') or id(session)}",
        mc_version=options.version,
        reviewed_glossary_loader=db.reviewed_lang_glossary,
    )
    candidates = _group_retranslation_items_by_cache_type(candidates)
    items = candidates
    report.candidate_profile_counts = _cache_type_counts(candidates)
    lm_cfg = _load_lm_config()
    tracker = _RepairRunProgress(
        items,
        total_candidates=len(candidates),
        lm_cfg=lm_cfg,
    )
    report.planned_batches = tracker.live()["planned_batches_total"]
    total = len(candidates)
    operation = "格式修復" if report.mode == "quality_mismatch" else "舊 AI 重翻"
    if report.mode == "quality_mismatch":
        _log(
            session,
            f"🔎 掃描完成｜找到 {total:,} 筆格式不一致的來源項目"
            f"（{_format_profile_breakdown(_cache_type_counts(candidates))}）",
        )
        if report.skipped_newline_only_candidates:
            _log(
                session,
                f"⏭️ 略過 {report.skipped_newline_only_candidates:,} 筆只有實際換行數不同的項目"
                "｜不佔用 AI 修復名額",
            )
        _log(
            session,
            f"📋 修復規劃｜{len(items):,} 筆需由 AI 修復",
        )
    else:
        _log(
            session,
            f"🔎 掃描完成｜找到 {total:,} 筆待處理來源項目"
            f"（來源：{source_label(SRC_AI)}；{_format_profile_breakdown(_cache_type_counts(candidates))}）",
        )
        _log(
            session,
            f"📋 重翻規劃｜{len(items):,} 筆需由 AI 重翻",
        )
    _log(session, f"🧭 執行項目｜{operation}；本次略過既有翻譯快取，重新呼叫 AI")
    _log(session, tracker.start_line(items))
    if not options.write_cache:
        report.cache_stats_note = "本次未啟用快取寫入"
    session.set_summary({**report.as_dict(), "live": tracker.live()})
    return items, tracker, lm_cfg


def _filter_newline_only_repair_entries(
    entries: Sequence[SameSourceAIEntry],
) -> tuple[list[SameSourceAIEntry], int]:
    """Keep hard format repairs; newline-only mismatches are intentionally skipped."""
    selected: list[SameSourceAIEntry] = []
    skipped = 0
    for entry in entries:
        eligible, hard_issues, mixed = repair_input_issues_for_kind(
            entry.en_us,
            entry.current_ai_translation,
            kind=entry.kind,
        )
        if eligible and not hard_issues and not mixed:
            skipped += 1
            continue
        selected.append(entry)
    return selected, skipped


def _report_cancelled_repair(db, options, session, report, mode, started_at):
    """Publish the committed partial result when the user cancels repair."""
    report.status = "CANCELLED"
    report.elapsed_sec = monotonic() - started_at
    report.unprocessed_candidates = max(
        0, report.candidates - report.processed_candidates
    )
    _set_cache_report_stats(options, report)
    _log(session, "⏹ 已取消；已完成並提交的項目保留，其他舊譯文未更動", "warning")
    if db is not None:
        report.remaining = _remaining_candidate_count(db, options, mode)
    _log_omitted_candidate_details(session, report)
    session.set_summary(report.as_dict())


def _report_repair_failure(options, session, report, operation_label, exc):
    """Turn a worker-boundary exception into an explicit failed session report."""
    logger.error(
        "Mod DB %s 失敗: %s\n%s",
        operation_label,
        exc,
        traceback.format_exc(),
        extra={"ui_mirrored": True},
    )
    report.status = "FAILED"
    report.not_submitted_candidates = max(
        0, report.candidates - report.attempted_candidates
    )
    report.unprocessed_candidates = max(
        0, report.candidates - report.processed_candidates
    )
    report.last_error = f"{type(exc).__name__}: {exc}"
    _set_cache_report_stats(options, report)
    session.set_error()
    add_log_unmirrored(session, f"[致命錯誤] {operation_label}失敗：{exc}", "error")
    _log(
        session,
        f"失敗統計：候選 {report.candidates}，已送出 {report.attempted_candidates}，"
        f"已完成 {report.processed_candidates}，其中失敗 {report.failed}，"
        f"未送出 {report.not_submitted_candidates}，"
        f"未處理 {report.unprocessed_candidates}",
        "warning",
    )
    _log_omitted_candidate_details(session, report)
    session.set_summary(report.as_dict())


def run_moddb_retranslate_service(
    options: TranslateOptions,
    session,
    entries: Sequence[SameSourceAIEntry],
    *,
    mode: str = "same_source_ai",
    review_preview: SameSourceAIRepairPreview | None = None,
    manage_session: bool = True,
) -> None:
    """Direct-AI repair for the exact entries shown by the user's preview."""
    ensure_pipeline_logging()
    db = None
    report = SameSourceAIRepairReport(
        candidates=len(entries),
        mode=mode,
        strict_newline=mode == "quality_mismatch" and options.repair_newline_only,
        sync_other_versions=mode == "quality_mismatch"
        and options.repair_sync_other_versions,
        input_newline_mismatch_candidates=(
            review_preview.input_newline_mismatch_candidates if review_preview else 0
        ),
        input_newline_mismatch_with_other_hard_issues=(
            review_preview.input_newline_mismatch_with_other_hard_issues
            if review_preview
            else 0
        ),
        skipped_newline_only_candidates=(
            review_preview.skipped_newline_only_candidates if review_preview else 0
        ),
    )
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
        _report_cancelled_repair(db, options, session, report, mode, started_at)
    except Exception as exc:  # noqa: BLE001 - service boundary reports failure in TaskSession
        _report_repair_failure(options, session, report, operation_label, exc)
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
    if mode == "quality_mismatch" and entries:
        report.review_run_id = uuid.uuid4().hex
        report.review_store_path = str(
            moddb_repair_review_store.create_run(db.path, report.review_run_id)
        )
    if not entries:
        report.remaining = _remaining_candidate_count(db, options, mode)
        report.unprocessed_candidates = 0
        _set_cache_report_stats(options, report)
        message = (
            "沒有符合條件的來源譯文特殊字元不一致項目"
            if mode == "quality_mismatch"
            else "沒有符合條件的舊 AI 同原文譯文"
        )
        _log(session, message)
        session.set_progress(1.0)
        session.set_summary(report.as_dict())
        return

    with cancel_scope(cancelled):
        _translate_snapshot(db, options, session, entries, report, cancelled)


__all__ = [
    "SameSourceAIRepairPreview",
    "SameSourceAIRepairReport",
    "cache_profile_label",
    "preview_same_source_ai_retranslation",
    "run_moddb_retranslate_service",
]
