"""鍵盤快捷鍵模組。

提供全域鍵盤快捷鍵處理功能。
"""

import flet as ft

# 快捷鍵定義：key -> (描述, 處理函數)
# 支援 Ctrl+1~0 跳轉 1~10，Ctrl+Shift+0 跳轉 11
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
    # 數字鍵 0：第 10 個 View (lm)
    "0": {"label": "LM", "view_index": 9},
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
        self._current_view_getter = None
        self._search_callback = None
        self._save_callback = None

    def set_current_view_getter(self, getter):
        """設定取得目前頁面的函式（Ctrl+F 用來找該頁的搜尋框）。"""
        self._current_view_getter = getter

    # 各頁面的搜尋框屬性名稱（依優先順序；顯示中的才會被聚焦）
    SEARCH_FIELD_ATTRS = ("detail_search_tf", "mod_search_tf", "search_box", "tf_query_input")

    def _find_search_field(self):
        view = self._current_view_getter() if self._current_view_getter else None
        # 頁面外面包了一層 wrap_view 卡片
        inner = getattr(view, "content", None) or view
        for attr in self.SEARCH_FIELD_ATTRS:
            field = getattr(inner, attr, None)
            if field is not None and getattr(field, "visible", True) is not False:
                return field
        return None

    def set_search_callback(self, callback):
        """設定搜尋回調函數（開啟快速跳轉面板）"""
        self._search_callback = callback

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

        # 數字鍵：快速跳轉 (1-9, 0)
        if key in SHORTCUTS_DEFINITION:
            view_info = SHORTCUTS_DEFINITION[key]
            self.change_view_callback(view_info["view_index"])
            self._show_toast(f"跳轉到：{view_info['label']}")
            return

        # Ctrl+Shift+0：跳轉到第 11 個 View (merge)
        if key == "0" and e.shift:
            self.change_view_callback(10)  # merge 是第 11 個，index 為 10
            self._show_toast("跳轉到：合併")
            return

        # F 鍵：聚焦目前頁面的搜尋框（規則、JAR 圖示預覽、快取查詢）
        if key == "f":
            field = self._find_search_field()
            if field is not None:
                # Flet 1.0 的 focus() 是 coroutine，需交給 event loop 執行
                self.page.run_task(field.focus)
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

        # P 鍵：快速跳轉面板
        if key == "p":
            if hasattr(self, '_search_callback') and self._search_callback:
                self._search_callback(None)
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
