"""語系合併輸出的格式檢查：譯文與原文特殊字元不一致的條目只回報、不改動輸出。

獨立成模組是因為 ``lang_merge_dict`` 必須維持純函式、不能依賴 ``translation_db``
（``translation_db.repository`` 會 import 它，放在那裡會形成循環）。
"""

from __future__ import annotations

from typing import Any

from translation_tool.translation_db.quality import format_mismatches
from translation_tool.utils.log_unit import log_warning


def warn_format_mismatches(
    final_tw: dict[str, Any] | None, en_data: dict[str, Any] | None, label: str
) -> int:
    """合併結果裡，譯文與原文特殊字元不一致的條目：記一則警告（筆數與前幾筆），回傳筆數。

    只回報、不改動輸出：模組作者自帶的 zh_tw、zh_cn 轉繁與既有譯文都照常保留，
    讓使用者知道哪些條目可能壞掉（色碼、``%s``、``{0}``、具名佔位符被弄丟或改動）。
    """
    found = format_mismatches(en_data or {}, final_tw or {})
    if found:
        samples = "；".join(
            f"{key}（{'、'.join(issues[:2])}）"
            for key, issues in list(found.items())[:3]
        )
        log_warning(
            f"⚠️ {label}輸出的 {len(found)} 筆譯文特殊字元與原文不一致（已保留，請檢查）：{samples}"
        )
    return len(found)
