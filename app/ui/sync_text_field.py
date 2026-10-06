"""輸入值會即時同步回後端的單行 ``TextField``。

Flet 只有在控制項掛了事件處理函式時，才會在每次輸入時把值送回 Python；沒有的話，
Web 模式手動輸入的內容要等失焦／送出才會進到 ``.value``，按「執行」時就讀到舊值
（一鍵流程 ``input=[]``、打包輸出 ZIP 仍用預設路徑）。

``app/`` 內一律用 ``SyncTextField``（``tests/test_text_field_value_sync.py`` 的 AST
契約測試強制），不要直接建立 ``ft.TextField``。多行／密碼／唯讀欄位不掛：每個按鍵都往返，
對大段文字不划算。呼叫端自己指定 ``on_change`` 時完全不動。
"""

from __future__ import annotations

import flet as ft

from translation_tool.utils.log_unit import log_info


def _sync_value(e) -> None:
    """把 Web 事件中的最新值寫回後端控制項。"""
    if e is not None and getattr(e, "control", None) is not None:
        value = getattr(e, "data", None)
        current = getattr(e.control, "value", "")
        event_name = getattr(e, "name", None)
        # Web 的 change 事件可能已帶回新文字，但 Python 控制項仍保留舊值；
        # change 資料是這次輸入的完整值，必須優先寫回控制項。
        if event_name == "change" and isinstance(value, str):
            e.control.value = value
        # blur 事件有些 renderer 只送空資料，不能因此清除原本的值；
        # 只有控制項仍為空時，才使用非空事件資料作為補救。
        elif not current and value:
            e.control.value = value


def _sync_blur(e) -> None:
    """失焦：先記錄後端「控制項的值」與「事件帶來的值」（診斷用），再同步。

    畫面有值、後端卻讀到舊值時，這一行能分辨兩種情況：事件根本沒送到 Python
    （log 完全沒有這行），或事件到了但控制項的值沒更新（兩個值不同）。
    每次編輯只在失焦時記一行，不會每個按鍵都寫。
    """
    control = getattr(e, "control", None)
    if control is not None:
        current = getattr(control, "value", None)
        data = getattr(e, "data", None)
        log_info(
            f"[欄位同步] blur：label={getattr(control, 'label', None)!r}, "
            f"hint={getattr(control, 'hint_text', None)!r}, "
            f"控制項值={current!r}（{len(current or '')} 字）, "
            f"事件資料={data!r}（{len(data) if isinstance(data, str) else 'n/a'}）"
        )
    _sync_value(e)


class SyncTextField(ft.TextField):
    def __init__(self, *args, **kwargs):
        """在控制項建立前就註冊 Web 同步事件。"""
        # Flet 會在基底建構子內準備事件註冊資料；要在 super() 前傳入，
        # 才能確保 Web renderer 真的把 handler 發佈到前端，而不是只改到
        # Python 物件上的屬性。
        is_single_line = not (
            kwargs.get("multiline", False)
            or kwargs.get("password", False)
            or kwargs.get("read_only", False)
        )
        if is_single_line:
            if kwargs.get("on_change") is None:
                kwargs["on_change"] = _sync_value
            if kwargs.get("on_blur") is None:
                kwargs["on_blur"] = _sync_blur
        super().__init__(*args, **kwargs)
        self._ensure_sync_handlers()

    def _ensure_sync_handlers(self) -> None:
        """為可編輯單行欄位註冊輸入與失焦同步事件。"""
        if self.multiline or self.password or self.read_only:
            return
        if self.on_change is None:
            self.on_change = _sync_value
        if self.on_blur is None:
            self.on_blur = _sync_blur

    def init(self):
        super().init()
        # Flet 生命週期可能在不同 renderer 以不同順序呼叫 init；再次確保
        # handler 存在，避免 Web renderer 在第一次 build 時遺漏事件註冊。
        self._ensure_sync_handlers()
