"""app/views/format_consistency_checker.py 模組。

用途：提供「格式一致性檢查」的 UI 元件：逐檔比對 en_us 與 zh_tw，列出譯文與原文的
特殊字元（色碼、``%s``、``{0}``、具名佔位符等）不一致的條目。
維護注意：版面與選資料夾沿用 ``UntranslatedChecker``，這裡只覆寫文字與服務。
"""

import flet as ft

from app.services import run_format_consistency_check_service
from app.views.untranslated_checker import UntranslatedChecker


class FormatConsistencyChecker(UntranslatedChecker):
    """FormatConsistencyChecker 元件。"""

    DESCRIPTION = (
        "逐檔比對 en_us 與 zh_tw，列出譯文與原文的特殊字元（色碼、%s、{0}、{name} 等）"
        "不一致的條目；找不到對應繁中檔或沒有譯文的條目不在此檢查範圍。"
    )
    OUT_LABEL = "格式不一致報告 輸出資料夾"
    START_LABEL = "開始檢查"
    START_ICON = ft.Icons.RULE
    SERVICE = staticmethod(run_format_consistency_check_service)
