# /minecraft_translator_flet/main.py

import flet as ft
import logging
import threading

# 🌟 關鍵修正 1：導入配置和日誌設定函式 🌟
# 假設 config_manager.py 的路徑是 translation_tool/utils/config_manager.py
from translation_tool.utils.config_manager import load_config, setup_logging

# 導入「目前啟用」的 View 模組
# 注意：main.py 內被註解停用的頁面（查詢/品管/輸出打包/Icon 校對）
# 代表你打算重寫或刪除，所以這裡先不 import，也不實例化，避免後續誤改。
from app.views.translation_view import TranslationView
from app.views.extractor_view import ExtractorView
from app.views.rules_view import RulesView
from app.views.config_view import ConfigView
from app.views.merge_view import MergeView
from app.views.lm_view import LMView
from app.views.cache_view import CacheView
from app.services import cache_rebuild_index_service

# UI 共用：統一卡片外框（從 main.py 抽出，集中管理）
from app.ui.view_wrapper import wrap_view

# 初始化一個全域 Logger 以便在 main 中記錄
logger = logging.getLogger("main_app")

def main(page: ft.Page):
    page.title = "Minecraft 模組包繁體化工具"
    page.window_width = 1200
    page.window_height = 850
    page.window_min_width = 1050
    page.window_min_height = 760
    page.window_resizable = True
    page.bgcolor = "surfaceVariant" # 設定背景色，讓內容卡片突顯

    # 使用 Material 3 主題
    page.theme = ft.Theme(
        font_family="Noto Sans TC",
        use_material3=True,
        color_scheme_seed=ft.Colors.INDIGO, # 使用更現代的主色調
        visual_density=ft.VisualDensity.COMFORTABLE,
    )
    page.theme_mode = ft.ThemeMode.LIGHT # 預設淺色模式

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    # 1. 初始化內容視圖的包裝器（統一卡片外框）
    # 目的：讓每個 View 看起來像放在一張「紙/卡片」上（統一邊距、圓角、陰影）。
    # 重要：wrapper 已抽到 app.ui.view_wrapper，避免 main.py 變成樣式集中地。

    # 初始化各個「目前啟用」的功能頁面
    # 注意：被註解停用的頁面（查詢/品管/輸出打包/Icon 校對）先不實例化。
    config_view = wrap_view(ConfigView(page))
    rules_view = wrap_view(RulesView(page))
    cache_view = wrap_view(CacheView(page))

    translation_view = wrap_view(TranslationView(page, file_picker))
    extractor_view = wrap_view(ExtractorView(page, file_picker))
    lm_view = wrap_view(LMView(page, file_picker))
    merge_view = wrap_view(MergeView(page, file_picker))

    # 定義導覽結構：(Icon, Label, ViewObject)
    nav_destinations = [
        (ft.Icons.SETTINGS, "設定", config_view),
        (ft.Icons.RULE, "規則", rules_view),
        (ft.Icons.STORAGE, "快取管理", cache_view),
        #(ft.Icons.SEARCH, "查詢", lookup_view),
        #(ft.Icons.CHECK_CIRCLE, "品管", qc_view),
        (ft.Icons.TRANSLATE, "任務 翻譯工具", translation_view),
        (ft.Icons.UNARCHIVE, "jar 提取", extractor_view),
        (ft.Icons.AUTO_AWESOME, "機器翻譯", lm_view),
        (ft.Icons.CALL_MERGE, "檔案合併", merge_view),
        #(ft.Icons.ARCHIVE, "輸出打包", bundler_view),
        #(ft.Icons.IMAGE_SEARCH, "Icon 校對", icon_preview_view),
    ]
    view_window_sizes = {
        0: (1280, 960),  # 設定
        1: (1280, 960),  # 簡繁替換規則
        2: (1360, 940),  # 快取管理
        3: (1280, 960),  # 任務翻譯工具
        4: (1280, 900),  # jar提取
        5: (1280, 920),  # 機器翻譯
        6: (1280, 920),  # 檔案合併
    }

    def resize_window_for_view(selected_index: int):
        width, height = view_window_sizes.get(selected_index, (1280, 960))
        try:
            page.window.maximized = False
            page.window.width = width
            page.window.height = height
        except Exception:
            # ??????
            page.window_width = width
            page.window_height = height
    # 內容顯示區
    # 預設顯示第一個視圖 (設定)
    content_area = ft.Container(
        content=nav_destinations[0][2], 
        expand=True
    )

    # 切換視圖的函式
    def change_view(e):
        selected_index = e.control.selected_index
        _, _, target_view = nav_destinations[selected_index]
        content_area.content = target_view
        resize_window_for_view(selected_index)
        page.update()

    # 切換深色/淺色模式的函式
    def toggle_theme_mode(e):
        is_light = page.theme_mode == ft.ThemeMode.LIGHT
        page.theme_mode = ft.ThemeMode.DARK if is_light else ft.ThemeMode.LIGHT
        
        # 更新按鈕圖示
        toggle_icon_btn.icon = ft.Icons.LIGHT_MODE if is_light else ft.Icons.DARK_MODE
        toggle_icon_btn.tooltip = "切換為淺色模式" if is_light else "切換為深色模式"
        page.update()

    # 建立主題切換按鈕
    toggle_icon_btn = ft.IconButton(
        icon=ft.Icons.DARK_MODE,
        tooltip="切換為深色模式",
        on_click=toggle_theme_mode
    )

    # 2. 建立 NavigationRail (側邊導覽列)
    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=80,
        min_extended_width=200,
        extended=True, # 預設展開
        group_alignment=-0.95, # 項目靠上對齊
        destinations=[
            ft.NavigationRailDestination(
                icon=icon, 
                selected_icon=icon, 
                label=label
            ) for icon, label, _ in nav_destinations
        ],
        on_change=change_view,
        bgcolor=ft.Colors.SURFACE, # 導覽列背景
        
        # 導覽列首部元件 (收合按鈕)
        leading=ft.Container(
            content=ft.IconButton(
                ft.Icons.MENU, 
                on_click=lambda _: setattr(rail, 'extended', not rail.extended) or page.update(),
                tooltip="收合/展開選單"
            ),
            margin=ft.margin.only(bottom=10)
        ),
        
        # 導覽列尾部元件 (主題切換)
        trailing=ft.Container(
            content=toggle_icon_btn,
            margin=ft.margin.only(top=10)
        ),
    )

    # 3. 最終佈局
    # 使用 Row 將導覽列與內容區左右並排
    layout = ft.Row(
        controls=[
            rail,
            ft.VerticalDivider(width=1, thickness=1, color="outlineVariant"), # 分隔線
            content_area,
        ],
        expand=True,
        spacing=0,
    )

    page.add(layout)
    resize_window_for_view(0)
    page.update()

    # 啟動時自動重建全域搜尋索引（背景執行，避免卡住 UI）
    def _rebuild_index_on_startup():
        try:
            cache_rebuild_index_service()
            logger.info("啟動時全域搜尋索引重建完成")
        except Exception as ex:
            logger.error(f"啟動時索引重建失敗: {ex}", exc_info=True)

    threading.Thread(target=_rebuild_index_on_startup, daemon=True).start()

if __name__ == "__main__":
    # ----------------------------------------------------
    # 🌟 關鍵修正 2：在 Flet 啟動前初始化日誌系統 🌟
    # 這是為了確保所有被導入的模組（如 lang_merger）在實例化 Logger 時，
    # 能夠正確繼承到由 setup_logging 設定的日誌級別（例如 DEBUG）。
    try:
        # 1. 載入配置 (這將讀取 config.json)
        # config 變數將包含所有應用程式設定，包括 log_level
        config = load_config()
        
        # 2. 設定日誌 (根據配置中的 log_level 調整根記錄器)
        setup_logging(config)
        
        # 診斷：確認根記錄器是否已設定成功
        root_level = logging.getLogger().getEffectiveLevel()
        logger.info(f"日誌系統初始化成功，根記錄器級別已設為 {logging.getLevelName(root_level)} ({root_level})。")
            
    except Exception as e:
        # 如果配置或日誌設定失敗，則無法記錄日誌，只能用 print
        print(f"致命錯誤：配置或日誌系統初始化失敗！錯誤: {e}")
        # 即使失敗，也嘗試讓應用程式運行，但日誌會不正常
    # ----------------------------------------------------
    
    # 啟動 Flet 應用程式
    ft.app(target=main)

    # 網頁版
    #ft.app(
    #    target=main,
    #    view=ft.WEB_BROWSER,  # ⭐ 重點
    #    port=8550,            # 可選
    #)