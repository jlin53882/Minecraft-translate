"""Background service adapters for local ModDB smart proofreading."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from translation_tool.core.lm_config_rules import (
    get_current_api_key,
    get_translation_provider,
)
from translation_tool.translation_db import (
    SmartReviewCandidate,
    SmartReviewRunResult,
    SmartReviewSnapshot,
    TranslationDB,
)
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.utils.config_manager import load_config


def smart_review_model_options(config: dict | None = None) -> list[tuple[str, str]]:
    """Return enabled model labels without exposing credentials."""
    cfg = config or load_config()
    models = cfg.get("lm_translator", {}).get("models", {})
    if not isinstance(models, dict):
        return []
    return [
        (str(name), str(name))
        for name, settings in models.items()
        if isinstance(settings, dict) and settings.get("enabled", False)
    ]


def _open(
    path: str | Path, priority: tuple[int, ...], *, readonly: bool
) -> TranslationDB:
    return TranslationDB(path, priority=priority, readonly=readonly, create=False)


def preview_moddb_smart_review_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    *,
    version: str | None = None,
    mod_id: str | None = None,
    kind: str | None = None,
    effective_source: int | None = SRC_AI,
    review_status: str = "unreviewed",
    query: str | None = None,
    limit: int = 1000,
) -> list[SmartReviewSnapshot]:
    db = _open(path, priority, readonly=True)
    try:
        return db.smart_review_candidates(
            version=version,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            review_status=review_status,
            query=query,
            limit=limit,
        )
    finally:
        db.close()


def run_moddb_smart_review_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    *,
    model_name: str,
    version: str | None = None,
    limit: int = 1000,
    batch_size: int = 40,
    progress: Callable[[dict[str, Any]], None] | None = None,
    require_reviewed_reference: bool = False,
    prioritize_named_keys: bool = False,
    mod_id: str | None = None,
    kind: str | None = None,
    effective_source: int | None = SRC_AI,
    review_status: str = "unreviewed",
    query: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> SmartReviewRunResult:
    cfg = load_config()
    provider = get_translation_provider()
    api_key = get_current_api_key() if provider == "gemini" else ""
    db = _open(path, priority, readonly=False)
    try:
        return run_smart_review(
            db,
            model_name=model_name,
            api_key=api_key,
            provider=provider,
            version=version,
            limit=limit,
            batch_size=batch_size,
            progress=progress,
            lm_config=cfg,
            require_reviewed_reference=require_reviewed_reference,
            prioritize_named_keys=prioritize_named_keys,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            review_status=review_status,
            query=query,
            cancel_check=cancel_check,
        )
    finally:
        db.close()


def list_moddb_smart_review_candidates_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    *,
    status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[SmartReviewCandidate]:
    db = _open(path, priority, readonly=True)
    try:
        return db.list_smart_review_candidates(
            run_id, status=status, limit=limit, offset=offset
        )
    finally:
        db.close()


def count_moddb_smart_review_candidates_for_run_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    *,
    status: str | None = None,
) -> int:
    db = _open(path, priority, readonly=True)
    try:
        return db.count_smart_review_candidates_for_run(run_id, status=status)
    finally:
        db.close()


def count_moddb_smart_review_candidates_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    *,
    version: str | None = None,
    mod_id: str | None = None,
    kind: str | None = None,
    effective_source: int | None = SRC_AI,
    review_status: str = "unreviewed",
    query: str | None = None,
    require_reviewed_reference: bool = False,
) -> int:
    db = _open(path, priority, readonly=True)
    try:
        return db.count_smart_review_candidates(
            version=version,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            review_status=review_status,
            query=query,
            require_reviewed_reference=require_reviewed_reference,
        )
    finally:
        db.close()


def list_moddb_smart_review_runs_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db = _open(path, priority, readonly=True)
    try:
        return db.list_smart_review_runs(limit=limit)
    finally:
        db.close()


def get_moddb_smart_review_run_from_path(
    path: str | Path, priority: tuple[int, ...], run_id: str
) -> dict[str, Any] | None:
    db = _open(path, priority, readonly=True)
    try:
        return db.get_smart_review_run(run_id)
    finally:
        db.close()


def save_moddb_smart_review_draft_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
    draft_zh_tw: str,
) -> str:
    db = _open(path, priority, readonly=False)
    try:
        return db.save_smart_review_draft(run_id, ordinal, draft_zh_tw)
    finally:
        db.close()


def apply_moddb_smart_review_candidate_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
    *,
    draft_zh_tw: str | None = None,
) -> str:
    db = _open(path, priority, readonly=False)
    try:
        return db.apply_smart_review_candidate(
            run_id, ordinal, draft_zh_tw=draft_zh_tw
        )
    finally:
        db.close()


def reject_moddb_smart_review_candidate_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
) -> str:
    db = _open(path, priority, readonly=False)
    try:
        return db.reject_smart_review_candidate(run_id, ordinal)
    finally:
        db.close()


__all__ = [
    "SmartReviewCandidate",
    "apply_moddb_smart_review_candidate_from_path",
    "count_moddb_smart_review_candidates_for_run_from_path",
    "count_moddb_smart_review_candidates_from_path",
    "get_moddb_smart_review_run_from_path",
    "list_moddb_smart_review_candidates_from_path",
    "list_moddb_smart_review_runs_from_path",
    "preview_moddb_smart_review_from_path",
    "reject_moddb_smart_review_candidate_from_path",
    "run_moddb_smart_review_from_path",
    "save_moddb_smart_review_draft_from_path",
    "smart_review_model_options",
]
