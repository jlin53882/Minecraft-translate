"""MergeView 的控制項與版面組裝（由 merge_view.py 拆出，#114）。"""

import threading
from typing import Any

import flet as ft

from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style
from app.ui.sync_text_field import SyncTextField
from app.views._log import LogView
from app.views.merge.merge_db_options import MergeDbOptions
from translation_tool.utils.config_manager import load_config


class MergeWidgetsMixin:
    """MergeView 的控制項與版面組裝（由 merge_view.py 拆出，#114）。"""

    def _init_merge_options(self, file_picker, page) -> None:
        """合併狀態與一般／zh_cn／Patchouli 選項控制項。"""
        self._page = page
        self.file_picker = file_picker
        self.db_options = MergeDbOptions(
            lambda: self._page.update(),
            on_missing_database=lambda: show_snack(
                self._page,
                "尚未建立 Mod 資料庫，請先到「Mod 資料庫」頁掃描 JAR 建立資料庫。",
                C.GOLD,
            ),
        )

        self.session = tag_session(
            TaskSession(max_logs=2000), "語系合併", "merge", page=self._page
        )
        self._ui_stop = threading.Event()
        self._poller = PollerHandle()  # 輪詢的 owner：卸載時 stop、重新掛載時 resume
        self._merge_tracking = False  # 合併進行中（輪詢尚未見到 DONE／ERROR）
        self._run_output_dir: str | None = (
            None  # 2026-08-04: snapshot for _open_output_folder
        )
        self.selected_zips: list[str] = []
        self.zip_path_field = None
        # 合併統計（用於 DONE 時顯示摘要）
        # 2026-08-04: 兼容 ZIP + Folder 兩種模式
        self._merge_stats: dict[str, Any] = {}
        # LogView widget 接管 append + UI controls 數量控制（取代 LogPresenter）
        # 設定在下面 line ~213 統一處理

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
        # patchouli_skip_en_us_when_zh_cn_exists: 優先 zh_tw，無則信任 zh_cn（達門檻時跳過 en_us）
        # - 有效翻譯由 patchouli_effective_translation_threshold（預設 0.5）判定：內容中日韓文字佔比超過此閾值
        # - 此開關只影響 Patchouli Book 的 en_us 資料夾，不影響 root-level lang 檔案
        # - 當 process_zh_cn_switch=False 時，此開關連動 Disabled（因 zh_cn 已被全域略過）
        self.patchouli_skip_zh_cn_switch = ft.Switch(
            label="優先使用已有繁中，無則信任簡中（跳過英文）",
            value=False,
            on_change=lambda e: self._on_merge_field_changed(
                "patchouli_skip_en_us_when_zh_cn_exists", e.control.value
            ),
        )
        # patchouli_effective_translation_threshold: 有效翻譯比例閾值（0.0~1.0）
        # 用於判斷 Patchouli Book 的 zh 語言資料夾是否有「有效翻譯」
        # 當 zh_tw 或 zh_cn 的有效翻譯比例 >= 此閾值時，會觸發跳過 en_us（如果 patchouli_skip_zh_cn_switch=True）
        self.patchouli_threshold_field = SyncTextField(
            value="0.5",
            width=96,
            hint_text="空白用預設值",
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            on_change=lambda e: self._on_merge_field_changed(
                "patchouli_effective_translation_threshold",
                float(v)
                if (v := e.control.value.strip()) and self._safe_float(v) is not None
                else 0.5,
            ),
        )

    def _init_merge_io_controls(self) -> None:
        """輸出資料夾、清單、狀態、日誌與按鈕控制項。"""
        # zh_en_letter_threshold: zh_tw 英文含量的閾值
        # 用於 is_already_zh() 判斷：超過此數值的英文字母視為英文內容
        self.zh_en_letter_threshold_field = SyncTextField(
            value="2",
            width=64,
            hint_text="空白用預設值",
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            on_change=lambda e: self._on_merge_field_changed(
                "zh_en_letter_threshold",
                int(v)
                if (v := e.control.value.strip()) and self._safe_int(v) is not None
                else 2,
            ),
        )
        # _zh_cn_disabled_note: 提示文字，當 process_zh_cn_switch=False 時顯示
        # 提醒使用者需要先開啟「處理 zh_cn 檔案」才能使用下方的 Patchouli 進階設定
        self._zh_cn_disabled_note = ft.Text(
            "需先開啟「處理 zh_cn 檔案」",
            size=11,
            color=C.RED,
            visible=False,
        )
        self.output_dir_field = kit.text_field(
            "輸出資料夾",
            hint="請選擇合併結果輸出位置",
            icon=ft.Icons.FOLDER_COPY_OUTLINED,
            mono=True,
            expand=True,
            path=True,
        )

        self.zip_list_view = ft.ListView(height=160, spacing=4, auto_scroll=False)
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"))
        apply_status_style(self.status_chip, "neutral")
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        # LogView widget 接管 append + UI controls 數量控制（取代 LogPresenter）
        self.log_view = LogView(
            page=self._page,
            mode="append",
            max_lines=2000,
        )

        self.pick_zip_button = kit.button(
            "新增 ZIP",
            "secondary",
            icon=ft.Icons.ADD,
            tooltip="選擇要合併的 ZIP 檔案",
            on_click=self.pick_zips,
        )
        self.start_button = kit.button(
            "開始合併",
            "primary",
            icon=ft.Icons.PLAY_ARROW,
            size="lg",
            tooltip="開始執行合併流程",
            on_click=self.start_merge,
        )
        self.cancel_button = kit.button(
            "取消",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            tooltip="在目前檢查點停止合併",
            on_click=self.cancel_merge,
        )
        self.cancel_button.visible = False

        self.input_mode_group = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(label="ZIP", value="zip"),
                    ft.Radio(label="資料夾", value="folder"),
                ],
                spacing=15,
            ),
            value="folder",
        )

    def _init_merge_input_panels(self) -> None:
        """ZIP／資料夾輸入面板與輸入模式切換。"""
        self.folder_path_field = kit.text_field(
            hint="選擇 Mod 來源資料夾",
            icon=ft.Icons.FOLDER_OUTLINED,
            mono=True,
            expand=True,
            path=True,
        )
        # Web 模式無法使用原生檔案選擇器，保留可用真實鍵盤輸入的 ZIP 路徑欄位。
        self.zip_path_field = kit.text_field(
            hint="Web 可直接輸入 ZIP 完整路徑",
            icon=ft.Icons.ARCHIVE_OUTLINED,
            mono=True,
            expand=True,
            path=True,
        )
        self.zip_panel = ft.Container(
            visible=False,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self.zip_path_field,
                            self.pick_zip_button,
                            ft.Text(
                                "可加入多個 ZIP，會依序合併。",
                                size=12,
                                color=C.MUTED,
                            ),
                        ],
                        spacing=10,
                    ),
                    self.zip_list_view,
                ],
                spacing=10,
            ),
        )
        self.folder_panel = ft.Container(
            visible=True,
            content=ft.Row(
                [
                    self.folder_path_field,
                    kit.pick_button(
                        ft.Icons.FOLDER_OPEN_OUTLINED,
                        "選擇資料夾",
                        self.pick_folder_input,
                    ),
                ],
                spacing=6,
            ),
        )

        def on_input_mode_changed(e=None):
            mode = self.input_mode_group.value
            if mode == "folder":
                self.zip_list_view.disabled = True
            else:
                self.zip_list_view.disabled = False
            self.zip_panel.visible = mode == "zip"
            self.folder_panel.visible = mode == "folder"
            self.update()

        self.input_mode_group.on_change = on_input_mode_changed

    def _build_general_and_zh_cn_sections(self):
        """一般選項與 zh_cn 區塊。"""

        general_options_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("一般選項", weight=ft.FontWeight.W_600, size=15),
                    self.only_lang_checkbox,
                    ft.Text(
                        "開啟後，只處理語言檔；其他內容檔案會略過。",
                        size=12,
                        color=C.MUTED,
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
                        "超過此數值判定為英文，用於 lang 過濾，預設 2。",
                        size=12,
                        color=C.MUTED,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=C.PANEL,
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
                        color=C.MUTED,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=C.PANEL,
            border_radius=10,
        )
        return general_options_section, zh_cn_section

    def _build_patchouli_section(self):
        """Patchouli 進階設定區塊。"""

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
                                            "翻譯來源優先級：繁中 > 簡中(達門檻) > 英文",
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
                                    "內容中日韓文字佔比達門檻時，視為有效翻譯並跳過英文",
                                    size=12,
                                    color=C.MUTED,
                                ),
                                self._skip_disabled_note(),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=C.PANEL,
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
                                    color=C.MUTED,
                                ),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=C.PANEL,
                        border_radius=8,
                    ),
                ],
                spacing=10,
            ),
            padding=12,
            bgcolor=C.PANEL,
            border_radius=10,
        )
        return patchouli_section

    def _build_extracted_section(self):
        """檔案合併（階段 2）區塊與載入設定值。"""

        cfg = load_config()
        lang_merger_cfg = cfg.get("lang_merger", {})
        pending_name = lang_merger_cfg.get("pending_folder_name", "待翻譯")
        organized_name = lang_merger_cfg.get("pending_organized_folder_name", "整理")
        min_count = lang_merger_cfg.get("filtered_pending_min_count", 2)
        self.patchouli_skip_zh_cn_switch.value = lang_merger_cfg.get(
            "patchouli_skip_en_us_when_zh_cn_exists", False
        )
        self.patchouli_threshold_field.value = str(
            lang_merger_cfg.get("patchouli_effective_translation_threshold", 0.5)
        )
        self.zh_en_letter_threshold_field.value = str(
            lang_merger_cfg.get("zh_en_letter_threshold", 2)
        )
        self.extracted_merge_switch = ft.Switch(
            label="合併 XX_extracted → assets/(階段 2)",
            value=True,
            on_change=lambda e: self._on_merge_field_changed(
                "enable_extracted_to_assets_merge", e.control.value
            ),
        )

        extracted_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("檔案合併(階段 2)", weight=ft.FontWeight.W_600, size=15),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            "合併 XX_extracted → assets/",
                                            weight=ft.FontWeight.W_500,
                                            size=14,
                                            expand=True,
                                        ),
                                        self.extracted_merge_switch,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text(
                                    "把 {XX_extracted}/{modid}/lang/*.json 的 key 補到 assets/{modid}/lang/*.json,"
                                    " 關閉不動(內部未變)。",
                                    size=12,
                                    color=C.MUTED,
                                ),
                            ],
                            spacing=4,
                        ),
                        padding=10,
                        bgcolor=C.PANEL,
                        border_radius=8,
                    ),
                ],
                spacing=10,
            ),
            padding=12,
            bgcolor=C.PANEL,
            border_radius=10,
        )

        self.extracted_merge_switch.value = lang_merger_cfg.get(
            "enable_extracted_to_assets_merge", True
        )
        return extracted_section, min_count, organized_name, pending_name

    def _build_merge_info_and_input_card(self, min_count, organized_name, pending_name):
        """說明區塊與輸入卡片。"""
        self._info_container = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=C.DIA, size=18),
                            ft.Text(
                                "📁 輸出資料夾說明",
                                weight="bold",
                                size=12,
                                color=C.MUTED,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        f"• 「{organized_name}」（key數≥{min_count}）→ 送機器翻譯",
                        size=12,
                        color=C.MUTED,
                    ),
                    ft.Text(
                        f"• 「{pending_name}」（key數<{min_count}）→ 未過濾，跳過不要送翻譯",
                        size=12,
                        color=C.MUTED,
                    ),
                ],
                spacing=4,
            ),
            padding=10,
            bgcolor=C.DIA_BG,
            border_radius=8,
        )

        input_card = kit.section_card(
            "輸入來源",
            ft.Column(
                [
                    kit.section_label("輸入模式"),
                    self.input_mode_group,
                    self.folder_panel,
                    self.zip_panel,
                ],
                spacing=10,
            ),
            icon=ft.Icons.DOWNLOAD,
            tone="dia",
        )
        return input_card

    def _build_merge_output_cards_and_layout(
        self,
        extracted_section,
        general_options_section,
        input_card,
        patchouli_section,
        zh_cn_section,
    ) -> None:
        """輸出／規則／日誌卡片與整體版面。"""
        output_card = kit.section_card(
            "輸出",
            ft.Column(
                [
                    ft.Row(
                        [
                            self.output_dir_field,
                            kit.pick_button(
                                ft.Icons.FOLDER_OPEN_OUTLINED,
                                "選擇輸出資料夾",
                                lambda e: self.pick_output_dir(),
                            ),
                        ],
                        spacing=8,
                    ),
                    self.start_button,
                    self.cancel_button,
                    ft.Row([self.status_chip], wrap=True),
                    self.progress_bar,
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="em",
        )
        rules_card = kit.section_card(
            "合併規則",
            ft.Column(
                [
                    general_options_section,
                    zh_cn_section,
                    patchouli_section,
                    extracted_section,
                ],
                spacing=12,
            ),
            icon=ft.Icons.TUNE,
            tone="ench",
        )
        log_card = kit.section_card(
            "執行日誌",
            # LogView widget 自帶深色容器 + 圓角 + 等寬字（從 theme）
            ft.Container(content=self.log_view, height=280),
            icon=ft.Icons.TERMINAL,
            tone="gold",
        )

        self.controls = [
            kit.page_header(
                "語系比對合併",
                "整合 en_us、zh_cn、zh_tw 三種來源，只留下真正需要翻譯的條目，並保留已完成的繁中",
                icon=ft.Icons.CALL_MERGE,
                tone="ench",
            ),
            self._info_container,
            ft.Row(
                [
                    ft.Column(
                        [input_card, self.db_options.card, output_card],
                        spacing=16,
                        expand=5,
                    ),
                    ft.Column([rules_card, log_card], spacing=16, expand=7),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

    def _merge_summary_output_block(self, oc, organized_name, pending_name):
        """輸出統計區塊的控制項。"""
        # 輸出統計 block
        oc_rows = []
        for label, count in [
            ("lang_output", oc.get("lang_output", 0)),
            ("assets", oc.get("assets", 0)),
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
        return output_block

    def _merge_summary_failed_block(self, failed_list, unit):
        """處理失敗清單區塊的控制項。"""
        # 失敗 ZIP block (2026-08-05: 用獨立 scroll area，避免 472 個失敗超出畫面)
        failed_block = []
        if failed_list:
            failed_rows = []
            for item in failed_list:
                # service 寫入的 key 是小寫 "name";"Name" 保留相容舊資料。
                # log fallback (failed_zip_details) 則是純檔名字串。
                if isinstance(item, dict):
                    name = item.get("name") or item.get("Name") or "?"
                    err = str(item.get("error") or "未知錯誤")
                else:
                    name, err = str(item), "未知錯誤"
                failed_rows.append(
                    ft.Text(
                        f"├─ {name}",
                        size=13,
                        color=C.GOLD,
                    )
                )
                if len(err) > 80:
                    err = err[:80] + "..."
                failed_rows.append(ft.Text(f"│  └─ {err}", size=12, color=C.MUTED))
            failed_block = [
                ft.Divider(),
                ft.Text(f"📋 處理失敗的 {unit}", size=14, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=ft.ListView(
                        controls=failed_rows,
                        height=200,  # 固定高度，獨立捲軸
                        spacing=2,
                    ),
                    padding=5,
                ),
            ]
        return failed_block

    def _merge_summary_content(self, f_zips, failed_block, output_block, s_zips, unit):
        """摘要對話框內容。"""
        content = ft.Column(
            [
                ft.Text("合併結果摘要", size=16, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=C.EM, size=20),
                        ft.Text(f"成功處理 {unit}：{s_zips} 個", size=14),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ERROR, color=C.RED, size=20),
                        ft.Text(f"失敗 {unit}：{f_zips} 個", size=14),
                    ],
                    spacing=8,
                ),
                *output_block,
                *failed_block,
                ft.Divider(),
                ft.Text("詳見上方日誌", size=12, color=C.DIM),
            ],
            spacing=10,
            tight=True,
        )
        return content
