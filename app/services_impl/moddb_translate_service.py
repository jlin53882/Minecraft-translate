"""Mod 資料庫批次機翻服務：把資料庫裡「沒有任何譯文」的條目交給機翻引擎，結果寫回資料庫。

流程：先沿用其他版本已有的相同譯文（不呼叫 AI）→ 挑出仍未翻譯的條目（可限定模組與筆數）
→ 分批機翻 → 每批檢查特殊字元（換行、`§`、`%s` 等）是否與原文一致 → 一致者以「AI 機翻」
來源寫回。只填空白、不覆蓋任何既有譯文；來源優先序最低，之後的人工或匯入譯文會自動蓋過。結果預設也會寫入翻譯快取（可關閉）。
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
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
from app.views.moddb.formatting import token_issues
from translation_tool.core.lm_translator_main import translate_batch_smart
from translation_tool.core.lm_translator_skeleton import (
    TranslatorHooks,
    run_translator_skeleton,
)
from translation_tool.translation_db.models import WriteBackItem
from translation_tool.translation_db.schema import KIND_LANG
from translation_tool.utils.cache_manager import add_to_cache, save_translation_cache
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    is_cancelled,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 2000
_FLAGGED_LOG_LIMIT = 20
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "reused": self.reused,
            "translated": self.translated,
            "written": self.written,
            "flagged": self.flagged,
            "status": self.status,
            "dry_run": self.dry_run,
            "remaining": self.remaining,
        }


def build_items(rows: list[tuple[int, str, str, str, str]]) -> list[dict[str, Any]]:
    """資料庫條目轉成機翻引擎的項目；``_entry_id`` 會原樣帶回，用來對應寫回的條目。"""
    return [
        {
            "file": f"{mod_id}/{kind}",
            "path": key,
            "text": en_us,
            "source_text": en_us,
            "cache_type": "lang" if kind == KIND_LANG else "patchouli",
            "_entry_id": eid,
            "_kind": kind,
            "_mod_id": mod_id,
        }
        for eid, kind, mod_id, key, en_us in rows
    ]


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
    rows = db.untranslated_entries(options.version, mods, options.limit or None)
    report.candidates = len(rows)
    report.remaining = db.count_untranslated(options.version, mods)
    if options.dry_run:
        _log(
            session,
            f"🔎 預覽：{report.remaining} 筆未翻譯，本次將翻譯前 {len(rows)} 筆"
            "（未呼叫 AI、未寫入）",
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


def _translate_rows(
    session, db, options: TranslateOptions, report: TranslateReport, rows, cancelled
) -> None:
    """分批機翻並寫回；每批先檢查特殊字元，一致者才寫入。"""
    buffer: list[WriteBackItem] = []
    failures = {"streak": 0, "aborted": False}

    def on_translated_item(item: dict[str, Any]) -> None:
        if item.get("_untranslated"):
            failures["streak"] += 1
            if failures["streak"] >= ABORT_AFTER_FAILURES:
                failures["aborted"] = True
            return
        failures["streak"] = 0
        report.translated += 1
        source, text = item["source_text"], item["text"]
        issues = token_issues(source, text)
        if issues:
            report.flagged += 1
            if len(report.flagged_samples) < _FLAGGED_LOG_LIMIT:
                note = f"{item['_mod_id']} / {item['path']}：{'、'.join(issues)}"
                report.flagged_samples.append(note)
                _log(session, f"⚠️ 特殊字元不一致，未寫入：{note}", "warning")
            return
        buffer.append(
            WriteBackItem(item["_kind"], item["_mod_id"], item["path"], source, text)
        )

    def flush() -> None:
        if not buffer:
            return
        batch, buffer[:] = list(buffer), []
        done = db.write_back(options.version, batch, fill_other_versions=True)
        report.written += done.written

    def on_progress(progress: float, message: str, _eta: float) -> None:
        session.set_progress(progress)
        if message:
            _log(session, message)

    def translate_batch(batch, batch_total):
        if cancelled() or is_cancelled() or failures["aborted"]:
            raise TaskCancelled()
        return translate_batch_smart(batch, total=batch_total)

    items = build_items(rows)
    with cancel_scope(cancelled):
        result = run_translator_skeleton(
            items,
            total_for_smart=len(items),
            translate_batch_smart=translate_batch,
            write_new_cache=options.write_cache,
            reload_cache=options.write_cache,
            cache_add=add_to_cache if options.write_cache else _noop_cache,
            cache_save=save_translation_cache if options.write_cache else _noop_cache,
            hooks=TranslatorHooks(
                on_translated_item=on_translated_item,
                on_batch_flushed=flush,
                on_progress=on_progress,
            ),
        )
    flush()
    report.status = "ABORTED" if failures["aborted"] else result.status
    if failures["aborted"]:
        _log(
            session,
            f"❌ 連續 {ABORT_AFTER_FAILURES} 筆都翻譯失敗，已中止（避免白白耗用額度與時間）。"
            "請檢查設定頁的 API Key 與模型名稱，並查看日誌中的「伺服器回應」了解原因；"
            "已翻成功的部分已寫入資料庫。",
            "error",
        )
    report.remaining = db.count_untranslated(options.version, list(options.mod_ids))
    _log(
        session,
        f"完成（狀態 {report.status}）：AI 回傳 {report.translated} 筆、"
        f"寫入 {report.written} 筆、特殊字元不一致未寫入 {report.flagged} 筆；"
        f"此範圍仍有 {report.remaining} 筆未翻譯",
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
