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


def _sync_value(e) -> None:
    """不做事：只是讓前端在每次輸入時把值送回後端。"""


class SyncTextField(ft.TextField):
    def init(self):
        super().init()
        if (
            self.on_change is None
            and not self.multiline
            and not self.password
            and not self.read_only  # 唯讀欄位使用者改不了，不需要同步
        ):
            self.on_change = _sync_value
