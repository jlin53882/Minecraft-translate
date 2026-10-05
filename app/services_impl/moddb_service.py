"""Mod 翻譯資料庫的服務層（供 UI 使用）：開啟資料庫、版本清單、掃描任務、儀表板摘要。

UI 不直接碰 SQL；資料庫存取都經過 ``translation_tool.translation_db``。
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging
from translation_tool.translation_db import (
    SOURCE_NAMES,
    DbSettings,
    EntryDetail,
    EntryRow,
    TranslationDB,
    VersionStat,
    load_db_settings,
    open_db,
)
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator
from translation_tool.translation_db.schema import (
    SRC_AI,
    SRC_CUSTOM,
    SRC_I18N,
    SRC_JAR_CN,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
)
from translation_tool.utils.cancellation import cancel_scope
from translation_tool.utils.config_manager import load_config

# View 經由本模組取用資料庫型別與常數（View 不直接 import translation_tool 引擎）
__all__ = [
    "SOURCE_NAMES",
    "SRC_AI",
    "SRC_CUSTOM",
    "SRC_I18N",
    "SRC_JAR_CN",
    "SRC_JAR_TW",
    "SRC_MANUAL",
    "SRC_SUBTITLE",
    "DbSettings",
    "EntryDetail",
    "EntryRow",
    "ScanOptions",
    "TranslationDB",
    "VersionStat",
    "current_settings",
    "load_db_settings",
    "open_database",
    "pack_format_hint",
    "run_moddb_scan_service",
    "summarize_database",
    "version_choices",
]

logger = logging.getLogger(__name__)

VERSION_FILE = (
    Path(__file__).resolve().parents[2]
    / "translation_tool"
    / "core"
    / "resource_pack_version.json"
)


def current_settings() -> DbSettings:
    """目前設定（每次讀取，存檔後不必重開頁面）。"""
    return load_db_settings()


def open_database(*, create: bool = True) -> TranslationDB | None:
    """依設定開啟資料庫；``create=False`` 時檔案不存在回傳 None。"""
    return open_db(current_settings(), create=create)


def version_choices(db: TranslationDB | None = None) -> list[str]:
    """遊戲版本候選：資料庫已有的版本在前，其後是資源包版本清單（與打包頁同一份）。"""
    seen: list[str] = []
    if db is not None:
        seen.extend(db.versions())
    try:
        labels = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        # 新版本在檔案後方，倒序讓常用的新版在前
        for label in reversed(list(labels)):
            if label not in seen:
                seen.append(label)
    except (OSError, ValueError) as exc:
        logger.debug("讀取版本清單失敗：%s", exc)
    return seen


def pack_format_hint(label: str) -> str:
    """版本標籤對應的 pack_format 說明（沒有對應時回傳空字串）。"""
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        info = data.get(label)
        if info:
            lo, hi = info.get("min_format"), info.get("max_format")
            return f"pack_format {lo}" if lo == hi else f"pack_format {lo}–{hi}"
    except (OSError, ValueError):
        pass
    return ""


def summarize_database() -> dict[str, Any] | None:
    """儀表板用的簡要摘要；資料庫不存在或無法開啟時回傳 None（不會建立檔案）。"""
    db = open_database(create=False)
    if db is None:
        return None
    try:
        stats = db.version_stats()
        total = sum(s.total for s in stats)
        translated = total - sum(s.untranslated for s in stats)
        return {
            "versions": [s.mc_version for s in stats],
            "entries": total,
            "translated": translated,
            "progress": round(100 * translated / total) if total else 0,
            "target_version": current_settings().version,
        }
    finally:
        db.close()


def run_moddb_scan_service(
    folder: str,
    options: ScanOptions,
    session,
    *,
    manage_session: bool = True,
) -> None:
    """掃描 mods 資料夾並寫入資料庫（背景執行；進度與日誌寫入 ``session``）。"""
    ensure_pipeline_logging()
    db: TranslationDB | None = None

    def cancelled() -> bool:
        return bool(getattr(session, "cancel_requested", False))

    try:
        if manage_session:
            session.start()
        UI_LOG_HANDLER.set_session(session)
        db = open_database(create=not options.dry_run)
        if db is None and not options.dry_run:
            session.add_log("[錯誤] 無法開啟 Mod 資料庫，請檢查設定中的資料庫路徑")
            session.set_error()
            return
        workers = int(
            load_config().get("translator", {}).get("parallel_execution_workers", 4)
            or 4
        )
        workers = max(1, min(workers, os.cpu_count() or 4))
        with cancel_scope(cancelled):
            for update in scan_folder_generator(
                db, folder, options, should_cancel=cancelled, workers=workers
            ):
                if update.get("log"):
                    session.add_log(update["log"])
                if update.get("progress") is not None:
                    session.set_progress(update["progress"])
                report = update.get("report")
                if report is not None:
                    session.set_summary(report.as_dict())
    except Exception as exc:  # noqa: BLE001 - 背景任務：任何失敗都要回報到 session，不可讓執行緒默默結束
        logger.error("Mod 資料庫掃描失敗: %s\n%s", exc, traceback.format_exc())
        session.add_log(f"[致命錯誤] 掃描失敗：{exc}")
        session.set_error()
    finally:
        if db is not None:
            db.close()
        UI_LOG_HANDLER.set_session(None)
        if manage_session:
            session.finish()
