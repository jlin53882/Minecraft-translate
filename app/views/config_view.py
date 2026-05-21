"""app/views/config_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
from app.ui import theme
from translation_tool.utils.log_unit import log_info
from app.services_impl.config_service import load_config_json, save_config_json
from app.views.config.config_actions import load_config_into_view, save_config_from_view
from app.views.config.config_form import (
    build_card as build_config_card,
    build_footer as build_config_footer,
    build_header as build_config_header,
    build_key_field,
    build_key_row,
)
from translation_tool.core.lm_config_rules import validate_api_keys_from_ui

NAV_ITEMS = [
    {"id": "general", "label": "一般設定", "icon": ft.Icons.SETTINGS},
    {"id": "api_models", "label": "API & 模型設定", "icon": ft.Icons.KEY},
    {"id": "translation_behavior", "label": "翻譯行為設定", "icon": ft.Icons.TRANSLATE},
    {"id": "merger", "label": "語言合併器設定", "icon": ft.Icons.MERGE_TYPE},
    {"id": "prompts", "label": "提示詞管理", "icon": ft.Icons.MESSAGE},
    {"id": "species_lookup", "label": "學名查詢管理", "icon": ft.Icons.SEARCH},
    {"id": "batch_limits", "label": "批次與限制", "icon": ft.Icons.DEVELOPER_BOARD},
    {"id": "extractor", "label": "Jar 提取設定", "icon": ft.Icons.FOLDER_OPEN},
]


class ConfigView(ft.Column):
    """ConfigView 類別。

    用途：封裝與 ConfigView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    DEFAULT_MODELS = {
        "gemini-2.5-flash": True,
    }

    def __init__(self, page: ft.Page):
        """初始化 ConfigView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=0)
        self._page = page
        self._registry = None
        self.controls_map = {}
        self._selected_nav = "general"

        self._init_controls()

        self.scroll_container = ft.Column(
            scroll=ft.ScrollMode.ADAPTIVE,
            expand=True,
            spacing=15,
            controls=[
                self._build_header(),
                ft.Row(
                    controls=[
                        self._build_nav_column(),
                        self._build_content_area(),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    spacing=15,
                ),
            ],
        )

        self.footer = self._build_footer()

        self.controls = [self.scroll_container, self.footer]

        self.load_config()

    def _init_controls(self):
        """初始化所有輸入控制項"""
        self.controls_map["logging.log_level"] = ft.Dropdown(
            label="日誌等級",
            options=[
                ft.dropdown.Option(level)
                for level in ["DEBUG", "INFO", "WARNING", "ERROR"]
            ],
            dense=True,
            helper_text="用於：logging module",
        )
        self.controls_map["logging.log_dir"] = ft.TextField(
            label="日誌資料夾名稱", dense=True, helper="用於：logging module"
        )

        self.controls_map["translator.output_dir_name"] = ft.TextField(
            label="主要輸出資料夾名稱", dense=True, helper="用於：翻譯結果輸出"
        )
        self.controls_map["ftb_translator.output_dir_name"] = ft.TextField(
            label="FTB 任務輸出資料夾名稱", dense=True, helper="用於：FTB任務翻譯輸出"
        )
        self.controls_map["translator.replace_rules_path"] = ft.TextField(
            label="替換規則檔案名稱", dense=True, helper="用於：replace_rules_loader"
        )
        self.controls_map["translator.cache_directory"] = ft.TextField(
            label="快取資料夾名稱", dense=True, helper="用於：翻譯快取系統"
        )
        self.controls_map["translator.enable_cache_saving"] = ft.Checkbox(
            label="啟用通用翻譯快取"
        )
        self.controls_map["translator.parallel_execution_workers"] = ft.TextField(
            label="檔案處理多執行緒數量", dense=True, helper="用於：平行執行器"
        )

        self.controls_map["species_cache.cache_directory"] = ft.TextField(
            label="學名快取資料夾", hint_text="用於：學名查詢系統", dense=True
        )
        self.controls_map["species_cache.cache_filename"] = ft.TextField(
            label="學名存放檔案名稱", hint_text="用於：學名TSV快取", dense=True
        )
        self.controls_map["species_cache.wikipedia_language"] = ft.TextField(
            label="Wiki 查詢語言", hint_text="用於：維基百科API", dense=True
        )
        self.controls_map["species_cache.wikipedia_rate_limit_delay"] = ft.TextField(
            label="查詢延遲(秒)", hint_text="用於：API速率限制", dense=True
        )

        self.controls_map["output_bundler.output_zip_name"] = ft.TextField(
            label="最終打包 ZIP 檔名", hint_text="用於：BundlerView自動帶入", dense=True
        )

        self.controls_map["lang_merger.pending_folder_name"] = ft.TextField(
            label="待翻譯資料夾名稱", hint_text="用於：語言合併器", dense=True
        )
        self.controls_map["lang_merger.pending_organized_folder_name"] = ft.TextField(
            label="待翻譯整理資料夾名稱", hint_text="用於：lang_merger", dense=True
        )
        self.controls_map["lang_merger.filtered_pending_min_count"] = ft.TextField(
            label="待翻譯整理json筆數最小出現次數", hint_text="用於：整理分類邏輯", dense=True
        )
        self.controls_map["lang_merger.quarantine_folder_name"] = ft.TextField(
            label="語言合併器格式問題隔離資料夾名稱", hint_text="用於：格式錯誤隔離", dense=True
        )
        self.controls_map["lang_merger.patchouli_skip_en_us_when_zh_cn_exists"] = ft.Checkbox(
            label="允許 zh_cn 觸發跳過 en_us", value=False
        )
        self.controls_map["lang_merger.patchouli_effective_translation_threshold"] = ft.TextField(
            label="en_us 跳過門檻", hint_text="有效翻譯比例閾值 0.0~1.0", dense=True,
            keyboard_type=ft.KeyboardType.NUMBER
        )
        self.controls_map["lang_merger.zh_en_letter_threshold"] = ft.TextField(
            label="zh 英文含量閾值", hint_text="超過此數值則判定為英文內容", dense=True,
            keyboard_type=ft.KeyboardType.NUMBER
        )

        self.controls_map["lm_translator.temperature"] = ft.TextField(
            label="模型溫度 (Temperature)", hint_text="用於：LM翻譯請求", dense=True
        )
        self.controls_map["lm_translator.rate_limit.timeout"] = ft.TextField(
            label="API 請求 Timeout", helper="用於：API超時控制", dense=True, keyboard_type=ft.KeyboardType.NUMBER
        )
        self.controls_map["lm_translator.rate_limit.sleep_seconds_between_batches"] = ft.TextField(
            label="批次間延遲 (秒)", helper="用於：翻譯批次間延遲", dense=True, keyboard_type=ft.KeyboardType.NUMBER
        )
        self.controls_map["lm_translator.lm_translate_folder_name"] = ft.TextField(
            label="LM 翻譯輸出資料夾", helper="用於：翻譯結果輸出", dense=True
        )

        self.controls_map["species_cache.cache_directory"] = ft.TextField(
            label="學名快取資料夾", dense=True, helper="用於：學名查詢系統"
        )
        self.controls_map["species_cache.cache_filename"] = ft.TextField(
            label="學名存放檔案名稱", dense=True, helper="用於：學名TSV快取"
        )
        self.controls_map["species_cache.wikipedia_language"] = ft.TextField(
            label="Wiki 查詢語言", dense=True, helper="用於：維基百科API"
        )
        self.controls_map["species_cache.wikipedia_rate_limit_delay"] = ft.TextField(
            label="查詢延遲(秒)", dense=True, helper="用於：API速率限制"
        )

        self.controls_map["output_bundler.output_zip_name"] = ft.TextField(
            label="最終打包 ZIP 檔名", dense=True, helper="用於：BundlerView自動帶入"
        )

        self.controls_map["lang_merger.pending_folder_name"] = ft.TextField(
            label="待翻譯資料夾名稱", dense=True, helper="用於：語言合併器"
        )
        self.controls_map["lang_merger.pending_organized_folder_name"] = ft.TextField(
            label="待翻譯整理資料夾名稱", dense=True, helper="用於：lang_merger"
        )
        self.controls_map["lang_merger.filtered_pending_min_count"] = ft.TextField(
            label="待翻譯整理json筆數最小出現次數", dense=True, helper="用於：整理分類邏輯"
        )
        self.controls_map["lang_merger.quarantine_folder_name"] = ft.TextField(
            label="語言合併器格式問題隔離資料夾名稱", dense=True, helper="用於：格式錯誤隔離"
        )

        self.controls_map["lm_translator.temperature"] = ft.TextField(
            label="模型溫度 (Temperature)", dense=True, helper="用於：LM翻譯請求"
        )
        self.controls_map["lm_translator.rate_limit.timeout"] = ft.TextField(
            label="API 請求 Timeout", dense=True, keyboard_type=ft.KeyboardType.NUMBER, helper="用於：API超時控制"
        )
        self.controls_map["lm_translator.lm_translate_folder_name"] = ft.TextField(
            label="LM 翻譯輸出資料夾", dense=True, helper="用於：翻譯結果輸出"
        )

        self.controls_map["lm_translator.patchouli_system_prompt"] = ft.TextField(
            label="Patchouli 提示詞 (System Prompt)",
            multiline=True,
            expand=True,
            text_size=13,
            helper="用於：Patchouli翻譯請求",
        )
        self.controls_map["lm_translator.lang_system_prompt"] = ft.TextField(
            label="Lang 提示詞 (System Prompt)",
            multiline=True,
            expand=True,
            text_size=13,
            helper="用於：Lang檔案翻譯請求",
        )

        self.controls_map["lm_translator.initial_batch_size_patchouli"] = ft.TextField(
            label="Patchouli 請求大小", dense=True, helper="用於：批次翻譯請求"
        )
        self.controls_map["lm_translator.initial_batch_size_lang"] = ft.TextField(
            label="Lang 請求大小", dense=True, helper="用於：批次翻譯請求"
        )
        self.controls_map["lm_translator.initial_batch_size_ftb"] = ft.TextField(
            label="FTB Quests 請求大小", dense=True, helper="用於：批次翻譯請求"
        )
        self.controls_map["lm_translator.initial_batch_size_kubejs"] = ft.TextField(
            label="KubeJS 請求大小", dense=True, helper="用於：批次翻譯請求"
        )
        self.controls_map["lm_translator.initial_batch_size_md"] = ft.TextField(
            label="MD 請求大小", dense=True, helper="用於：批次翻譯請求"
        )
        self.controls_map["lm_translator.min_batch_size"] = ft.TextField(
            label="最小錯誤請求大小", dense=True, helper="用於：錯誤時批次縮小"
        )
        self.controls_map["lm_translator.batch_shrink_factor"] = ft.TextField(
            label="錯誤縮小比例", dense=True, helper="用於：批次失敗時縮小率"
        )

        self.controls_map["lm_translator.translator.skip_terms"] = ft.TextField(
            label="略過翻譯 (Skip Terms)",
            multiline=True,
            expand=True,
            text_size=13,
            helper="用於：翻譯時略過含關鍵字的項目",
        )
        self.controls_map["lm_translator.translator.translatable_keywords"] = (
            ft.TextField(
                label="可翻譯欄位 (Keywords)",
                multiline=True,
                expand=True,
                text_size=13,
                helper="用於：判斷哪些JSON欄位需翻譯",
            )
        )
        self.controls_map["lm_translator.patchouli.dir_names"] = ft.TextField(
            label="Patchouli 資料夾",
            multiline=True,
            expand=True,
            text_size=13,
            helper="用於：find_patchouli_json 掃描目錄",
        )

        self.controls_map["extractor.output_folder_names.lang_extract"] = ft.TextField(
            label="Lang 提取輸出資料夾", helper="未填入輸出路徑時自動帶入此名稱", dense=True
        )
        self.controls_map["extractor.output_folder_names.book_extract"] = ft.TextField(
            label="Book 提取輸出資料夾", helper="未填入輸出路徑時自動帶入此名稱", dense=True
        )
        self.controls_map["extractor.output_folder_names.lang_preview"] = ft.TextField(
            label="Lang 預覽輸出資料夾", helper="未填入輸出路徑時自動帶入此名稱", dense=True
        )
        self.controls_map["extractor.output_folder_names.book_preview"] = ft.TextField(
            label="Book 預覽輸出資料夾", helper="未填入輸出路徑時自動帶入此名稱", dense=True
        )
        self.controls_map["extractor.output_folder_names.dual_extract"] = ft.TextField(
            label="Dual 提取輸出資料夾", helper="Lang + Book 同時提取時使用", dense=True
        )
        self.controls_map["extractor.output_folder_names.dual_preview"] = ft.TextField(
            label="Dual 預覽輸出資料夾", helper="Lang + Book 同時預覽時使用", dense=True
        )

        self.new_model_field = ft.TextField(
            label="新增模型名稱", hint_text="gemini-2.5-flash", expand=True, dense=True
        )
        self.add_model_button = ft.IconButton(
            icon=ft.Icons.ADD, tooltip="新增模型", on_click=self.on_add_model_clicked
        )
        self.models_column = ft.Column(spacing=5)
        self.controls_map["lm_translator.models"] = self.models_column

        self.add_key_button = ft.IconButton(
            icon=ft.Icons.ADD,
            tooltip="新增 API Key",
            on_click=lambda e: self.add_key_row(),
        )
        self.key_fields: list[ft.TextField] = []
        self.keys_column = ft.Column(spacing=5)
        self.controls_map["lm_translator.keys"] = self.keys_column

    def _build_nav_item(self, item: dict) -> ft.Container:
        """建立導覽項目按鈕"""
        is_selected = self._selected_nav == item["id"]

        btn = ft.Container(
            padding=12,
            border_radius=8,
            bgcolor=ft.Colors.BLUE_200 if is_selected else ft.Colors.GREY_100,
            on_click=lambda e, iid=item["id"]: self._on_nav_click(iid),
            content=ft.Row(
                [
                    ft.Icon(item["icon"], size=18, color=ft.Colors.BLUE_800 if is_selected else ft.Colors.BLUE_GREY_600),
                    ft.Text(item["label"], weight=ft.FontWeight.BOLD, size=13, color=ft.Colors.BLUE_900 if is_selected else ft.Colors.BLUE_GREY_700),
                ],
                spacing=10,
                alignment=ft.MainAxisAlignment.START,
            ),
        )
        return btn

    def _on_nav_click(self, nav_id: str):
        """處理導覽點擊"""
        self._selected_nav = nav_id
        self._rebuild_nav()
        self._show_content(nav_id)

    def _rebuild_nav(self):
        """重新建構導覽列"""
        self.nav_column.controls = [self._build_nav_item(item) for item in NAV_ITEMS]
        self.nav_column.update()

    def _show_content(self, nav_id: str):
        """切換顯示內容"""
        for cid, container in self._content_containers.items():
            container.visible = (cid == nav_id)
        self.content_scroll.update()

    def _build_nav_column(self) -> ft.Container:
        """建立左側導覽列"""
        nav_items = []
        for item in NAV_ITEMS:
            is_selected = self._selected_nav == item["id"]
            btn = ft.Container(
                padding=12,
                border_radius=8,
                bgcolor=ft.Colors.BLUE_200 if is_selected else ft.Colors.GREY_100,
                on_click=lambda e, iid=item["id"]: self._on_nav_click(iid),
                content=ft.Row(
                    [
                        ft.Icon(item["icon"], size=18, color=ft.Colors.BLUE_800 if is_selected else ft.Colors.BLUE_GREY_600),
                        ft.Text(item["label"], weight=ft.FontWeight.BOLD, size=13, color=ft.Colors.BLUE_900 if is_selected else ft.Colors.BLUE_GREY_700),
                    ],
                    spacing=10,
                    alignment=ft.MainAxisAlignment.START,
                ),
            )
            nav_items.append(btn)

        self.nav_column = ft.Column(nav_items, spacing=6)
        nav_container = ft.Container(
            width=200,
            content=ft.Column(
                [
                    ft.Text(
                        "設定分類",
                        weight=ft.FontWeight.BOLD,
                        size=14,
                        color=ft.Colors.BLUE_GREY_800,
                    ),
                    ft.Divider(height=1, color=ft.Colors.GREY_300),
                    self.nav_column,
                ],
                spacing=8,
            ),
            padding=10,
            bgcolor=ft.Colors.GREY_50,
            border_radius=10,
        )
        return nav_container

    def _build_content_area(self) -> ft.Column:
        """建立右側內容區（所有分類內容）"""
        self._content_containers = {}

        general_content = ft.Column(
            spacing=15,
            controls=[
                self._build_card("日誌設定 (Logging)", [
                    self.controls_map["logging.log_level"],
                    self.controls_map["logging.log_dir"],
                ]),
                self._build_card("翻譯與處理設定 (Translator)", [
                    self.controls_map["translator.output_dir_name"],
                    self.controls_map["ftb_translator.output_dir_name"],
                    self.controls_map["translator.replace_rules_path"],
                    self.controls_map["translator.cache_directory"],
                    self.controls_map["translator.parallel_execution_workers"],
                    self.controls_map["translator.enable_cache_saving"],
                ]),
                self._build_card("成品打包器 (Output Bundler)", [
                    self.controls_map["output_bundler.output_zip_name"],
                ]),
            ],
        )

        api_models_content = ft.Column(
            spacing=15,
            controls=[
                self._build_lm_keys_card(),
                self._build_lm_models_card(),
            ],
        )

        translation_behavior_content = ft.Column(
            spacing=15,
            controls=[
                self._build_lm_basic_card(),
                self._build_lm_filter_card(),
            ],
        )

        prompts_content = ft.Column(
            spacing=15,
            controls=[
                self._build_lm_prompts_card(),
            ],
        )

        species_lookup_content = ft.Column(
            spacing=15,
            controls=[
                self._build_card("學名查詢設定 (Species Cache)", [
                    self.controls_map["species_cache.cache_directory"],
                    self.controls_map["species_cache.cache_filename"],
                    self.controls_map["species_cache.wikipedia_language"],
                    self.controls_map["species_cache.wikipedia_rate_limit_delay"],
                ]),
            ],
        )

        batch_limits_content = ft.Column(
            spacing=15,
            controls=[
                self._build_lm_batch_card(),
            ],
        )

        merger_content = ft.Column(
            spacing=15,
            controls=[
                self._build_lang_merger_card(),
            ],
        )

        extractor_content = ft.Column(
            spacing=15,
            controls=[
                self._build_card("JAR 輸出資料夾命名", [
                    ft.Row([
                        ft.Column([self.controls_map["extractor.output_folder_names.lang_extract"]], expand=1),
                        ft.Column([self.controls_map["extractor.output_folder_names.book_extract"]], expand=1),
                        ft.Column([self.controls_map["extractor.output_folder_names.dual_extract"]], expand=1),
                    ]),
                    ft.Row([
                        ft.Column([self.controls_map["extractor.output_folder_names.lang_preview"]], expand=1),
                        ft.Column([self.controls_map["extractor.output_folder_names.book_preview"]], expand=1),
                        ft.Column([self.controls_map["extractor.output_folder_names.dual_preview"]], expand=1),
                    ]),
                ]),
            ],
        )

        self._content_containers["general"] = general_content
        self._content_containers["api_models"] = api_models_content
        self._content_containers["translation_behavior"] = translation_behavior_content
        self._content_containers["prompts"] = prompts_content
        self._content_containers["species_lookup"] = species_lookup_content
        self._content_containers["batch_limits"] = batch_limits_content
        self._content_containers["merger"] = merger_content
        self._content_containers["extractor"] = extractor_content

        self.content_scroll = ft.Container(
            expand=True,
            content=ft.Stack(
                [
                    general_content,
                    api_models_content,
                    translation_behavior_content,
                    prompts_content,
                    species_lookup_content,
                    batch_limits_content,
                    merger_content,
                    extractor_content,
                ]
            ),
        )

        for cid, container in self._content_containers.items():
            container.visible = (cid == self._selected_nav)

        return self.content_scroll

    def _build_lm_basic_card(self) -> ft.Control:
        top_row = ft.Row(
            [
                ft.Column([self.controls_map["lm_translator.temperature"]], expand=1),
                ft.Column([self.controls_map["lm_translator.rate_limit.timeout"]], expand=1),
                ft.Column([self.controls_map["lm_translator.rate_limit.sleep_seconds_between_batches"]], expand=1),
                ft.Column([self.controls_map["lm_translator.lm_translate_folder_name"]], expand=2),
            ]
        )
        return self._build_card("基本設定", [top_row])

    def _build_lm_prompts_card(self) -> ft.Control:
        prompts_row = ft.Container(
            height=250,
            content=ft.Row(
                [
                    ft.Column([self.controls_map["lm_translator.patchouli_system_prompt"]], expand=1),
                    ft.VerticalDivider(width=1),
                    ft.Column([self.controls_map["lm_translator.lang_system_prompt"]], expand=1),
                ],
                spacing=10,
            ),
        )
        return self._build_card("提示詞 (System Prompts)", [prompts_row])

    def _build_lm_batch_card(self) -> ft.Control:
        batch_row_1 = ft.Row(
            [
                ft.Column([self.controls_map["lm_translator.initial_batch_size_patchouli"]], expand=1),
                ft.Column([self.controls_map["lm_translator.initial_batch_size_lang"]], expand=1),
                ft.Column([self.controls_map["lm_translator.initial_batch_size_ftb"]], expand=1),
            ]
        )
        batch_row_2 = ft.Row(
            [
                ft.Column([self.controls_map["lm_translator.initial_batch_size_kubejs"]], expand=1),
                ft.Column([self.controls_map["lm_translator.initial_batch_size_md"]], expand=1),
                ft.Column([self.controls_map["lm_translator.min_batch_size"]], expand=1),
                ft.Column([self.controls_map["lm_translator.batch_shrink_factor"]], expand=1),
            ]
        )
        return self._build_card("批次大小與限制", [batch_row_1, batch_row_2])

    def _build_lm_filter_card(self) -> ft.Control:
        lists_row = ft.Container(
            height=200,
            content=ft.Row(
                [
                    ft.Column([self.controls_map["lm_translator.translator.skip_terms"]], expand=1),
                    ft.VerticalDivider(width=1),
                    ft.Column([self.controls_map["lm_translator.translator.translatable_keywords"]], expand=1),
                    ft.VerticalDivider(width=1),
                    ft.Column([self.controls_map["lm_translator.patchouli.dir_names"]], expand=1),
                ],
                spacing=5,
            ),
        )
        return self._build_card("過濾條件與目錄", [lists_row])

    def _build_lm_models_card(self) -> ft.Control:
        models_section = ft.Container(
            bgcolor=theme.GREY_50,
            padding=10,
            border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("模型清單 (Models List)", weight=ft.FontWeight.BOLD),
                            self.new_model_field,
                            self.add_model_button,
                        ]
                    ),
                    self.models_column,
                ]
            ),
        )
        return self._build_card("模型設定", [models_section])

    def _build_lm_keys_card(self) -> ft.Control:
        keys_section = ft.Container(
            bgcolor=theme.GREY_50,
            padding=10,
            border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("API 金鑰 (API Keys)", weight=ft.FontWeight.BOLD),
                            self.add_key_button,
                        ]
                    ),
                    self.keys_column,
                ]
            ),
        )
        return self._build_card("API 金鑰設定", [keys_section])

    def _build_lang_merger_card(self) -> ft.Control:
        return self._build_card(
            "語言合併器設定 (Lang Merger)",
            [
                ft.Row(
                    [
                        ft.Column([self.controls_map["lang_merger.pending_folder_name"]], expand=1),
                        ft.Column([self.controls_map["lang_merger.pending_organized_folder_name"]], expand=1),
                    ]
                ),
                ft.Row(
                    [
                        ft.Column([self.controls_map["lang_merger.filtered_pending_min_count"]], expand=1),
                        ft.Column([self.controls_map["lang_merger.quarantine_folder_name"]], expand=1),
                    ]
                ),
                ft.Container(height=8),
                ft.Text("Patchouli 進階設定", weight=ft.FontWeight.W_600, size=14),
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Column([self.controls_map["lang_merger.patchouli_skip_en_us_when_zh_cn_exists"]], expand=1),
                                ft.Column([self.controls_map["lang_merger.patchouli_effective_translation_threshold"]], expand=1),
                                ft.Column([self.controls_map["lang_merger.zh_en_letter_threshold"]], expand=1),
                            ]
                        ),
                        ft.Text(
                            "允許 zh_cn 觸發跳過 en_us｜en_us 跳過門檻（0.0~1.0，預設 0.5）｜zh 英文含量閾值（預設 2，超過判定為英文）",
                            size=11,
                            color=theme.GREY_600,
                        ),
                    ],
                    spacing=2,
                ),
            ],
        )

    def _build_header(self):
        """建立頁面標題"""
        return build_config_header(self)

    def _build_footer(self):
        """建立底部儲存列"""
        return build_config_footer(self)

    def _build_card(self, title, controls_list):
        """建立設定卡片"""
        return build_config_card(self, title, controls_list)

    def _show_snack_bar(self, message: str, color: str = theme.ERROR):
        """顯示 SnackBar 訊息提示"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def add_model_row(self, model_name: str):
        """新增模型項目到列表"""
        cb = ft.Checkbox(
            label=model_name,
            value=True,
            expand=True,
            label_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_500),
        )
        order_text = ft.Text(
            "00",
            size=12,
            color=theme.GREY_600,
            weight=ft.FontWeight.W_500,
            width=28,
            text_align=ft.TextAlign.RIGHT,
        )
        btn_up = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_UP,
            tooltip="上移",
            icon_size=18,
            on_click=lambda e: self.move_model_row(cb, -1),
        )
        btn_down = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_DOWN,
            tooltip="下移",
            icon_size=18,
            on_click=lambda e: self.move_model_row(cb, +1),
        )
        btn_delete = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip="刪除模型",
            icon_size=18,
            on_click=lambda e: self.remove_model_by_checkbox(cb),
        )

        row = ft.Container(
            padding=12,
            border_radius=8,
            bgcolor=theme.WHITE,
            border=ft.Border.all(1, theme.GREY_200),
            content=ft.Row(
                [
                    order_text,
                    ft.Row([cb], expand=True),
                    ft.Row([btn_up, btn_down, btn_delete], spacing=2),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        row._order_text = order_text
        row._checkbox = cb
        self.models_column.controls.append(row)
        self._refresh_model_order_labels()

    def move_model_row(self, cb: ft.Checkbox, direction: int):
        """移動模型順序（上移/下移）"""
        controls = self.models_column.controls
        idx = next((i for i, r in enumerate(controls) if r._checkbox is cb), None)
        if idx is None:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(controls):
            return
        controls[idx], controls[new_idx] = controls[new_idx], controls[idx]
        self._refresh_model_order_labels()

    def remove_model_by_checkbox(self, cb: ft.Checkbox):
        """刪除勾選的模型項目"""
        row = next((r for r in self.models_column.controls if r._checkbox is cb), None)
        if row:
            self.models_column.controls.remove(row)
        self._refresh_model_order_labels()

    def on_add_model_clicked(self, e):
        """處理新增模型按鈕點擊事件"""
        name = self.new_model_field.value.strip()
        if not name:
            self._show_snack_bar("模型名稱不能為空")
            return
        if any(r._checkbox.label == name for r in self.models_column.controls):
            self._show_snack_bar("此模型已存在")
            return
        self.add_model_row(name)
        self.new_model_field.value = ""
        self.page.update()

    def _build_key_field(self, value: str = ''):
        """建立 API Key 輸入欄位"""
        return build_key_field(value=value)

    def _build_key_row(self, tf: ft.TextField):
        """建立 API Key 列"""
        return build_key_row(self, tf)

    def add_key_row(self):
        """新增 API Key 列"""
        tf = self._build_key_field()
        row = self._build_key_row(tf)
        self.key_fields.append(tf)
        self.keys_column.controls.append(row)
        self.keys_column.update()

    def remove_key_row(self, row: ft.Row):
        """刪除 API Key 列表中的指定列"""
        if row in self.keys_column.controls:
            idx = self.keys_column.controls.index(row)
            self.keys_column.controls.remove(row)
            self.key_fields.pop(idx)
        self.keys_column.update()

    def _refresh_model_order_labels(self):
        """重新整理模型順序編號"""
        for idx, row in enumerate(self.models_column.controls):
            if hasattr(row, "_order_text"):
                row._order_text.value = f"{idx + 1:02d}"
        self.page.update()

    def load_config(self):
        """載入設定檔"""
        config = load_config_json()
        return load_config_into_view(self, config)

    def _success_color(self):
        """取得成功顏色"""
        return theme.SUCCESS

    def save_config_clicked(self, e):
        """儲存設定"""
        return save_config_from_view(
            self,
            load_config_json_fn=load_config_json,
            save_config_json_fn=save_config_json,
            validate_api_keys_from_ui_fn=validate_api_keys_from_ui,
            registry=self._registry,
        )

    @property
    def page(self):
        return self._page

    def set_registry(self, registry):
        """儲存 registry 參考，讓 save_config_clicked 能廣播到其他 views"""
        self._registry = registry