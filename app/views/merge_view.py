"""app/views/merge_view.py 模組。
用途：提供 ZIP 合併頁面 UI 與執行流程。
維護注意：本檔案的 docstring 與中文註解用於維護說明，不代表行為變更。
"""

import threading
import time
from pathlib import Path
from typing import Any

import flet as ft

from app.logging import LogPresenter
from translation_tool.utils.log_unit import log_info
from translation_tool.utils.config_manager import load_config, save_config
from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service
from app.task_session import TaskSession
from app.ui import theme
from app.ui.components import primary_button, styled_card


class MergeView(ft.Column):
    """ZIP 合併頁面（視覺風格對齊 Translation / Extractor）。"""

    page: ft.Page
    file_picker: ft.FilePicker
    session: TaskSession
    _ui_stop: threading.Event
    selected_zips: list[str]
    _merge_stats: dict[str, Any]
    log_presenter: LogPresenter
    only_lang_checkbox: ft.Checkbox
    process_zh_cn_switch: ft.Switch
    patchouli_skip_zh_cn_switch: ft.Switch
    patchouli_threshold_field: ft.TextField
    zh_en_letter_threshold_field: ft.TextField
    output_dir_field: ft.TextField
    _zh_cn_disabled_note: ft.Text | None
    zip_list_view: ft.ListView
    status_chip: ft.Chip
    progress_bar: ft.ProgressBar
    log_view: ft.ListView
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

    def _on_merge_field_changed(self, key: str, value: Any) -> None:
        """寫入 lang_merger 單一欄位到 config.json，支援兩邊同步。"""
        try:
            cfg = load_config()
            if "lang_merger" not in cfg:
                cfg["lang_merger"] = {}
            cfg["lang_merger"][key] = value
            save_config(cfg)
        except Exception:
            pass

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker) -> None:
        """初始化 MergeView。"""
        super().__init__(expand=True, spacing=16, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self.file_picker = file_picker

        self.session = TaskSession(max_logs=2000)
        self._ui_stop = threading.Event()
        self.selected_zips: list[str] = []
        # 合併統計（用於 DONE 時顯示摘要）
        self._merge_stats = {
            "success_zips": 0,
            "failed_zips": 0,
            "failed_zip_details": "",
        }
        # LogPresenter 接管 append 與 UI controls 數量控制
        self.log_presenter = LogPresenter(mode="append", max_ui_lines=2000)

        # ── 一般選項 ──────────────────────────────────────────────────────────
        # only_process_lang: 只處理 /lang/ 目錄下的 zh_cn/zh_tw/en_us 檔案，其他目錄全部跳過
        self.only_lang_checkbox = ft.Checkbox(
            label="只處理 lang 檔案",
            value=True,
        )

        # ── zh_cn 全域處理 ─────────────────────────────────────────────────
        # process_zh_cn_files: 全域開關，關閉時所有 /zh_cn/ 路徑都跳過（包含 lang/zh_cn.json 和 Patchouli 內的 zh_cn/）
        # 此開關為其他設定的「主開關」，連動停用下方兩個設定
        self.process_zh_cn_switch = ft.Switch(
            label="處理 zh_cn 檔案",
            value=True,
            on_change=self._on_zh_cn_switch_changed,
        )

        # ── Patchouli 進階設定 ─────────────────────────────────────────────
        # patchouli_skip_en_us_when_zh_cn_exists: 當 en_us 對應的 zh_tw 或 zh_cn 有「有效翻譯」時，跳過 en_us
        # - 有效翻譯由 patchouli_effective_translation_threshold（預設 0.5）判定：內容中日韓文字佔比超過此閾值
        # - 此開關只影響 Patchouli Book 的 en_us 資料夾，不影響 root-level lang 檔案
        # - 當 process_zh_cn_switch=False 時，此開關連動 Disabled（因 zh_cn 已被全域略過）
        self.patchouli_skip_zh_cn_switch = ft.Switch(
            label="允許 zh_cn 觸發跳過 en_us",
            value=False,
            on_change=lambda e: self._on_merge_field_changed("patchouli_skip_en_us_when_zh_cn_exists", e.control.value),
        )
        # patchouli_effective_translation_threshold: 有效翻譯比例閾值（0.0~1.0）
        # 用於判斷 Patchouli Book 的 zh 語言資料夾是否有「有效翻譯」
        # 當 zh_tw 或 zh_cn 的有效翻譯比例 >= 此閾值時，會觸發跳過 en_us（如果 patchouli_skip_zh_cn_switch=True）
        self.patchouli_threshold_field = ft.TextField(
            value="0.5",
            width=96,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            on_change=lambda e: self._on_merge_field_changed(
                "patchouli_effective_translation_threshold", float(e.control.value)
            ),
        )
        # zh_en_letter_threshold: zh_tw 英文含量的閾值
        # 用於 is_already_zh() 判斷：超過此數值的英文字母視為英文內容
        self.zh_en_letter_threshold_field = ft.TextField(
            value="2",
            width=64,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            on_change=lambda e: self._on_merge_field_changed(
                "zh_en_letter_threshold", int(e.control.value)
            ),
        )
        # _zh_cn_disabled_note: 提示文字，當 process_zh_cn_switch=False 時顯示
        # 提醒使用者需要先開啟「處理 zh_cn 檔案」才能使用下方的 Patchouli 進階設定
        self._zh_cn_disabled_note = ft.Text(
            "需先開啟「處理 zh_cn 檔案」",
            size=11,
            color=theme.ERROR,
            visible=False,
        )
        self.output_dir_field = ft.TextField(
            label="輸出資料夾",
            hint_text="請選擇合併結果輸出位置",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        self.zip_list_view = ft.ListView(height=160, spacing=4, auto_scroll=False)
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"), bgcolor=theme.GREY_200)
        self.progress_bar = ft.ProgressBar(
            value=0, height=8, bgcolor=theme.GREY_200, color=theme.BLUE
        )
        self.log_view = ft.ListView(expand=True, spacing=4, auto_scroll=True)

        self.pick_zip_button = primary_button(
            "新增 ZIP",
            icon=ft.Icons.ADD,
            tooltip="選擇要合併的 ZIP 檔案",
            on_click=self.pick_zips,
            bgcolor=theme.PRIMARY,
        )
        self.start_button = primary_button(
            "開始合併 ZIP",
            icon=ft.Icons.PLAY_ARROW,
            tooltip="開始執行 ZIP 合併流程",
            on_click=self.start_merge,
            bgcolor=theme.SUCCESS,
        )

        general_options_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("一般選項", weight=ft.FontWeight.W_600, size=15),
                    self.only_lang_checkbox,
                    ft.Text(
                        "開啟後，只處理語言檔；其他內容檔案會略過。",
                        size=12,
                        color=theme.GREY_600,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=theme.GREY_50,
            border_radius=10,
        )

        zh_cn_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("zh_cn 處理", weight=ft.FontWeight.W_600, size=15),
                    self.process_zh_cn_switch,
                    ft.Text(
                        "關閉後，所有 zh_cn 檔案都會略過。",
                        size=12,
                        color=theme.GREY_600,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=theme.GREY_50,
            border_radius=10,
        )

        patchouli_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Patchouli 進階設定", weight=ft.FontWeight.W_600, size=15),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            "允許 zh_cn 觸發跳過 en_us",
                                            weight=ft.FontWeight.W_500,
                                            size=14,
                                            expand=True,
                                        ),
                                        self.patchouli_skip_zh_cn_switch,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text(
                                    "zh_cn 達門檻時，跳過對應 en_us。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
                                self._skip_disabled_note(),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=theme.WHITE,
                        border_radius=8,
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            "en_us 跳過門檻",
                                            weight=ft.FontWeight.W_500,
                                            size=14,
                                            expand=True,
                                        ),
                                        self.patchouli_threshold_field,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text(
                                    "預設 0.5，範圍 0.0 ~ 1.0。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
                                ft.Container(height=6),
                                ft.Row(
                                    [
                                        ft.Text(
                                            "zh 英文含量閾值",
                                            weight=ft.FontWeight.W_500,
                                            size=14,
                                            expand=True,
                                        ),
                                        self.zh_en_letter_threshold_field,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text(
                                    "超過此數值判定為英文內容，預設 2。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=theme.WHITE,
                        border_radius=8,
                    ),
                ],
                spacing=10,
            ),
            padding=12,
            bgcolor=theme.GREY_50,
            border_radius=10,
        )

        cfg = load_config()
        lang_merger_cfg = cfg.get("lang_merger", {})
        pending_name = lang_merger_cfg.get("pending_folder_name", "待翻譯")
        organized_name = lang_merger_cfg.get("pending_organized_folder_name", "整理")
        min_count = lang_merger_cfg.get("filtered_pending_min_count", 2)
        self.patchouli_skip_zh_cn_switch.value = lang_merger_cfg.get("patchouli_skip_en_us_when_zh_cn_exists", False)
        self.patchouli_threshold_field.value = str(lang_merger_cfg.get("patchouli_effective_translation_threshold", 0.5))
        self.zh_en_letter_threshold_field.value = str(lang_merger_cfg.get("zh_en_letter_threshold", 2))
        self._info_container = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=theme.BLUE, size=18),
                            ft.Text(
                                "📁 輸出資料夾說明",
                                weight="bold",
                                size=12,
                                color=theme.GREY_700,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        f"• 「{organized_name}」（key數≥{min_count}）→ 送機器翻譯",
                        size=12,
                        color=theme.GREY_700,
                    ),
                    ft.Text(
                        f"• 「{pending_name}」（key數<{min_count}）→ 未過濾，跳過不要送翻譯",
                        size=12,
                        color=theme.GREY_700,
                    ),
                ],
                spacing=4,
            ),
            padding=10,
            bgcolor=theme.BLUE_50,
            border_radius=8,
        )

        self.controls = [
            self._info_container,
            styled_card(
                title="ZIP 清單",
                icon=ft.Icons.ARCHIVE,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                self.pick_zip_button,
                                ft.Text(
                                    "可加入多個 ZIP，會依序合併。",
                                    size=12,
                                    color=theme.GREY_600,
                                ),
                            ],
                            spacing=10,
                        ),
                        self.zip_list_view,
                    ],
                    spacing=10,
                ),
            ),
            styled_card(
                title="輸出與選項",
                icon=ft.Icons.FOLDER,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                self.output_dir_field,
                                ft.IconButton(
                                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                                    icon_color=theme.BLUE_GREY_700,
                                    tooltip="選擇輸出資料夾",
                                    on_click=lambda e: self.pick_output_dir(),
                                ),
                            ],
                            spacing=6,
                        ),
                        general_options_section,
                        zh_cn_section,
                        patchouli_section,
                    ],
                    spacing=12,
                ),
            ),
            styled_card(
                title="執行狀態",
                icon=ft.Icons.TIMELINE,
                content=ft.Column(
                    [
                        ft.Row([self.status_chip], wrap=True),
                        self.progress_bar,
                        self.start_button,
                    ],
                    spacing=10,
                ),
            ),
            styled_card(
                title="執行日誌",
                icon=ft.Icons.RECEIPT_LONG,
                content=ft.Container(
                    height=280,
                    bgcolor="#2b2f36",
                    border=ft.Border.all(1, "#4b5563"),
                    border_radius=8,
                    padding=10,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    content=self.log_view,
                ),
            ),
        ]

    def pick_zips(self, e: ft.ControlEvent) -> None:
        """開啟 ZIP 檔案選擇對話框。"""
        self.file_picker.on_upload = self._on_zip_picked
        self.file_picker.pick_files(
            dialog_title="選擇 ZIP 檔案",
            allow_multiple=True,
            allowed_extensions=["zip"],
        )

    def _on_zip_picked(self, e: ft.FilePickerUploadEvent) -> None:
        """處理 ZIP 檔案選擇結果。"""
        if not e.files:
            return
        for f in e.files:
            if f.path and f.path not in self.selected_zips:
                self.selected_zips.append(f.path)
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
        result = await self.file_picker.get_directory_path(dialog_title="選擇輸出資料夾")
        if result:
            self.output_dir_field.value = result
            self.page.update()

    def start_merge(self, e: ft.ControlEvent) -> None:
        """處理開始合併按鈕事件。"""
        if not self.selected_zips or not (self.output_dir_field.value or "").strip():
            self._show_snack_bar("請先選擇 ZIP 與輸出資料夾")
            return

        self.start_button.disabled = True
        self.zip_list_view.disabled = True
        self.log_view.controls.clear()
        self._set_status("執行中", theme.BLUE_200)

        self.session.start()
        self.session.add_log("[系統] 開始 ZIP 合併任務")
        self._start_ui_poller()

        def _run_merge():
            # ⚠️ generator 必須完整迭代，否則程式碼不會執行
            for _ in run_merge_zip_batch_service(
                self.selected_zips,
                self.output_dir_field.value,
                self.session,
                self.only_lang_checkbox.value,
            ):
                pass

        threading.Thread(target=_run_merge, daemon=True).start()

    def _start_ui_poller(self) -> None:
        """啟動 UI 輪詢器，定期同步進度與日誌。"""
        self._ui_stop.clear()
        self.log_presenter.reset()

        def poll():
            while not self._ui_stop.is_set():
                snap = self.session.snapshot()
                status = snap["status"]
                progress = snap["progress"]
                logs = snap["logs"]

                if status == "RUNNING":
                    self._set_status("執行中", theme.BLUE_200)
                elif status == "DONE":
                    self._set_status("任務完成", theme.GREEN_200)
                    # 直接從 session 取得 service 統計的 summary
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
                    self._show_merge_summary(self._merge_stats)
                elif status == "ERROR":
                    self._set_status("任務發生錯誤", theme.RED_200)

                self.progress_bar.value = progress

                # LogPresenter 接管 append + truncate + scroll
                self.log_presenter.sync(self.log_view, logs)

                if status in ("DONE", "ERROR"):
                    self.start_button.disabled = False
                    self.zip_list_view.disabled = False
                    self.page.update()
                    break

                self.page.update()
                time.sleep(0.1)

        threading.Thread(target=poll, daemon=True).start()

    def _set_status(self, text: str, color: str) -> None:
        """更新狀態晶片顯示。"""
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color

    def _show_snack_bar(self, message: str, color: str = theme.ERROR) -> None:
        """顯示 SnackBar 訊息。"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _show_merge_summary(self, summary: dict[str, Any]) -> None:
        """顯示合併結果摘要（使用 overlay 確保穩定顯示）。"""
        cfg = load_config()
        lang_merger_cfg = cfg.get("lang_merger", {})
        pending_name = lang_merger_cfg.get("pending_folder_name", "待翻譯")
        organized_name = lang_merger_cfg.get("pending_organized_folder_name", "待翻譯整理需翻譯")

        s_zips = summary.get("success_zips", 0)
        f_zips = summary.get("failed_zips", 0)
        failed_list = summary.get("failed_zips_list", [])
        oc = summary.get("output_counts", {})

        # 輸出統計 block
        oc_rows = []
        for label, count in [
            ("lang_output", oc.get("lang_output", 0)),
            (pending_name, oc.get(pending_name, 0)),
            (organized_name, oc.get(organized_name, 0)),
            ("patchouli_output", oc.get("patchouli_output", 0)),
            ("other_output", oc.get("other_output", 0)),
            ("errordata_output", oc.get("errordata_output", 0)),
        ]:
            if count > 0:
                oc_rows.append(ft.Text(f"├─ {label}：{count} 個", size=13))
        output_block = (
            [ft.Divider(), ft.Text("📁 輸出統計", size=14, weight=ft.FontWeight.BOLD)]
            + oc_rows
            if oc_rows
            else []
        )

        # 失敗 ZIP block
        failed_block = []
        if failed_list:
            for item in failed_list:
                failed_block.append(
                    ft.Text(
                        f"├─ {item.get('Name', '?')}",
                        size=13,
                        color=ft.Colors.ORANGE_700,
                    )
                )
                err = item.get("error", "未知錯誤")
                # 截斷過長錯誤訊息
                if len(err) > 80:
                    err = err[:80] + "..."
                failed_block.append(ft.Text(f"│  └─ {err}", size=12, color="#cccccc"))
            failed_block = [
                ft.Divider(),
                ft.Text("📋 處理失敗的 ZIP", size=14, weight=ft.FontWeight.BOLD),
            ] + failed_block

        content = ft.Column(
            [
                ft.Text("合併結果摘要", size=16, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=theme.GREEN, size=20),
                        ft.Text(f"成功處理 ZIP：{s_zips} 個", size=14),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ERROR, color=theme.RED, size=20),
                        ft.Text(f"失敗 ZIP：{f_zips} 個", size=14),
                    ],
                    spacing=8,
                ),
                *output_block,
                *failed_block,
                ft.Divider(),
                ft.Text("詳見上方日誌", size=12, color="#aaaaaa"),
            ],
            spacing=10,
            tight=True,
        )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("合併完成"),
            content=ft.Container(content=content, width=520),
            actions=[
                ft.TextButton(
                    "開啟輸出資料夾", on_click=lambda e: self._open_output_folder()
                ),
                ft.TextButton(
                    "關閉", on_click=lambda e: self._close_dialog_overlay(dialog)
                ),
            ],
        )

        # 使用 overlay 方式，穩定性高於 page.open()
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def _open_output_folder(self) -> None:
        """開啟輸出資料夾（使用檔案總管）。"""
        import subprocess

        snack = ft.SnackBar(ft.Text("正在開啟輸出資料夾..."), bgcolor=theme.BLUE_700)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
        subprocess.Popen(["explorer", self.output_dir_field.value], shell=True)

    def _close_dialog_overlay(self, dialog: ft.AlertDialog) -> None:
        """關閉 overlay 對話框。"""
        try:
            dialog.open = False
            self.page.update()
        except Exception:
            pass

    @property
    def page(self):
        return self._page
