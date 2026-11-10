"""原文欄位在各畫面的標題：依條目實際的來源語系顯示，不一律標成英文。

KubeJS／FTB Quests 的語系檔沒有 en_us 時，掃描會以其他語系（例如 pt_br）為原文，
資料庫的原文欄存的就是那個語系的文字。
"""

from __future__ import annotations

ENGLISH_SOURCE_LABEL = "英文原文・en_us"
UNKNOWN_LOCALE = "語系未知"
# 語系由語系檔決定的種類：還原不出語系時是真的未知，不能當成英文。
# 其餘種類（jar 內容、KubeJS 腳本字串、FTB 任務結構）沒有記語系時，原文是英文。
_LOCALE_FILE_KINDS = frozenset({"kubejs_lang", "ftb_quest_lang"})


def _displayed_locale(kind: str | None, locale: object) -> str:
    """``locale`` 是服務層給的來源語系（已正規化，例如 ``pt_br``）；無法確定時為空。"""
    if isinstance(locale, str) and locale:
        return locale
    return UNKNOWN_LOCALE if kind in _LOCALE_FILE_KINDS else "en_us"


def source_text_parts(kind: str | None, locale: object) -> tuple[str, str]:
    """``(名稱, 語系)``：``("英文原文", "en_us")`` 或 ``("原文", "pt_br")``。"""
    shown = _displayed_locale(kind, locale)
    return ("英文原文" if shown == "en_us" else "原文", shown)


def source_text_label(kind: str | None, locale: object) -> str:
    """含語系的標題，例如「英文原文・en_us」「原文・pt_br」「原文・語系未知」。"""
    return "・".join(source_text_parts(kind, locale))


def source_text_name(kind: str | None, locale: object) -> str:
    """不另外列語系的短標題：英文時是「英文原文」，否則「原文（pt_br）」。"""
    name, shown = source_text_parts(kind, locale)
    return name if shown == "en_us" else f"{name}（{shown}）"
