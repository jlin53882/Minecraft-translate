"""Lookup pipeline service wrappers.

PR17：將 lookup 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import json
import logging

from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.utils.species_cache import (
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    SpeciesLookup,
    is_potential_species_name,
    lookup_species,
)

logger = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = "在本地快取和線上查詢中均未找到結果。"


def _parent_note(lookup: SpeciesLookup) -> str:
    """查無時上一層物種的結果：參考名稱，或上一層查詢受限流／失敗的提醒。"""
    if not lookup.parent_name:
        return ""
    if lookup.parent_value:
        return f"（上一層物種 {lookup.parent_name}：{lookup.parent_value}，僅供參考）"
    if lookup.parent_status == STATUS_RATE_LIMITED:
        return (
            f"（上一層物種 {lookup.parent_name} 查詢遭維基百科限流"
            f"（{lookup.parent_detail}），請稍後再試）"
        )
    if lookup.parent_status == STATUS_ERROR:
        return (
            f"（上一層物種 {lookup.parent_name} 查詢失敗"
            f"（{lookup.parent_detail}），請稍後再試）"
        )
    return ""


def describe_lookup(lookup: SpeciesLookup) -> str:
    """單筆查詢的結果文字；查無與暫時失敗分開說明。"""
    if lookup.status == STATUS_FOUND:
        return lookup.value or ""
    if lookup.status == STATUS_NOT_FOUND:
        return NOT_FOUND_MESSAGE + _parent_note(lookup)
    if lookup.status == STATUS_RATE_LIMITED:
        return f"維基百科回應請求過於頻繁，請稍後再試（{lookup.detail}）。"
    return f"查詢失敗，請稍後再試（{lookup.detail}）。"


def _batch_category(lookup: SpeciesLookup) -> str:
    """批次結束摘要的分類；與 ``_batch_value`` 的結果文字一一對應。"""
    if lookup.status == STATUS_FOUND:
        return "找到"
    if lookup.status == STATUS_NOT_FOUND:
        return "未找到"
    return "查詢失敗"


def _batch_value(lookup: SpeciesLookup) -> str:
    """批次結果 JSON 中每個名稱的值。"""
    if lookup.status == STATUS_FOUND:
        return lookup.value or ""
    if lookup.status == STATUS_NOT_FOUND:
        return "未找到" + _parent_note(lookup)
    return f"查詢失敗：{lookup.detail}"


def run_manual_lookup_service(name: str) -> str:
    """執行學名查詢"""
    if not is_potential_species_name(name):
        return f"'{name}' 不像是一個有效的學名格式 (例如：Felis catus)。"
    return describe_lookup(lookup_species(name))


def run_batch_lookup_service(json_text: str):
    """執行此 generator 並逐步回報進度（yield update dict）。"""
    try:
        names = json.loads(json_text)
        if not isinstance(names, list):
            message = "錯誤：JSON 內容必須是一個列表 (List)。"
            logger.error(message)
            yield {"log": message, "error": True}
            return

        results = {}
        total = len(names)
        stopped = False
        counts = dict.fromkeys(("找到", "未找到", "查詢失敗", "格式錯誤", "未查詢"), 0)

        first = GLOBAL_LOG_LIMITER.filter(
            {"log": f"開始批次查詢 {total} 個學名...", "progress": 0}
        )
        if first is not None:
            yield first

        for i, name in enumerate(names):
            if not is_potential_species_name(name):
                results[name] = "格式錯誤"
                counts["格式錯誤"] += 1
            else:
                lookup = lookup_species(name)
                results[name] = _batch_value(lookup)
                counts[_batch_category(lookup)] += 1
                if lookup.rate_limited:  # 含上一層物種查詢遇到的限流
                    remaining = names[i + 1 :]
                    counts["未查詢"] += len(remaining)
                    for rest in remaining:
                        results.setdefault(rest, "未查詢（已因限流停止）")
                    message = (
                        "維基百科回應請求過於頻繁，已停止批次查詢；"
                        f"其餘 {len(remaining)} 個名稱未查詢，請稍後再試。"
                    )
                    logger.warning(message)
                    yield {"log": message, "progress": (i + 1) / total}
                    stopped = True
                    break

            update = GLOBAL_LOG_LIMITER.filter(
                {
                    "log": f"({i + 1}/{total}) 已查詢: {name}",
                    "progress": (i + 1) / total,
                }
            )
            if update is not None:
                yield update

        final = GLOBAL_LOG_LIMITER.filter(
            {
                "log": (
                    "--- 批次查詢已因限流停止（部分完成）："
                    if stopped
                    else "--- 批次查詢完成："
                )
                + "・".join(
                    f"{label} {n:,}"
                    for label, n in counts.items()
                    if n or label in ("找到", "未找到")
                )
                + " ---",
                "result": json.dumps(results, indent=2, ensure_ascii=False),
            }
        )
        if final is not None:
            yield final

    except json.JSONDecodeError:
        message = "輸入的不是有效的 JSON 格式。"
        logger.error(message)
        yield {"log": message, "error": True}
    except Exception as e:  # noqa: BLE001 - 錯誤已記錄或回報給呼叫端，不中斷整批流程
        message = f"查詢時發生錯誤: {e}"
        logger.error(message)
        yield {"log": message, "error": True}
