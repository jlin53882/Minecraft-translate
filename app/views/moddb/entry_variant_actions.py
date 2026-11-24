"""條目詳情裡「其他原文」的動作：把目前譯文沿用為該份原文的譯文。"""

from __future__ import annotations

import sqlite3

from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb import entry_writes
from translation_tool.utils.log_unit import log_exception, log_info, log_warning


def adopt_current_translation(panel, variant) -> None:
    """背景寫入：把目前選取條目的生效譯文沿用為 ``variant`` 這份其他原文的人工譯文。

    譯文與這份原文的特殊字元不一致、畫面上的譯文已過期時不寫入，並顯示原因；
    資料庫錯誤寫進後台 log，其他例外（程式錯誤）往上丟。
    """
    entry, db = panel.selected, panel.db()
    if not entry or not db or not entry.zh_tw:
        show_snack(panel._page, "目前條目沒有可沿用的譯文。", C.GOLD)
        return
    where = f"{entry.mc_version} {entry.mod_id} {entry.key}"

    def work() -> tuple[bool, str]:
        return db.adopt_current_translation_for_variant(
            entry.id, variant.en_us, entry.zh_tw
        )

    def apply(result, error) -> None:
        if error is not None:
            if not isinstance(error, sqlite3.Error):
                raise error
            log_exception(f"Mod 資料庫沿用目前譯文給其他原文失敗：{where}")
            show_snack(
                panel._page,
                f"沿用失敗（資料庫錯誤：{error}）；詳情見後台 log",
                C.RED,
            )
            return
        done, reason = result
        if not done:
            log_warning(f"Mod 資料庫沿用目前譯文給其他原文被拒絕：{where}（{reason}）")
            show_snack(panel._page, reason, C.GOLD)
            return
        log_info(f"Mod 資料庫沿用目前譯文給其他原文：{where}")
        entry_writes.after_data_change(
            panel, "已沿用目前譯文給這份原文（只供流水線使用，未審核）"
        )

    launched = entry_writes.run_write(
        panel._page,
        work,
        apply,
        name="沿用目前譯文給其他原文",
        owner="moddb",
        label="沿用其他原文譯文",
    )
    if not launched:
        show_snack(panel._page, "沿用無法啟動，或應用程式正在關閉。", C.GOLD)
