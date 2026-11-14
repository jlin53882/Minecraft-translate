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
    preview_new_source_names,
    strip_quotes,
)
from app.services_impl.moddb_source_service import (
    prune_unused_custom_sources_report,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.sync_text_field import _sync_change, _synced
from translation_tool.utils.log_unit import log_debug, log_error


class DbLocationBanner(ft.Container):
    """資料庫資料夾路徑＋狀態（已建立／尚未建立／無法使用）。"""

    def __init__(self) -> None:
        self.folder_text = ft.Text(
            "", size=13, selectable=True, color=C.TEXT, font_family=design.FONT_MONO
        )
        self.file_text = ft.Text("", size=12, color=C.MUTED)
        self.status_box = ft.Container()
        self.note = ft.Text("", size=11.5, color=C.DIM)
        self.on_pruned: Callable[[set[str]], None] | None = None
        self._pruning = False
        self.prune_btn = kit.button(
            "清除未使用的自訂來源",
            "ghost",
            size="sm",
            icon=ft.Icons.CLEANING_SERVICES_OUTLINED,
            on_click=self._on_prune,
        )
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
                    self.prune_btn,
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
        unsaved = normalize_db_path(path_text) != normalize_db_path(
            current_settings().path
        )
        self.prune_btn.disabled = unsaved or self._pruning
        self.prune_btn.tooltip = (
            "資料庫路徑尚未儲存；請先儲存設定，再清除未使用的自訂來源"
            if unsaved
            else None
        )
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

    def _page_or_none(self):
        try:
            return self.page
        except RuntimeError:  # 尚未掛上頁面（單元測試或建構期間）
            return None

    def _on_prune(self, _e=None) -> None:
        """移除資料庫登錄裡沒有任何資料引用的自訂來源（有資料的一律保留）。

        掃描整個資料庫的引用可能耗時，在頁面擁有的背景工作中執行；
        執行期間停用按鈕並顯示忙碌文字，完成（或失敗）後才恢復並顯示結果。
        """
        if self.prune_btn.disabled:
            return
        db_path = current_settings().path
        self._set_pruning(True)
        page = self._page_or_none()
        if page is None:
            self._finish_prune(*self._prune_worker(db_path))
            return
        launched = launch_page_operation(
            page,
            lambda: self._prune_in_background(page, db_path),
            name="清除未使用的自訂來源",
            owner="config-db-location",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
        )
        if not launched:
            self._finish_prune(None, RuntimeError("應用程式正在關閉，未啟動清除"))

    def _set_pruning(self, busy: bool) -> None:
        """切換清除中的忙碌狀態（按鈕停用＋說明文字）。"""
        self._pruning = busy
        if busy:
            self.prune_btn.disabled = True
            self.note.value = "正在檢查並清除未使用的自訂來源，請稍候…"
            self._safe_update()

    def _prune_worker(self, db_path: str):
        """實際清除；回傳 (報告, 例外)，不碰任何控制項。"""
        try:
            return prune_unused_custom_sources_report(db_path), None
        except Exception as exc:  # noqa: BLE001 - 失敗也要恢復按鈕並顯示原因
            log_error(f"清除未使用的自訂來源失敗：{exc}", exc_info=True)
            return None, exc

    def _prune_in_background(self, page, db_path: str) -> None:
        """背景執行緒入口：做完後把結果排回 UI 執行緒。"""
        report, error = self._prune_worker(db_path)

        async def _apply() -> None:
            self._finish_prune(report, error)

        try:
            page.run_task(_apply)
        except RuntimeError as exc:  # 頁面已銷毀，沒有東西可更新
            log_debug(f"清除結果略過（頁面已關閉）：{exc}")

    def _finish_prune(self, report, error) -> None:
        """在 UI 執行緒套用清除結果並恢復按鈕。"""
        self._pruning = False
        try:
            self.refresh()  # 依「路徑是否已儲存」還原按鈕狀態
        except Exception as exc:  # noqa: BLE001 - 設定讀取失敗時仍要恢復按鈕
            log_debug(f"DbLocationBanner 重新整理略過：{exc}")
            self.prune_btn.disabled = False
        if error is not None:
            self.note.value = f"清除未使用的自訂來源失敗：{error}"
            self._safe_update()
            return
        removed, kept, gone_tokens = report
        if removed:
            self.note.value = f"已移除未使用的自訂來源：{'、'.join(removed)}。"
            if self.on_pruned is not None:
                self.on_pruned(gone_tokens)
        elif kept:
            self.note.value = f"沒有可清除的項目；{'、'.join(kept)} 仍有資料在使用。"
        else:
            self.note.value = "沒有可清除的自訂來源。"
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.update()
        except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響設定頁
            log_debug(f"DbLocationBanner 更新略過：{exc}")

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


def attach_priority_hooks(field: ft.TextField) -> Callable[[], None]:
    """來源優先順序輸入框：即時列出「將新增的自訂來源」，打錯字也看得出來。

    名稱對得上內建或已登錄的來源就沿用；其他名稱儲存後會新增為自訂來源。
    回傳「重新檢查」函式（載入設定後呼叫）。
    """
    base_helper = field.helper if isinstance(field.helper, str) else ""

    def check() -> None:
        _known, new = preview_new_source_names(field.value or "")
        note = (
            f"將新增自訂來源：{'、'.join(new)}（儲存後登錄；若是打錯字，請先修正）"
            if new
            else "所有名稱都是已存在的來源。"
        )
        field.helper = f"{note}\n{base_helper}" if base_helper else note
        field.helper_style = ft.TextStyle(size=11.5, color=C.GOLD if new else C.MUTED)

    def handler(e=None):
        check()
        try:
            field.update()
        except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響輸入
            log_debug(f"來源優先順序欄位更新略過：{exc}")

    field.on_change = _synced(handler, _sync_change)
    return check


def attach_prune_cleanup(
    banner: DbLocationBanner, field: ft.TextField, recheck: Callable[[], None]
) -> None:
    """清除自訂來源後，把來源優先順序輸入框裡指向它們的行一併拿掉（儲存後生效）。"""

    def drop(gone: set[str]) -> None:
        lines = (field.value or "").splitlines()
        field.value = "\n".join(
            line for line in lines if not _line_points_to(line, gone)
        )
        recheck()
        try:
            field.update()
        except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響設定頁
            log_debug(f"來源優先順序欄位更新略過：{exc}")

    banner.on_pruned = drop


def _line_points_to(line: str, gone: set[str]) -> bool:
    text = line.strip()
    name, sep, _code = text.partition("（自訂 #")
    return text in gone or (bool(sep) and name in gone)
