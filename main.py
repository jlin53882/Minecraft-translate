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
WINDOW_MIN_WIDTH = 1050  # 視窗最小寬度（防止版面破碎）
WINDOW_MIN_HEIGHT = 760   # 視窗最小高度

import logging

import flet as ft

from app.startup_tasks import start_background_startup_tasks
from app.ui import theme
from app.ui.keyboard_shortcuts import create_keyboard_handler
from app.ui.quick_jump import show_quick_jump_panel
from app.view_registry import build_navigation_destinations, build_view_registry, get_window_size

logger = logging.getLogger("main_app")


def bootstrap_runtime():
    """
    初始化 runtime（config + logging），只應在 script entry 被呼叫一次。

    流程：
    1. load_config()      → 讀取並合併 default / example / user 三層設定
    2. setup_logging()    → 根據 config 設定日誌等級、輸出格式、檔案路徑
    3. 驗證日誌系統是否成功初始化（取根 logger 的 effective level 確認）

    注意：main.py 可被測試環境 import，因此不在模組層執行此初始化，
    而是延後到 `if __name__ == "__main__"` 才呼叫，確保測試自行控制 runtime。
    """
    from translation_tool.utils.config_manager import load_config, setup_logging

    config = load_config()
    setup_logging(config)

    root_level = logging.getLogger().getEffectiveLevel()
    logger.info(
        f"日誌系統初始化成功，根記錄器級別已設為 {logging.getLevelName(root_level)} ({root_level})。"
    )


def main(page: ft.Page):
    """
    Flet 應用程式的主要 entry point，由 ft.app(target=main) 觸發。

    職責依序：
    1. 設定視窗外觀（標題、尺寸、顏色主題）
    2. 建立 view registry（所有子頁面的實例）
    3. 組裝 NavigationRail（左側導航列）＋ content area（右側內容區）
    4. 註冊鍵盤快捷鍵與快速跳轉面板
    5. 最後以背景執行緒啟動 startup_tasks（索引重建等）
    """
    # ----------------------------------------------------------
    # Step 1：視窗外觀設定
    # ----------------------------------------------------------
    page.title = "Minecraft 模組包繁體化工具"
    page.window_width = WINDOW_WIDTH_DEFAULT
    page.window_height = WINDOW_HEIGHT_DEFAULT
    page.window_min_width = WINDOW_MIN_WIDTH
    page.window_min_height = WINDOW_MIN_HEIGHT
    page.window_resizable = True                     # 允許使用者拖曳改大小
    page.bgcolor = "surfaceVariant"                # Flet M3 淺色主題的背景色

    # Material Design 3 主題設定
    page.theme = ft.Theme(
        font_family="Noto Sans TC",                 # 全域中文字型
        use_material3=True,                         # 啟用 M3 设计语言
        color_scheme_seed=ft.Colors.INDIGO,         # 以 INDIGO 為品牌色彩种子，M3 會自動生成完整色盤
        visual_density=ft.VisualDensity.COMFORTABLE,  # 舒適的控制項密度
    )
    page.theme_mode = ft.ThemeMode.LIGHT            # 預設淺色主題（另可切換 DARK）

    # ----------------------------------------------------------
    # Step 2：建立 FilePicker（所有 view 共享，作為跨 view 的檔案選擇構件）
    # ----------------------------------------------------------
    file_picker = ft.FilePicker()

    # 建立所有子頁面的 registry，清單中每個元素為：
    #   { 'key': str, 'view': ft.Control }  （key 用於 get_window_size 查表）
    registry = build_view_registry(page, file_picker)

    # 讓首頁（translation）和 pipeline 頁保有 registry 參考，
    # 以便它們能夠主動切換到其他 view（set_registry / set_view_registry）。
    # 用 named lookup 取代 registry[10] 魔數，未來 view 順序變更時自動跟著調整。
    _first_view = registry[0]
    _pipeline_view = next(
        (item for item in registry if item.get('key') == 'pipeline'),
        None,
    )
    _first_view['view'].content.set_registry(registry)
    if _pipeline_view is None:
        raise RuntimeError(
            "pipeline view not found in registry — "
            "check build_view_registry() keys. "
            f"Available keys: {[item.get('key') for item in registry]}"
        )
    _pipeline_view['view'].content.set_view_registry(registry)

    # ----------------------------------------------------------
    # Helper：根據 view key 調整視窗尺寸
    # ----------------------------------------------------------
    def resize_window_for_view(view_key: str):
        """
        根據即將切換的 view_key 查表取得理想尺寸，並套用到視窗。
        成功最大化視窗時用 window.width/height；否則降級用 page 屬性。
        """
        width, height = get_window_size(view_key)
        try:
            page.window.maximized = False           # 先取消最大化，確保能改尺寸
            page.window.width = width
            page.window.height = height
        except Exception:
            # 少數環境（舊版 Flet / mobile）window 屬性不同，降級處理
            page.window_width = width
            page.window_height = height

    # ----------------------------------------------------------
    # Step 3：建立內容區域（單一 Container，置換不同 view 實例）
    # ----------------------------------------------------------
    # content_area 永遠 expand=True，填滿 NavigationRail 之外的全部空間
    content_area = ft.Container(content=registry[0]['view'], expand=True)

    # ----------------------------------------------------------
    # Step 4：鍵盤快捷鍵系統
    # ----------------------------------------------------------
    # create_keyboard_handler 回傳一個 handler 物件，負責：
    #   - 攔截鍵盤事件（page.on_keyboard_event）
    #   - 提供搜尋/快速跳轉回呼的 setter
    keyboard_handler = create_keyboard_handler(
        page, registry, lambda idx: change_view_by_index(idx)
    )
    page.on_keyboard_event = keyboard_handler.handle_keyboard

    # ----------------------------------------------------------
    # 切換 view 的兩種方式：事件觸發 vs 索引直接呼叫
    # ----------------------------------------------------------

    def change_view(e):
        """
        由 NavigationRail 的 on_change 事件觸發。
        從事件中取出 selected_index，找出對應的 registry 項目，
        置換 content_area.content，並調整視窗尺寸後 update()。
        """
        selected_index = e.control.selected_index
        item = registry[selected_index]
        content_area.content = item['view']
        resize_window_for_view(item['key'])
        page.update()

    def change_view_by_index(index: int):
        """
        由鍵盤快捷鍵或快速跳轉面板直接呼叫（不經過 UI 事件）。
        除了切換 view 內容，還會同步 NavigationRail 的 selected_index，
        確保左右側邊欄狀態與內容一致。
        """
        if 0 <= index < len(registry):
            item = registry[index]
            content_area.content = item['view']
            resize_window_for_view(item['key'])
            rail.selected_index = index
            page.update()

    # ----------------------------------------------------------
    # 主題切換（淺色 ↔ 深色）
    # ----------------------------------------------------------
    def toggle_theme_mode(e):
        """
        點擊右上方太陽/月亮按鈕時呼叫。
        翻轉 theme_mode，並同步更新按鈕的 icon 和 tooltip，
        同時通知 theme.manager 更新內部狀態。
        """
        is_light = page.theme_mode == ft.ThemeMode.LIGHT
        page.theme_mode = ft.ThemeMode.DARK if is_light else ft.ThemeMode.LIGHT
        toggle_icon_btn.icon = ft.Icons.LIGHT_MODE if is_light else ft.Icons.DARK_MODE
        toggle_icon_btn.tooltip = "切換為淺色模式" if is_light else "切換為深色模式"
        theme.manager.set_mode('dark' if is_light else 'light')
        page.update()

    # 主題切換按鈕，平常顯示在 NavigationRail trailing 區域
    toggle_icon_btn = ft.IconButton(
        icon=ft.Icons.DARK_MODE,                  # 預設顯示月亮（當前為淺色）
        tooltip="切換為深色模式",
        on_click=toggle_theme_mode,
    )

    # ----------------------------------------------------------
    # 快速跳轉面板（Ctrl+P / 搜尋按鈕）
    # ----------------------------------------------------------
    def on_quick_jump(e):
        """
        開啟快速跳轉浮動面板，讓使用者輸入關鍵字即時篩選並切換 view。
        """
        show_quick_jump_panel(page, registry, change_view_by_index)

    # 將快速跳轉的回呼註冊到鍵盤 handler（可透過快捷鍵觸發）
    keyboard_handler.set_search_callback(on_quick_jump)

    # ----------------------------------------------------------
    # Step 5：組裝 NavigationRail（左側導航列）
    # ----------------------------------------------------------
    # 將 registry 轉換成 NavigationRail 所需的 destinations 格式
    destinations = build_navigation_destinations(registry)

    # NavigationRail 參數說明：
    #   label_type=ALL          → 所有項目都顯示文字標籤
    #   extended=True           → 預設展開（顯示文字），可收合
    #   min_width/min_extended_width → 控制收合/展開時的寬度
    #   group_alignment=-0.95    → 將 destinations 垂直往上偏移，靠攏頂部
    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=80,
        min_extended_width=200,
        extended=True,
        group_alignment=-0.95,
        destinations=destinations,
        on_change=change_view,                     # 切換時更新內容區
        bgcolor=ft.Colors.SURFACE,
        # leading：左上角的收合按鈕＋搜尋按鈕
        leading=ft.Container(
            content=ft.Column(
                [
                    # 收合/展開 NavigationRail 的漢堡選單按鈕
                    ft.IconButton(
                        ft.Icons.MENU,
                        on_click=lambda _: (
                            setattr(rail, "extended", not rail.extended) or page.update()
                        ),
                        tooltip="收合/展開選單",
                    ),
                    # 快速跳轉搜尋按鈕
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
        # trailing：右下方的主題切換按鈕
        trailing=ft.Container(
            content=toggle_icon_btn,
            margin=ft.Margin.only(top=10),
        ),
    )

    # ----------------------------------------------------------
    # Step 6：組裝最終 layout（Row：左側導航列 ＋ 垂直分隔線 ＋ 內容區）
    # ----------------------------------------------------------
    layout = ft.Row(
        controls=[
            rail,
            ft.VerticalDivider(width=1, thickness=1, color="outlineVariant"),  # 視覺分隔線
            content_area,
        ],
        expand=True,
        spacing=0,                          # 讓 content_area 完全填滿右側，無間距
    )

    page.add(layout)                        # 首次把 layout 加入 page（只做一次）
    resize_window_for_view(registry[0]['key'])  # 套用首頁的預設尺寸
    page.update()

    # ----------------------------------------------------------
    # Step 7：背景啟動任務（索引重建等不影響啟動速度的慢工作）
    # ----------------------------------------------------------
    start_background_startup_tasks()


if __name__ == "__main__":
    """
    腳本直接執行時才初始化 runtime 並啟動 Flet App。
    測試環境 import 此檔案時不會觸發這段邏輯。
    """
    try:
        bootstrap_runtime()
    except Exception as e:
        # bootstrap 失敗可能是 config 格式錯誤或 logging 初始化失敗，
        # 印出訊息後仍嘗試啟動（讓使用者能看到 GUI 介面）
        print(f"致命錯誤：配置或日誌系統初始化失敗！錯誤: {e}")

    ft.app(target=main)
