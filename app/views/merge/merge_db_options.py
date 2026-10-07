"""語系合併頁的「Mod 資料庫補譯」選項卡（開關＋目標版本）。

預設值取自設定（``translation_db.merge_enabled`` / ``translation_db.version``）；
使用者在頁面上動過之後，這一頁的選擇優先於設定，不會被設定覆寫。沒動過時，
每次回到這一頁都會重新讀設定，所以在設定頁改的值會跟著變。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import load_db_settings, summarize_database
from app.ui import kit
from app.ui.design import C
from translation_tool.utils.log_unit import log_debug, log_warning


class MergeDbOptions:
    """開關與版本欄位；``use_db`` / ``version`` 交給合併服務（None＝用設定檔的值）。"""

    def __init__(self, page_update) -> None:
        self._page_update = page_update
        self._touched = False
        settings = load_db_settings()
        row = kit.SwitchRow(
            "使用 Mod 資料庫補譯",
            "純英文條目先向資料庫找相同（模組、鍵值、原文）的譯文；只補沒有譯文的條目",
            settings.merge_enabled,
            divider=False,
        )
        self.switch = row.switch
        self.switch.on_change = self._on_changed
        self.version_field = kit.text_field(
            "目標版本",
            hint="例如 1.21.1（留空則使用設定中的預設版本）",
            value=settings.version,
            on_change=self._on_changed,
        )
        self.info = ft.Text("", size=11.5, color=C.DIM)
        self.refresh_info()
        self.card = kit.section_card(
            "Mod 資料庫補譯",
            ft.Column(
                [row, self.version_field, self.info],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            icon=ft.Icons.DATASET_OUTLINED,
            tone="dia",
        )

    @property
    def use_db(self) -> bool:
        return bool(self.switch.value)

    @property
    def version(self) -> str | None:
        """頁面填的版本；空白時回傳 None（使用設定中的預設版本）。"""
        return (self.version_field.value or "").strip() or None

    def sync_from_config(self) -> None:
        """回到這一頁時：使用者沒動過就跟著設定；動過就維持頁面上的選擇。"""
        if self._touched:
            return
        settings = load_db_settings()
        self.switch.value = settings.merge_enabled
        self.version_field.value = settings.version
        self.refresh_info()

    def _on_changed(self, _e=None) -> None:
        self._touched = True
        self.refresh_info()
        try:
            self._page_update()
        except Exception as exc:  # noqa: BLE001 - 頁面尚未掛載時只是不即時更新提示
            log_debug(f"語系合併資料庫提示更新略過：{exc}")

    def refresh_info(self) -> None:
        """顯示資料庫狀態；沒建立或沒有版本時說明這次會略過補譯。"""
        try:
            info = summarize_database()
        except Exception as exc:  # noqa: BLE001 - 只影響提示文字，不應讓頁面載入失敗
            log_warning(f"讀取 Mod 資料庫摘要失敗：{exc!r}")
            info = {"problem": f"讀取摘要失敗（{exc}），詳情請看後台 log"}
        if info is None:
            text = "尚未建立資料庫：這次合併會略過資料庫補譯（到「Mod 資料庫」頁掃描後即可使用）。"
        elif info.get("problem"):
            text = f"⚠ 資料庫無法使用：{info['problem']}（這次會略過補譯）"
        else:
            text = (
                f"資料庫 {info['entries']:,} 條目（已翻譯 {info['progress']}%）・"
                f"版本：{'、'.join(info['versions'][:4]) or '—'}"
            )
        if self.use_db and not (self.version or load_db_settings().version):
            text += (
                "\n⚠ 尚未指定目標版本：請填寫上方「目標版本」，否則這次不會使用資料庫。"
            )
        self.info.value = text
