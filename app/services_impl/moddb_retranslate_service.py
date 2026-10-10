"""Preview and repair existing source translations using direct AI output."""

from __future__ import annotations

import logging
import sqlite3
import time
import traceback
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
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
from app.views.moddb.formatting import token_issues
from translation_tool.core.lm_batch_budget import (
    BudgetConfig,
    get_tracker,
    profile_for_cache_type,
    select_batch_size,
)
from translation_tool.core.lm_translator_main import translate_batch_smart
from translation_tool.core.lm_translator_shared_cache import get_default_cache_rules
from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.models import SameSourceAIEntry
from translation_tool.translation_db.quality import repair_output_issues
from translation_tool.translation_db.run_progress import format_live
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.utils.cache_manager import (
    add_to_cache_with_receipt,
    initialize_translation_cache,
    save_translation_cache_keys,
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
    ai_representatives: int = 0
    dedup_reused_candidates: int = 0
    representative_profile_counts: tuple[tuple[str, int], ...] = ()
    skipped_input_newline_mismatch: int = 0
    skipped_newline_with_other_hard_issues: int = 0

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
    skipped_input_newline_mismatch: int = 0
    skipped_newline_with_other_hard_issues: int = 0
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
    ai_representatives: int = 0
    ai_submitted_items: int = 0
    ai_validated_items: int = 0
    ai_submission_unit: str = "items_passed_to_translate_batch_smart"
    dedup_mapped_candidates: int = 0
    dedup_reused_candidates: int = 0
    candidate_profile_counts: dict[str, int] = field(default_factory=dict)
    representative_profile_counts: dict[str, int] = field(default_factory=dict)
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
    """Track source-row progress and profile-specific representative ETA samples."""

    def __init__(
        self,
        items: Sequence[dict[str, Any]],
        fanout: dict[int, tuple[dict[str, Any], ...]],
        *,
        total_candidates: int,
        lm_cfg: dict[str, Any],
        started_mono: float | None = None,
        started_wall: float | None = None,
    ) -> None:
        self.total_candidates = total_candidates
        self._fanout = fanout
        self.lm_cfg = lm_cfg
        self.started_mono = monotonic() if started_mono is None else started_mono
        self.started_wall = time.time() if started_wall is None else started_wall
        self.batches_done = 0
        self.profile_batches_done: dict[str, int] = {}
        self.profile_candidate_totals: dict[str, int] = {}
        self.profile_candidate_processed: dict[str, int] = {}
        self.profile_representative_totals: dict[str, int] = {}
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
            self.profile_representative_totals[cache_type] = (
                self.profile_representative_totals.get(cache_type, 0) + 1
            )
            self.profile_candidate_totals[cache_type] = (
                self.profile_candidate_totals.get(cache_type, 0)
                + len(fanout.get(id(item), (item,)))
            )
        self._planned_batches_by_profile: dict[str, int] = {}
        self._planned_first_sizes: dict[str, int] = {}
        self._planned_signatures: dict[str, tuple[Any, ...]] = {}
        self._last_batch_sizes: dict[str, int] = {}
        for cache_type, ranges in self._profile_ranges.items():
            batches, first_size = _plan_profile_ranges(
                self._items, cache_type, ranges, self.lm_cfg
            )
            self._planned_batches_by_profile[cache_type] = batches
            self._planned_first_sizes[cache_type] = first_size
            self._planned_signatures[cache_type] = _batch_plan_signature(
                cache_type, self.lm_cfg
            )

    def update(
        self,
        cache_type: str,
        *,
        candidate_count: int,
        representative_count: int,
        elapsed: float,
    ) -> None:
        self.batches_done += 1
        self.profile_batches_done[cache_type] = (
            self.profile_batches_done.get(cache_type, 0) + 1
        )
        self.profile_candidate_processed[cache_type] = (
            self.profile_candidate_processed.get(cache_type, 0) + candidate_count
        )
        self.profile_items_submitted[cache_type] = (
            self.profile_items_submitted.get(cache_type, 0) + representative_count
        )
        self.profile_sample_items[cache_type] = (
            self.profile_sample_items.get(cache_type, 0) + representative_count
        )
        self.profile_sample_seconds[cache_type] = self.profile_sample_seconds.get(
            cache_type, 0.0
        ) + max(0.0, elapsed)
        self._last_batch_sizes[cache_type] = representative_count

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
        remaining_batches = sum(remaining_batches_by_profile.values())
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
                "ai_representatives": self.profile_representative_totals[cache_type],
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
        for (
            cache_type,
            total_representatives,
        ) in self.profile_representative_totals.items():
            if check is not None:
                check()
            submitted = self.profile_items_submitted.get(cache_type, 0)
            remaining_count = max(0, total_representatives - submitted)
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
        planned = sum(self._planned_batches_by_profile.values())
        return (
            f"📦 AI 代表 {len(items):,} 筆；依目前 profile／token 預算預估 "
            f"{planned:,} 個外層批次（{_format_profile_breakdown(counts)}；"
            "候選進度以來源列計，內部 LM 重試不計外層批次）"
        )

    def line(self, live: dict[str, Any]) -> str:
        return format_live(live)

    def elapsed(self) -> float:
        return max(0.0, monotonic() - self.started_mono)

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
    """Stream all eligible source rows while retaining only selected rep fanout."""
    limit = options.limit if options.limit and options.limit > 0 else None
    representatives: list[dict[str, Any]] = []
    targets: dict[int, list[tuple[dict[str, Any], SameSourceAIEntry]]] = {}
    by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    selected_entry_ids: set[int] = set()
    eligible = skipped_newline = skipped_mixed = 0
    for entry, newline_mismatch, mixed in db.iter_repairable_translation_entries(
        options.version,
        list(options.mod_ids),
        check=lambda: _raise_preview_timeout(deadline),
    ):
        _raise_preview_timeout(deadline)
        skipped_newline, skipped_mixed = _count_newline_skips(
            newline_mismatch, mixed, skipped_newline, skipped_mixed
        )
        if entry is None:
            continue
        eligible += 1
        if not _could_map_to_selected_representative(
            entry, limit, representatives, selected_entry_ids
        ):
            # Entry ID is part of the complete equivalence key; a new entry
            # cannot fan out into any selected representative.
            continue
        candidate = _build_retranslation_items([entry], deadline=deadline)[0]
        _add_limited_repair_candidate(
            candidate,
            entry,
            representatives,
            targets,
            by_key,
            selected_entry_ids,
            limit,
        )
    selected = [
        pair
        for representative in representatives
        for pair in targets[id(representative)]
    ]
    selected_items = _group_retranslation_items_by_cache_type(
        [item for item, _row in selected], deadline=deadline
    )
    rows_by_key = {
        (int(item["_entry_id"]), int(item.get("_source_id", SRC_AI))): row
        for item, row in selected
    }
    selected_entries = [
        rows_by_key[(int(item["_entry_id"]), int(item.get("_source_id", SRC_AI)))]
        for item in selected_items
    ]
    return eligible, skipped_newline, skipped_mixed, selected_entries


def _count_newline_skips(newline_mismatch, mixed, skipped, skipped_mixed):
    return skipped + int(newline_mismatch), skipped_mixed + int(
        newline_mismatch and mixed
    )


def _could_map_to_selected_representative(entry, limit, representatives, selected_ids):
    """Keep scanning counts, but build prompt items only for selected entry IDs."""
    return (
        limit is None or len(representatives) < limit or entry.entry_id in selected_ids
    )


def _add_limited_repair_candidate(
    candidate, entry, representatives, targets, by_key, selected_entry_ids, limit
):
    """Add one row to a selected representative or admit a new representative."""
    key = _retranslation_equivalence_key(candidate)
    source_id = int(candidate.get("_source_id", SRC_AI))
    representative = _find_unmapped_repair_representative(
        key, source_id, by_key, targets
    )
    if representative is not None:
        targets[id(representative)].append((candidate, entry))
        return
    if limit is not None and len(representatives) >= limit:
        return
    representatives.append(candidate)
    targets[id(candidate)] = [(candidate, entry)]
    by_key.setdefault(key, []).append(candidate)
    selected_entry_ids.add(entry.entry_id)


def _find_unmapped_repair_representative(key, source_id, by_key, targets):
    """Find an equivalent selected group that does not already include this source."""
    for candidate in by_key.get(key, ()):
        if all(
            int(target.get("_source_id", SRC_AI)) != source_id
            for target, _row in targets[id(candidate)]
        ):
            return candidate
    return None


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
        total, skipped_newline, skipped_mixed, entries = _limited_repair_snapshot(
            db, options, deadline
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
    items = _group_retranslation_items_by_cache_type(items, deadline=deadline)
    _raise_preview_timeout(deadline)
    representatives, _fanout = _deduplicate_retranslation_items(
        items, enabled=mode == "quality_mismatch", deadline=deadline
    )
    entry_cache_types = [_retranslation_cache_type(item) for item in items]
    profile_counts = _cache_type_counts(items, deadline=deadline)
    representative_profile_counts = _cache_type_counts(
        representatives, deadline=deadline
    )
    lm_cfg = _load_lm_config()
    estimated_batches = _count_planned_batches(
        representatives, lm_cfg, deadline=deadline
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
        ai_representatives=len(representatives),
        dedup_reused_candidates=len(entries) - len(representatives),
        representative_profile_counts=tuple(representative_profile_counts.items()),
        skipped_input_newline_mismatch=(
            skipped_newline if mode == "quality_mismatch" else 0
        ),
        skipped_newline_with_other_hard_issues=(
            skipped_mixed if mode == "quality_mismatch" else 0
        ),
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


def _retranslation_equivalence_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Key every prompt/context field except source-row CAS state."""
    source_row_fields = {
        "_source_id",
        "_expected_old_zh_tw",
        "_expected_revision",
        "_review_status",
    }
    return tuple(
        (key, _stable_retranslation_value(value))
        for key, value in sorted(item.items())
        if key not in source_row_fields
    )


def _stable_retranslation_value(value: Any) -> Any:
    """Convert nested prompt context into a deterministic, hashable key."""
    if isinstance(value, dict):
        return tuple(
            (key, _stable_retranslation_value(nested))
            for key, nested in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_stable_retranslation_value(nested) for nested in value)
    if isinstance(value, set):
        return tuple(sorted(_stable_retranslation_value(nested) for nested in value))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _deduplicate_retranslation_items(
    items: Sequence[dict[str, Any]],
    *,
    enabled: bool,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], dict[int, tuple[dict[str, Any], ...]]]:
    """Share one task-local AI answer only across source rows of the same entry."""
    representatives: list[dict[str, Any]] = []
    fanout: dict[int, list[dict[str, Any]]] = {}
    by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in items:
        _raise_preview_timeout(deadline)
        key = _retranslation_equivalence_key(item) if enabled else (id(item),)
        source_id = item.get("_source_id")
        group = next(
            (
                candidate
                for candidate in by_key.get(key, ())
                if all(
                    target.get("_source_id") != source_id
                    for target in fanout[id(candidate)]
                )
            ),
            None,
        )
        if group is None:
            representatives.append(item)
            fanout[id(item)] = [item]
            by_key.setdefault(key, []).append(item)
        else:
            fanout[id(group)].append(item)
    return representatives, {
        representative_id: tuple(targets)
        for representative_id, targets in fanout.items()
    }


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
    preferred = _get_default_batch_size(cache_type, None)
    if preferred <= 0:
        preferred = 50
    profile_limit = min(len(items), preferred)
    homogeneous = 1
    while (
        homogeneous < profile_limit
        and _retranslation_cache_type(items[homogeneous]) == cache_type
    ):
        homogeneous += 1
    fit_count = select_batch_size(
        items[:homogeneous],
        profile_for_cache_type(cache_type),
        preferred,
        lm_cfg,
    )
    return items[: max(1, fit_count)], cache_type


def _load_lm_config() -> dict[str, Any]:
    config = load_config()
    lm_cfg = config.get("lm_translator", {}) if isinstance(config, dict) else {}
    return lm_cfg if isinstance(lm_cfg, dict) else {}


def _count_planned_batches(
    items: Sequence[dict[str, Any]],
    lm_cfg: dict[str, Any],
    *,
    deadline: float | None = None,
    check=None,
) -> int:
    """Count bounded windows without repeatedly copying the unplanned suffix."""
    return sum(
        _count_planned_batches_by_profile(
            items, lm_cfg, deadline=deadline, check=check
        ).values()
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
    fanout: dict[int, tuple[dict[str, Any], ...]],
    cache_keys_to_save: dict[str, dict[str, int | None]],
    cancelled,
) -> tuple[int, bool]:
    """Validate one representative result, then independently CAS each source row."""
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
            _finalize_representative_result(
                db,
                options,
                session,
                original,
                result,
                has_result,
                report,
                fanout[id(original)],
                cache_keys_to_save,
                cancelled,
            )
            or stop_after_batch
        )
    return len(results), stop_after_batch


def _finalize_representative_result(
    db,
    options,
    session,
    original,
    result,
    has_result,
    report,
    targets,
    cache_keys_to_save,
    cancelled,
) -> bool:
    """Validate and fan out one AI result, including one CAS per equivalent source."""
    if not has_result:
        report.last_error = (
            report.last_error or "翻譯未回傳完整本批結果，未回傳項目保留舊譯文"
        )
        _mark_batch_targets_failed(
            db,
            session,
            report,
            targets,
            "missing_result",
            "翻譯引擎未回傳本筆結果，保留舊譯文",
            cancelled,
        )
        return True
    if not _valid_translation(result, original):
        report.last_error = report.last_error or "翻譯結果格式無效，舊譯文已保留"
        _mark_batch_targets_failed(
            db,
            session,
            report,
            targets,
            "invalid_result",
            "AI 結果格式無效，保留舊譯文",
            cancelled,
        )
        return True
    translated_text = result["text"]
    issues = (
        repair_output_issues(original["source_text"], translated_text)
        if report.mode == "quality_mismatch"
        else token_issues(original["source_text"], translated_text)
    )
    if issues:
        _mark_batch_targets_flagged(
            db, session, report, targets, translated_text, issues, cancelled
        )
        return False
    report.ai_validated_items += 1
    for target in targets:
        if cancelled() or is_cancelled():
            raise TaskCancelled()
        _finalize_source_target(
            db,
            options,
            session,
            report,
            target,
            translated_text,
            cache_keys_to_save,
            shared=len(targets) > 1,
            reused=target is not original,
        )
    return False


def _mark_batch_targets_failed(
    db, session, report, targets, category, message, cancelled
):
    """Record one missing or invalid representative result for every mapped source."""
    for target in targets:
        if cancelled() or is_cancelled():
            raise TaskCancelled()
        report.failed += 1
        report.processed_candidates += 1
        _log_candidate(db, session, report, target, category, message, "warning")


def _mark_batch_targets_flagged(
    db, session, report, targets, translated_text, issues, cancelled
):
    """Keep the old translation when a validated answer changes protected tokens."""
    old_source = "原來源譯文" if report.mode == "quality_mismatch" else "舊 AI 譯文"
    message = f"特殊字元不一致，保留{old_source}：{'、'.join(issues)}"
    for target in targets:
        if cancelled() or is_cancelled():
            raise TaskCancelled()
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
        _log_candidate(
            db, session, report, target, "token_mismatch", message, "warning"
        )


def _finalize_source_target(
    db, options, session, report, target, text, cache_keys_to_save, *, shared, reused
):
    """CAS one source row, then report only cache mutations owned by this run."""
    replace = _replace_candidate_translation(db, target, text, report)
    report.translated += 1
    report.dedup_reused_candidates += int(reused)
    suffix = "（共用已驗證 AI 回覆；獨立 CAS）" if shared else ""
    if replace.status == "updated":
        report.updated += 1
        _log_candidate(db, session, report, target, "updated", f"✅ 更新成功{suffix}")
    elif replace.status == "unchanged":
        report.unchanged += 1
        _log_candidate(
            db, session, report, target, "unchanged", f"譯文已相同，未新增歷史{suffix}"
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
            f"⚠️ 資料已變動，跳過覆寫{suffix}",
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
        )
    return db.replace_ai_translation(entry_id, old_text, text, **identity)


def apply_repair_review_item(
    database_path: str | Path, item: dict[str, Any], draft: str
) -> str:
    """Apply one human-confirmed rejected draft using the original source CAS."""
    if str(Path(database_path).resolve()) != str(
        Path(item.get("database_identity", "")).resolve()
    ):
        return "stale"
    try:
        if Path(database_path).stat().st_ino != int(item["database_file_id"]):
            return "stale"
    except (KeyError, OSError, TypeError, ValueError):
        return "stale"
    priority = tuple(int(value) for value in item.get("database_priority", ()))
    db = TranslationDB(database_path, priority=priority, create=False)
    try:
        if str(Path(db.path).resolve()) != str(
            Path(item["database_identity"]).resolve()
        ):
            return "stale"
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
        )
        return "applied" if result.status == "updated" else "stale"
    finally:
        db.close()


def validate_repair_draft(source: str, translated: str) -> list[str]:
    """View-facing validator entrypoint for manually edited repair drafts."""
    return repair_output_issues(source, translated)


def apply_and_record_repair_review(
    database_path: str | Path,
    store_path: str | Path,
    run_id: str,
    item: dict[str, Any],
    draft: str,
) -> str:
    """Apply one draft and durably update its review state on the worker thread."""
    result = apply_repair_review_item(database_path, item, draft)
    moddb_repair_review_store.save_review_state(
        store_path,
        run_id,
        int(item["item_index"]),
        "applied" if result == "applied" else "stale",
        draft,
    )
    return result


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
        f"完成：候選 {report.candidates}；AI 代表計劃 {report.ai_representatives}，"
        f"送出候選 {report.attempted_candidates}，已完成候選 {report.processed_candidates}；"
        f"交給 translate_batch_smart {report.ai_submitted_items} 個 item "
        f"（不是 API/HTTP 次數），已驗證 {report.ai_validated_items}，"
        f"安全共用 {report.dedup_reused_candidates}；更新 {report.updated}，"
        f"仍相同 {report.unchanged}，格式檢查未通過 {report.flagged}，"
        f"資料已變動跳過 {report.skipped_changed}，失敗 {report.failed}，"
        f"未處理 {report.unprocessed_candidates}，未送出候選 {report.not_submitted_candidates}，"
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
            for entry, _newline_mismatch, _mixed in db.iter_repairable_translation_entries(
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
                cache_type, set(key_versions), expected_versions=key_versions
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
    items, tracker, lm_cfg, sleep_seconds = _initialize_snapshot_run(
        entries, session, report, options
    )
    fanout = tracker._fanout
    cache_keys_to_save: dict[str, dict[str, int | None]] = {}
    while items:
        _raise_if_repair_cancelled(items, fanout, report, cancelled)
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
            fanout,
            cancelled,
            cache_keys_to_save,
        )
        if stop_after_batch:
            break
        items = remainder
        if items and sleep_seconds > 0:
            try:
                interruptible_sleep(sleep_seconds)
            except TaskCancelled:
                _set_not_submitted_candidates(report, items, fanout)
                raise
    _finish_snapshot(db, options, session, report, tracker)


def _set_not_submitted_candidates(report, items, fanout):
    report.not_submitted_candidates = sum(len(fanout[id(item)]) for item in items)


def _raise_if_repair_cancelled(items, fanout, report, cancelled):
    if cancelled() or is_cancelled():
        _set_not_submitted_candidates(report, items, fanout)
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
    fanout,
    cancelled,
    cache_keys_to_save: dict[str, dict[str, int | None]],
):
    """Submit one homogeneous batch, finalize each CAS, then publish its progress."""
    report.batches += 1
    report.ai_submitted_items += len(batch)
    report.attempted_candidates += sum(
        len(fanout.get(id(item), (item,))) for item in batch
    )
    batch_started = monotonic()
    tail = items[len(batch) :]
    try:
        translated, status = translate_batch_smart(batch, report.ai_representatives)
        if cancelled() or is_cancelled():
            raise TaskCancelled()
        result_count, finalize_stop = _finalize_batch_results(
            db,
            options,
            session,
            batch,
            list(translated or []),
            report,
            fanout,
            cache_keys_to_save,
            cancelled,
        )
        completion_stop = _handle_batch_completion(
            options, session, report, batch, result_count, status, cache_keys_to_save
        )
        stop_after_batch = finalize_stop or completion_stop
        if stop_after_batch:
            _set_not_submitted_candidates(report, tail, fanout)
        tracker.update(
            cache_type,
            candidate_count=sum(len(fanout[id(item)]) for item in batch),
            representative_count=len(batch),
            elapsed=monotonic() - batch_started,
        )

        def check_planning_cancelled():
            if cancelled() or is_cancelled():
                raise TaskCancelled()

        live = tracker.live(
            processed=report.processed_candidates, check=check_planning_cancelled
        )
        session.set_progress(report.processed_candidates / max(report.candidates, 1))
        session.set_summary({**report.as_dict(), "live": live})
        _log(session, f"⏱ {tracker.line(live)}")
    except TaskCancelled:
        _set_not_submitted_candidates(report, tail, fanout)
        report.unprocessed_candidates = max(
            0, report.candidates - report.processed_candidates
        )
        _flush_after_interrupted_batch(options, session, report, cache_keys_to_save)
        raise
    except Exception:
        _set_not_submitted_candidates(report, tail, fanout)
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


def _initialize_snapshot_run(entries, session, report, options):
    """Group candidates and prepare the rate-limited translator run."""
    candidates = _group_retranslation_items_by_cache_type(
        _build_retranslation_items(entries)
    )
    items, fanout = _deduplicate_retranslation_items(
        candidates, enabled=report.mode == "quality_mismatch"
    )
    report.ai_representatives = len(items)
    report.dedup_mapped_candidates = len(candidates) - len(items)
    report.candidate_profile_counts = _cache_type_counts(candidates)
    report.representative_profile_counts = _cache_type_counts(items)
    lm_cfg = _load_lm_config()
    tracker = _RepairRunProgress(
        items,
        fanout,
        total_candidates=len(candidates),
        lm_cfg=lm_cfg,
    )
    rate_cfg = lm_cfg.get("rate_limit", {}) if isinstance(lm_cfg, dict) else {}
    sleep_seconds = float(rate_cfg.get("sleep_seconds_between_batches", 0.0) or 0.0)
    total = len(candidates)
    _log(
        session,
        (
            f"🔎 各來源特殊字元不一致候選 {total:,} 筆"
            if report.mode == "quality_mismatch"
            else f"🔎 舊 AI 機翻修復候選 {total:,} 筆；來源：{source_label(SRC_AI)}"
        )
        + f"；候選 {total:,}，AI 代表 {len(items):,}，"
        f"等價映射 {report.dedup_mapped_candidates:,}，"
        f"{_format_profile_breakdown(_cache_type_counts(candidates))}；"
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
        + f"：{total:,} 候選，{len(items):,} 個 AI 代表",
    )
    _log(session, tracker.start_line(items))
    if not options.write_cache:
        report.cache_stats_note = "本次未啟用快取寫入"
    session.set_summary({**report.as_dict(), "live": tracker.live()})
    return items, tracker, lm_cfg, sleep_seconds


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
        skipped_input_newline_mismatch=(
            review_preview.skipped_input_newline_mismatch if review_preview else 0
        ),
        skipped_newline_with_other_hard_issues=(
            review_preview.skipped_newline_with_other_hard_issues
            if review_preview
            else 0
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
