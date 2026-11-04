"""智慧校對的 needs_context 升級：補上相鄰鍵證據後，只對這些條目再問一次。

第一次判斷只看同 key、同文與詞庫證據。許多 ``needs_context`` 其實只是缺少「同一組鍵的
其他欄位」：例如 ``config.xxx`` 的子鍵 ``config.xxx.tooltip``、父鍵，或同父鍵的其他欄位。
本模組在第一次結果之後，為 ``needs_context`` 條目補上這些相鄰鍵再請模型判斷一次：已審核的譯文優先；
沒有已審核譯文時，帶非 AI 來源的未審核譯文並明確標示 unreviewed（只當命名參考）；
都沒有就只帶英文原文。AI 自己產生且未審核的譯文不附上，避免循環論證。

* 第一次請求的 payload 不變，所以歷史重用（需要完整 local context 相同）不受影響。
* 升級結果必須通過同一組 strict 驗證才會取代原結果；請求失敗時維持第一次結果。
* 模型若引用相鄰鍵證據，該證據不在已保存的 context 內，因此引用會被改寫進 reason，
  ``evidence_ids`` 只保留原本 context 內存在的 id。
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Sequence
from typing import Any

from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import SRC_AI, SRC_AI_REPAIR
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_SYSTEM_PROMPT,
    build_smart_review_payload,
    smart_review_response_schema,
    smart_review_system_prompt,
)
from translation_tool.translation_db.smart_review_provider import (
    SmartReviewProviderSession,
    _SmartReviewRetryRuntime,
)
from translation_tool.translation_db.smart_review_run_state import (
    SmartReviewBatch,
    SmartReviewRunState,
)
from translation_tool.translation_db.smart_review_validation import (
    parse_smart_review_response,
)
from translation_tool.utils.cancellation import TaskCancelled

ADJACENT_EVIDENCE_LIMIT = 12
_ROW_FETCH_LIMIT = 64

SMART_REVIEW_ESCALATION_PROMPT = """

補充說明：這是第二次判斷。第一次你對這些條目回答了 needs_context，現在每筆的 context 已加入
reference_type 為 adjacent_key 的相鄰鍵資料（同一組鍵的父鍵、子鍵或同父鍵的其他欄位，含它們的
英文與目前譯文）。請用這些資料判斷：
1. 相鄰鍵的 review_status 標示譯文可信度；只有 reviewed 可當作標準答案，unreviewed 只能當命名與用語參考，no_translation 只有英文，只能看出命名結構。
2. 若相鄰資料已足以確定 no_change 或 suggest_revision，請改判並引用該相鄰鍵的 key。
3. 若仍缺資料，維持 needs_context，並在 reason 具體說明還缺哪一項。"""


def adjacent_key_context(
    db: Any, snapshot: SmartReviewSnapshot, *, limit: int = ADJACENT_EVIDENCE_LIMIT
) -> list[dict[str, Any]]:
    """同模組、同 kind、同版本中與目前鍵相鄰的條目（父鍵、子鍵、同父鍵手足）。

    只用 ``idx_entry_kind_mod_key`` 的鍵範圍查詢，不排序；挑選與截斷在記憶體完成。
    """
    key = snapshot.key
    parent = key.rsplit(".", 1)[0] if "." in key else ""
    rows: dict[int, list[tuple]] = {}
    for low, high in _key_ranges(key, parent):
        for row in db._q(_ADJACENT_SQL, _params(snapshot, low, high)):
            rows.setdefault(int(row[0]), []).append(row)
    if parent:
        for row in db._q(_ADJACENT_SQL, _params(snapshot, parent, parent + "\x00")):
            rows.setdefault(int(row[0]), []).append(row)
    rows.pop(snapshot.entry_id, None)
    twins = set(snapshot.twin_entry_ids)
    picked = [
        _pick_translation(entry_rows)
        for entry_id, entry_rows in rows.items()
        if entry_id not in twins and str(entry_rows[0][1]) != key
    ]
    ordered = sorted(picked, key=lambda row: _distance(key, str(row[1])))
    return [_evidence_item(row) for row in ordered[:limit]]


# AI 產生且未審核的譯文是模型自己的輸出；拿來佐證校對會變成循環論證，所以不附上。
_SELF_GENERATED_SOURCES = frozenset({SRC_AI, SRC_AI_REPAIR})


def _pick_translation(entry_rows: list[tuple]) -> tuple:
    """同一條目有多個來源時：已審核優先，其次非 AI 來源的未審核譯文；都沒有就只留英文。"""
    with_text = [row for row in entry_rows if row[3]]
    reviewed = [row for row in with_text if row[4] == "reviewed"]
    others = [row for row in with_text if row[5] not in _SELF_GENERATED_SOURCES]
    for pool in (reviewed, others):
        if pool:
            return pool[0]
    entry_id, key, en_us = entry_rows[0][:3]
    return (entry_id, key, en_us, "", None, None)


_ADJACENT_SQL = (
    "SELECT e.id, e.key, e.en_us, f.zh_tw, f.review_status, f.source "
    "FROM entry e LEFT JOIN translation f ON f.entry_id=e.id "
    "WHERE e.kind=? AND e.mod_id=? AND e.mc_version=? AND e.key>=? AND e.key<? "
    f"LIMIT {_ROW_FETCH_LIMIT}"
)


def _params(snapshot: SmartReviewSnapshot, low: str, high: str) -> tuple[Any, ...]:
    return (snapshot.kind, snapshot.mod_id, snapshot.mc_version, low, high)


def _key_ranges(key: str, parent: str) -> list[tuple[str, str]]:
    """子鍵（``key.*``）與同父鍵手足（``parent.*``）的鍵範圍；``/`` 緊接在 ``.`` 之後。"""
    ranges = [(key + ".", key + "/")]
    if parent:
        ranges.append((parent + ".", parent + "/"))
    return ranges


def _distance(key: str, other: str) -> tuple[int, int, str]:
    """離目前鍵越近越前：共同前綴越長、長度差越小。"""
    common = 0
    for a, b in zip(key, other, strict=False):
        if a != b:
            break
        common += 1
    return (-common, abs(len(key) - len(other)), other)


def _evidence_item(row: tuple) -> dict[str, Any]:
    entry_id, key, en_us, zh_tw, review_status, source = row
    status = (review_status or "unreviewed") if zh_tw else "no_translation"
    limitations = {
        "reviewed": "adjacent key in the same module; reviewed translation",
        "unreviewed": "adjacent key in the same module; translation NOT reviewed, "
        "naming reference only",
        "no_translation": "adjacent key in the same module; English only, no translation",
    }.get(status, "adjacent key in the same module; naming reference only")
    return {
        "id": f"adj:{entry_id}",
        "reference_type": "adjacent_key",
        "key": str(key),
        "en_us": str(en_us),
        "zh_tw": str(zh_tw or ""),
        "review_status": status,
        "source": source,
        "limitations": limitations,
    }


def _needs_context_ids(results: Sequence[dict[str, Any]]) -> set[str]:
    return {
        str(result.get("id"))
        for result in results
        if not result.get("validation_error")
        and result.get("decision") == "needs_context"
    }


def enrich_with_adjacent_context(
    db: Any, batch: Sequence[SmartReviewSnapshot]
) -> list[SmartReviewSnapshot]:
    """回傳補上相鄰鍵證據的快照；沒有相鄰資料的條目維持原樣。"""
    enriched: list[SmartReviewSnapshot] = []
    for snapshot in batch:
        extra = adjacent_key_context(db, snapshot)
        enriched.append(
            dataclasses.replace(snapshot, context=(*snapshot.context, *extra))
            if extra
            else snapshot
        )
    return enriched


def merge_escalated_results(
    results: list[dict[str, Any]],
    escalated: Sequence[dict[str, Any]],
    enriched: Sequence[SmartReviewSnapshot],
) -> tuple[list[dict[str, Any]], int]:
    """以通過驗證的升級結果取代原 needs_context；回傳（結果，被改判的筆數）。"""
    by_id = {str(r.get("id")): r for r in escalated if not r.get("validation_error")}
    adjacent_by_ordinal = {
        str(snapshot.ordinal): {
            str(item["id"]): item
            for item in snapshot.context
            if item.get("reference_type") == "adjacent_key"
        }
        for snapshot in enriched
    }
    merged: list[dict[str, Any]] = []
    resolved = 0
    for result in results:
        result_id = str(result.get("id"))
        new = by_id.get(result_id)
        if new is None or result_id not in adjacent_by_ordinal:
            merged.append(result)
            continue
        merged.append(_adopt(new, adjacent_by_ordinal[result_id]))
        resolved += new.get("decision") != "needs_context"
    return merged, resolved


def _adopt(new: dict[str, Any], adjacent: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把對相鄰鍵的引用寫進 reason；已保存的 context 不含相鄰鍵，所以不留在 evidence_ids。"""
    cited = [
        adjacent[str(i)] for i in new.get("evidence_ids") or [] if str(i) in adjacent
    ]
    if not cited:
        return new
    notes = "；".join(
        f"{item['key']}＝{item['zh_tw']}（{item['review_status']}）"
        if item["zh_tw"]
        else f"{item['key']}（尚無譯文；英文：{item['en_us']}）"
        for item in cited
    )
    return {
        **new,
        "evidence_ids": [
            i for i in new.get("evidence_ids") or [] if str(i) not in adjacent
        ],
        "reason": f"{new.get('reason', '')}（相鄰鍵佐證：{notes}）",
    }


def escalate_needs_context_results(
    db: Any,
    session: SmartReviewProviderSession,
    state: SmartReviewRunState,
    batch_ctx: SmartReviewBatch,
    batch: Sequence[SmartReviewSnapshot],
    results: list[dict[str, Any]],
    retry_runtime: _SmartReviewRetryRuntime,
    outer_attempt: int,
) -> list[dict[str, Any]]:
    """對 needs_context 條目補相鄰鍵證據後再判斷一次；失敗時維持第一次結果。"""
    ids = _needs_context_ids(results)
    pending = [item for item in batch if str(item.ordinal) in ids]
    if not pending:
        return results
    enriched = [
        snapshot
        for snapshot in enrich_with_adjacent_context(db, pending)
        if any(
            item.get("reference_type") == "adjacent_key" for item in snapshot.context
        )
    ]
    if not enriched:
        return results  # 沒有任何相鄰資料可補，再問一次也不會有新資訊

    state.bump("context_escalation_attempts")
    state.bump("context_escalation_items", len(enriched))
    state.emit_batch_event(
        batch_ctx,
        "context_escalation_start",
        outer_attempt,
        expected_count=len(enriched),
    )
    escalated = _request(session, state, batch_ctx, retry_runtime, enriched)
    merged, resolved = merge_escalated_results(results, escalated, enriched)
    state.bump("context_escalation_resolved", resolved)
    state.emit_batch_event(
        batch_ctx,
        "context_escalation_complete",
        outer_attempt,
        expected_count=len(enriched),
        resolved_count=resolved,
    )
    return merged


def _ignore_network_event(_event: dict[str, object]) -> None:
    """升級請求不單獨回報 network 事件；結果統計在 ``context_escalation_*`` 事件。"""


def _request(
    session: SmartReviewProviderSession,
    state: SmartReviewRunState,
    batch_ctx: SmartReviewBatch,
    retry_runtime: _SmartReviewRetryRuntime,
    enriched: Sequence[SmartReviewSnapshot],
) -> list[dict[str, Any]]:
    """送出升級請求並嚴格解析；任何失敗都回傳空清單（維持第一次結果）。"""
    from translation_tool.core.lm_translator_main import _wait_for_request_slot

    api_key = session.claim_api_key()
    if session.provider == "gemini" and not api_key:
        state.bump("context_escalation_request_errors")
        return []
    started = time.monotonic()
    meta: dict[str, Any] = {}
    try:
        _wait_for_request_slot(
            retry_runtime, session.model_name, cancel_check=session.cancel_check
        )
        raw = session.call(
            system_prompt=smart_review_system_prompt(
                SMART_REVIEW_SYSTEM_PROMPT + SMART_REVIEW_ESCALATION_PROMPT, enriched
            ),
            payload=build_smart_review_payload(enriched),
            api_key=api_key,
            meta_out=meta,
            response_schema=smart_review_response_schema(
                [str(item.ordinal) for item in enriched]
            ),
            network_event_callback=_ignore_network_event,
        )
        state.record_usage(meta)
        return parse_smart_review_response(raw, list(enriched))
    except TaskCancelled:
        state.cancelled = True
        return []
    except Exception:  # noqa: BLE001 - 升級是選用的補強，失敗不得影響第一次結果
        state.record_usage(meta)
        state.bump("context_escalation_request_errors")
        return []
    finally:
        state.add_timing_seconds(batch_ctx, "api_ms", time.monotonic() - started)
