"""智慧校對的 provider prompt、回應結構與請求負載組裝。

本模組只放不碰資料庫、不碰 provider 的純資料：決策／問題類型常數、
system prompt、strict JSON schema，以及送給模型的 payload 組裝。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.utils.config_manager import (
    DEFAULT_SMART_REVIEW_BATCH_SIZE,
    DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE,
)

SMART_REVIEW_DECISIONS = ("no_change", "suggest_revision", "needs_context")
SMART_REVIEW_DEFAULT_BATCH_SIZE = DEFAULT_SMART_REVIEW_BATCH_SIZE
SMART_REVIEW_MAX_BATCH_SIZE = DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE
SMART_REVIEW_VALIDATION_REPAIR_LIMIT = 1
SMART_REVIEW_ISSUE_TYPES = (
    "meaning",
    "terminology",
    "grammar",
    "style",
    "format",
    "context",
)
SMART_REVIEW_SYSTEM_PROMPT = """你是 Minecraft 繁體中文翻譯的本地校對助手。
你只能根據每筆資料提供的英文原文、目前譯文與 local context 判斷，不得查詢網路或補造外部資料。
你的輸出只是人工審查候選，不是自動翻譯，也不會直接寫入資料庫。

規則：
1. 每個 id 必須原樣保留，不能新增、刪除、重排或改寫。
2. decision 只能是 no_change、suggest_revision、needs_context。
3. 只有確定有必要修改時才使用 suggest_revision；不確定時使用 needs_context。
4. suggest_revision 必須提供完整的繁體中文 suggested_zh_tw；其他兩種 decision 的 suggested_zh_tw 必須為空字串。
5. 每筆都必須提供非空且具體的 reason，不可填空字串、null、「無」、「N/A」、只有標點或泛稱。suggest_revision 要指出原文／目前譯文哪裡造成語意或專有名詞錯誤，以及建議如何修正；不能只寫「翻譯錯誤」、「更自然」或「建議優化」。
6. no_change 要說明原文與目前譯文為何可以保留，至少指出可核對的詞句或語意；suggest_revision 要在 reason 中引用可核對的原文或目前譯文片段；needs_context 要明確指出仍缺少哪一項資料。
7. needs_context 要說明缺少什麼本地資料；沒有可靠佐證時不得寫成「已確認」或宣稱查過網路。
8. 保留 Minecraft § 格式碼、佔位符、Patchouli／指南結構與原文語意。
9. evidence_ids 只能引用該筆 context 中已有的 id；沒有引用時使用空陣列。衝突 evidence 不可當成唯一正解。
10. 輸出前逐筆自我檢查：reason 非空、decision 與 suggested_zh_tw 相符、reason 能支持 decision，且 evidence_ids 都存在於該筆 context。
"""
SMART_REVIEW_REPAIR_SYSTEM_PROMPT = """你是 Minecraft 繁體中文翻譯的本地校對回應修復助手。
你只能修復每筆資料標示的 validation_error，不得改變原文、目前譯文、id 或 local context 的事實。
輸出仍是人工審查候選，不是自動翻譯，也不會直接寫入資料庫。

規則：
1. 每個 id 必須原樣保留，且每筆輸入各回傳一次。
2. decision 只能是 no_change、suggest_revision、needs_context。
3. 每筆 reason 必須是非空、具體、可核對的繁體中文，不能填「無」、N/A、只有標點或泛稱。
4. no_change 的 suggested_zh_tw 必須是空字串；suggest_revision 必須提供不同且通過格式檢查的完整繁體中文；needs_context 的 suggested_zh_tw 必須是空字串。
5. suggest_revision 的 reason 必須指出原文或目前譯文中可核對的差異；不確定時使用 needs_context，不得補造外部資料。
6. evidence_ids 只能引用該筆 context 已有的 id，不能新增或猜測 id。
7. 保留 Minecraft § 格式碼、佔位符、Patchouli／指南結構與原文語意。
8. 輸出前逐筆檢查所有 required 欄位、reason、suggested_zh_tw 與 evidence_ids；只回傳 JSON，不要 Markdown 或額外說明。
"""


def smart_review_response_schema(ids: Sequence[str]) -> dict[str, Any]:
    """建立限制在目前批次識別碼內的 provider JSON 結構。"""
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": list(ids)},
                        "decision": {
                            "type": "string",
                            "enum": list(SMART_REVIEW_DECISIONS),
                        },
                        "suggested_zh_tw": {"type": "string"},
                        "issue_types": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(SMART_REVIEW_ISSUE_TYPES),
                            },
                        },
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "number"},
                        # Keep length enforcement local in ``_validate_reason``.
                        # Gemini's structured-output schema supports only a
                        # subset of JSON Schema and rejects ``maxLength``.
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "decision",
                        "suggested_zh_tw",
                        "issue_types",
                        "evidence_ids",
                        "confidence",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    return schema


def build_smart_review_payload(
    snapshots: Sequence[SmartReviewSnapshot],
) -> dict[str, list[dict[str, Any]]]:
    """建立送給模型的不可預測識別碼智慧校對負載。"""
    return {
        "items": [
            {
                "id": str(item.ordinal),
                "kind": item.kind,
                "mc_version": item.mc_version,
                "mod_id": item.mod_id,
                "key": item.key,
                "source_text": item.en_us,
                "current_translation": item.current_zh_tw,
                "context": list(item.context),
            }
            for item in snapshots
        ]
    }


def build_smart_review_validation_repair_payload(
    snapshots: Sequence[SmartReviewSnapshot],
    results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """只建立驗證失敗條目的受限修復負載，不重送已通過的條目。"""
    invalid_by_id = {
        str(result.get("id")): str(result.get("validation_error") or "")[:160]
        for result in results
        if result.get("validation_error")
    }
    invalid_snapshots = [
        snapshot for snapshot in snapshots if str(snapshot.ordinal) in invalid_by_id
    ]
    payload = build_smart_review_payload(invalid_snapshots)
    payload["validation_errors"] = [
        {"id": str(snapshot.ordinal), "error": invalid_by_id[str(snapshot.ordinal)]}
        for snapshot in invalid_snapshots
    ]
    return payload
