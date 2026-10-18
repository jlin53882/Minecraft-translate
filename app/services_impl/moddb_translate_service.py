"""Mod 資料庫批次機翻服務：把資料庫裡「沒有任何譯文」的條目交給機翻引擎，結果寫回資料庫。

流程：先沿用其他版本已有的相同譯文（不呼叫 AI）→ 挑出仍未翻譯的條目（可限定模組與筆數）
→ 分批機翻 → 每批檢查特殊字元（換行、`§`、`%s` 等）是否與原文一致 → 一致者以「AI 機翻」
來源寫回。只填空白、不覆蓋任何既有譯文；來源優先序最低，之後的人工或匯入譯文會自動蓋過。結果預設也會寫入翻譯快取（可關閉）。
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
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
from translation_tool.utils.cache_manager import add_to_cache, save_translation_cache
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    is_cancelled,
)
from translation_tool.utils.config_manager import load_config

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


def _translate_rows(
    session, db, options: TranslateOptions, report: TranslateReport, rows, cancelled
) -> None:
    """分批機翻並寫回；每批先檢查特殊字元，一致者才寫入。"""
    buffer: list[WriteBackItem] = []
    failures = {"streak": 0, "aborted": False, "db_error": "", "db_stop": False}

    def on_translated_item(item: dict[str, Any]) -> None:
        if item.get("_untranslated"):
            failures["streak"] += 1
            if failures["streak"] >= ABORT_AFTER_FAILURES:
                failures["aborted"] = True
            return
        failures["streak"] = 0
        report.translated += 1
        source, text = item["source_text"], item["text"]
        issues = translation_issues(source, text, kind=item.get("_kind"))
        if issues:
            report.flagged += 1
            report.flagged_entries[item["_entry_id"]] = text
            if len(report.flagged_samples) < _FLAGGED_LOG_LIMIT:
                note = f"{item['_mod_id']} / {item['path']}：{'、'.join(issues)}"
                report.flagged_samples.append(note)
                _log(session, f"⚠️ 特殊字元不一致，未寫入：{note}", "warning")
            return
        buffer.append(
            WriteBackItem(item["_kind"], item["_mod_id"], item["path"], source, text)
        )

    def flush() -> None:
        _flush_buffer(session, db, options.version, buffer, report, failures)

    def on_progress(progress: float, message: str, _eta: float) -> None:
        session.set_progress(progress)
        if message:
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
    _update_remaining(report)
    _log(
        session,
        f"完成（狀態 {report.status}）：AI 回傳 {report.translated} 筆、"
        f"寫入 {report.written} 筆、特殊字元不一致未寫入 {report.flagged} 筆；"
        f"此範圍仍有 {report.remaining} 筆未翻譯；"
        f"共送出 {report.batches:,} 批，耗時 {format_duration(report.elapsed_sec)}",
        "warning" if report.status != "DONE" or report.flagged else "info",
    )
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
