"""app/views/config/db_location.py：設定頁最上方的「資料庫位置」資訊列。

顯示 Mod 資料庫實際所在的資料夾與檔案狀態，更新程式或搬移資料夾後
一眼就能確認資料庫有沒有找得到。設定值空白時使用資料目錄內的預設檔名；
第一次建立資料庫時，實際路徑會自動寫進 config.json 並顯示在這裡。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_service import (
    current_settings,
    describe_db_path,
    normalize_db_path,
    strip_quotes,
)
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

    def refresh(self, path_text: object | None = None) -> None:
        """依設定（或輸入框裡還沒儲存的值）重算路徑與狀態。"""
        if path_text is None:
            path_text = current_settings().path
        level, message, path = describe_db_path(path_text)
        blank = not normalize_db_path(path_text)
        tone, label = {
            "ok": ("em", "已建立"),
            "info": ("gold", "尚未建立"),
            "warn": (
                "red" if path.is_file() else "gold",
                "無法使用" if path.is_file() else "找不到",
            ),
        }[level]
        self.folder_text.value = str(path.parent)
        self.file_text.value = f"檔案：{path.name}"
        self.status_box.content = kit.chip(label, tone)
        if level == "warn" and path.is_file():
            self.note.value = message
        elif blank:
            self.note.value = (
                "設定的「資料庫檔案」是空白，使用資料目錄內的預設檔名；"
                "第一次建立資料庫（到「Mod 資料庫」掃描匯入）時，實際路徑會自動寫入設定。"
            )
        elif level == "warn":
            self.note.value = (
                f"{message}。若資料夾搬移過，請在下方「資料庫檔案」改成新的路徑；"
                "清空欄位並儲存則改用資料目錄內的預設位置。"
            )
        else:
            self.note.value = (
                "要搬移資料庫時，請把檔案放到新位置後，在下方「資料庫檔案」改成新路徑。"
            )

    def safe_refresh(self, path_text: object | None = None) -> None:
        try:
            self.refresh(path_text)
            self.update()
        except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響設定頁
            log_debug(f"DbLocationBanner 更新略過：{exc}")


_LEVEL_COLORS = {"ok": C.EM, "info": C.MUTED, "warn": C.RED}


def attach_path_hooks(
    field: ft.TextField, banner: DbLocationBanner
) -> Callable[[], None]:
    """資料庫路徑輸入框：貼上帶引號的路徑自動去引號，並即時檢查檔案是否存在。

    輸入中只去引號（不動空白，避免打不出含空白的路徑）；離開欄位時再整理前後空白。
    狀態同時顯示在欄位下方與頁面最上方的資訊列。回傳「重新檢查」函式（載入設定後呼叫）。
    """
    base_helper = field.helper if isinstance(field.helper, str) else ""

    def check() -> None:
        level, message, _path = describe_db_path(field.value)
        field.helper = f"{message}\n{base_helper}" if base_helper else message
        field.helper_style = ft.TextStyle(size=11.5, color=_LEVEL_COLORS[level])
        banner.safe_refresh(field.value)

    def chain(original, clean):
        def handler(e=None):
            if original is not None:
                original(e)
            value = field.value or ""
            cleaned = clean(value)
            if cleaned != value:
                field.value = cleaned
            check()
            try:
                field.update()
            except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響輸入
                log_debug(f"資料庫路徑欄位更新略過：{exc}")

        handler._sync_wrapped = True
        return handler

    field.on_change = chain(field.on_change, strip_quotes)
    field.on_blur = chain(field.on_blur, normalize_db_path)
    return check
