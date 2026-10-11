"""app/views/moddb/formatting.py：Mod 資料庫頁共用的顯示文字與色調（純函式，不依賴 Flet）。"""

from __future__ import annotations

from app.services_impl.moddb_service import (
    format_tokens,
    token_issues,
    whitespace_note,
)

__all__ = [
    "format_tokens",
    "token_issues",
    "whitespace_note",
]

STATE_LABELS = {
    "all": "全部",
    "none": "未翻譯",
    "diff": "版本不同",
    "changed": "原文已變動",
    "same": "翻譯與原文相同",
    "manual": "人工",
    "ok": "其他譯文",
}
# 條目狀態 → 強調色組（design.tone 的名稱）
STATE_TONES = {
    "none": "neutral",
    "diff": "gold",
    "changed": "gold",
    "manual": "ench",
    "ok": "dia",
}
KIND_LABELS = {"lang": "語言檔", "patchouli": "Patchouli 手冊"}


def kind_label(kind: str) -> str:
    """條目類型的顯示名稱；日後資料庫新增的類型沒登錄名稱時直接顯示代碼。"""
    return KIND_LABELS.get(kind, kind)


def shorten(text: str, limit: int = 60) -> str:
    text = visible_breaks(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_count(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def percent(part: int, total: int) -> int:
    return round(100 * part / total) if total else 0


def impact_text(version: str, new_text: str, impacts) -> str:
    """儲存前的影響說明；``impacts`` 為 ``Impact`` 清單（含自己）。"""
    others = [i for i in impacts if not i.is_self]
    if not others:
        return f"這個內容只出現在 {version}，儲存後不會影響其他版本。"
    differ = [i for i in others if i.old_zh_tw and i.old_zh_tw != new_text]
    names = "、".join(i.mc_version for i in others)
    text = f"儲存後，{len(others)} 個版本（{names}）的相同原文會一併改為「{shorten(new_text, 30)}」。"
    if differ:
        detail = "、".join(
            f"{i.mc_version}「{shorten(i.old_zh_tw, 16)}」" for i in differ
        )
        text += f" 其中 {len(differ)} 個目前的譯文不同：{detail}。"
    return text + " 原譯文保留在記錄中，可還原。"


# --- 特殊字元：換行 ---------------------------------------------------------------
def visible_breaks(text: str) -> str:
    """清單預覽用：真正的換行顯示成 ¶（折成一行），其餘空白折疊。"""
    flat = (text or "").replace("\r\n", "\n").replace("\n", " ¶ ")
    return " ".join(flat.split())
