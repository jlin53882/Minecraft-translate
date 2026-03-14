"""鍵盤快捷鍵模組。

提供全域鍵盤快捷鍵處理功能。
"""

import flet as ft

# 快捷鍵定義：key -> (描述, 處理函數)
SHORTCUTS_DEFINITION = {
    # 數字鍵 1-9：快速跳轉
    "1": {"label": "設定", "view_index": 0},
    "2": {"label": "規則", "view_index": 1},
    "3": {"label": "快取", "view_index": 2},
    "4": {"label": "翻譯", "view_index": 3},
    "5": {"label": "QC", "view_index": 4},
    "6": {"label": "查詢", "view_index": 5},
    "7": {"label": "打包", "view_index": 6},
    "8": {"label": "提取", "view_index": 7},
    "9": {"label": "翻譯結果", "view_index": 8},
}


class KeyboardShortcutHandler:
    """鍵盤快捷鍵處理器"""

    def __init__(self, page: ft.Page, view_registry: list, change_view_callback):
        """初始化處理器

        參數：
            page: Flet Page 物件
            view_registry: View 註冊表
            change_view_callback: 切換頁面的回調函數
        """
        self.page = page
        self.view_registry = view_registry
        self.change_view_callback = change_view_callback
        self._search_field = None
        self._save_callback = None

    def set_search_field(self, search_field):
        """設定搜尋框控制項"""
        self._search_field = search_field

    def set_save_callback(self, callback):
        """設定儲存回調函數"""
        self._save_callback = callback

    def handle_keyboard(self, e: ft.KeyboardEvent):
        """處理鍵盤事件

        參數：
            e: KeyboardEvent 事件物件
            e.key: 按鍵名稱
            e.ctrl: Ctrl 是否按下
            e.shift: Shift 是否按下
            e.alt: Alt 是否按下
            e.meta: Meta (Cmd/Win) 是否按下
        """
        # 檢查是否按下 Ctrl 或 Meta (Command)
        is_ctrl = e.ctrl or e.meta

        if not is_ctrl:
            return

        key = e.key.lower()

        # 數字鍵：快速跳轉
        if key in SHORTCUTS_DEFINITION:
            view_info = SHORTCUTS_DEFINITION[key]
            self.change_view_callback(view_info["view_index"])
            self._show_toast(f"跳轉到：{view_info['label']}")
            return

        # F 鍵：搜尋
        if key == "f":
            if self._search_field:
                self._search_field.focus()
            return

        # S 鍵：儲存
        if key == "s":
            if self._save_callback:
                self._save_callback()
            return

        # R 鍵：重新整理
        if key == "r":
            self._show_toast("重新整理...")
            # 重新整理邏輯可由各頁面自行處理
            return

    def _show_toast(self, message: str):
        """顯示簡短提示"""
        snack = ft.SnackBar(
            content=ft.Text(message),
            duration=1,
        )
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()


def create_keyboard_handler(page: ft.Page, view_registry: list, change_view_callback):
    """建立鍵盤快捷鍵處理器"""
    return KeyboardShortcutHandler(page, view_registry, change_view_callback)
