"""app/views/config_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
import traceback

from app.services_impl.config_service import load_config_json, save_config_json
from app.ui.components import primary_button
from translation_tool.core.lm_config_rules import validate_api_keys_from_ui

class ConfigView(ft.Column):
    """ConfigView 類別。

    用途：封裝與 ConfigView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    DEFAULT_MODELS = {
        "gemini-2.5-flash": True,
    }

    def __init__(self, page: ft.Page):
        # 設定 Root Column 不滾動，為了做 Fixed Footer
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`__init__`, `_init_controls`, `Column`
        
        回傳：None
        """
        super().__init__(expand=True, spacing=0)
        self.page = page
        self.controls_map = {}
        
        # --- 初始化所有控制項 (Controls) ---
        self._init_controls()

        # --- 建立 UI 佈局 (Layout) ---
        # 1. 滾動區域 (主要內容)
        self.scroll_container = ft.Column(
            scroll=ft.ScrollMode.ADAPTIVE,
            expand=True,
            spacing=15,
            controls=[
                self._build_header(),
                ft.Row(
                    controls=[
                        self._build_left_column(),
                        self._build_right_column(),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    spacing=15,   
                ),
                self._build_lang_merger_card(), # Lang Merger 放底部全寬
                ft.Container(height=20) # 底部留白，避免內容被 Footer 遮住太緊
            ]
        )

        # 2. 固定 Footer (Save Bar)
        self.footer = self._build_footer()

        self.controls = [self.scroll_container, self.footer]

        self.load_config()

    def _init_controls(self):
        """初始化所有輸入控制項"""
        # Logging
        self.controls_map['logging.log_level'] = ft.Dropdown(label="日誌等級", options=[ft.dropdown.Option(l) for l in ["DEBUG", "INFO", "WARNING", "ERROR"]], dense=True)
        self.controls_map['logging.log_dir'] = ft.TextField(label="日誌資料夾名稱", dense=True)

        # Translator
        self.controls_map['translator.output_dir_name'] = ft.TextField(label="主要輸出資料夾名稱", dense=True)
        self.controls_map['translator.replace_rules_path'] = ft.TextField(label="替換規則檔案名稱", dense=True)
        self.controls_map['translator.cache_directory'] = ft.TextField(label="快取資料夾名稱", dense=True)
        self.controls_map['translator.enable_cache_saving'] = ft.Checkbox(label="啟用通用翻譯快取")
        self.controls_map['translator.parallel_execution_workers'] = ft.TextField(label="檔案處理多執行緒數量", dense=True)

        # Species Cache
        self.controls_map['species_cache.cache_directory'] = ft.TextField(label="學名快取資料夾", dense=True)
        self.controls_map['species_cache.cache_filename'] = ft.TextField(label="學名存放檔案名稱", dense=True)
        self.controls_map['species_cache.wikipedia_language'] = ft.TextField(label="Wiki 查詢語言", dense=True)
        self.controls_map['species_cache.wikipedia_rate_limit_delay'] = ft.TextField(label="查詢延遲(秒)", dense=True)

        # Output Bundler
        self.controls_map['output_bundler.output_zip_name'] = ft.TextField(label="最終打包 ZIP 檔名", dense=True)

        # Lang Merger
        self.controls_map['lang_merger.pending_folder_name'] = ft.TextField(label="待翻譯資料夾名稱", dense=True)
        self.controls_map['lang_merger.pending_organized_folder_name'] = ft.TextField(label="待翻譯整理資料夾名稱", dense=True)
        self.controls_map['lang_merger.filtered_pending_min_count'] = ft.TextField(label="待翻譯整理json筆數最小出現次數", dense=True)
        self.controls_map['lang_merger.quarantine_folder_name'] = ft.TextField(label="語言合併器格式問題隔離資料夾名稱", dense=True)

        # LM Translator - Basic
        self.controls_map['lm_translator.temperature'] = ft.TextField(label="模型溫度 (Temperature)", dense=True)
        self.controls_map['lm_translator.rate_limit.timeout'] = ft.TextField(label="API 請求 Timeout", dense=True, keyboard_type=ft.KeyboardType.NUMBER)
        self.controls_map['lm_translator.lm_translate_folder_name'] = ft.TextField(label="LM 翻譯輸出資料夾", dense=True)

        # LM Translator - Prompts (Fixed Height in UI)
        self.controls_map['lm_translator.patchouli_system_prompt'] = ft.TextField(
            label="Patchouli 提示詞 (System Prompt)", multiline=True, expand=True, text_size=13
        )
        self.controls_map['lm_translator.lang_system_prompt'] = ft.TextField(
            label="Lang 提示詞 (System Prompt)", multiline=True, expand=True, text_size=13
        )

        # LM Translator - Batch Sizes
        self.controls_map['lm_translator.iniital_batch_size_patchouli'] = ft.TextField(label="Patchouli 請求大小", dense=True)
        self.controls_map['lm_translator.iniital_batch_size_lang'] = ft.TextField(label="Lang 請求大小", dense=True)
        self.controls_map['lm_translator.initial_batch_size_ftb'] = ft.TextField(label="FTB Quests 請求大小", dense=True)
        self.controls_map['lm_translator.initial_batch_size_kubejs'] = ft.TextField(label="KubeJS 請求大小", dense=True)
        self.controls_map['lm_translator.initial_batch_size_md'] = ft.TextField(label="MD 請求大小", dense=True)
        self.controls_map['lm_translator.min_batch_size'] = ft.TextField(label="最小錯誤請求大小", dense=True)
        self.controls_map['lm_translator.batch_shrink_factor'] = ft.TextField(label="錯誤縮小比例", dense=True)

        # LM Translator - Lists
        self.controls_map["lm_translator.translator.skip_terms"] = ft.TextField(
            label="略過翻譯 (Skip Terms)", multiline=True, expand=True, text_size=13, hint_text="每行一個關鍵字"
        )
        self.controls_map["lm_translator.translator.translatable_keywords"] = ft.TextField(
            label="可翻譯欄位 (Keywords)", multiline=True, expand=True, text_size=13, hint_text="每行一個欄位名"
        )
        self.controls_map["lm_translator.patchouli.dir_names"] = ft.TextField(
            label="Patchouli 資料夾", multiline=True, expand=True, text_size=13, hint_text="每行一個資料夾名"
        )

        # LM Translator - Models & Keys
        self.new_model_field = ft.TextField(label="新增模型名稱", hint_text="gemini-2.5-flash", expand=True, dense=True)
        self.add_model_button = ft.IconButton(icon=ft.Icons.ADD, tooltip="新增模型", on_click=self.on_add_model_clicked)
        self.models_column = ft.Column(spacing=5)
        self.controls_map["lm_translator.models"] = self.models_column

        self.add_key_button = ft.IconButton(icon=ft.Icons.ADD, tooltip="新增 API Key", on_click=lambda e: self.add_key_row())
        self.key_fields: list[ft.TextField] = []
        self.keys_column = ft.Column(spacing=5)
        self.controls_map["lm_translator.keys"] = self.keys_column


    # --- UI 建構區塊 ---

    def _build_header(self):
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Container`
        
        回傳：依函式內 return path。
        """
        return ft.Container(
            padding=ft.padding.only(left=5, bottom=10),
            content=ft.Row([
                ft.Icon(ft.Icons.SETTINGS_APPLICATIONS, size=28, color=ft.Colors.BLUE_GREY_800),
                ft.Text("全域設定 (Global Settings)", style=ft.TextThemeStyle.HEADLINE_MEDIUM, color=ft.Colors.BLUE_GREY_900)
            ])
        )

    def _build_left_column(self):
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Column`
        
        回傳：依函式內 return path。
        """
        return ft.Column(
            expand=1,
            spacing=15,
            controls=[
                self._build_card("日誌設定 (Logging)", [
                    self.controls_map['logging.log_level'],
                    self.controls_map['logging.log_dir']
                ]),
                self._build_card("翻譯與處理設定 (Translator)", [
                    self.controls_map['translator.output_dir_name'],
                    self.controls_map['translator.replace_rules_path'],
                    self.controls_map['translator.cache_directory'],
                    self.controls_map['translator.parallel_execution_workers'],
                    self.controls_map['translator.enable_cache_saving'],
                ]),
                self._build_card("學名查詢設定 (Species Cache)", [
                    self.controls_map['species_cache.cache_directory'],
                    self.controls_map['species_cache.cache_filename'],
                    self.controls_map['species_cache.wikipedia_language'],
                    self.controls_map['species_cache.wikipedia_rate_limit_delay']
                ]),
                self._build_card("成品打包器 (Output Bundler)", [
                    self.controls_map['output_bundler.output_zip_name']
                ]),
            ]
        )

    def _build_right_column(self):
        # LM Translator Section content
        
        # 1. Top Params
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Row`, `Container`
        
        回傳：依函式內 return path。
        """
        top_row = ft.Row([
            ft.Column([self.controls_map['lm_translator.temperature']], expand=1),
            ft.Column([self.controls_map['lm_translator.rate_limit.timeout']], expand=1),
            ft.Column([self.controls_map['lm_translator.lm_translate_folder_name']], expand=2),
        ])

        # 2. Prompts (Side-by-side with fixed height)
        prompts_row = ft.Container(
            height=250, # Fixed height to avoid explosion
            content=ft.Row([
                ft.Column([self.controls_map['lm_translator.patchouli_system_prompt']], expand=1),
                ft.VerticalDivider(width=1),
                ft.Column([self.controls_map['lm_translator.lang_system_prompt']], expand=1),
            ], spacing=10)
        )

        # 3. Batch Sizes (2 Rows)
        batch_row_1 = ft.Row([
            ft.Column([self.controls_map['lm_translator.iniital_batch_size_patchouli']], expand=1),
            ft.Column([self.controls_map['lm_translator.iniital_batch_size_lang']], expand=1),
            ft.Column([self.controls_map['lm_translator.initial_batch_size_ftb']], expand=1),
        ])
        batch_row_2 = ft.Row([
            ft.Column([self.controls_map['lm_translator.initial_batch_size_kubejs']], expand=1),
            ft.Column([self.controls_map['lm_translator.initial_batch_size_md']], expand=1),
            ft.Column([self.controls_map['lm_translator.min_batch_size']], expand=1),
            ft.Column([self.controls_map['lm_translator.batch_shrink_factor']], expand=1),
        ])

        # 4. Lists (Side-by-side with fixed height)
        lists_row = ft.Container(
            height=200,
            content=ft.Row([
                ft.Column([self.controls_map["lm_translator.translator.skip_terms"]], expand=1),
                ft.VerticalDivider(width=1),
                ft.Column([self.controls_map["lm_translator.translator.translatable_keywords"]], expand=1),
                ft.VerticalDivider(width=1),
                ft.Column([self.controls_map["lm_translator.patchouli.dir_names"]], expand=1),
            ], spacing=5)
        )

        # 5. Models & Keys
        models_section = ft.Container(
            bgcolor=ft.Colors.GREY_50, padding=10, border_radius=8,
            content=ft.Column([
                ft.Row([ft.Text("Models List", weight=ft.FontWeight.BOLD), self.new_model_field, self.add_model_button]),
                self.models_column
            ])
        )

        keys_section = ft.Container(
            bgcolor=ft.Colors.GREY_50, padding=10, border_radius=8,
            content=ft.Column([
                ft.Row([ft.Text("API Keys", weight=ft.FontWeight.BOLD), self.add_key_button]),
                self.keys_column
            ])
        )

        return ft.Column(
            expand=2,
            controls=[
                self._build_card("大型語言模型設定 (LM Translator)", [
                    top_row,
                    ft.Divider(height=20, color=ft.Colors.GREY_200),
                    ft.Text("System Prompts", weight=ft.FontWeight.BOLD, size=14, color=ft.Colors.GREY_700),
                    prompts_row,
                    ft.Divider(height=20, color=ft.Colors.GREY_200),
                    ft.Text("Batch Sizes & Limits", weight=ft.FontWeight.BOLD, size=14, color=ft.Colors.GREY_700),
                    batch_row_1,
                    batch_row_2,
                    ft.Divider(height=20, color=ft.Colors.GREY_200),
                    ft.Text("Filtering & Directories", weight=ft.FontWeight.BOLD, size=14, color=ft.Colors.GREY_700),
                    lists_row,
                    ft.Divider(height=20, color=ft.Colors.GREY_200),
                    models_section,
                    keys_section
                ])
            ]
        )

    def _build_lang_merger_card(self):
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`_build_card`
        
        回傳：依函式內 return path。
        """
        return self._build_card("語言合併器設定 (Lang Merger)", [
            ft.Row([
                ft.Column([self.controls_map['lang_merger.pending_folder_name']], expand=1),
                ft.Column([self.controls_map['lang_merger.pending_organized_folder_name']], expand=1),
            ]),
            ft.Row([
                ft.Column([self.controls_map['lang_merger.filtered_pending_min_count']], expand=1),
                ft.Column([self.controls_map['lang_merger.quarantine_folder_name']], expand=1),
            ])
        ])

    def _build_footer(self):
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Container`
        
        回傳：依函式內 return path。
        """
        return ft.Container(
            padding=ft.padding.symmetric(horizontal=20, vertical=10),
            bgcolor=ft.Colors.WHITE,
            border=ft.border.only(top=ft.BorderSide(1, ft.Colors.GREY_300)),
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=5, color=ft.Colors.BLACK12, offset=ft.Offset(0, -1)),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text("提示：修改後請務必點擊儲存", color=ft.Colors.GREY_600, size=12),
                    primary_button(
                        "儲存所有設定",
                        icon=ft.Icons.SAVE,
                        tooltip="寫入 config.json（請確認 API Keys 有填好）",
                        on_click=self.save_config_clicked,
                    )
                ]
            )
        )

    def _build_card(self, title, controls_list):
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Card`
        
        回傳：依函式內 return path。
        """
        return ft.Card(
            elevation=2,
            surface_tint_color=ft.Colors.WHITE,
            content=ft.Container(
                padding=15,
                content=ft.Column([
                    ft.Text(title, style=ft.TextThemeStyle.TITLE_MEDIUM, color=ft.Colors.BLUE_800, weight=ft.FontWeight.BOLD),
                    ft.Divider(height=10, thickness=1, color=ft.Colors.BLUE_50),
                    *controls_list
                ], spacing=12)
            )
        )

    # --- 邏輯功能 (與原程式碼相同，僅移動位置) ---

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_600):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`SnackBar`
        
        回傳：None
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def add_model_row(self, model_name: str):
        """加入此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Checkbox`, `Text`, `IconButton`
        
        回傳：None
        """
        cb = ft.Checkbox(label=model_name, value=True, expand=True, label_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_500))
        order_text = ft.Text("00", size=12, color=ft.Colors.GREY_600, weight=ft.FontWeight.W_500, width=28, text_align=ft.TextAlign.RIGHT)
        btn_up = ft.IconButton(icon=ft.Icons.KEYBOARD_ARROW_UP, tooltip="上移", icon_size=18, on_click=lambda e: self.move_model_row(cb, -1))
        btn_down = ft.IconButton(icon=ft.Icons.KEYBOARD_ARROW_DOWN, tooltip="下移", icon_size=18, on_click=lambda e: self.move_model_row(cb, +1))
        btn_delete = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="刪除模型", icon_size=18, on_click=lambda e: self.remove_model_by_checkbox(cb))

        row = ft.Container(
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            border_radius=8,
            bgcolor=ft.Colors.WHITE,
            border=ft.border.all(1, ft.Colors.GREY_200),
            content=ft.Row(
                [order_text, ft.Row([cb], expand=True), ft.Row([btn_up, btn_down, btn_delete], spacing=2)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        row._order_text = order_text
        row._checkbox = cb
        self.models_column.controls.append(row)
        self._refresh_model_order_labels()

    def move_model_row(self, cb: ft.Checkbox, direction: int):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`next`, `_refresh_model_order_labels`
        
        回傳：None
        """
        controls = self.models_column.controls
        idx = next((i for i, r in enumerate(controls) if r._checkbox is cb), None)
        if idx is None: return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(controls): return
        controls[idx], controls[new_idx] = controls[new_idx], controls[idx]
        self._refresh_model_order_labels()

    def remove_model_by_checkbox(self, cb: ft.Checkbox):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`next`, `_refresh_model_order_labels`
        
        回傳：None
        """
        row = next((r for r in self.models_column.controls if r._checkbox is cb), None)
        if row: self.models_column.controls.remove(row)
        self._refresh_model_order_labels()

    def on_add_model_clicked(self, e):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`strip`, `add_model_row`
        
        回傳：None
        """
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

    def add_key_row(self):
        """加入此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`TextField`, `Row`
        
        回傳：None
        """
        tf = ft.TextField(label="API Key", password=True, can_reveal_password=True, expand=True, dense=True)
        row = ft.Row(controls=[tf, ft.IconButton(icon=ft.Icons.DELETE, on_click=lambda e: self.remove_key_row(row))])
        self.key_fields.append(tf)
        self.keys_column.controls.append(row)
        self.keys_column.update()

    def remove_key_row(self, row: ft.Row):
        """處理此函式的工作（細節以程式碼為準）。
        
        回傳：None
        """
        if row in self.keys_column.controls:
            idx = self.keys_column.controls.index(row)
            self.keys_column.controls.remove(row)
            self.key_fields.pop(idx)
        self.keys_column.update()

    def _refresh_model_order_labels(self):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`enumerate`
        
        回傳：None
        """
        for idx, row in enumerate(self.models_column.controls):
            if hasattr(row, "_order_text"):
                row._order_text.value = f"{idx + 1:02d}"
        self.page.update()

    def load_config(self):
        """載入此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`load_config_json`
        
        回傳：None
        """
        config = load_config_json()
        log_cfg = config.get("logging", {})
        trans_cfg = config.get("translator", {})
        species_cfg = config.get("species_cache", {})
        lm_cfg = config.get("lm_translator", {})
        bundle_cfg = config.get("output_bundler", {})
        
        self.controls_map['logging.log_level'].value = log_cfg.get("log_level")
        self.controls_map['logging.log_dir'].value = log_cfg.get("log_dir")
        self.controls_map['translator.output_dir_name'].value = trans_cfg.get("output_dir_name","zh_tw_generated")
        self.controls_map['translator.replace_rules_path'].value = trans_cfg.get("replace_rules_path","replace_rules.json")
        self.controls_map['translator.cache_directory'].value = trans_cfg.get("cache_directory","快取資料夾")
        self.controls_map['translator.enable_cache_saving'].value = trans_cfg.get("enable_cache_saving")
        self.controls_map['translator.parallel_execution_workers'].value = str(trans_cfg.get("parallel_execution_workers","4"))
        self.controls_map['species_cache.cache_directory'].value = species_cfg.get("cache_directory","學名資料庫")
        self.controls_map['species_cache.cache_filename'].value = species_cfg.get("cache_filename","species_cache.tsv")
        self.controls_map['species_cache.wikipedia_language'].value = species_cfg.get("wikipedia_language")
        self.controls_map['species_cache.wikipedia_rate_limit_delay'].value = str(species_cfg.get("wikipedia_rate_limit_delay"))
        self.controls_map['lm_translator.temperature'].value = str(lm_cfg.get("temperature"))
        self.controls_map['lm_translator.rate_limit.timeout'].value = str(lm_cfg.get("rate_limit", {}).get("timeout", "600"))
        self.controls_map['output_bundler.output_zip_name'].value = bundle_cfg.get("output_zip_name")
        self.controls_map['lang_merger.pending_folder_name'].value = config.get("lang_merger", {}).get("pending_folder_name", "待翻譯")
        self.controls_map['lang_merger.pending_organized_folder_name'].value = config.get("lang_merger", {}).get("pending_organized_folder_name", "待翻譯整理需翻譯")
        self.controls_map['lang_merger.filtered_pending_min_count'].value = str(config.get("lang_merger", {}).get("filtered_pending_min_count", 2))
        self.controls_map['lm_translator.lm_translate_folder_name'].value = str(config.get("lm_translator", {}).get("lm_translate_folder_name", "LM翻譯後"))
        self.controls_map['lm_translator.patchouli_system_prompt'].value = str(config.get("lm_translator", {}).get("patchouli_system_prompt", "你是專業的 Minecraft patchouli 手冊翻譯員，專精於《當個創世神》繁體中文（台灣）官方譯名或台灣用語的翻譯。"))
        self.controls_map['lm_translator.lang_system_prompt'].value = str(config.get("lm_translator", {}).get("lang_system_prompt", "你是專業的 Minecraft Lang翻譯員，你正在翻譯 Minecraft 語言檔案（JSON格式）。"))
        self.controls_map['lang_merger.quarantine_folder_name'].value = config.get("lang_merger", {}).get("quarantine_folder_name", "skipped_json")
        self.controls_map['lm_translator.iniital_batch_size_patchouli'].value = int(config.get("lm_translator", {}).get("iniital_batch_size_patchouli", 100))
        self.controls_map['lm_translator.iniital_batch_size_lang'].value = int(config.get("lm_translator", {}).get("iniital_batch_size_lang", 300))
        self.controls_map['lm_translator.initial_batch_size_ftb'].value = int(config.get("lm_translator", {}).get("initial_batch_size_ftb", 100))
        self.controls_map['lm_translator.initial_batch_size_kubejs'].value = int(config.get("lm_translator", {}).get("initial_batch_size_kubejs", 200))
        self.controls_map['lm_translator.initial_batch_size_md'].value = int(config.get("lm_translator", {}).get("initial_batch_size_md", 100))
        self.controls_map['lm_translator.min_batch_size'].value = int(config.get("lm_translator", {}).get("min_batch_size", 50))
        self.controls_map['lm_translator.batch_shrink_factor'].value = float(config.get("lm_translator", {}).get("batch_shrink_factor", 0.75))
        self.controls_map['lm_translator.patchouli.dir_names'].value = "\n".join(config.get("lm_translator", {}).get("patchouli", {}).get("dir_names", "patchouli_books"))
        self.controls_map['lm_translator.translator.skip_terms'].value = "\n".join(config.get("lm_translator", {}).get("translator", {}).get("skip_terms", ["api documentation"]))
        self.controls_map['lm_translator.translator.translatable_keywords'].value = "\n".join(config.get("lm_translator", {}).get("translator", {}).get("translatable_keywords", "text"))
        
        # Models
        self.models_column.controls.clear()
        models_cfg = lm_cfg.get("models")
        if "models" not in lm_cfg:
            models_cfg = {name: {"enabled": enabled} for name, enabled in self.DEFAULT_MODELS.items()}
        else:
            models_cfg = models_cfg or {}
        for name, cfg in models_cfg.items():
            self.add_model_row(name)
            self.models_column.controls[-1]._checkbox.value = bool(cfg.get("enabled", False))

        # Keys
        self.key_fields.clear()
        self.keys_column.controls.clear()
        for key in lm_cfg.get("keys", []):
            tf = ft.TextField(value=key, password=True, can_reveal_password=True, expand=True, dense=True)
            row = ft.Row(controls=[tf, ft.IconButton(icon=ft.Icons.DELETE, on_click=lambda e: self.remove_key_row(row))])
            self.key_fields.append(tf)
            self.keys_column.controls.append(row)   

    def save_config_clicked(self, e):
        """保存此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`load_config_json`, `save_config_json`, `load_config`
        
        回傳：None
        """
        new_config = load_config_json()  
        try:
            new_config["logging"]["log_level"] = self.controls_map['logging.log_level'].value
            new_config["logging"]["log_dir"] = self.controls_map['logging.log_dir'].value
            new_config["translator"]["output_dir_name"] = self.controls_map['translator.output_dir_name'].value
            new_config["translator"]["replace_rules_path"] = self.controls_map['translator.replace_rules_path'].value
            new_config["translator"]["cache_directory"] = self.controls_map['translator.cache_directory'].value
            new_config["translator"]["enable_cache_saving"] = self.controls_map['translator.enable_cache_saving'].value
            new_config["translator"]["parallel_execution_workers"] = int(self.controls_map['translator.parallel_execution_workers'].value)
            new_config["species_cache"]["cache_directory"] = self.controls_map['species_cache.cache_directory'].value
            new_config["species_cache"]["cache_filename"] = self.controls_map['species_cache.cache_filename'].value
            new_config["species_cache"]["wikipedia_language"] = self.controls_map['species_cache.wikipedia_language'].value
            new_config["species_cache"]["wikipedia_rate_limit_delay"] = float(self.controls_map['species_cache.wikipedia_rate_limit_delay'].value)
            new_config["lm_translator"]["temperature"] = float(self.controls_map['lm_translator.temperature'].value)
            new_config["lm_translator"]["rate_limit"]["timeout"] = int(self.controls_map['lm_translator.rate_limit.timeout'].value)
            new_config["output_bundler"]["output_zip_name"] = self.controls_map['output_bundler.output_zip_name'].value
            new_config["lang_merger"]["pending_folder_name"] = self.controls_map['lang_merger.pending_folder_name'].value
            new_config["lang_merger"]["pending_organized_folder_name"] = self.controls_map['lang_merger.pending_organized_folder_name'].value
            new_config["lang_merger"]["filtered_pending_min_count"] = int(self.controls_map['lang_merger.filtered_pending_min_count'].value)
            new_config["lm_translator"]["lm_translate_folder_name"] = str(self.controls_map['lm_translator.lm_translate_folder_name'].value)
            new_config["lang_merger"]["quarantine_folder_name"] = self.controls_map['lang_merger.quarantine_folder_name'].value
            new_config["lm_translator"]["patchouli_system_prompt"]=self.controls_map['lm_translator.patchouli_system_prompt'].value
            new_config["lm_translator"]["lang_system_prompt"] = self.controls_map['lm_translator.lang_system_prompt'].value
            new_config["lm_translator"]["iniital_batch_size_patchouli"] = int(self.controls_map['lm_translator.iniital_batch_size_patchouli'].value)
            new_config["lm_translator"]["iniital_batch_size_lang"] = int(self.controls_map['lm_translator.iniital_batch_size_lang'].value)
            new_config["lm_translator"]["initial_batch_size_ftb"] = int(self.controls_map['lm_translator.initial_batch_size_ftb'].value)
            new_config["lm_translator"]["initial_batch_size_kubejs"] = int(self.controls_map['lm_translator.initial_batch_size_kubejs'].value)
            new_config["lm_translator"]["initial_batch_size_md"]= int(self.controls_map['lm_translator.initial_batch_size_md'].value)
            new_config["lm_translator"]["min_batch_size"] = int(self.controls_map['lm_translator.min_batch_size'].value)
            new_config["lm_translator"]["batch_shrink_factor"] =float(self.controls_map['lm_translator.batch_shrink_factor'].value)
            
            new_config["lm_translator"]["patchouli"]["dir_names"] = [
                line.strip() for line in self.controls_map['lm_translator.patchouli.dir_names'].value.splitlines() if line.strip()
            ]
            new_config["lm_translator"]["translator"]["skip_terms"] = [
                line.strip() for line in self.controls_map['lm_translator.translator.skip_terms'].value.splitlines() if line.strip()
            ]
            new_config["lm_translator"]["translator"]["translatable_keywords"] = [
                line.strip() for line in self.controls_map['lm_translator.translator.translatable_keywords'].value.splitlines() if line.strip()
            ]

            # 1️⃣ 從 UI 收集 keys
            api_keys = [key_field.value.strip() for key_field in self.key_fields if key_field.value and key_field.value.strip()]
            # 2️⃣ 驗證key
            validate_api_keys_from_ui(api_keys)
            # 3️⃣ 寫回 config
            new_config["lm_translator"]["keys"] = api_keys      
            
            models = {}
            for row in self.models_column.controls:
                cb = row._checkbox
                models[cb.label] = {"enabled": bool(cb.value)}
            new_config["lm_translator"]["models"] = models                               

        except (ValueError, TypeError, RuntimeError) as err:
            traceback.print_exc()
            self._show_snack_bar(f"❌ 發生錯誤：{type(err).__name__}: {err}")
            return
        save_config_json(new_config)
        self.load_config()
        self._show_snack_bar("✅ 設定已成功儲存！", ft.Colors.GREEN_600)
