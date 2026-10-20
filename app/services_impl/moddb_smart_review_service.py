"""提供本地 ModDB 智慧校對的背景服務介面。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from translation_tool.core.format_diagnostics import (
    FormatDiagnosticsResult,
    analyze_format_diagnostics,
)
from translation_tool.core.lm_config_rules import (
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

SMART_REVIEW_DEFAULT_SOURCE = SRC_AI


def smart_review_format_diagnostics(
    source: str, translated: str, *, kind: str = "lang"
) -> FormatDiagnosticsResult:
    """回傳 ModDB 智慧校對使用的有界格式診斷結果。"""
    return analyze_format_diagnostics(source, translated, kind=kind)


def smart_review_model_options(config: dict | None = None) -> list[tuple[str, str]]:
    """依智慧校對提供者設定回傳可選模型，並保留目前設定的模型。"""
    cfg = load_config() if config is None else config
    lm_config = cfg.get("lm_translator", {})
    if not isinstance(lm_config, dict):
        return []

    provider = str(lm_config.get("provider") or "gemini").strip().lower()
    if provider == "chatgpt":
        model_name = str(lm_config.get("chatgpt_model") or "").strip()
        return [(model_name, model_name)] if model_name else []

    models = lm_config.get("models", {})
    if not isinstance(models, dict):
        return []

    options = [
        (str(name), str(name))
        for name, settings in models.items()
        if isinstance(settings, dict) and settings.get("enabled", False)
    ]
    preferred = "gemini-3.1-flash-lite"
    return sorted(options, key=lambda option: option[0] != preferred)


def _open(
    path: str | Path, priority: tuple[int, ...], *, readonly: bool
) -> TranslationDB:
    """以指定模式開啟資料庫，供智慧校對服務執行操作。"""
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
    """從指定資料庫建立智慧校對預覽，不修改翻譯資料。"""
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
    """從指定資料庫執行智慧校對並逐步回傳執行進度。"""
    cfg = load_config()
    lm_config = cfg.get("lm_translator", {}) if isinstance(cfg, dict) else {}
    if not isinstance(lm_config, dict):
        lm_config = {}
    provider = get_translation_provider()
    db = _open(path, priority, readonly=False)
    try:
        return run_smart_review(
            db,
            model_name=model_name,
            # Gemini key selection/rotation belongs to the shared smart-review
            # runner; do not claim one key here and reuse it for every batch.
            api_key="",
            provider=provider,
            version=version,
            limit=limit,
            batch_size=batch_size,
            progress=progress,
            lm_config=lm_config,
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
    """從指定資料庫列出智慧校對候選項目。"""
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
    """計算指定智慧校對執行中的候選數量。"""
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
    """計算指定資料庫目前範圍內的智慧校對候選數量。"""
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


def get_moddb_smart_review_run_from_path(
    path: str | Path, priority: tuple[int, ...], run_id: str
) -> dict[str, Any] | None:
    """讀取指定資料庫中的智慧校對執行狀態。"""
    db = _open(path, priority, readonly=True)
    try:
        return db.get_smart_review_run(run_id)
    finally:
        db.close()


def list_moddb_smart_review_runs_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """從指定資料庫列出可重新開啟的智慧校對批次。"""
    db = _open(path, priority, readonly=True)
    try:
        return db.list_smart_review_runs(limit=limit, offset=offset)
    finally:
        db.close()


def save_moddb_smart_review_draft_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
    draft_zh_tw: str,
) -> str:
    """保存指定候選的人工智慧校對草稿，不直接改寫翻譯。"""
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
    """以 CAS 條件套用指定智慧校對候選。"""
    db = _open(path, priority, readonly=False)
    try:
        return db.apply_smart_review_candidate(run_id, ordinal, draft_zh_tw=draft_zh_tw)
    finally:
        db.close()


def reject_moddb_smart_review_candidate_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
) -> str:
    """拒絕指定智慧校對候選並保留目前翻譯。"""
    db = _open(path, priority, readonly=False)
    try:
        return db.reject_smart_review_candidate(run_id, ordinal)
    finally:
        db.close()


__all__ = [
    "SMART_REVIEW_DEFAULT_SOURCE",
    "SmartReviewCandidate",
    "apply_moddb_smart_review_candidate_from_path",
    "count_moddb_smart_review_candidates_for_run_from_path",
    "count_moddb_smart_review_candidates_from_path",
    "get_moddb_smart_review_run_from_path",
    "list_moddb_smart_review_runs_from_path",
    "list_moddb_smart_review_candidates_from_path",
    "preview_moddb_smart_review_from_path",
    "reject_moddb_smart_review_candidate_from_path",
    "run_moddb_smart_review_from_path",
    "save_moddb_smart_review_draft_from_path",
    "smart_review_format_diagnostics",
    "smart_review_model_options",
]
