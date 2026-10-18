"""Mod 資料庫批次機翻服務：把資料庫裡「沒有任何譯文」的條目交給機翻引擎，結果寫回資料庫。

流程：先沿用其他版本已有的相同譯文（不呼叫 AI）→ 挑出仍未翻譯的條目（可限定模組與筆數）
→ 分批機翻 → 每批檢查特殊字元（換行、`§`、`%s` 等）是否與原文一致 → 一致者以「AI 機翻」
來源寫回。只填空白、不覆蓋任何既有譯文；來源優先序最低，之後的人工或匯入譯文會自動蓋過。結果預設也會寫入翻譯快取（可關閉）。
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.moddb_service import (
    database_problem,
    open_database,
    warm_stats_quietly,
)
from app.services_impl.pipelines._pipeline_logging import (
    ensure_pipeline_logging,
    mirror_session_log,
)
from app.tasks.task_session import add_log_unmirrored
from translation_tool.core.lm_batch_planner import (
    count_planned_batches,
    group_items_by_batch_key,
)
from translation_tool.core.lm_moddb_context import attach_moddb_context
from translation_tool.core.lm_translator_main import (
    estimate_batch_fixed_input_tokens,
    translate_batch_smart,
)
from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size
from translation_tool.core.lm_translator_skeleton import (
    TranslatorHooks,
    run_translator_skeleton,
)
from translation_tool.translation_db.models import WriteBackItem
from translation_tool.translation_db.quality import translation_issues
from translation_tool.translation_db.run_progress import (
    RunProgress,
    estimate_batches,
    format_duration,
    format_live,
    tick_live,
)
from translation_tool.translation_db.schema import cache_type_for_kind
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.cache_manager import add_to_cache, save_translation_cache
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    is_cancelled,
)
from translation_tool.utils.config_manager import load_config, resolve_project_path
from translation_tool.utils.replace_rules import find_replacement_conflicts

# View 經由本模組取用進度格式化函式（View 不直接 import translation_tool 引擎）
__all__ = [
    "ABORT_AFTER_FAILURES",
    "DEFAULT_LIMIT",
    "TranslateOptions",
    "TranslateReport",
    "estimate_batch_count",
    "estimate_scope_batches",
    "estimate_scope_plan",
    "format_duration",
    "format_live",
    "plan_batches",
    "run_moddb_translate_service",
    "tick_live",
]

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 2000
_FLAGGED_LOG_LIMIT = 20
DB_FLUSH_RETRIES = 3  # 寫入資料庫失敗時的重試次數
ABORT_AFTER_FAILURES = 500  # 連續這麼多筆都沒翻成功就中止（API Key／模型設定有問題）


@dataclass(frozen=True)
class TranslateOptions:
    """批次機翻的選項。"""

    version: str
    mod_ids: tuple[str, ...] = ()  # 空 = 全部模組
    limit: int = DEFAULT_LIMIT  # 單次最多翻幾筆；0 = 不限
    dry_run: bool = False
    reuse_other_versions: bool = True
    write_cache: bool = True  # 結果除了寫入資料庫，也寫入翻譯快取（「快取資料」資料夾）
    # 快取寫入方式：False = 寫入編號分片（滿 2,500 筆自動換下一片）；True = 每次另存成獨立的時間戳分片
    new_cache_shard: bool = False
    # 格式修復：連「只有實際換行數不同」的項目也送 AI，且輸出必須與原文換行數一致
    repair_newline_only: bool = False
    # 格式修復：原文與舊譯文都相同的其他版本條目一併改成新譯文
    repair_sync_other_versions: bool = False


@dataclass
class TranslateReport:
    """批次機翻的結果統計。"""

    candidates: int = 0  # 這次要處理的未翻譯筆數
    reused: int = 0  # 沿用其他版本譯文（未呼叫 AI）
    translated: int = 0  # AI 已回傳
    written: int = 0  # 已寫入資料庫
    flagged: int = 0  # 特殊字元與原文不一致，未寫入
    status: str = "DONE"
    dry_run: bool = False
    remaining: int = 0  # 該範圍內仍未翻譯的筆數
    flagged_samples: list[str] = field(default_factory=list)
    # 特殊字元不一致而沒寫入的條目：條目 id → AI 回傳的譯文（畫面「檢視」用，預填給人工修正）
    flagged_entries: dict[int, str] = field(default_factory=dict)
    batches: int = 0  # 實際完成的批數
    planned_batches: int = 0  # 開始時依相同 planner 估出的批數下界
    elapsed_sec: float = 0.0
    # Structured counters shared by the batch loop, backend log, and UI summary.
    ai_response: int = 0
    quality_retry_candidates: int = 0
    quality_retry_fixed: int = 0
    quality_retry_unresolved: int = 0
    quality_retry_quota_failed: int = 0
    quota_failed_items: int = 0
    contract_failed_items: int = 0
    max_tokens_items: int = 0
    quality_rejected: int = 0
    source_fallback: int = 0
    cache_written: int = 0
    db_written: int = 0
    untranslated: int = 0
    oversized_items: int = 0
    quality_issue_records: list[dict[str, Any]] = field(default_factory=list)
    quality_issues_file: str | None = None
    replace_rule_conflicts: int = 0
    replace_rule_conflict_details: list[dict[str, Any]] = field(default_factory=list)
    contract_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "reused": self.reused,
            "translated": self.translated,
            "written": self.written,
            "flagged": self.flagged,
            "flagged_entries": dict(self.flagged_entries),
            "status": self.status,
            "dry_run": self.dry_run,
            "remaining": self.remaining,
            "batches": self.batches,
            "planned_batches": self.planned_batches,
            "elapsed_sec": self.elapsed_sec,
            "ai_response": self.ai_response,
            "quality_retry_candidates": self.quality_retry_candidates,
            "quality_retry_fixed": self.quality_retry_fixed,
            "quality_retry_unresolved": self.quality_retry_unresolved,
            "quality_retry_quota_failed": self.quality_retry_quota_failed,
            "quota_failed_items": self.quota_failed_items,
            "contract_failed_items": self.contract_failed_items,
            "max_tokens_items": self.max_tokens_items,
            "quality_rejected": self.quality_rejected,
            "source_fallback": self.source_fallback,
            "cache_written": self.cache_written,
            "db_written": self.db_written,
            "untranslated": self.untranslated,
            "oversized_items": self.oversized_items,
            "quality_issues_file": self.quality_issues_file,
            "replace_rule_conflicts": self.replace_rule_conflicts,
        }


def _update_remaining(report: TranslateReport) -> None:
    """Keep the KPI scoped to this run and retain items that were not written.

    Flagged results are processed but deliberately not written back, so
    ``candidates - written`` includes both unprocessed and flagged items.
    """
    report.remaining = max(0, report.candidates - report.written)


def build_items(
    rows: list[tuple[int, str, str, str, str]],
    *,
    check: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """資料庫條目轉成引擎項目；``check`` 可在每列轉換前執行 deadline/cancel 檢查。"""
    items = []
    for eid, kind, mod_id, key, en_us in rows:
        if check is not None:
            check()
        items.append(
            {
                "file": f"{mod_id}/{kind}",
                "path": key,
                "text": en_us,
                "source_text": en_us,
                # Guide (.md/.txt) keeps the Patchouli prompt flow but belongs
                # to the md cache bucket; .txt is intentionally included.
                "cache_type": cache_type_for_kind(kind),
                # The shared loop uses this boundary to prevent cross-module or
                # cross-kind payloads. Session state is attached later because
                # build_items is also used by preview/tests without a TaskSession.
                "_batch_group": (str(mod_id), str(kind)),
                "_entry_id": eid,
                "_kind": kind,
                "_mod_id": mod_id,
            }
        )
    return items


def plan_batches(
    items: list[dict[str, Any]],
    *,
    check: Callable[[], None] | None = None,
    lm_cfg: dict[str, Any] | None = None,
    fixed_input_tokens_for_batch: Callable[[list[dict[str, Any]]], float] | None = None,
) -> int:
    """依實際 cache/profile、模組種類邊界與固定 prompt token 規劃批次。"""
    if lm_cfg is None:
        from translation_tool.utils.config_manager import load_config

        config = load_config()
        lm_cfg = config.get("lm_translator", {}) if isinstance(config, dict) else {}
    fixed = fixed_input_tokens_for_batch
    if fixed is None and lm_cfg.get("token_budget_enabled", True):
        fixed = partial(estimate_batch_fixed_input_tokens, lm_cfg=lm_cfg)
    return count_planned_batches(
        items,
        lm_cfg=lm_cfg,
        batch_size_for_type=lambda cache_type: _get_default_batch_size(
            cache_type, None
        ),
        fixed_input_tokens_for_batch=fixed,
        check=check,
    )


def _attach_translation_sessions(
    items: list[dict[str, Any]], *, operation_id: str, db, version: str | None = None
) -> None:
    """Attach one session/glossary to every kind belonging to the same module."""
    attach_moddb_context(
        items,
        operation_id=operation_id,
        mc_version=version,
        reviewed_glossary_loader=db.reviewed_lang_glossary,
    )


def estimate_batch_count(total: int, cache_type: str = "lang") -> int:
    """筆數 → 預估批數（畫面在開始前顯示；依設定的每批筆數）。"""
    if total <= 0:
        return 0
    return estimate_batches(
        {cache_type: total}, lambda k: _get_default_batch_size(k, None)
    )


def estimate_scope_plan(
    db,
    *,
    version: str,
    mod_ids: tuple[str, ...] | list[str] = (),
    limit: int = 0,
    reuse_other_versions: bool = True,
) -> tuple[int, int]:
    """Return the exact selected AI rows and their shared planned batch count."""
    rows = db.untranslated_entries(
        version,
        list(mod_ids),
        limit or None,
        exclude_reusable=reuse_other_versions,
    )
    items = group_items_by_batch_key(build_items(rows))
    _attach_translation_sessions(
        items,
        operation_id=f"count:{version}:{','.join(mod_ids)}",
        db=db,
        version=version,
    )
    return len(rows), plan_batches(items)


def estimate_scope_batches(
    db,
    *,
    version: str,
    mod_ids: tuple[str, ...] | list[str] = (),
    limit: int = 0,
    reuse_other_versions: bool = True,
) -> int:
    """Return the planned batch count for the exact filtered AI rows."""
    return estimate_scope_plan(
        db,
        version=version,
        mod_ids=mod_ids,
        limit=limit,
        reuse_other_versions=reuse_other_versions,
    )[1]


def _log(session, text: str, level: str = "info") -> None:
    mirror_session_log(session, logger, text, level, prefix="[Mod 資料庫機翻] ")


def _log_backend(text: str, level: str = "info") -> None:
    """Write detail to the backend without feeding it back into the UI."""
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        text,
        extra={"ui_mirrored": True},
    )


def _log_split(session, ui_text: str, backend_text: str, level: str = "info") -> None:
    """Emit one concise UI line and one detailed backend record."""
    add_log_unmirrored(session, ui_text, level)
    _log_backend(backend_text, level)


def _sync_loop_result(report: TranslateReport, result) -> None:
    """Copy shared-loop accounting into the single Mod DB report object."""
    report.ai_response = int(getattr(result, "ai_response", report.translated) or 0)
    report.translated = report.ai_response
    report.cache_written = int(
        getattr(result, "cache_written", report.cache_written) or 0
    )
    report.source_fallback = int(
        getattr(result, "source_fallback", report.source_fallback) or 0
    )
    report.quality_rejected = int(
        getattr(result, "quality_rejected", report.quality_rejected) or 0
    )
    report.untranslated = int(getattr(result, "untranslated", report.untranslated) or 0)
    report.max_tokens_items = int(
        getattr(result, "max_tokens_items", report.max_tokens_items) or 0
    )
    report.contract_failed_items = int(
        getattr(result, "contract_failed_items", report.contract_failed_items) or 0
    )
    report.quota_failed_items = int(
        getattr(result, "quota_failed_items", report.quota_failed_items) or 0
    )
    report.oversized_items = int(
        getattr(result, "oversized_items", report.oversized_items) or 0
    )
    report.quality_retry_candidates = int(
        getattr(result, "quality_retry_candidates", report.quality_retry_candidates)
        or 0
    )
    report.quality_retry_fixed = int(
        getattr(result, "quality_retry_fixed", report.quality_retry_fixed) or 0
    )
    report.quality_retry_unresolved = int(
        getattr(result, "quality_retry_unresolved", report.quality_retry_unresolved)
        or 0
    )
    report.quality_retry_quota_failed = int(
        getattr(
            result,
            "quality_retry_quota_failed",
            report.quality_retry_quota_failed,
        )
        or 0
    )
    report.contract_errors = list(
        getattr(result, "contract_errors", report.contract_errors) or ()
    )


def _write_quality_issue_file(session, report: TranslateReport) -> None:
    """Persist every quality issue while keeping the UI to a bounded summary."""
    if not report.quality_issue_records:
        return
    raw_task_id = str(
        getattr(session, "task_id", None)
        or getattr(session, "id", None)
        or "unknown-task"
    )
    task_id = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_task_id)
    path = (
        get_data_root()
        / "logs"
        / datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
        / f"quality-issues-{task_id}.json"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.quality_issue_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        _log_backend(
            f"品質問題完整清單寫入失敗：{type(exc).__name__}: {exc}", "warning"
        )
        return
    report.quality_issues_file = str(path)


def _collect_replace_rule_conflicts() -> list[dict[str, Any]]:
    """Read-only conflict detection for the active replace-rules file."""
    config = load_config()
    translator = config.get("translator", {}) if isinstance(config, dict) else {}
    configured_path = (
        translator.get("replace_rules_path", "replace_rules.json")
        if isinstance(translator, dict)
        else "replace_rules.json"
    )
    try:
        path = resolve_project_path(str(configured_path))
        raw_rules = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return []
    if not isinstance(raw_rules, list):
        return []
    conflicts = find_replacement_conflicts(raw_rules)
    return [
        {
            "source_index": source_index,
            "source": source,
            "destination_index": destination_index,
            "destination": destination,
        }
        for source_index, source, destination_index, destination in conflicts
    ]


def _record_backend_summary(report: TranslateReport) -> None:
    """Record the full structured summary and non-UI diagnostic details."""
    payload = report.as_dict()
    payload.update(
        {
            "quality_issue_records": report.quality_issue_records,
            "contract_errors": report.contract_errors,
            "replace_rule_conflict_details": report.replace_rule_conflict_details,
        }
    )
    _log_backend(
        "Mod DB translation summary "
        + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "warning" if report.status != "DONE" else "info",
    )


def _noop_cache(*_args, **_kwargs) -> bool:
    """不寫翻譯快取時使用（結果只存進資料庫）。"""
    return True


def _select_rows(session, db, options: TranslateOptions, report: TranslateReport):
    """沿用其他版本譯文後挑出要機翻的條目；預覽或沒有條目時回傳 None（已寫好摘要）。"""
    mods = list(options.mod_ids)
    if options.reuse_other_versions and not options.dry_run:
        report.reused = db.reuse_from_other_versions(options.version, mods)
        if report.reused:
            _log(session, f"♻️ 沿用其他版本相同譯文 {report.reused} 筆（未呼叫 AI）")
    # 預覽不寫入，所以沿用的筆數用查詢估算，並從「要送 AI 的條目」中排除
    preview_reuse = options.dry_run and options.reuse_other_versions
    rows = db.untranslated_entries(
        options.version, mods, options.limit or None, exclude_reusable=preview_reuse
    )
    report.candidates = len(rows)
    report.remaining = (
        db.count_untranslated(options.version, mods)
        if options.dry_run
        else report.candidates
    )
    if options.dry_run:
        if preview_reuse:
            report.reused = db.count_reusable(options.version, mods)
            _log(
                session,
                f"♻️ 預覽：其中約 {report.reused:,} 筆其他版本已有相同譯文，"
                "開始機翻時會直接沿用（不呼叫 AI）；"
                "只有「模組、鍵值、原文都相同」才算，原文不同的不會沿用",
            )
        preview_items = group_items_by_batch_key(build_items(rows), check=None)
        _attach_translation_sessions(
            preview_items,
            operation_id=f"preview:{getattr(session, 'id', '') or id(session)}",
            db=db,
            version=options.version,
        )
        report.planned_batches = plan_batches(preview_items)
        _log(
            session,
            f"🔎 預覽：{report.remaining:,} 筆未翻譯，本次將送 AI 翻譯 {len(rows):,} 筆"
            f"，預估約 {report.planned_batches:,} 批（未呼叫 AI、未寫入；"
            "實際時間依 API 速度而定，開始後每批結束會更新預估）",
        )
        for _eid, _kind, mod_id, key, en_us in rows[:5]:
            _log(session, f"　{mod_id} / {key}：{en_us[:60]!r}")
    elif not rows:
        _log(session, "沒有需要機翻的條目")
    if options.dry_run or not rows:
        session.set_progress(1.0)
        session.set_summary(report.as_dict())
        return None
    return rows


def _flush_buffer(session, db, version, buffer, report, failures) -> None:
    """把緩衝寫進資料庫；寫入成功才清緩衝，失敗會重試，仍失敗就留著並記下錯誤。"""
    if not buffer:
        return
    for attempt in range(1, DB_FLUSH_RETRIES + 1):
        try:
            done = db.write_back(version, list(buffer), fill_other_versions=True)
        except Exception as exc:  # noqa: BLE001 - 持久化失敗需重試並回報
            failures["db_error"] = f"{type(exc).__name__}: {exc}"
            logger.error(
                "寫入資料庫失敗（第 %d/%d 次，待寫 %d 筆）: %s",
                attempt,
                DB_FLUSH_RETRIES,
                len(buffer),
                exc,
                extra={"ui_mirrored": True},
            )
            _log(
                session,
                f"寫入資料庫失敗（第 {attempt}/{DB_FLUSH_RETRIES} 次）：{exc}",
                "error",
            )
            continue
        buffer.clear()
        failures["db_error"] = ""
        report.written += done.written
        return


def _log_db_failure(session, buffer, failures) -> None:
    _log(
        session,
        f"❌ 資料庫寫入失敗，已停止後續翻譯；尚有 {len(buffer)} 筆譯文未寫入"
        f"（{failures['db_error'] or '最後補寫成功，但後續批次未執行'}）。"
        "請排除問題（磁碟空間/檔案鎖定）後重新執行。",
        "error",
    )


def _record_flagged_translation(
    session,
    report: TranslateReport,
    item: dict[str, Any],
    issues: list[str] | tuple[str, ...],
) -> None:
    """記錄格式不一致結果，供校對畫面保留 AI 譯文草稿。"""
    report.flagged += 1
    report.flagged_entries[item["_entry_id"]] = item["text"]
    if len(report.flagged_samples) < _FLAGGED_LOG_LIMIT:
        note = f"{item['_mod_id']} / {item['path']}：{'、'.join(issues)}"
        report.flagged_samples.append(note)
        _log(session, f"⚠️ 特殊字元不一致，未寫入：{note}", "warning")


def _quality_issues_for_item(
    item: dict[str, Any], source: str, text: str
) -> list[str] | tuple[str, ...]:
    """Normalize the shared quality rejection before Mod DB write-back."""
    issues = item.get("_quality_issues") or translation_issues(
        source, text, kind=item.get("_kind")
    )
    if item.get("_cache_rejected") and not issues:
        return ("格式驗證失敗",)
    return issues


def _record_quality_rejection(report: TranslateReport, item: dict[str, Any]) -> None:
    """把共享迴圈判定品質不符的項目計入報告（計數、樣本與完整清單）。"""
    report.translated += 1
    report.ai_response = report.translated
    report.quality_rejected += 1
    report.flagged = report.quality_rejected
    source = str(item.get("source_text") or "")
    translated = str(item.get("_quality_translation_text", item.get("text") or ""))
    issues = list(item.get("_quality_issues") or ())
    if not issues:
        issues = list(_quality_issues_for_item(item, source, translated))
    entry_id = item.get("_entry_id")
    if isinstance(entry_id, int):
        report.flagged_entries[entry_id] = translated
    if len(report.flagged_samples) < _FLAGGED_LOG_LIMIT:
        report.flagged_samples.append(
            f"{item.get('_mod_id', '')} / {item.get('path', '')}：{'、'.join(issues)}"
        )
    report.quality_issue_records.append(
        {
            "kind": item.get("_kind", ""),
            "mod_id": item.get("_mod_id", ""),
            "key": item.get("path", ""),
            "issues": issues,
            "source_text": source,
            "translated_text": translated,
            "status": "QUALITY_REJECTED",
        }
    )


def _batch_ui_line(
    message: str,
    report: TranslateReport,
    current: dict[str, int],
    previous: dict[str, int],
) -> str:
    """由同一份報告計數組出單行批次摘要；正常批次不帶診斷欄位。"""
    batch_ai = current["ai_response"] - previous["ai_response"]
    batch_written = current["written"] - previous["written"]
    batch_fallback = current["source_fallback"] - previous["source_fallback"]
    batch_quality = current["quality_rejected"] - previous["quality_rejected"]
    batch_total = batch_ai + batch_fallback
    cache_type = message.partition("(")[2].partition(")")[0]
    progress = f"{report.ai_response + report.source_fallback}/{report.candidates}"
    unwritten = []
    if batch_fallback:
        unwritten.append(f"{batch_fallback} 筆回填原文")
    if batch_quality:
        unwritten.append(f"{batch_quality} 筆品質不符")
    if unwritten:
        return (
            f"⚠️ 批次完成 ({cache_type}) | 寫入: {batch_written}/{batch_total} | "
            f"{'、'.join(unwritten)}，未寫入 | 總進度: {progress}"
        )
    return f"✅ 批次完成 ({cache_type}) | 成功: {batch_written} | 總進度: {progress}"


def _log_final_summary(session, report: TranslateReport) -> None:
    """依報告決定最終狀態（有警告則 DONE_WITH_WARNINGS），並寫出畫面與後台兩種摘要。"""
    if report.status == "DONE" and any(
        (
            report.flagged,
            report.source_fallback,
            report.max_tokens_items,
            report.contract_failed_items,
            report.quality_retry_unresolved,
            report.remaining,
        )
    ):
        report.status = "DONE_WITH_WARNINGS"
    final_level = "warning" if report.status != "DONE" else "info"
    quality_hint = (
        f" | 品質未通過 {report.quality_rejected} 筆，完整清單：{report.quality_issues_file}"
        if report.quality_rejected and report.quality_issues_file
        else ""
    )
    _log_split(
        session,
        (
            f"{'⚠️' if final_level == 'warning' else '✅'} 翻譯完成 | "
            f"寫入 {report.written}/{report.candidates} | "
            f"仍有 {report.remaining} 筆需要處理 | 共送出 {report.batches:,} 批"
            f"{quality_hint}"
        ),
        f"完成（狀態 {report.status}）：AI 回傳 {report.ai_response} 筆、"
        f"寫入 {report.written} 筆、品質拒絕 {report.quality_rejected} 筆、"
        f"來源回填 {report.source_fallback} 筆、MAX_TOKENS {report.max_tokens_items} 筆、"
        f"契約失敗 {report.contract_failed_items} 筆、快取寫入 {report.cache_written} 筆、"
        f"replace_rules 衝突 {report.replace_rule_conflicts} 筆；"
        f"此範圍仍有 {report.remaining} 筆未翻譯；"
        f"共送出 {report.batches:,} 批，耗時 {format_duration(report.elapsed_sec)}",
        final_level,
    )


def _settle_report(session, report, options, result, failures, buffer, tracker) -> None:
    """共享迴圈結束後，把迴圈結果、資料庫失敗與中止狀態收斂成最終報告。"""
    _sync_loop_result(report, result)
    if not options.write_cache:
        # The shared loop still invokes a no-op callback to preserve its
        # generic contract; it must not be reported as a real cache write.
        report.cache_written = 0
    report.flagged = report.quality_rejected
    report.db_written = report.written
    report.quality_issue_records = [
        dict(record) for record in report.quality_issue_records
    ]
    if failures["db_error"] or failures["db_stop"]:
        report.status = "FAILED"
        session.set_error()
        _log_db_failure(session, buffer, failures)
    else:
        report.status = "ABORTED" if failures["aborted"] else result.status
    report.batches = tracker.batches_done
    report.elapsed_sec = tracker.elapsed()
    if report.status in ("FAILED", "ABORTED", "ALL_KEYS_EXHAUSTED"):
        session.set_error()  # 未完成不可記成 DONE（P2-5）
    if failures["aborted"]:
        _log(
            session,
            f"❌ 連續 {ABORT_AFTER_FAILURES} 筆都翻譯失敗，已中止（避免白白耗用額度與時間）。"
            "請檢查設定頁的 API Key 與模型名稱，並查看日誌中的「伺服器回應」了解原因；"
            "已翻成功的部分已寫入資料庫。",
            "error",
        )
    report.replace_rule_conflict_details = _collect_replace_rule_conflicts()
    report.replace_rule_conflicts = len(report.replace_rule_conflict_details)
    _write_quality_issue_file(session, report)
    _update_remaining(report)


def _batch_backend_line(
    message: str,
    report: TranslateReport,
    current: dict[str, int],
    previous: dict[str, int],
) -> str:
    """與畫面同一份計數的後台批次紀錄（取代共享迴圈的原始「成功」字樣）。"""
    cache_type = message.partition("(")[2].partition(")")[0]
    return (
        f"[BatchResult] kind={cache_type} "
        f"ai_response={current['ai_response'] - previous['ai_response']} "
        f"quality_rejected={current['quality_rejected'] - previous['quality_rejected']} "
        f"source_fallback={current['source_fallback'] - previous['source_fallback']} "
        f"db_written={current['written'] - previous['written']} "
        f"progress={report.ai_response + report.source_fallback}/{report.candidates} "
        f"raw={message}"
    )


def _translate_rows(
    session, db, options: TranslateOptions, report: TranslateReport, rows, cancelled
) -> None:
    """分批機翻並寫回；每批先檢查特殊字元，一致者才寫入。"""
    buffer: list[WriteBackItem] = []
    failures = {"streak": 0, "aborted": False, "db_error": "", "db_stop": False}
    last_batch_counts = {
        "ai_response": 0,
        "written": 0,
        "source_fallback": 0,
        "quality_rejected": 0,
    }

    def on_translated_item(item: dict[str, Any]) -> None:
        if not item.get("_quality_checked") and not item.get("_untranslated"):
            issues = translation_issues(
                str(item.get("source_text") or ""),
                str(item.get("text") or ""),
                kind=item.get("_kind"),
            )
            if issues:
                item = {
                    **item,
                    "text": item.get("source_text", ""),
                    "_untranslated": True,
                    "_quality_rejected": True,
                    "_quality_translation_text": item.get("text", ""),
                    "_quality_issues": tuple(issues),
                }
        quality_rejected = bool(
            item.get("_quality_rejected") or item.get("_cache_rejected")
        )
        if quality_rejected:
            failures["streak"] = 0
            _record_quality_rejection(report, item)
            return
        if item.get("_untranslated"):
            failures["streak"] += 1
            report.source_fallback += 1
            if failures["streak"] >= ABORT_AFTER_FAILURES:
                failures["aborted"] = True
            return
        failures["streak"] = 0
        report.translated += 1
        report.ai_response = report.translated
        source, text = item["source_text"], item["text"]
        # The shared loop owns the quality gate. Keep this callback focused on
        # DB write candidates so quality rejection cannot be mistaken for an
        # ordinary source fallback or validated twice.
        buffer.append(
            WriteBackItem(item["_kind"], item["_mod_id"], item["path"], source, text)
        )

    def flush() -> None:
        _flush_buffer(session, db, options.version, buffer, report, failures)

    def on_progress(progress: float, message: str, _eta: float) -> None:
        session.set_progress(progress)
        if message:
            if message.startswith("✅ 批次完成"):
                current = {
                    "ai_response": report.ai_response,
                    "written": report.written,
                    "source_fallback": report.source_fallback,
                    "quality_rejected": report.quality_rejected,
                }
                ui_text = _batch_ui_line(message, report, current, last_batch_counts)
                backend_text = _batch_backend_line(
                    message, report, current, last_batch_counts
                )
                _log_split(session, ui_text, backend_text)
                last_batch_counts.update(current)
            else:
                _log(session, message)
        if tracker.update(round(progress * len(items))):
            _log(session, f"⏱ {tracker.line()}")
        _update_remaining(report)
        session.set_summary({**report.as_dict(), "live": tracker.live()})

    def translate_batch(batch, batch_total):
        if failures["db_error"]:
            failures["db_stop"] = True  # 因資料庫失敗而停止；之後補寫成功也不算整體完成
        if cancelled() or is_cancelled() or failures["aborted"] or failures["db_error"]:
            raise TaskCancelled()
        return translate_batch_smart(batch, total=batch_total)

    items = group_items_by_batch_key(build_items(rows))
    operation_id = str(getattr(session, "id", "") or id(session))
    _attach_translation_sessions(
        items, operation_id=operation_id, db=db, version=options.version
    )
    report.planned_batches = plan_batches(items)
    tracker = RunProgress(total=len(items), planned_batches=report.planned_batches)
    _log(session, tracker.start_line())
    # 第一批結束前也先給畫面即時資料（已用時間從開始就會跳動）
    session.set_summary({**report.as_dict(), "live": tracker.live()})
    lm_config = load_config()
    lm_cfg = lm_config.get("lm_translator", {}) if isinstance(lm_config, dict) else {}
    fixed_input_tokens_for_batch = (
        estimate_batch_fixed_input_tokens
        if isinstance(lm_cfg, dict) and lm_cfg.get("token_budget_enabled", True)
        else None
    )
    with cancel_scope(cancelled):
        result = run_translator_skeleton(
            items,
            total_for_smart=len(items),
            translate_batch_smart=translate_batch,
            # Real provider requests are throttled by ModelQuotaRegistry.
            sleep_seconds_between_batches=0.0,
            write_new_cache=options.new_cache_shard,
            reload_cache=options.write_cache,
            cache_add=add_to_cache if options.write_cache else _noop_cache,
            cache_save=save_translation_cache if options.write_cache else _noop_cache,
            fixed_input_tokens_for_batch=fixed_input_tokens_for_batch,
            hooks=TranslatorHooks(
                on_translated_item=on_translated_item,
                on_batch_flushed=flush,
                on_progress=on_progress,
            ),
        )
    flush()
    _settle_report(session, report, options, result, failures, buffer, tracker)
    _log_final_summary(session, report)
    _record_backend_summary(report)
    if result.last_error:
        _log(session, f"最後一次錯誤：{result.last_error}", "warning")
    session.set_summary(report.as_dict())


def run_moddb_translate_service(
    options: TranslateOptions, session, *, manage_session: bool = True
) -> None:
    """批次機翻資料庫未翻譯條目（背景執行；進度與日誌寫入 ``session``）。"""
    ensure_pipeline_logging()
    db = None

    def cancelled() -> bool:
        return bool(getattr(session, "cancel_requested", False))

    report = TranslateReport(dry_run=options.dry_run)
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
            session.set_error()
            return
        scope = f"{len(options.mod_ids)} 個模組" if options.mod_ids else "全部模組"
        _log(
            session,
            f"開始{'預覽' if options.dry_run else '機翻'}：版本 {options.version}，"
            f"{scope}，單次上限 {options.limit or '不限'} 筆",
        )
        rows = _select_rows(session, db, options, report)
        if rows is not None:
            _translate_rows(session, db, options, report, rows, cancelled)
    except Exception as exc:  # noqa: BLE001 - 背景任務：任何失敗都要回報到 session
        logger.error(
            "Mod 資料庫機翻失敗（版本 %s）: %s\n%s",
            options.version,
            exc,
            traceback.format_exc(),
            extra={"ui_mirrored": True},
        )
        add_log_unmirrored(
            session,
            f"[致命錯誤] 機翻失敗：{exc}（詳細堆疊請看後台 log）",
            "error",
        )
        session.set_error()
    finally:
        if db is not None:
            if not options.dry_run:
                warm_stats_quietly(db)
            db.close()
        UI_LOG_HANDLER.set_session(None)
        if manage_session:
            session.finish()
