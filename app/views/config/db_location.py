"""app/views/config/db_location.py：設定頁最上方的「資料庫位置」資訊列。

顯示 Mod 資料庫實際所在的資料夾與檔案狀態，更新程式或搬移資料夾後
一眼就能確認資料庫有沒有找得到。設定值空白時使用資料目錄內的預設檔名；
第一次建立資料庫時，實際路徑會自動寫進 config.json 並顯示在這裡。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import current_settings, database_problem
from app.ui import design, kit
from app.ui.design import C
from translation_tool.utils.log_unit import log_debug


class DbLocationBanner(ft.Container):
    """資料庫資料夾路徑＋狀態（已建立／尚未建立／無法使用）。"""

    def __init__(self) -> None:
        self.folder_text = ft.Text(
            "", size=13, selectable=True, color=C.TEXT, font_family=design.FONT_MONO
        )
        self.file_text = ft.Text("", size=12, color=C.MUTED)
        self.status_box = ft.Container()
        self.note = ft.Text("", size=11.5, color=C.DIM)
        super().__init__(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.STORAGE_OUTLINED, size=18, color=C.DIA),
                            kit.section_label("Mod 資料庫資料夾"),
                            self.status_box,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.folder_text,
                    self.file_text,
                    self.note,
                ],
                spacing=4,
            ),
        )
        self.refresh()

    def refresh(self) -> None:
        """依目前設定重算路徑與狀態（設定儲存後、切回本頁時呼叫）。"""
        settings = current_settings()
        path = settings.resolved_path()
        problem = database_problem()
        if problem:
            tone, label = "red", "無法使用"
        elif path.is_file():
            tone, label = "em", "已建立"
        else:
            tone, label = "gold", "尚未建立"
        self.folder_text.value = str(path.parent)
        self.file_text.value = f"檔案：{path.name}"
        self.status_box.content = kit.chip(label, tone)
        if problem:
            self.note.value = problem
        elif not settings.path.strip():
            self.note.value = (
                "設定的「資料庫檔案」是空白，使用資料目錄內的預設檔名；"
                "第一次建立資料庫（到「Mod 資料庫」掃描匯入）時，實際路徑會自動寫入設定。"
            )
        elif not path.is_file():
            self.note.value = (
                "找不到這個檔案。若資料夾搬移過，請在下方「資料庫檔案」改成新的路徑；"
                "清空欄位並儲存則改用資料目錄內的預設位置。"
            )
        else:
            self.note.value = (
                "要搬移資料庫時，請把檔案放到新位置後，在下方「資料庫檔案」改成新路徑。"
            )

    def safe_refresh(self) -> None:
        try:
            self.refresh()
            self.update()
        except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響設定頁
            log_debug(f"DbLocationBanner 更新略過：{exc}")
