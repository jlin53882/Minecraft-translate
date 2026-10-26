"""app/views/moddb/operation_summaries.py：Mod 資料庫各操作的預覽與結果文字。

同一個操作的預覽與實際執行結果用同一組欄位與用語（例如批次取代的「可替換／已替換、含跨版本、
略過、品質惡化」），結果只多出寫入後才知道的數字。這裡只組文字（純函式），畫面與後台記錄
由各對話框負責；後台記錄與畫面訊息用同一段文字。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from app.services_impl import moddb_cleanup_service as svc
from app.services_impl.moddb_service import (
    BatchReplacePlan,
    BatchReplaceResult,
    ReviewPreviewItem,
)
from app.views.moddb.formatting import format_count


# ------------------------------------------------------------------ 資料清理
def cleanup_category_lines(counts: dict[str, int]) -> str:
    """各清理類別的筆數（每行一類，沒有資料的類別不列）。"""
    return "\n".join(
        f"　{svc.CLEANUP_CATEGORIES.get(cid, cid)}：{format_count(n)} 筆"
        for cid, n in counts.items()
        if n
    )


def cleanup_plan_lines(plan, *, applied: bool) -> list[str]:
    """預覽與刪除結果共用同一組欄位：筆數、類別、已有譯文或歷史的筆數、逐項略過原因。"""
    if applied:
        head = (
            f"已刪除 {format_count(plan.deletable)} 筆"
            f"（檢查檔標記 {format_count(plan.requested)} 筆）"
        )
    else:
        head = (
            f"檢查檔標記要刪除 {format_count(plan.requested)} 筆；"
            f"現在實際可刪 {format_count(plan.deletable)} 筆"
        )
    lines = [head]
    if plan.deletable:
        lines.append(cleanup_category_lines(dict(plan.by_category)))
    if plan.with_translation:
        lines.append(
            f"其中 {format_count(plan.with_translation)} 筆已有譯文或歷史："
            + (
                "已連同所有來源的譯文與歷史一起移除"
                if applied
                else "刪除會連同所有來源的譯文與歷史一起移除"
            )
            + "，可用「還原選取的清理」復原"
        )
    lines.extend(
        f"略過 {format_count(n)} 筆：{reason}" for reason, n in plan.skipped.items()
    )
    return lines


# ------------------------------------------------------------------ 批次取代
def batch_replace_counts_text(
    verb: str,
    updated: int,
    extra_versions: int,
    skipped: int,
    plan: BatchReplacePlan | None,
) -> str:
    """預覽（可替換）與結果（已替換）共用的筆數：跨版本、略過與品質變化。"""
    text = f"{verb} {updated:,d} 筆"
    if extra_versions:
        text += f"（含跨版本 {extra_versions:,d} 筆）"
    if skipped:
        text += f"　·　略過 {skipped:,d} 筆"
    if plan is not None and plan.quality_worsened_count:
        text += f"　·　品質惡化 {plan.quality_worsened_count:,d} 筆"
    if plan is not None and plan.quality_mixed_count:
        text += f"　·　品質混合變化 {plan.quality_mixed_count:,d} 筆"
    return text


def batch_replace_result_text(
    result: BatchReplaceResult, plan: BatchReplacePlan | None
) -> str:
    """寫入完成的訊息：欄位與預覽相同，另加只有寫入後才知道的「被來源優先序遮住」筆數。"""
    extra = plan.extra_version_count if plan is not None else 0
    text = batch_replace_counts_text(
        "已替換", result.updated, extra, result.skipped, plan
    )
    if result.shadowed:
        # 新譯文已存進人工來源，但自訂的來源優先序讓別的來源仍然生效
        text += (
            f"。其中 {result.shadowed:,} 筆因目前來源優先序仍顯示原本的譯文，"
            f"生效譯文只改變 {result.updated - result.shadowed:,} 筆"
        )
    return text + "。可在記錄中整批還原。"


# ------------------------------------------------------------------ 審核
def bulk_review_result_text(plan, excluded: Collection, result) -> str:
    """批量審核完成的訊息：用預覽的欄位（納入、同步其他版本），並列出預覽時的預估以便對照。"""
    included = len(plan.candidates) - len(excluded)
    parts = [
        f"納入 {format_count(included)} 筆，已審核 {format_count(result.reviewed)} 筆"
    ]
    if plan.propagate:
        sibling_total, _replaced = plan.sibling_totals(excluded)
        parts.append(
            f"同步其他版本 {format_count(result.synced)} 筆"
            f"（預覽 {format_count(sibling_total)} 筆）"
        )
    if result.stale_skipped:
        parts.append(f"{format_count(result.stale_skipped)} 筆已被其他操作改動而略過")
    return "；".join(parts)


def manual_review_result_text(
    done: Sequence, preview: Sequence[ReviewPreviewItem] | None
) -> str:
    """單筆審核完成的訊息，欄位與確認視窗的「審核版本／略過版本」相同（審核版本含本版本）。"""
    others = [change.mc_version for change in done if not change.is_self]
    if not others:
        text = "已標記為人工已審核（來源不變）"
    else:
        text = (
            f"已審核 {len(done)} 個版本：本版本＋同步 {len(others)} 個"
            f"（{'、'.join(others)}）"
        )
    skipped = [item.mc_version for item in preview or () if not item.included]
    if skipped:
        text += f"；略過 {len(skipped)} 個版本（{'、'.join(skipped)}）"
    return text


# ------------------------------------------------------------------ 機翻與修復
_SAME_AS_OLD_TOOLTIP = "只計入 AI 有效回傳且內容與舊譯文完全相同的筆數；格式不符、失敗與資料競態跳過各自另計。"


def translation_kpi_titles(
    mode: str, repair_condition: str | None, *, preview: bool = False
) -> list[tuple[str, str | None]]:
    """機翻頁四張卡的 (標題, 說明)；預覽與實際機翻同一組欄位，預覽只在標題標示。"""
    if mode == "repair":
        quality = repair_condition == "quality_mismatch"
        return [
            ("本次來源譯文" if quality else "本次候選", None),
            ("成功更新來源譯文" if quality else "成功更新 AI 譯文", None),
            (
                "AI 草稿待人工確認" if quality else "重翻後仍相同",
                _SAME_AS_OLD_TOOLTIP if repair_condition == "same_source_ai" else None,
            ),
            ("範圍內仍不一致" if quality else "範圍內仍符合修復條件", None),
        ]
    if preview:
        return [
            ("沿用其他版本（預覽）", None),
            ("將送 AI 翻譯（預覽）", None),
            ("特殊字元不一致（預覽）", None),
            ("此範圍仍未翻譯（預覽）", None),
        ]
    return [
        ("沿用其他版本", None),
        ("已寫入（AI 機翻）", None),
        ("特殊字元不一致", None),
        ("此範圍仍未翻譯", None),
    ]


def translation_scope_text(
    missing: int,
    reusable: int,
    limit: int,
    will: int,
    batches_text: str,
    *,
    warn_large: bool = False,
) -> str:
    """機翻範圍說明：未翻譯、可沿用、上限、本次送 AI 的筆數與批數（粗估或預覽規劃）。"""
    text = f"此範圍有 {format_count(missing)} 筆未翻譯"
    if reusable:
        text += (
            f"，其中約 {format_count(reusable)} 筆其他版本已有相同譯文、"
            "開始時會直接沿用（原文不同的不算）"
        )
    text += f"；上限 {format_count(limit)} 筆" if limit else "；上限為 0（不限）"
    text += (
        f"，本次送 AI 翻譯{'最多 ' if limit else '全部 '}{format_count(will)} 筆"
        f"（{batches_text}）"
    )
    if warn_large:
        text += "；筆數很多，會消耗大量 API 額度，建議先按「先預覽」確認"
    return text


def scope_batches_text(
    planned_batches: int | None, preview_batches: int | None, rough_batches: int
) -> str:
    """範圍說明的批數：背景規劃的批數、同範圍預覽規劃的批數，或依每批筆數的粗估。"""
    if planned_batches is not None:
        return f"預估約 {planned_batches:,} 批"
    if preview_batches is not None:
        return f"預覽規劃 {preview_batches:,} 批"
    return f"預估約 {rough_batches:,} 批，粗估；按「先預覽」取得精確批數"
