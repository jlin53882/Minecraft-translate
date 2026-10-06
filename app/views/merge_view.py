"""app/views/merge_view.py 模組。
用途：提供 ZIP 合併頁面 UI 與執行流程。
維護注意：本檔案的 docstring 與中文註解用於維護說明，不代表行為變更。
"""

import asyncio
import threading
import traceback
from pathlib import Path
from typing import Any

import flet as ft

from app import config_store
from app.services_impl.pipelines.extract_service import open_output_folder
from app.services_impl.pipelines.merge_service import (
    run_merge_folder_batch_service,
    run_merge_zip_batch_service,
)
from app.tasks.task_session import TaskSession, add_log_unmirrored
from app.ui.design import C
from app.ui.snack import show_snack
from app.ui.status_chip import set_chip_status
from app.views._log import LogView
from app.views.config.config_actions import load_config_into_view
from app.views.merge.merge_widgets import MergeWidgetsMixin
from translation_tool.utils.cancellation import TaskCancelled
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_error, log_info, log_warning


class MergeView(MergeWidgetsMixin, ft.Column):
    """ZIP 合併頁面（視覺風格對齊 Translation / Extractor）。"""

    page: ft.Page
    file_picker: ft.FilePicker
    session: TaskSession
    _ui_stop: threading.Event
    selected_zips: list[str]
    _merge_stats: dict[str, Any]
    log_view: LogView
    only_lang_checkbox: ft.Checkbox
    process_zh_cn_switch: ft.Switch
    _view_registry: list[dict] | None = None
    patchouli_skip_zh_cn_switch: ft.Switch
    patchouli_threshold_field: ft.TextField
    zh_en_letter_threshold_field: ft.TextField
    extracted_merge_switch: ft.Switch
    output_dir_field: ft.TextField
    _zh_cn_disabled_note: ft.Text | None
    zip_list_view: ft.ListView
    status_chip: ft.Chip
    progress_bar: ft.ProgressBar
    pick_zip_button: ft.Button
    start_button: ft.Button
    controls: list[ft.Control]

    def _skip_disabled_note(self) -> ft.Text | None:
        """回傳 zh_cn 關聯設定停用時的提示文字元件。"""
        return self._zh_cn_disabled_note

    def _on_zh_cn_switch_changed(self, e: ft.ControlEvent) -> None:
        """主開關互鎖：process_zh_cn_switch 為其他設定的「主開關」。

        - 關閉時：連動停用 patchouli_skip_zh_cn_switch，並將它的值強制還原為 False
        - 開啟時：解鎖讓它可自行調整
        - 原因：zh_cn 被全域略過時，Patchouli 的 en_us skip 設定無意義
        """
        enabled = bool(e.control.value)
        self.patchouli_skip_zh_cn_switch.disabled = not enabled
        if self._zh_cn_disabled_note:
            self._zh_cn_disabled_note.visible = not enabled
        if not enabled:
            self.patchouli_skip_zh_cn_switch.value = False
        self.update()

    def _safe_int(self, s: str) -> int | None:
        """安全轉 int，失敗回 None。"""
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    def _safe_float(self, s: str) -> float | None:
        """安全轉 float，失敗回 None。"""
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    def set_view_registry(self, registry: list[dict]) -> None:
        """讓 main.py 傳入 view registry，藉此廣播更新到 ConfigView。"""
        self._view_registry = registry

    def _broadcast_config_change_to_config_view(self) -> None:
        """當 MergeView 改動 config 時，通知已存在的 ConfigView 重新讀取。"""
        if self._view_registry is None:
            return
        from app.view_registry import built_view

        for item in self._view_registry:
            # 只通知已建立的頁面（尚未建立的頁面建立時會讀取最新設定）
            view = built_view(item)
            if view is None:
                continue
            wrapped = view.content if hasattr(view, "content") else view
            inner = wrapped.content if hasattr(wrapped, "content") else wrapped
            if hasattr(inner, "controls_map") and hasattr(inner, "load_config"):
                try:
                    cfg = load_config()
                    load_config_into_view(inner, cfg)
                except Exception as exc:  # noqa: BLE001 - 通知失敗不影響合併頁，但要留下紀錄
                    log_warning(
                        f"[MergeView] 同步設定到已開啟的頁面失敗：{exc!r}",
                        exc_info=True,
                    )

    def _on_merge_field_changed(self, key: str, value: Any) -> None:
        """寫入 lang_merger 單一欄位到 config.json，支援兩邊同步。"""
        try:
            # 經由 ConfigStore：只改這一個欄位、受寫入鎖保護、並通知外殼等訂閱者
            config_store.set_value(f"lang_merger.{key}", value)
            self._broadcast_config_change_to_config_view()
        except Exception as exc:  # noqa: BLE001 - 欄位寫入失敗不可中斷 UI，但設定沒存成功必須留下紀錄
            log_warning(
                f"[MergeView] 寫入設定 lang_merger.{key}={value!r} 失敗：{exc!r}",
                exc_info=True,
            )

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker) -> None:
        """初始化 MergeView。"""
        super().__init__(expand=True, spacing=16, scroll=ft.ScrollMode.AUTO)
        self._init_merge_options(file_picker, page)
        self._init_merge_io_controls()
        self._init_merge_input_panels()
        general_options_section, zh_cn_section = (
            self._build_general_and_zh_cn_sections()
        )
        patchouli_section = self._build_patchouli_section()
        extracted_section, min_count, organized_name, pending_name = (
            self._build_extracted_section()
        )
        input_card = self._build_merge_info_and_input_card(
            min_count, organized_name, pending_name
        )
        self._build_merge_output_cards_and_layout(
            extracted_section,
            general_options_section,
            input_card,
            patchouli_section,
            zh_cn_section,
        )

    async def pick_zips(self, e: ft.ControlEvent) -> None:
        """開啟 ZIP 檔案選擇對話框。2026-08-04: 改用 await (桌面版)。"""
        result = await self.file_picker.pick_files(
            dialog_title="選擇 ZIP 檔案",
            allow_multiple=True,
            allowed_extensions=["zip"],
        )
        if not result:
            return
        # 桌面版回傳 list[FilePickerFile] (每個有 .path)
        for f in result if isinstance(result, list) else [result]:
            path = f.path if hasattr(f, "path") else str(f)
            if path and path not in self.selected_zips:
                self.selected_zips.append(path)
        self._refresh_zip_list()
        self.page.update()

    def _refresh_zip_list(self) -> None:
        """重新整理 ZIP 檔案清單顯示。"""
        self.zip_list_view.controls.clear()
        for path in self.selected_zips:
            name = Path(path).name
            self.zip_list_view.controls.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(name, expand=True),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            tooltip="移除",
                            on_click=lambda e, p=path: self._remove_zip(p),
                        ),
                    ],
                )
            )

    def _remove_zip(self, path: str) -> None:
        """移除指定的 ZIP 檔案。"""
        if path in self.selected_zips:
            self.selected_zips.remove(path)
            self._refresh_zip_list()
            self.page.update()

    def pick_output_dir(self) -> None:
        """開啟輸出目錄選擇對話框。"""
        self._page.run_task(self._async_pick_output_dir)

    async def _async_pick_output_dir(self):
        """async 實作：選擇輸出資料夾並更新 output_dir_field。"""
        result = await self.file_picker.get_directory_path(
            dialog_title="選擇輸出資料夾"
        )
        if result:
            self.output_dir_field.value = result
            self.page.update()

    def pick_folder_input(self, e: ft.ControlEvent) -> None:
        """開啟資料夾選擇對話框。"""
        self._page.run_task(self._async_pick_folder_input)

    async def _async_pick_folder_input(self):
        """async 實作：選擇輸入資料夾並更新 folder_path_field。"""
        result = await self.file_picker.get_directory_path(
            dialog_title="選擇 Mod 來源資料夾"
        )
        if result:
            self.folder_path_field.value = result
            self.page.update()

    def _log_start_inputs(self, input_mode) -> None:
        """診斷：記錄按下按鈕當下「後端」實際收到的欄位值；畫面上看得到、後端卻是空的
        （Web 輸入事件沒同步）時，只看後台 log 就能分辨，不必猜。"""
        log_info(
            f"[合併] 開始按鈕：mode={input_mode!r}, "
            f"folder={self.folder_path_field.value!r}, "
            f"zips={len(self._zip_paths_for_run())}, output={self.output_dir_field.value!r}"
        )

    def _zip_paths_for_run(self) -> list[str]:
        """合併目前選取的 ZIP，並納入 Web 可手動輸入的單一 ZIP 路徑。"""
        paths = list(self.selected_zips)
        typed = (self.zip_path_field.value if self.zip_path_field else "") or ""
        typed = typed.strip()
        if typed and typed not in paths:
            paths.append(typed)
        return paths

    def cancel_merge(self, e: ft.ControlEvent | None = None) -> None:
        """要求目前合併在下一個檢查點停止。"""
        if self.session.snapshot().get("status") != "RUNNING":
            return
        self.session.request_cancel()
        self.cancel_button.disabled = True
        self._set_status("取消中…", "gold")
        self.session.add_log("[系統] 使用者要求取消合併", level="warning")
        self.page.update()

    def start_merge(self, e: ft.ControlEvent) -> None:
        """處理開始合併按鈕事件。"""
        input_mode = self.input_mode_group.value
        self._log_start_inputs(input_mode)
        if input_mode == "folder":
            if not (self.folder_path_field.value or "").strip():
                show_snack(self.page, "請先選擇來源資料夾")
                return
        else:
            zip_paths = self._zip_paths_for_run()
            if not zip_paths:
                show_snack(self.page, "請先選擇 ZIP 檔案")
                return
        if not (self.output_dir_field.value or "").strip():
            show_snack(self.page, "請先選擇輸出資料夾")
            return

        self.start_button.disabled = True
        self.cancel_button.visible = True
        self.cancel_button.disabled = False
        self.zip_list_view.disabled = True
        # PR refactor/unified-log-view: LogView widget API 不暴露 .controls
        # (LogView 是 ft.Container,內部有 _list_view 控制項)。
        # 必須走公開的 .clear() 才能清空現有 log 行,
        # 否則會 AttributeError: 'LogView' object has no attribute 'controls'。
        self.log_view.clear()
        self.progress_bar.value = 0.0
        self._set_status("執行中", C.DIA_BG)

        self.session.start()
        source_desc = (
            f"資料夾 {self.folder_path_field.value}"
            if input_mode == "folder"
            else f"{len(self.selected_zips)} 個 ZIP"
        )
        self.session.add_log(
            f"[系統] 開始合併任務｜來源：{source_desc}｜輸出：{self.output_dir_field.value}"
        )
        self._start_ui_poller()

        def _run_merge():
            try:
                _run_merge_service()
            except TaskCancelled:
                # 取消屬於正常終止，不讓背景執行緒把 traceback 噴到 Web 主控台。
                self.session.add_log("[取消] 合併已停止", level="warning")
            except Exception as ex:  # noqa: BLE001 - 背景執行緒邊界：失敗要寫進 session，否則輪詢永遠等不到結束
                # 完整堆疊寫後台；畫面顯示例外類型與訊息，並指向後台 log
                log_error(
                    f"[MergeView] 合併執行失敗（模式：{input_mode}，"
                    f"輸出：{self.output_dir_field.value}）：{ex!r}\n{traceback.format_exc()}"
                )
                add_log_unmirrored(
                    self.session,
                    f"[錯誤] 合併執行失敗：{type(ex).__name__}: {ex}（完整堆疊已寫入後台 log）",
                    "error",
                )
                self.session.set_error()
                self.session.finish()  # set_error() → finish()：TaskManager 才會離開 active

        def _run_merge_service():
            if input_mode == "folder":
                for _ in run_merge_folder_batch_service(
                    input_dir=self.folder_path_field.value,
                    output_dir=self.output_dir_field.value,
                    session=self.session,
                    only_process_lang=self.only_lang_checkbox.value,
                    process_zh_cn=self.process_zh_cn_switch.value,
                    patchouli_skip=self.patchouli_skip_zh_cn_switch.value,
                    patchouli_threshold=self._safe_float(
                        self.patchouli_threshold_field.value or ""
                    )
                    or 0.5,
                    zh_en_threshold=self._safe_int(
                        self.zh_en_letter_threshold_field.value or ""
                    )
                    or 2,
                ):
                    pass
            else:
                for _ in run_merge_zip_batch_service(
                    zip_paths=list(self._zip_paths_for_run()),
                    output_dir=self.output_dir_field.value,
                    session=self.session,
                    only_process_lang=self.only_lang_checkbox.value,
                    process_zh_cn=self.process_zh_cn_switch.value,
                    patchouli_skip=self.patchouli_skip_zh_cn_switch.value,
                    patchouli_threshold=self._safe_float(
                        self.patchouli_threshold_field.value or ""
                    )
                    or 0.5,
                    zh_en_threshold=self._safe_int(
                        self.zh_en_letter_threshold_field.value or ""
                    )
                    or 2,
                ):
                    pass

        threading.Thread(target=_run_merge, daemon=True).start()

    def _start_ui_poller(self) -> None:
        """啟動 UI 輪詢器（在 Flet event loop 上），定期同步進度與日誌。

        輪詢由 ``self._poller`` 持有：卸載（will_unmount）時 stop、重新掛載（did_mount）時接續，
        重複啟動不會累積；DONE／ERROR 後自行結束。背景工作執行緒不碰 UI。
        """
        self._ui_stop.clear()
        self._merge_tracking = True
        self.log_view.clear()
        # 2026-08-04 B6: 快照 output_dir,避免合併期間使用者改欄位導致開錯資料夾
        self._run_output_dir = self.output_dir_field.value
        self._poller.start(self.page, self._poll_merge)

    async def _poll_merge(self, alive=lambda: True) -> None:
        """每 0.1 秒同步一次，直到任務結束或輪詢被停止（unmount）。"""
        while alive() and not self._ui_stop.is_set():
            self._sync_ui_once()
            if self._ui_stop.is_set() or not alive():
                break
            await asyncio.sleep(0.1)

    def will_unmount(self) -> None:
        """換頁／關閉：停止輪詢（idempotent）。合併任務本身照常執行。"""
        self._poller.stop()

    def did_mount(self) -> None:
        """重新掛載：合併仍在追蹤就接續輪詢（任務已結束時補上最終狀態與摘要）。"""
        if self._merge_tracking and not self._ui_stop.is_set():
            self._poller.start(self.page, self._poll_merge)

    def _sync_ui_once(self) -> None:
        """在 event loop 上同步一次 session 狀態到畫面。"""
        # poller 可能已排入多個 _sync_ui；DONE/ERROR 處理過後的就直接略過，
        # 避免摘要視窗重複跳出
        if self._ui_stop.is_set():
            return
        try:
            snap = self.session.snapshot()
            status = snap["status"]
            progress = snap["progress"]
            logs = snap["logs"]

            if status == "RUNNING":
                self._set_status("執行中", C.DIA_BG)
            elif status == "DONE":
                cancelled = bool(getattr(self.session, "cancel_requested", False))
                self._set_status(
                    "已取消" if cancelled else "任務完成",
                    "gold" if cancelled else C.EM_BG,
                )
                self.cancel_button.visible = False
                snap_summary = snap.get("summary")
                if snap_summary:
                    self._merge_stats = snap_summary
                else:
                    # fallback：從 logs 解析
                    success_zips = 0
                    failed_zips = 0
                    failed_zip_details = []
                    for log_line in logs:
                        text = (
                            log_line.text
                            if hasattr(log_line, "text")
                            else str(log_line)
                        )
                        if "[完成]" in text and "[錯誤]" not in text:
                            success_zips += 1
                        elif "[錯誤]" in text:
                            failed_zips += 1
                            for zp in self.selected_zips:
                                if zp in text:
                                    failed_zip_details.append(Path(zp).name)
                                    break
                    self._merge_stats = {
                        "success_zips": success_zips,
                        "failed_zips": failed_zips,
                        "failed_zip_details": failed_zip_details,
                    }
                if not cancelled:
                    self._show_merge_summary(self._merge_stats)
                # 2026-08-02:DONE/ERROR 後停止 poller,避免無限 background update
                self._ui_stop.set()
            elif status == "ERROR":
                self._set_status("任務發生錯誤", C.RED_BG)
                self.cancel_button.visible = False
                self._ui_stop.set()

            self.progress_bar.value = progress

            # 2026-08-04 修正: 先 re-enable 按鈕，再 call sync_from_session (內部 page.update())
            # 否則按鈕 disabled=False 在 page.update() 之後才設，UI 永遠看不到
            if status in ("DONE", "ERROR"):
                self.start_button.disabled = False
                self.zip_list_view.disabled = False

            # LogView 接管 append + truncate + scroll
            # 內部會自己 page.update()
            self.log_view.sync_from_session(self.session)
        except Exception as e:  # noqa: BLE001
            log_warning(f"[MergeView] _sync_ui 錯誤: {e!r}")

    def _set_status(self, text: str, tone="neutral") -> None:
        """更新狀態晶片（``tone`` 為色組名稱，也接受舊背景色）。"""
        set_chip_status(self.status_chip, text, tone)
        self.page.update()

    def _show_merge_summary(self, summary: dict[str, Any]) -> None:
        """顯示合併結果摘要（使用 overlay 確保穩定顯示）。"""
        cfg = load_config()
        lang_merger_cfg = cfg.get("lang_merger", {})
        pending_name = lang_merger_cfg.get("pending_folder_name", "待翻譯")
        organized_name = lang_merger_cfg.get(
            "pending_organized_folder_name", "待翻譯整理需翻譯"
        )

        # 2026-08-04 修正 A1: 兼容 ZIP (success_zips) 與 Folder (success_folders) 兩種 key
        is_folder = "success_folders" in summary or "failed_folders" in summary
        unit = "資料夾" if is_folder else "ZIP"
        if is_folder:
            s_zips = summary.get("success_folders", 0)
            f_zips = summary.get("failed_folders", 0)
        else:
            s_zips = summary.get("success_zips", 0)
            f_zips = summary.get("failed_zips", 0)
        failed_list = (
            summary.get("failed_zips_list")
            or summary.get("failed_folders_list")
            or summary.get("failed_zip_details", [])
        )
        oc = summary.get("output_counts", {})

        output_block = self._merge_summary_output_block(
            oc, organized_name, pending_name
        )
        failed_block = self._merge_summary_failed_block(failed_list, unit)
        content = self._merge_summary_content(
            f_zips, failed_block, output_block, s_zips, unit
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("合併完成"),
            content=ft.Container(content=content, width=520),
            actions=[
                ft.TextButton(
                    "開啟輸出資料夾", on_click=lambda e: self._open_output_folder()
                ),
                ft.TextButton("關閉", on_click=lambda e: self._close_dialog_overlay()),
            ],
        )

        # 2026-08-02 修正:
        #   不再用 page.overlay.append + dialog.open = True (Flet 0.85 不能可靠關閉)
        #   改用 Flet 0.85 內建 page.show_dialog() 完整管理 dialog lifecycle
        self.page.show_dialog(dialog)

    def _open_output_folder(self) -> None:
        """開啟輸出資料夾（使用檔案總管）。"""

        snack = ft.SnackBar(ft.Text("正在開啟輸出資料夾..."), bgcolor=C.DIA)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
        # 2026-08-04 B6: 用合併開始時的快照,避免使用者中途改欄位導致開錯資料夾
        target = getattr(self, "_run_output_dir", None) or self.output_dir_field.value
        if not open_output_folder(target):
            show_snack(self.page, "⚠️ 無法開啟輸出資料夾", C.GOLD)

    def _close_dialog_overlay(self) -> None:
        """關閉頂層 dialog (跟 _show_merge_summary 用 page.show_dialog 對稱)。

        2026-08-02 修正:
            - 用 Flet 0.85 內建 page.pop_dialog() (對稱 page.show_dialog)
            - 不再用 page.overlay.remove()(會破壞 Flet dialog 內部狀態)
            - 不直接設 dialog.open=False (無法可靠觸發 close)

        Notes:
            page.pop_dialog() 接受可選參數 (specific dialog),
            無參數會關閉最頂層 (topmost)。
            確保對應 _show_merge_summary 內 page.show_dialog(dialog) 開啟的 dialog。
        """
        try:
            self.page.pop_dialog()
            # 2026-08-04: 先改 progress_bar 再 call _set_status (內部 page.update)
            self.progress_bar.value = 0.0
            self._set_status("尚未開始", C.DIM)
        except Exception as e:  # noqa: BLE001
            log_warning(f"[MergeView] pop_dialog 錯誤: {e!r}")

    @property
    def page(self):
        return self._page
