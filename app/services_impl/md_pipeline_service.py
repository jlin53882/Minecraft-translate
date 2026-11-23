"""Flet 任務邊界：執行 Markdown 指南的 ModDB 流水線（來源 → DB → LM → 資源包 overlay）。"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services_impl.moddb_service import open_database
from app.services_impl.moddb_translate_service import (
    TranslateOptions,
    run_moddb_translate_service,
)
from app.tasks.task_session import add_log_unmirrored
from translation_tool.translation_db.md_pipeline import (
    MdPipelineOptions,
    discover_md_source_folders,
    run_md_pipeline,
)
from translation_tool.translation_db.schema import KIND_GUIDE
from translation_tool.utils.cancellation import TaskCancelled
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)


def get_md_use_zh_cn_default() -> bool:
    """取得 Markdown 流水線「讀取 zh_cn」勾選的預設值。"""
    return bool(load_config().get("translator", {}).get("md_use_zh_cn_base", True))


def discover_md_sources(source: str) -> list[str]:
    """選到模組包根目錄時列出 kubejs/assets 與 resourcepacks/*；其他資料夾直接回傳自己。"""
    return discover_md_source_folders(source)


def _empty_translation_report(*, dry_run: bool) -> dict[str, bool | int | str]:
    return {
        "status": "DONE",
        "dry_run": dry_run,
        "candidates": 0,
        "ai_items": 0,
        "written": 0,
        "planned_batches": 0,
    }


def _translation_runner(target_version: str, use_translation_db: bool):
    """流水線的 LM 翻譯階段：只翻本次來源涉及的模組、只翻指南條目；資料庫設定關閉時略過。"""

    def translate_missing(current_db, scan, pipeline_options, task_session):
        mod_ids = scan.mod_ids
        if not mod_ids or not use_translation_db:
            return _empty_translation_report(dry_run=pipeline_options.dry_run)
        return run_moddb_translate_service(
            TranslateOptions(
                version=target_version,
                mod_ids=mod_ids,
                kind=KIND_GUIDE,
                limit=0,
                dry_run=pipeline_options.dry_run,
                write_cache=not pipeline_options.dry_run,
            ),
            task_session,
            manage_session=False,
            db_override=current_db,
        )

    return translate_missing


def _zh_cn_converter():
    """zh_cn 轉繁中：OpenCC s2twp 後套用替換規則（與 Markdown 輸出同一個規則檔）。"""
    from translation_tool.utils.log_unit import log_warning
    from translation_tool.utils.text_processor import convert_text, load_replace_rules

    try:
        rules = load_replace_rules(
            load_config()
            .get("translator", {})
            .get("replace_rules_path", "replace_rules.json")
        )
    except Exception as exc:  # noqa: BLE001 - 規則檔問題只是略過替換，不該讓注入失敗
        log_warning(f"[MD] 載入替換規則失敗，簡轉繁略過替換：{exc!r}")
        rules = []
    return lambda text: convert_text(text, rules)


_TRANSLATION_SEVERITY = ("DONE", "DONE_WITH_WARNINGS", "SKIPPED")


def _worse_translation(
    current: dict[str, Any] | None, other: dict[str, Any]
) -> dict[str, Any]:
    """多個來源的 LM 翻譯摘要合併：數字相加，狀態取最嚴重者。"""
    merged = dict(current or {})
    for name, number in other.items():
        if name == "status":
            continue
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            merged[name] = merged.get(name, 0) + number
        else:
            merged.setdefault(name, number)

    def rank(status: Any) -> int:
        text = str(status or "DONE")
        # 未知狀態（FAILED、ERROR、ABORTED…）一律視為最嚴重
        if text in _TRANSLATION_SEVERITY:
            return _TRANSLATION_SEVERITY.index(text)
        return len(_TRANSLATION_SEVERITY)

    worst = max((current or {}).get("status"), other.get("status"), key=rank)
    if worst is not None:
        merged["status"] = worst
    return merged


def _expand_sources(
    sources: list[str], output_root: str | None, session=None
) -> list[str]:
    """每個來源先做模組包探索（沒按「分析來源」也能得到資源包結構），並排除輸出資料夾本身。"""
    output = Path(output_root).expanduser().resolve() if output_root else None
    expanded: list[str] = []
    for source in sources:
        for folder in discover_md_source_folders(source) or [source]:
            resolved = Path(folder).resolve()
            if output is not None and resolved == output:
                if session is not None:
                    add_log_unmirrored(
                        session, f"輸出資料夾本身不是來源，已略過：{folder}", "warning"
                    )
                continue
            if folder not in expanded:
                expanded.append(folder)
    return expanded


def _merge_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """多個來源資料夾各跑一次後合併成一份摘要：數字相加、提醒串接、狀態取最嚴重者。"""
    if len(summaries) == 1:
        return summaries[0]
    merged: dict[str, Any] = {}
    for summary in summaries:
        for key, value in summary.items():
            if isinstance(value, bool) or key == "status":
                merged.setdefault(key, value)
            elif isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif isinstance(value, list):
                merged[key] = [*merged.get(key, []), *value][:200]
            elif key == "translation" and isinstance(value, dict):
                merged[key] = _worse_translation(merged.get(key), value)
            elif isinstance(value, dict):
                inner = merged.setdefault(key, {})
                for name, number in value.items():
                    if isinstance(number, (int, float)) and not isinstance(
                        number, bool
                    ):
                        inner[name] = inner.get(name, 0) + number
                    else:
                        inner.setdefault(name, number)
    warned = any(s.get("status") == "DONE_WITH_WARNINGS" for s in summaries)
    merged["status"] = "DONE_WITH_WARNINGS" if warned else "DONE"
    return merged


def run_md_pipeline_service(
    source: str | Sequence[str],
    version: str,
    session,
    *,
    output_root: str | None = None,
    dry_run: bool = True,
    publish: bool = False,
    translate_missing: bool = False,
    translation_version: str | None = None,
    use_translation_db: bool = True,
    read_zh_cn: bool = True,
):
    """以既有 TaskSession 執行一次可取消的來源／DB／overlay run。

    預覽（``dry_run``）從 SQLite 層使用唯讀連線，不會寫入資料庫；
    ``use_translation_db=False`` 時不執行 LM 翻譯（Mod 資料庫設定為關閉）。
    """
    db = None
    try:
        session.start()
        db = open_database(create=not dry_run, readonly=dry_run)
        if db is None and not dry_run:
            raise RuntimeError("無法開啟目前設定的 ModDB")
        options = MdPipelineOptions(
            version=version,
            dry_run=dry_run,
            publish=publish,
            output_root=output_root,
            translate_missing=translate_missing,
            read_zh_cn=read_zh_cn,
        )
        runner = (
            _translation_runner(translation_version or version, use_translation_db)
            if translate_missing
            else None
        )
        sources = _expand_sources(
            [source] if isinstance(source, str) else list(source),
            output_root,
            session,
        )
        if not sources:
            raise ValueError("沒有可處理的來源資料夾（來源不可與輸出資料夾相同）")
        convert = _zh_cn_converter() if read_zh_cn else None
        reports = [
            run_md_pipeline(
                db,
                folder,
                options,
                session=session,
                convert=convert,
                translation_runner=runner,
            )
            for folder in sources
        ]
        for report in reports:
            reason = (getattr(report, "translation", None) or {}).get("reason")
            if reason:
                add_log_unmirrored(session, f"LM 翻譯已跳過：{reason}", "warning")
        session.set_summary(_merge_summaries([r.as_dict() for r in reports]))
        return reports
    except TaskCancelled:
        add_log_unmirrored(session, "⏹️ 流水線已取消，未發布未完成的輸出", "warning")
        session.set_summary({"status": "CANCELLED"})
        raise
    except Exception as exc:  # noqa: BLE001 - 任務邊界必須回報並終止
        logger.error("Markdown 流水線失敗：%s\n%s", exc, traceback.format_exc())
        add_log_unmirrored(session, f"❌ 流水線失敗：{exc}", "error")
        session.set_error()
        return None
    finally:
        if db is not None:
            db.close()
        session.finish()


__all__ = [
    "discover_md_sources",
    "get_md_use_zh_cn_default",
    "run_md_pipeline_service",
]
