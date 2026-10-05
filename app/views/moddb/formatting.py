"""app/views/moddb/formatting.py：Mod 資料庫頁共用的顯示文字與色調（純函式，不依賴 Flet）。"""

from __future__ import annotations

from app.services_impl.moddb_service import (
    SOURCE_NAMES,
    SRC_AI,
    SRC_JAR_CN,
    SRC_MANUAL,
)

STATE_LABELS = {
    "all": "全部",
    "none": "未翻譯",
    "diff": "版本不同",
    "manual": "人工",
    "ok": "有譯文",
}
# 條目狀態 → 強調色組（design.tone 的名稱）
STATE_TONES = {"none": "neutral", "diff": "gold", "manual": "ench", "ok": "dia"}
KIND_LABELS = {"lang": "語言檔", "patchouli": "Patchouli 手冊"}


def source_label(source: int | None) -> str:
    return SOURCE_NAMES.get(source, "—") if source is not None else "—"


def source_tone(source: int | None) -> str:
    """譯文來源的色調：人工 = 紫、簡中轉繁 = 金、AI = 中性、其餘 = 藍。"""
    if source == SRC_MANUAL:
        return "ench"
    if source == SRC_JAR_CN:
        return "gold"
    if source == SRC_AI or source is None:
        return "neutral"
    return "dia"


def shorten(text: str, limit: int = 60) -> str:
    text = " ".join((text or "").split())
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
