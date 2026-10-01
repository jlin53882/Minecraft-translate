"""鍵盤快捷鍵模組。

提供全域鍵盤快捷鍵處理功能。

- Ctrl+<數字>：跳到該頁（對應表在 ``app.view_registry.ViewSpec.shortcut``）
- Ctrl+P：快速跳轉面板；面板開著時 ↑ ↓ Esc 由面板處理
- Ctrl+F：聚焦目前頁面的搜尋框；Ctrl+S：儲存
"""

import flet as ft


def shortcut_index(view_registry, key: str) -> int | None:
    """Ctrl+<數字> 對應到 registry 的位置（快捷鍵定義在 ``ViewSpec.shortcut``）；沒有對應回傳 None。"""
    from app.view_registry import SPECS_BY_KEY

    for index, item in enumerate(view_registry):
        spec = SPECS_BY_KEY.get(item.get("key"))
        if spec is not None and spec.shortcut == key:
            return index
    return None


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
        self._palette_getter = None

    def set_current_view_getter(self, getter):
        """設定取得目前頁面的函式（Ctrl+F 用來找該頁的搜尋框）。"""
        self._current_view_getter = getter

    # 各頁面的搜尋框屬性名稱（依優先順序；顯示中的才會被聚焦）
    SEARCH_FIELD_ATTRS = (
        "detail_search_tf",
        "mod_search_tf",
        "search_box",
        "tf_query_input",
    )

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

    def set_palette_getter(self, getter):
        """設定取得「目前開著的快速跳轉面板」的函式（開著時，方向鍵 / Esc 交給它）。"""
        self._palette_getter = getter

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

        if self._palette_getter is not None:
            palette = self._palette_getter()
            if palette is not None and palette.handle_key(e.key):
                return

        if not is_ctrl:
            return

        key = e.key.lower()

        # 數字鍵：跳到對應頁面
        if len(key) == 1 and key.isdigit():
            index = shortcut_index(self.view_registry, key)
            if index is not None:
                item = self.view_registry[index]
                self.change_view_callback(index)
                self._show_toast(f"跳轉到：{item.get('label', item.get('key'))}")
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
            if hasattr(self, "_search_callback") and self._search_callback:
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
