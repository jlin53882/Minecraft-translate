"""app/ui/mc_text.py：Minecraft 格式碼（§）轉成 Flet 的文字片段，給資源包描述預覽用。

純函式、不依賴 Page。支援顏色碼 0-9 a-f、粗體 l、斜體 o、底線 n、刪除線 m、重設 r；
隨機字 k 不支援（直接忽略）。
"""

from __future__ import annotations

import flet as ft

# 顏色碼 → 顯示色（取自 Minecraft 官方色票）
MC_COLORS = {
    "0": "#000000",
    "1": "#0000AA",
    "2": "#00AA00",
    "3": "#00AAAA",
    "4": "#AA0000",
    "5": "#AA00AA",
    "6": "#FFAA00",
    "7": "#AAAAAA",
    "8": "#555555",
    "9": "#5555FF",
    "a": "#55FF55",
    "b": "#55FFFF",
    "c": "#FF5555",
    "d": "#FF55FF",
    "e": "#FFFF55",
    "f": "#FFFFFF",
}
SECTION_SIGN = "§"


def parse_mc_text(text: str) -> list[dict]:
    """把含 § 碼的字串切成 ``[{"text", "color", "bold", "italic", "underline", "strike"}]``。

    顏色碼會重設格式（跟遊戲行為一致）；``r`` 重設全部。未知的碼原樣保留（不吃掉字元）。
    """
    segments: list[dict] = []
    state = {
        "color": None,
        "bold": False,
        "italic": False,
        "underline": False,
        "strike": False,
    }
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            segments.append({"text": "".join(buffer), **state})
            buffer.clear()

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == SECTION_SIGN and i + 1 < len(text):
            code = text[i + 1].lower()
            if code in MC_COLORS:
                flush()
                state = {
                    "color": MC_COLORS[code],
                    "bold": False,
                    "italic": False,
                    "underline": False,
                    "strike": False,
                }
                i += 2
                continue
            if code in "lonmrk":
                flush()
                if code == "r":
                    state = {
                        "color": None,
                        "bold": False,
                        "italic": False,
                        "underline": False,
                        "strike": False,
                    }
                elif code == "l":
                    state = {**state, "bold": True}
                elif code == "o":
                    state = {**state, "italic": True}
                elif code == "n":
                    state = {**state, "underline": True}
                elif code == "m":
                    state = {**state, "strike": True}
                i += 2
                continue
        buffer.append(ch)
        i += 1
    flush()
    return segments


def mc_text_spans(
    text: str, default_color: str | None = None, size: float = 13
) -> list[ft.TextSpan]:
    """``parse_mc_text`` 的結果轉成 ``ft.TextSpan`` 列表。"""
    spans = []
    for seg in parse_mc_text(text):
        spans.append(
            ft.TextSpan(
                seg["text"],
                style=ft.TextStyle(
                    size=size,
                    color=seg["color"] or default_color,
                    weight=ft.FontWeight.BOLD if seg["bold"] else None,
                    italic=seg["italic"],
                    decoration=(
                        ft.TextDecoration.UNDERLINE
                        if seg["underline"]
                        else ft.TextDecoration.LINE_THROUGH
                        if seg["strike"]
                        else None
                    ),
                ),
            )
        )
    return spans
