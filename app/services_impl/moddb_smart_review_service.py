"""提供本地 ModDB 智慧校對的背景服務介面。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from translation_tool.core.batch_events import (
    FORMATTED_BATCH_EVENT_KINDS,
    batch_event_level,
    format_batch_event,
)
from translation_tool.core.batch_timing import (
    format_duration as format_timing_duration,
)
from translation_tool.core.batch_timing import timing_seconds
from translation_tool.core.format_diagnostics import (
    FormatDiagnosticsResult,
    analyze_format_diagnostics,
)
from translation_tool.core.lm_config_rules import (
    get_translation_provider,
)
from translation_tool.translation_db import (
    SmartReviewCandidate,
    SmartReviewMark,
    SmartReviewRunResult,
    SmartReviewSnapshot,
    TranslationDB,
)
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.translation_db.smart_review import (
    SMART_REVIEW_DEFAULT_BATCH_SIZE as _SMART_REVIEW_DEFAULT_BATCH_SIZE,
)
from translation_tool.translation_db.smart_review import (
    SMART_REVIEW_MANUAL_EDIT_STATUSES,
    resume_smart_review,
    run_smart_review,
)
from translation_tool.translation_db.smart_review import (
    SMART_REVIEW_MAX_BATCH_SIZE as _SMART_REVIEW_MAX_BATCH_SIZE,
)
from translation_tool.translation_db.smart_review_history import SmartReviewHistoryStore
from translation_tool.translation_db.smart_review_planner import plan_new_smart_review
from translation_tool.translation_db.smart_review_status import SmartReviewStatus
from translation_tool.utils.config_manager import get_smart_review_settings, load_config

SMART_REVIEW_DEFAULT_SOURCE = SRC_AI
SMART_REVIEW_DEFAULT_BATCH_SIZE = _SMART_REVIEW_DEFAULT_BATCH_SIZE
SMART_REVIEW_MAX_BATCH_SIZE = _SMART_REVIEW_MAX_BATCH_SIZE


@dataclass(frozen=True)
class SmartReviewBatchItemResult:
    """單筆批量審查的結果；保留 ordinal 供 UI 對應原候選。"""

    run_id: str
    ordinal: int
    key: str
    status: str
    error: str | None = None


@dataclass(frozen=True)
class SmartReviewBatchResult:
    """批量審查的部分成功結果與取消狀態。"""

    action: str
    requested: int
    processed: int
    cancelled: bool
    items: tuple[SmartReviewBatchItemResult, ...]

    def count(self, *statuses: str) -> int:
        """計算指定 service status 的筆數，讓 UI 不需理解資料庫細節。"""
        wanted = set(statuses)
        return sum(item.status in wanted for item in self.items)


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


def preview_moddb_smart_review_plan_from_path(
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
    use_recent_history: bool = True,
    skip_valid_history: bool = True,
) -> dict[str, int]:
    """回傳與執行共用的唯讀歷史規劃 KPI，不呼叫 provider。"""
    db = _open(path, priority, readonly=True)
    try:
        snapshots = db.smart_review_candidates(
            version=version,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            review_status=review_status,
            query=query,
            limit=limit,
        )
        consulted, historical = (
            SmartReviewHistoryStore(db.path).recent_candidate_payloads(max_runs=2)
            if use_recent_history
            else (0, [])
        )
        plan = plan_new_smart_review(
            snapshots,
            historical,
            skip_valid_history=skip_valid_history,
            history_runs_consulted=consulted,
        )
        return {
            "selected_count": len(plan.selected),
            "reused_count": plan.reused_count,
            "actual_ai_count": plan.actual_ai_count,
            "history_runs_consulted": plan.history_runs_consulted,
            "pending_review_count": sum(
                1
                for result in plan.reused_results
                if result.get("decision") != "no_change"
            ),
        }
    finally:
        db.close()


def run_moddb_smart_review_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    *,
    model_name: str,
    version: str | None = None,
    limit: int = 1000,
    batch_size: int | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    require_reviewed_reference: bool = False,
    prioritize_named_keys: bool = False,
    mod_id: str | None = None,
    kind: str | None = None,
    effective_source: int | None = SRC_AI,
    review_status: str = "unreviewed",
    query: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    task_id: str | None = None,
    use_recent_history: bool = False,
    skip_valid_history: bool = False,
    resume_run_id: str | None = None,
) -> SmartReviewRunResult:
    """從指定資料庫執行智慧校對並逐步回傳執行進度。"""
    cfg = load_config()
    lm_config = cfg.get("lm_translator", {}) if isinstance(cfg, dict) else {}
    if not isinstance(lm_config, dict):
        lm_config = {}
    if batch_size is None:
        batch_size = get_smart_review_settings(cfg)["default_batch_size"]
    provider = get_translation_provider()
    db = _open(path, priority, readonly=False)
    try:
        if resume_run_id:
            return resume_smart_review(
                db,
                run_id=resume_run_id,
                progress=progress,
                lm_config=lm_config,
                cancel_check=cancel_check,
                task_id=task_id,
            )
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
            task_id=task_id,
            use_recent_history=use_recent_history,
            skip_valid_history=skip_valid_history,
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


def load_moddb_smart_review_candidate_page_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    *,
    status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    known_total: int | None = None,
) -> tuple[int, list[SmartReviewCandidate]]:
    """在單次唯讀連線中取得候選總數與目前頁面的候選。"""
    db = _open(path, priority, readonly=True)
    try:
        return db.load_smart_review_candidate_page(
            run_id,
            status=status,
            limit=limit,
            offset=offset,
            known_total=known_total,
        )
    finally:
        db.close()


class SmartReviewReadSession:
    """同一個唯讀連線上的批次查詢；讓一次導覽操作只開一次資料庫。"""

    def __init__(self, db: TranslationDB) -> None:
        """包裝已開啟的唯讀資料庫。"""
        self._db = db

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """讀取批次資訊。"""
        return self._db.get_smart_review_run(run_id)

    def run_breakdown(self, run_id: str, *, error_limit: int = 5) -> dict[str, Any]:
        """讀取候選狀態分佈與最常見的驗證失敗原因。"""
        return self._db.smart_review_run_breakdown(run_id, error_limit=error_limit)

    def candidate_page(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        known_total: int | None = None,
    ) -> tuple[int, list[SmartReviewCandidate]]:
        """取得候選總數與指定範圍的候選。"""
        return self._db.load_smart_review_candidate_page(
            run_id,
            status=status,
            limit=limit,
            offset=offset,
            known_total=known_total,
        )


@contextmanager
def moddb_smart_review_read_session(
    path: str | Path, priority: tuple[int, ...]
) -> Iterator[SmartReviewReadSession]:
    """開啟一個唯讀連線供多次查詢共用，離開時關閉。"""
    db = _open(path, priority, readonly=True)
    try:
        yield SmartReviewReadSession(db)
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


def get_moddb_smart_review_run_breakdown_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    *,
    error_limit: int = 5,
) -> dict[str, Any]:
    """讀取指定執行的候選狀態分佈與最常見的驗證失敗原因。"""
    db = _open(path, priority, readonly=True)
    try:
        return db.smart_review_run_breakdown(run_id, error_limit=error_limit)
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


def realign_moddb_smart_review_candidate_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
) -> str:
    """把可人工編輯候選的快照對齊目前資料庫，不改狀態與翻譯。"""
    db = _open(path, priority, readonly=False)
    try:
        return db.realign_smart_review_candidate(run_id, ordinal)
    finally:
        db.close()


def apply_moddb_smart_review_manual_edit_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    run_id: str,
    ordinal: int,
    draft_zh_tw: str,
) -> str:
    """以人工編輯的譯文覆蓋套用候選（含已套用、無效、需補 context 等狀態）。"""
    db = _open(path, priority, readonly=False)
    try:
        return db.apply_smart_review_manual_edit(run_id, ordinal, draft_zh_tw)
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


def _apply_moddb_smart_review_fast_batch(
    db: TranslationDB,
    selected: tuple[SmartReviewCandidate, ...],
    *,
    action: str,
    progress: Callable[[dict[str, Any]], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> SmartReviewBatchResult | None:
    """使用資料層合併 JSON 寫入；舊版 SQLite-only 批次回傳 ``None``。"""
    if not selected or len({candidate.run_id for candidate in selected}) != 1:
        return None
    batch_apply = getattr(db, "apply_smart_review_candidate_batch", None)
    if not callable(batch_apply):
        return None
    results: list[SmartReviewBatchItemResult] = []
    run_id = selected[0].run_id

    def fast_progress(
        _processed: int,
        _total: int,
        batch_item: tuple[int, str, str | None],
    ) -> None:
        ordinal, status, error = batch_item
        candidate = selected[len(results)]
        item = SmartReviewBatchItemResult(
            candidate.run_id,
            ordinal,
            candidate.key,
            status,
            error,
        )
        results.append(item)
        if progress is not None:
            progress(
                {
                    "processed": len(results),
                    "total": len(selected),
                    "item": item,
                }
            )

    fast_result = batch_apply(
        run_id,
        tuple(
            {
                "run_id": candidate.run_id,
                "ordinal": candidate.ordinal,
                "status": candidate.status,
                "draft_zh_tw": candidate.draft_zh_tw,
                "suggested_zh_tw": candidate.suggested_zh_tw,
            }
            for candidate in selected
        ),
        action=action,
        progress_cancel=cancel_check,
        progress=fast_progress,
    )
    if fast_result is None:
        return None
    fast_items, cancelled = fast_result
    # Reconstruct only for adapters that omit the progress callback.
    if len(results) != len(fast_items):
        results = [
            SmartReviewBatchItemResult(
                selected[index].run_id,
                ordinal,
                selected[index].key,
                status,
                error,
            )
            for index, (ordinal, status, error) in enumerate(fast_items)
        ]
    return SmartReviewBatchResult(
        action=action,
        requested=len(selected),
        processed=len(results),
        cancelled=cancelled,
        items=tuple(results),
    )


def _apply_moddb_smart_review_legacy_batch(
    db: TranslationDB,
    selected: tuple[SmartReviewCandidate, ...],
    *,
    action: str,
    progress: Callable[[dict[str, Any]], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> tuple[list[SmartReviewBatchItemResult], bool]:
    """保留舊版 SQLite-only 批次的逐筆處理與部分成功語意。"""
    results: list[SmartReviewBatchItemResult] = []
    cancelled = False
    for index, candidate in enumerate(selected, start=1):
        if cancel_check is not None and cancel_check():
            cancelled = True
            break
        try:
            error = None
            if action == "apply":
                status = db.apply_smart_review_candidate(
                    candidate.run_id,
                    candidate.ordinal,
                    draft_zh_tw=candidate.draft_zh_tw,
                )
            elif action == "reject":
                status = db.reject_smart_review_candidate(
                    candidate.run_id, candidate.ordinal
                )
            else:
                status, error = _override_stale_smart_review_candidate(db, candidate)
            item = SmartReviewBatchItemResult(
                candidate.run_id,
                candidate.ordinal,
                candidate.key,
                str(status),
                error,
            )
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not stop the batch
            item = SmartReviewBatchItemResult(
                candidate.run_id,
                candidate.ordinal,
                candidate.key,
                "error",
                str(exc) or type(exc).__name__,
            )
        results.append(item)
        if progress is not None:
            progress({"processed": index, "total": len(selected), "item": item})
    return results, cancelled


def apply_moddb_smart_review_batch_from_path(
    path: str | Path,
    priority: tuple[int, ...],
    candidates: Sequence[SmartReviewCandidate],
    *,
    action: str = "apply",
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> SmartReviewBatchResult:
    """在背景工作中處理一組候選，沿用單筆 CAS 與歷史規則。

    ``apply`` 與 ``reject`` 維持一般批量的安全語意；``override`` 是明確的過期覆蓋
    動作，只接受 stale 候選，先把 JSON 的目前譯文快照對齊資料庫現況，再沿用人工
    編輯的 CAS 路徑套用草稿／建議。JSON run 會在資料層共用一次歷史鎖並合併
    分片寫入；每筆 SQLite CAS 仍可獨立成功或略過，方便大型批次在遇到單筆 stale、
    格式問題或已人工審核資料時繼續處理其他候選。
    """
    if action not in {"apply", "reject", "override"}:
        raise ValueError(f"未知的智慧校對批量動作：{action}")
    selected = tuple(candidates)
    db = _open(path, priority, readonly=False)
    try:
        fast_result = _apply_moddb_smart_review_fast_batch(
            db,
            selected,
            action=action,
            progress=progress,
            cancel_check=cancel_check,
        )
        if fast_result is not None:
            return fast_result
        results, cancelled = _apply_moddb_smart_review_legacy_batch(
            db,
            selected,
            action=action,
            progress=progress,
            cancel_check=cancel_check,
        )
    finally:
        db.close()
    if not cancelled and cancel_check is not None:
        cancelled = bool(cancel_check())
    return SmartReviewBatchResult(
        action=action,
        requested=len(selected),
        processed=len(results),
        cancelled=cancelled,
        items=tuple(results),
    )


def _override_stale_smart_review_candidate(
    db: TranslationDB, candidate: SmartReviewCandidate
) -> tuple[str, str | None]:
    """以明確覆蓋語意處理一筆 stale JSON 候選。

    ``realign_smart_review_candidate`` 會在 JSON history lock 下把
    ``current_zh_tw`` 與其他 CAS snapshot 欄位更新成資料庫現況；接著人工編輯套用
    再以這份新快照做 CAS。兩個步驟不是跨 SQLite／JSON 的單一交易，但第二步仍會
    阻止對齊後再次變更的資料被覆蓋，且 SQLite audit 可供 JSON projection 對帳。
    """
    if candidate.status != SmartReviewStatus.STALE:
        return "not_stale", "候選狀態不是已過期"
    proposed = candidate.draft_zh_tw or candidate.suggested_zh_tw
    if not proposed or not proposed.strip():
        return "not_ready", "沒有可覆蓋的草稿或 AI 建議"

    aligned = db.realign_smart_review_candidate(candidate.run_id, candidate.ordinal)
    if aligned not in {"realigned", "unchanged"}:
        return str(aligned), "無法更新 JSON 目前譯文快照"

    status = db.apply_smart_review_manual_edit(
        candidate.run_id, candidate.ordinal, proposed
    )
    if status == "changed":
        return SmartReviewStatus.STALE, "對齊後資料庫再次變更"
    if status == "manual_reviewed":
        return "manual_reviewed", "目前譯文已人工審核，不進行批量覆蓋"
    return str(status), None


__all__ = [
    "FORMATTED_BATCH_EVENT_KINDS",
    "SMART_REVIEW_DEFAULT_BATCH_SIZE",
    "SMART_REVIEW_DEFAULT_SOURCE",
    "SMART_REVIEW_MANUAL_EDIT_STATUSES",
    "SmartReviewBatchItemResult",
    "SmartReviewBatchResult",
    "SmartReviewCandidate",
    "SmartReviewMark",
    "SmartReviewReadSession",
    "SmartReviewStatus",
    "apply_moddb_smart_review_batch_from_path",
    "apply_moddb_smart_review_candidate_from_path",
    "apply_moddb_smart_review_manual_edit_from_path",
    "batch_event_level",
    "count_moddb_smart_review_candidates_for_run_from_path",
    "count_moddb_smart_review_candidates_from_path",
    "format_batch_event",
    "format_timing_duration",
    "get_moddb_smart_review_run_breakdown_from_path",
    "get_moddb_smart_review_run_from_path",
    "list_moddb_smart_review_candidates_from_path",
    "list_moddb_smart_review_runs_from_path",
    "load_moddb_smart_review_candidate_page_from_path",
    "moddb_smart_review_read_session",
    "preview_moddb_smart_review_from_path",
    "realign_moddb_smart_review_candidate_from_path",
    "reject_moddb_smart_review_candidate_from_path",
    "run_moddb_smart_review_from_path",
    "save_moddb_smart_review_draft_from_path",
    "smart_review_format_diagnostics",
    "smart_review_model_options",
    "timing_seconds",
]
