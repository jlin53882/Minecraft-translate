"""Lookup pipeline service wrappers.

PR17：將 lookup 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import json
import logging

from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.utils.species_cache import (
    is_potential_species_name,
    lookup_species_name,
)

logger = logging.getLogger(__name__)

def run_manual_lookup_service(name: str) -> str:
    """執行學名查詢"""
    if not is_potential_species_name(name):
        return f"'{name}' 不像是一個有效的學名格式 (例如：Felis catus)。"
    result = lookup_species_name(name)
    return result if result else "在本地快取和線上查詢中均未找到結果。"

def run_batch_lookup_service(json_text: str):
    """執行此 generator 並逐步回報進度（yield update dict）。

    """
    try:
        names = json.loads(json_text)
        if not isinstance(names, list):
            yield {"log": "錯誤：JSON 內容必須是一個列表 (List)。", "error": True}
            return

        results = {}
        total = len(names)

        first = GLOBAL_LOG_LIMITER.filter(
            {"log": f"開始批次查詢 {total} 個學名...", "progress": 0}
        )
        if first is not None:
            yield first

        for i, name in enumerate(names):
            if is_potential_species_name(name):
                result = lookup_species_name(name)
                results[name] = result if result else "未找到"
            else:
                results[name] = "格式錯誤"

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
                "log": "--- 批次查詢完成 ---",
                "result": json.dumps(results, indent=2, ensure_ascii=False),
            }
        )
        if final is not None:
            yield final

    except json.JSONDecodeError:
        logger.error({"log": "輸入的不是有效的 JSON 格式。"})
        yield {"log": "輸入的不是有效的 JSON 格式。", "error": True}
    except Exception as e:
        logger.error({"log": f"查詢時發生錯誤: {e}"})
        yield {"log": f"查詢時發生錯誤: {e}", "error": True}
