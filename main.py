"""main.py（Flet App 入口）

責任：
- 組裝各個 View（設定/規則/快取/翻譯/...）並處理切頁。
- 只在 `__main__` 路徑呼叫 bootstrap_runtime() 做一次性的 runtime 初始化。

維護注意：
- main.py 可能被測試 import；因此不能在 import 階段就做 logging/config 初始化。
- 快取搜尋索引重建會在啟動後用背景 thread 執行，避免主畫面卡住。
"""

# =========================================================
# Window Constants - 視窗尺寸常數
# =========================================================
WINDOW_WIDTH_DEFAULT = 1200  # 預設視窗寬度
WINDOW_HEIGHT_DEFAULT = 850  # 預設視窗高度
WINDOW_MIN_WIDTH = 1050  # 視窗最小寬度
WINDOW_MIN_HEIGHT = 760  # 視窗最小高度

import logging

import flet as ft

from app.startup_tasks import start_background_startup_tasks
from app.ui.keyboard_shortcuts import create_keyboard_handler
from app.ui.quick_jump import show_quick_jump_panel
from app.view_registry import build_navigation_destinations, build_view_registry, get_window_size

logger = logging.getLogger("main_app")


def bootstrap_runtime():
    """初始化 runtime（config + logging），只應在 script entry 被呼叫一次。"""
    from translation_tool.utils.config_manager import load_config, setup_logging

    config = load_config()
    setup_logging(config)

    root_level = logging.getLogger().getEffectiveLevel()
    logger.info(
        f"日誌系統初始化成功，根記錄器級別已設為 {logging.getLevelName(root_level)} ({root_level})。"
    )


def main(page: ft.Page):
    logger.info("=== main() 開始執行 ===")
    try:
        page.title = "Minecraft 模組包繁體化工具"
        page.window_width = WINDOW_WIDTH_DEFAULT
        page.window_height = WINDOW_HEIGHT_DEFAULT
        page.window_min_width = WINDOW_MIN_WIDTH
        page.window_min_height = WINDOW_MIN_HEIGHT
        page.window_resizable = True
        page.bgcolor = "surfaceVariant"

        page.theme = ft.Theme(
            font_family="Noto Sans TC",
            use_material3=True,
            color_scheme_seed=ft.Colors.INDIGO,
            visual_density=ft.VisualDensity.COMFORTABLE,
        )
        page.theme_mode = ft.ThemeMode.LIGHT

        logger.info("建立 FilePicker...")
        file_picker = ft.FilePicker()
        logger.info(f"FilePicker 類型: {type(file_picker)}")
        logger.info("FilePicker 已建立（自動註冊服務，不需添加到 page）")

        logger.info("建立 view registry...")
        registry = build_view_registry(page, file_picker)
        logger.info(f"registry 建立完成，共 {len(registry)} 個 view")

        def resize_window_for_view(view_key: str):
            logger.info(f"resize_window_for_view: {view_key}")
            width, height = get_window_size(view_key)
            try:
                page.window.maximized = False
                page.window.width = width
                page.window.height = height
            except Exception:
                page.window_width = width
                page.window_height = height

        content_area = ft.Container(content=registry[0]['view'], expand=True)
        logger.info(f"content_area 建立完成，初始 view: {registry[0]['key']}")

        # 建立鍵盤快捷鍵處理器
        keyboard_handler = create_keyboard_handler(
            page, registry, lambda idx: change_view_by_index(idx)
        )

        def change_view(e):
            logger.info(f"change_view 觸發, selected_index={e.control.selected_index}")
            selected_index = e.control.selected_index
            item = registry[selected_index]
            content_area.content = item['view']
            resize_window_for_view(item['key'])
            page.update()

        def change_view_by_index(index: int):
            """透過索引直接切換視圖"""
            logger.info(f"change_view_by_index: {index}")
            if 0 <= index < len(registry):
                item = registry[index]
                content_area.content = item['view']
                resize_window_for_view(item['key'])
                rail.selected_index = index
                page.update()

        # 註冊鍵盤事件處理
        page.on_keyboard_event = keyboard_handler.handle_keyboard

        def toggle_theme_mode(e):
            is_light = page.theme_mode == ft.ThemeMode.LIGHT
            page.theme_mode = ft.ThemeMode.DARK if is_light else ft.ThemeMode.LIGHT
            toggle_icon_btn.icon = ft.Icons.LIGHT_MODE if is_light else ft.ThemeMode.DARK
            toggle_icon_btn.tooltip = "切換為淺色模式" if is_light else "切換為深色模式"
            page.update()

        toggle_icon_btn = ft.IconButton(
            icon=ft.Icons.DARK_MODE,
            tooltip="切換為深色模式",
            on_click=toggle_theme_mode,
        )

        # 快速跳轉功能
        def on_quick_jump(e):
            show_quick_jump_panel(page, registry, change_view_by_index)

        # 快捷鍵綁定搜尋功能
        keyboard_handler.set_search_callback(on_quick_jump)

        logger.info("建立 NavigationRail...")
        destinations = build_navigation_destinations(registry)
        logger.info(f"NavigationRail destinations 建立完成，共 {len(destinations)} 個")

        rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            min_extended_width=200,
            extended=True,
            group_alignment=-0.95,
            destinations=destinations,
            on_change=change_view,
            bgcolor=ft.Colors.SURFACE,
            leading=ft.Container(
                content=ft.Column(
                    [
                        ft.IconButton(
                            ft.Icons.MENU,
                            on_click=lambda _: (
                                setattr(rail, "extended", not rail.extended) or page.update()
                            ),
                            tooltip="收合/展開選單",
                        ),
                        ft.IconButton(
                            ft.Icons.SEARCH,
                            on_click=on_quick_jump,
                            tooltip="快速跳轉 (Ctrl+P)",
                        ),
                    ],
                    spacing=5,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                margin=ft.Margin.only(bottom=10),
            ),
            trailing=ft.Container(
                content=toggle_icon_btn,
                margin=ft.Margin.only(top=10),
            ),
        )
        logger.info("NavigationRail 建立完成")

        layout = ft.Row(
            controls=[
                rail,
                ft.VerticalDivider(width=1, thickness=1, color="outlineVariant"),
                content_area,
            ],
            expand=True,
            spacing=0,
        )

        logger.info("新增 layout 到 page...")
        page.add(layout)
        resize_window_for_view(registry[0]['key'])
        logger.info("呼叫 page.update()...")
        page.update()
        logger.info("=== main() 初始化完成 ===")

        start_background_startup_tasks()
    except Exception as e:
        logger.error(f"main() 執行時發生錯誤: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        bootstrap_runtime()
    except Exception as e:
        print(f"致命錯誤：配置或日誌系統初始化失敗！錯誤: {e}")

    ft.app(target=main)
