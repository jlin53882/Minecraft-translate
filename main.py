import logging
import threading

import flet as ft

from app.services import cache_rebuild_index_service
from app.ui.view_wrapper import wrap_view
from app.views.cache_view import CacheView
from app.views.config_view import ConfigView
from app.views.extractor_view import ExtractorView
from app.views.lm_view import LMView
from app.views.merge_view import MergeView
from app.views.rules_view import RulesView
from app.views.translation_view import TranslationView

logger = logging.getLogger("main_app")


def bootstrap_runtime():
    """初始化 runtime，但只應在 script entry 被呼叫一次。"""

    # main.py 可以被測試或其他模組 import；
    # runtime 初始化（讀 config / 設定 logging）不能在 import 階段偷跑，
    # 否則 `import main` 就會帶出 side effect。
    from translation_tool.utils.config_manager import load_config, setup_logging

    config = load_config()
    setup_logging(config)

    root_level = logging.getLogger().getEffectiveLevel()
    logger.info(
        f"日誌系統初始化成功，根記錄器級別已設為 {logging.getLevelName(root_level)} ({root_level})。"
    )


def main(page: ft.Page):
    # 這個函式只負責組裝 Flet UI 與頁面切換邏輯；
    # runtime 初始化、logging 設定等啟動責任都留在 bootstrap_runtime()。
    page.title = "Minecraft 模組包繁體化工具"
    page.window_width = 1200
    page.window_height = 850
    page.window_min_width = 1050
    page.window_min_height = 760
    page.window_resizable = True
    page.bgcolor = "surfaceVariant"

    page.theme = ft.Theme(
        font_family="Noto Sans TC",
        use_material3=True,
        color_scheme_seed=ft.Colors.INDIGO,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )
    page.theme_mode = ft.ThemeMode.LIGHT

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    # 所有頁面都先經過 wrap_view()，把一致的卡片外框與邊距集中在 UI 共用層，
    # 避免 main.py 再變回樣式雜物間。
    config_view = wrap_view(ConfigView(page))
    rules_view = wrap_view(RulesView(page))
    cache_view = wrap_view(CacheView(page))
    translation_view = wrap_view(TranslationView(page, file_picker))
    extractor_view = wrap_view(ExtractorView(page, file_picker))
    lm_view = wrap_view(LMView(page, file_picker))
    merge_view = wrap_view(MergeView(page, file_picker))

    # nav_destinations 與 view_window_sizes 共享同一組 selected_index。
    # 後面若有新增/刪除頁面，兩邊要一起維護，不然切頁時視窗尺寸會對錯頁。
    nav_destinations = [
        (ft.Icons.SETTINGS, "設定", config_view),
        (ft.Icons.RULE, "規則", rules_view),
        (ft.Icons.STORAGE, "快取管理", cache_view),
        (ft.Icons.TRANSLATE, "任務 翻譯工具", translation_view),
        (ft.Icons.UNARCHIVE, "jar 提取", extractor_view),
        (ft.Icons.AUTO_AWESOME, "機器翻譯", lm_view),
        (ft.Icons.CALL_MERGE, "檔案合併", merge_view),
    ]
    view_window_sizes = {
        0: (1280, 960),
        1: (1280, 960),
        2: (1360, 940),
        3: (1280, 960),
        4: (1280, 900),
        5: (1280, 920),
        6: (1280, 920),
    }

    def resize_window_for_view(selected_index: int):
        width, height = view_window_sizes.get(selected_index, (1280, 960))
        try:
            page.window.maximized = False
            page.window.width = width
            page.window.height = height
        except Exception:
            page.window_width = width
            page.window_height = height

    content_area = ft.Container(content=nav_destinations[0][2], expand=True)

    def change_view(e):
        selected_index = e.control.selected_index
        _, _, target_view = nav_destinations[selected_index]
        content_area.content = target_view
        resize_window_for_view(selected_index)
        page.update()

    def toggle_theme_mode(e):
        is_light = page.theme_mode == ft.ThemeMode.LIGHT
        page.theme_mode = ft.ThemeMode.DARK if is_light else ft.ThemeMode.LIGHT
        toggle_icon_btn.icon = ft.Icons.LIGHT_MODE if is_light else ft.Icons.DARK_MODE
        toggle_icon_btn.tooltip = "切換為淺色模式" if is_light else "切換為深色模式"
        page.update()

    toggle_icon_btn = ft.IconButton(
        icon=ft.Icons.DARK_MODE,
        tooltip="切換為深色模式",
        on_click=toggle_theme_mode,
    )

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=80,
        min_extended_width=200,
        extended=True,
        group_alignment=-0.95,
        destinations=[
            ft.NavigationRailDestination(icon=icon, selected_icon=icon, label=label)
            for icon, label, _ in nav_destinations
        ],
        on_change=change_view,
        bgcolor=ft.Colors.SURFACE,
        leading=ft.Container(
            content=ft.IconButton(
                ft.Icons.MENU,
                on_click=lambda _: setattr(rail, "extended", not rail.extended) or page.update(),
                tooltip="收合/展開選單",
            ),
            margin=ft.margin.only(bottom=10),
        ),
        trailing=ft.Container(
            content=toggle_icon_btn,
            margin=ft.margin.only(top=10),
        ),
    )

    layout = ft.Row(
        controls=[
            rail,
            ft.VerticalDivider(width=1, thickness=1, color="outlineVariant"),
            content_area,
        ],
        expand=True,
        spacing=0,
    )

    page.add(layout)
    resize_window_for_view(0)
    page.update()

    def _rebuild_index_on_startup():
        # 索引重建放背景執行，避免主畫面啟動時被 I/O 卡住。
        try:
            cache_rebuild_index_service()
            logger.info("啟動時全域搜尋索引重建完成")
        except Exception as ex:
            logger.error(f"啟動時索引重建失敗: {ex}", exc_info=True)

    threading.Thread(target=_rebuild_index_on_startup, daemon=True).start()


if __name__ == "__main__":
    try:
        bootstrap_runtime()
    except Exception as e:
        print(f"致命錯誤：配置或日誌系統初始化失敗！錯誤: {e}")

    ft.app(target=main)
