"""智慧校對審查頁的候選呈現：本地證據、草稿格式診斷與依狀態決定的提示文字。

這裡只把候選（``SmartReviewCandidate``）轉成給審查者看的文字與診斷結果，不碰 Flet
控制項也不碰資料庫，所以可以脫離 UI 單獨測試。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services_impl.moddb_smart_review_service import (
    SMART_REVIEW_MANUAL_EDIT_STATUSES,
    SmartReviewCandidate,
    SmartReviewStatus,
    smart_review_format_diagnostics,
)

_CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}
_OFFICIAL_FULL_MATCHES = frozenset({"exact_key", "exact_text", "official_phrase"})
_REVIEWED_REFERENCE_TYPES = frozenset(
    {"reviewed_lang_glossary", "same_key_reviewed", "same_text_reviewed"}
)
_KNOWN_REFERENCE_TYPES = (
    _REVIEWED_REFERENCE_TYPES | {"official_locale"} | {"module_locale"}
)
# AI 沒有引用證據時，最多列出幾筆可參考資料／引用幾個證據 id。
_MAX_REFERENCE_LINES = 6
_MAX_CITED_EVIDENCE = 8
# 為什麼某筆不能套用：讓灰掉的按鈕有明確的說明。
_NOT_APPLICABLE = {
    SmartReviewStatus.APPLIED: "這筆已經套用；要再修改請編輯草稿後重新套用。",
    SmartReviewStatus.REJECTED: "這筆已選擇保留原譯文。",
    SmartReviewStatus.NEEDS_CONTEXT: "AI 表示需要更多上下文才能判斷，沒有提供建議譯文。",
    SmartReviewStatus.NO_CHANGE: "AI 認為目前譯文不用修改，沒有建議譯文。",
    SmartReviewStatus.INVALID: "AI 的回應沒有通過驗證，不能套用。",
    SmartReviewStatus.INVALID_FORMAT: "草稿的格式未通過檢查，這筆已鎖定；請重新校對。",
    SmartReviewStatus.STALE: "資料庫內容在校對後已變更，這筆已過期；請重新校對。",
}
_MANUAL_EDIT_HINTS = {
    SmartReviewStatus.APPLIED: "這筆已套用。修改草稿後按「重新套用」會覆蓋目前人工譯文（可從條目歷史還原）。",
    SmartReviewStatus.REJECTED: "這筆先前選擇保留原譯文；仍可編輯草稿後套用。",
    SmartReviewStatus.NEEDS_CONTEXT: "AI 沒有提供建議譯文；草稿預設為目前譯文，可直接修改後套用。",
    SmartReviewStatus.NO_CHANGE: "AI 認為不用修改；草稿預設為目前譯文，仍可修改後套用。",
    SmartReviewStatus.INVALID: "AI 的回應沒有通過驗證；草稿預設為目前譯文，可直接修改後套用。",
    SmartReviewStatus.INVALID_FORMAT: "先前的草稿格式未通過檢查；請修正草稿後套用。",
    SmartReviewStatus.STALE: "資料庫內容在校對後已變更；已對齊目前譯文，可直接修改後套用。",
}
_INVALID_STATUSES = frozenset(
    {SmartReviewStatus.INVALID, SmartReviewStatus.INVALID_FORMAT}
)


def is_manual_edit_row(row: SmartReviewCandidate | None) -> bool:
    """這筆候選是否走人工編輯套用（非 ready 但可編輯的狀態）。"""
    return row is not None and row.status in SMART_REVIEW_MANUAL_EDIT_STATUSES


def is_editable_row(row: SmartReviewCandidate | None) -> bool:
    """這筆候選的草稿框是否可編輯：``ready`` 或走人工編輯套用的狀態。"""
    return row is not None and (
        row.status == SmartReviewStatus.READY or is_manual_edit_row(row)
    )


def manual_edit_hint(row: SmartReviewCandidate) -> str:
    """說明人工編輯這筆候選的方式；未儲存的修改換筆後不會保留。"""
    hint = _MANUAL_EDIT_HINTS.get(row.status, "")
    if row.status in _INVALID_STATUSES and row.validation_error:
        hint += f"原因：{row.validation_error}。"
    return f"{hint}未套用的修改換筆後不會保留。" if hint else ""


def initial_draft_text(row: SmartReviewCandidate) -> str:
    """決定草稿框的起始內容：已套用以目前資料庫值為準，其他依序取草稿、建議、現況。"""
    if row.status == SmartReviewStatus.APPLIED:
        return row.current_zh_tw or ""
    if row.status in SMART_REVIEW_MANUAL_EDIT_STATUSES:
        return row.draft_zh_tw or row.suggested_zh_tw or row.current_zh_tw or ""
    return row.draft_zh_tw or row.suggested_zh_tw or ""


def not_applicable_reason(row: SmartReviewCandidate) -> str | None:
    """回傳這筆候選不能套用的原因；可套用時回傳 None。"""
    if row.status == SmartReviewStatus.READY:
        return None
    reason = _NOT_APPLICABLE.get(row.status, f"目前狀態「{row.status}」不能套用。")
    if row.status in _INVALID_STATUSES and row.validation_error:
        reason += f"原因：{row.validation_error}"
    return reason


@dataclass(frozen=True)
class FormatDiagnosis:
    """草稿格式診斷的呈現結果：文字、色調（muted／gold／red／em）與是否擋下儲存／套用。"""

    text: str
    tone: str
    has_issue: bool


def _is_official_full(item: dict) -> bool:
    """官方語系且比對到完整 key／原文／片語的證據。"""
    return (
        item.get("reference_type") == "official_locale"
        and item.get("match_type") in _OFFICIAL_FULL_MATCHES
    )


def _reference_label(item: dict) -> str:
    """將 context 來源轉成審查者看得懂的可信度分層。"""
    reference_type = str(item.get("reference_type", "local"))
    if reference_type == "official_locale":
        return "官方完整翻譯" if _is_official_full(item) else "官方術語"
    if reference_type == "reviewed_lang_glossary":
        return "已審核 lang 參考"
    if reference_type in {"same_key_reviewed", "same_text_reviewed"}:
        return "已審核資料庫參考"
    if reference_type == "module_locale":
        return "未審核模組翻譯（低信心，非官方標準）"
    return "本地參考"


def _format_evidence_item(display_id: str, item: dict) -> str:
    """格式化單筆證據及其版本、來源與品質限制。"""
    return (
        f"{display_id} · {_reference_label(item)} · "
        f"來源={item.get('source_label', item.get('source', ''))} · "
        f"版本={item.get('mc_version', '-')} · "
        f"信心={_CONFIDENCE_LABEL.get(str(item.get('confidence', 'low')), '未知')} · "
        f"{item.get('en_us', '')} → {item.get('zh_tw', '')} · "
        f"{item.get('limitations', '')}"
    )


def _reference_groups(context: list[dict]) -> tuple[tuple[str, list[dict]], ...]:
    """把未被 AI 引用的 context 依可信度分組，順序即顯示順序。"""
    return (
        ("官方完整翻譯參考", [i for i in context if _is_official_full(i)]),
        (
            "官方術語參考",
            [
                i
                for i in context
                if i.get("reference_type") == "official_locale"
                and not _is_official_full(i)
            ],
        ),
        (
            "已審核參考",
            [
                i
                for i in context
                if i.get("reference_type") in _REVIEWED_REFERENCE_TYPES
            ],
        ),
        (
            "模組語系參考",
            [i for i in context if i.get("reference_type") == "module_locale"],
        ),
        (
            "其他本地參考",
            [
                i
                for i in context
                if i.get("reference_type") not in _KNOWN_REFERENCE_TYPES
            ],
        ),
    )


def _uncited_reference_lines(context: list[dict]) -> list[str]:
    """AI 沒引用任何證據時，列出可參考資料（非本次 AI 證據）。"""
    if not context:
        return ["完全沒有可供參考的本地 context；請人工確認。"]
    lines = ["可參考資料（非本次 AI 證據）："]
    remaining = _MAX_REFERENCE_LINES
    for label, grouped in _reference_groups(context):
        if not grouped or remaining <= 0:
            continue
        lines.append(f"{label}：")
        selected = grouped[:remaining]
        lines.extend(
            _format_evidence_item(str(item.get("id")), item) for item in selected
        )
        remaining -= len(selected)
    return lines


def _cited_evidence_lines(row: SmartReviewCandidate, context: list[dict]) -> list[str]:
    """逐一解析 AI 引用的證據 id，並相容舊版 entry-only id。"""
    evidence_by_id = {str(item.get("id")): item for item in context}
    legacy_by_id: dict[str, list[dict]] = {}
    for item in context:
        legacy_id = item.get("legacy_id")
        if legacy_id:
            legacy_by_id.setdefault(str(legacy_id), []).append(item)
    lines = []
    for evidence_id in tuple(row.evidence_ids)[:_MAX_CITED_EVIDENCE]:
        requested_id = str(evidence_id)
        item = evidence_by_id.get(requested_id)
        if item is not None:
            lines.append(_format_evidence_item(requested_id, item))
            continue
        matches = legacy_by_id.get(requested_id, [])
        if len(matches) == 1:
            lines.append(
                _format_evidence_item(
                    f"{requested_id} → {matches[0].get('id')}", matches[0]
                )
            )
        elif matches:
            lines.append(f"{requested_id}（舊版 id 對應多個來源，無法唯一解析）")
        else:
            lines.append(f"{requested_id}（找不到本地證據）")
    return lines


class SmartReviewEvidencePresenter:
    """把候選的證據與草稿格式診斷轉成審查頁要顯示的內容（無狀態、不碰 UI）。"""

    @staticmethod
    def evidence_text(row: SmartReviewCandidate) -> str:
        """只顯示 AI 實際引用的證據，並相容解析舊版 entry-only id。"""
        context = list(row.context)
        if not tuple(row.evidence_ids):
            lines = ["AI 未引用任何本地證據。", *_uncited_reference_lines(context)]
        else:
            lines = _cited_evidence_lines(row, context)
        return "\n".join(lines)

    @staticmethod
    def diagnose_draft(row: SmartReviewCandidate, draft: str) -> FormatDiagnosis:
        """依目前草稿計算格式診斷；空白草稿對可編輯候選視為有問題（不可儲存／套用）。"""
        if not draft.strip():
            return FormatDiagnosis(
                "格式診斷：尚無可分析草稿", "muted", is_editable_row(row)
            )
        try:
            diagnostics = smart_review_format_diagnostics(
                row.en_us, draft, kind=row.kind
            )
        except (TypeError, ValueError) as exc:
            return FormatDiagnosis(f"格式診斷無法執行：{exc}", "gold", False)
        lines = diagnostics.summary_lines
        text = "格式診斷：" + "；".join(lines) if lines else "格式診斷：未發現格式問題"
        return FormatDiagnosis(
            text, "red" if diagnostics.has_issues else "em", diagnostics.has_issues
        )
