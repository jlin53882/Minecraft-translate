"""Flet 任務邊界：執行 KubeJS／FTB Quests 共用模組包流程。"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.moddb_service import open_database
from app.tasks.task_session import add_log_unmirrored
from translation_tool.translation_db.pack_pipeline import (
    PackPipelineOptions,
    discover_pack_candidates,
    run_pack_pipeline,
)
from translation_tool.utils.cancellation import TaskCancelled

logger = logging.getLogger(__name__)


def discover_kubejs_ftb_pack_candidates(source: str) -> list[str]:
    """Expose bounded pack-root discovery through the application service layer."""
    return discover_pack_candidates(source)


def run_kubejs_ftb_pipeline_service(
    source: str,
    version: str,
    session,
    *,
    output_root: str | None = None,
    scan_mode: str = "full",
    include_kubejs: bool = True,
    include_ftb: bool = True,
    dry_run: bool = True,
    publish: bool = False,
    pack_root: str | None = None,
):
    """以既有 TaskSession 執行一次可取消的來源／DB／overlay run。"""
    db = None
    try:
        session.start()
        # 預覽必須從 SQLite 層使用唯讀連線；``create=False`` 只保證不新建
        # 檔案，並不能阻止初始化、索引或優先序同步在連線時寫入資料庫。
        db = open_database(create=not dry_run, readonly=dry_run)
        if db is None and not dry_run:
            raise RuntimeError("無法開啟目前設定的 ModDB")
        options = PackPipelineOptions(
            version=version,
            scan_mode=scan_mode,
            include_kubejs=include_kubejs,
            include_ftb=include_ftb,
            dry_run=dry_run,
            publish=publish,
            output_root=output_root,
        )
        report = run_pack_pipeline(
            db,
            source,
            options,
            session=session,
            pack_root=pack_root,
        )
        session.set_summary(report.as_dict())
        return report
    except TaskCancelled:
        add_log_unmirrored(session, "⏹️ 流水線已取消，未發布未完成的 overlay", "warning")
        session.set_summary({"status": "CANCELLED"})
        raise
    except Exception as exc:  # noqa: BLE001 - 任務邊界必須回報並終止
        logger.error("KubeJS/FTB 流水線失敗：%s\n%s", exc, traceback.format_exc())
        add_log_unmirrored(session, f"❌ 流水線失敗：{exc}", "error")
        session.set_error()
        return None
    finally:
        if db is not None:
            db.close()
        session.finish()


__all__ = [
    "discover_kubejs_ftb_pack_candidates",
    "run_kubejs_ftb_pipeline_service",
]
