"""Mod DB 機翻的後台報告：品質問題清單（分批、去重）與易讀的結束摘要。

畫面日誌只放一行摘要；完整、可追蹤的資料寫在這裡產生的檔案與後台日誌：

- ``quality-issues-<task_id>.json``：結構化清單，依批次分組並去重，檔頭有中文說明。
- ``quality-issues-<task_id>.csv``：同一份資料的表格版，可直接用 Excel 開啟篩選。
- ``replace-rule-conflicts.json``：替換規則衝突明細（每天一份，內容相同就不重複產生）。
"""

from __future__ import annotations

import csv
import json
import os
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.tasks.task_session import STATUS_LABELS as TASK_STATUS_LABELS
from translation_tool.utils.file_replace import replace_with_retry

QUALITY_STATUS = "QUALITY_REJECTED"

FIELD_NOTES = {
    "kind": "內容類型：lang（語言檔）、md（手冊／指南）、patchouli（Patchouli 手冊）",
    "mod_id": "模組代號",
    "key": "條目的鍵（語言檔 key 或手冊路徑）",
    "issues": "品質檢查發現的問題（例如少了或多了某個格式符號、佔位符）",
    "source_text": "英文原文",
    "translated_text": "AI 回傳但被拒絕的譯文（未寫入資料庫與快取）",
    "status": f"{QUALITY_STATUS} = 品質檢查未通過，不是回填原文",
    "count": "同一項目（相同原文、譯文與問題）在本次任務中出現的次數",
    "batches": "出現在第幾批（從 1 開始）",
}

# 與 TaskSession 共用同義標籤；報告只補充「原因細節」與報告專用的 DRY_RUN。
STATUS_LABELS = {
    **TASK_STATUS_LABELS,
    "ABORTED": f"{TASK_STATUS_LABELS['ABORTED']}（連續失敗）",
    "DRY_RUN": "預覽（未呼叫 AI）",
}

CONTRACT_TYPE_LABELS = {
    "duplicate_id": "重複 id",
    "id_mismatch": "id 缺漏或多出",
    "invalid_shape": "回傳結構無效",
}

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def dedupe_quality_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合併完全相同的項目，並記下出現次數與批次。"""
    merged: dict[tuple, dict[str, Any]] = {}
    for record in records:
        issues = [str(issue) for issue in record.get("issues") or ()]
        key = (
            record.get("kind", ""),
            record.get("mod_id", ""),
            record.get("key", ""),
            record.get("source_text", ""),
            record.get("translated_text", ""),
            tuple(issues),
        )
        batch = record.get("batch")
        entry = merged.get(key)
        if entry is None:
            entry = {
                "kind": record.get("kind", ""),
                "mod_id": record.get("mod_id", ""),
                "key": record.get("key", ""),
                "issues": issues,
                "source_text": record.get("source_text", ""),
                "translated_text": record.get("translated_text", ""),
                "status": record.get("status", QUALITY_STATUS),
                "count": 0,
                "batches": [],
            }
            merged[key] = entry
        entry["count"] += 1
        if isinstance(batch, int) and batch not in entry["batches"]:
            entry["batches"].append(batch)
    return list(merged.values())


def _group_by_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        first_batch = item["batches"][0] if item["batches"] else 0
        groups.setdefault(first_batch, []).append(item)
    return [
        {"批次": batch or "未知", "項目數": len(group), "項目": group}
        for batch, group in sorted(groups.items())
    ]


def build_quality_report(
    task_id: str, records: list[dict[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    """組出依批次分組、已去重的品質問題報告（含中文說明與統計）。"""
    items = dedupe_quality_records(records)
    issue_counter: Counter[str] = Counter()
    mod_counter: Counter[str] = Counter()
    for item in items:
        mod_counter[item["mod_id"]] += item["count"]
        for issue in item["issues"]:
            issue_counter[issue] += item["count"]
    stamp = (now or datetime.now(UTC)).astimezone().isoformat(timespec="seconds")
    return {
        "說明": {
            "用途": "Mod DB 機翻時品質檢查未通過、因此沒有寫入資料庫與快取的項目完整清單。"
            "畫面日誌只顯示數量；這份檔案保留每一筆的原文、譯文與問題。",
            "同資料夾的表格版": "同名 .csv 檔是同一份資料，可用 Excel 開啟篩選。",
            "欄位": FIELD_NOTES,
        },
        "任務": task_id,
        "產生時間": stamp,
        "統計": {
            "品質不符筆數（含重複）": len(records),
            "去重後項目數": len(items),
            "依問題類型": dict(issue_counter.most_common()),
            "依模組": dict(mod_counter.most_common()),
        },
        "批次": _group_by_batch(items),
    }


def _tmp_path(path: Path) -> Path:
    """同資料夾、每次呼叫獨一無二的暫存檔名，多個程式實例同時寫入也不會互相覆蓋。"""
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def _csv_safe(value: Any) -> Any:
    """Excel 會把 = + - @ 開頭的儲存格當公式；加上單引號讓它顯示成原本的文字。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _write_csv(path: Path, items: list[dict[str, Any]]) -> None:
    # utf-8-sig：Excel 才不會把中文開成亂碼
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["批次", "類型", "模組", "鍵", "問題", "原文", "被拒絕的譯文", "出現次數"]
        )
        for item in items:
            writer.writerow(
                [
                    _csv_safe(value)
                    for value in (
                        "、".join(str(b) for b in item["batches"]),
                        item["kind"],
                        item["mod_id"],
                        item["key"],
                        "；".join(item["issues"]),
                        item["source_text"],
                        item["translated_text"],
                        item["count"],
                    )
                ]
            )


def write_quality_report(
    day_dir: Path, raw_task_id: str, records: list[dict[str, Any]]
) -> Path:
    """寫出 JSON 與 CSV，回傳 JSON 路徑。失敗時丟出 OSError／ValueError。"""
    task_id = _UNSAFE_FILENAME.sub("_", raw_task_id)
    day_dir.mkdir(parents=True, exist_ok=True)
    report = build_quality_report(task_id, records)
    json_path = day_dir / f"quality-issues-{task_id}.json"
    csv_path = json_path.with_suffix(".csv")
    flat = [item for group in report["批次"] for item in group["項目"]]
    json_tmp = _tmp_path(json_path)
    csv_tmp = _tmp_path(csv_path)
    try:
        json_tmp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_csv(csv_tmp, flat)
        # 兩個暫存檔都寫成功才換上正式檔。JSON 是主要報告，先換；CSV 是由同一份
        # 資料產生的表格版，若第二次替換失敗只會是舊版（下次任務會重新產生）。
        replace_with_retry(json_tmp, json_path)
        replace_with_retry(csv_tmp, csv_path)
    finally:
        json_tmp.unlink(missing_ok=True)
        csv_tmp.unlink(missing_ok=True)
    return json_path


def write_replace_rule_conflicts(
    day_dir: Path, conflicts: list[dict[str, Any]]
) -> Path | None:
    """每天只維護一份衝突明細；內容沒變就不重寫。規則檔本身不會被修改。"""
    path = day_dir / "replace-rule-conflicts.json"
    if not conflicts and not path.exists():
        return None  # 沒有衝突也沒有舊報告：不必建立檔案
    day_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "說明": {
            "用途": "替換規則中「輸出內容包含來源詞」的衝突明細，只做偵測，"
            "不會自動修改 replace_rules.json。",
            "欄位": {
                "source": "來源詞",
                "source_index": "來源詞所在規則的順序",
                "destination": "輸出內容（包含來源詞）",
                "destination_index": "輸出內容所在規則的順序",
            },
        },
        "數量": len(conflicts),
        "明細": conflicts,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return path
    tmp = _tmp_path(path)
    try:
        tmp.write_text(text, encoding="utf-8")
        replace_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def format_backend_summary(report: Any) -> str:
    """把結束摘要排成多行中文，取代單行 JSON。"""
    status = STATUS_LABELS.get(report.status, report.status)
    conflicts = report.replace_rule_conflicts
    examples = "、".join(
        f"「{c.get('source', '')}」→「{c.get('destination', '')}」"
        for c in report.replace_rule_conflict_details[:3]
    )
    lines = [
        f"[Mod DB 機翻結束摘要] 狀態：{report.status}（{status}）",
        (
            f"  範圍　　：候選 {report.candidates} 筆｜仍未翻譯 {report.remaining} 筆｜"
            f"共 {report.batches} 批（預估 {report.planned_batches} 批）｜"
            f"耗時 {report.elapsed_sec:.0f} 秒"
        ),
        (
            f"  AI 結果　：AI 回傳 {report.ai_response} 筆｜"
            f"寫入資料庫 {report.db_written} 筆｜寫入快取 {report.cache_written} 筆"
        ),
        (
            f"  未寫入　：品質拒絕 {report.quality_rejected} 筆｜"
            f"回填原文 {report.source_fallback} 筆"
            f"（MAX_TOKENS {report.max_tokens_items}／"
            f"回應格式錯誤 {report.contract_failed_items}／"
            f"單筆超限 {report.oversized_items}）"
        ),
        (
            f"  品質重試　：候選 {report.quality_retry_candidates}｜"
            f"已修正 {report.quality_retry_fixed}｜"
            f"未解決 {report.quality_retry_unresolved}｜"
            f"配額失敗 {report.quality_retry_quota_failed}"
        ),
    ]
    if conflicts:
        lines.append(
            f"  替換規則　：{conflicts} 筆衝突（只偵測，未修改規則）"
            + (f"；例：{examples}" if examples else "")
        )
    if report.quality_issues_file:
        lines.append(
            f"  品質清單　：{report.quality_issues_file}（同名 .csv 為表格版）"
        )
    if report.replace_rule_conflicts_file:
        lines.append(f"  衝突明細　：{report.replace_rule_conflicts_file}")
    if report.contract_errors:
        unique = list(dict.fromkeys(report.contract_errors))
        lines.append(f"  回應格式錯誤：{'；'.join(unique[:3])}")
    return "\n".join(lines)
