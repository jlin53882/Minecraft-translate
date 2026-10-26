"""更新邊界：讓大型控制樹只在自己明確更新時才被 diff。

Flet 的 ``page.update()`` 會走訪整棵控制樹；樹越大（Mod 資料庫頁有數千個控制項），
即使只改一個文字也要付出走訪成本，而且任何頁面更新（狀態列、Key 輪詢…）都會連帶掃過它。
被標成 *isolated* 的控制項是更新邊界：上層更新時不會走進它的子孫，
只有對它（或它的子孫）呼叫 ``update()`` 時才會 diff 這一段。

使用規則：

- 標成邊界的控制項，子孫的任何變更都必須由擁有者明確呼叫 ``update_regions(...)`` 或
  該控制項自己的 ``update()``；漏掉就不會顯示。
- 事件處理函式沒有呼叫任何 update 時，Flet 會對「最近的邊界」做自動更新，而不是整個 Page。
"""

from __future__ import annotations

from typing import Any

import flet as ft

from translation_tool.utils.log_unit import log_debug


def isolate[C](control: C) -> C:
    """把控制項標成更新邊界並回傳它（可串接在建構式外面）。"""
    control._isolated = True  # type: ignore[attr-defined]
    return control


def update_regions(*controls: Any) -> bool:
    """只更新指定的控制項（各自 diff 自己的子樹）；有任何一個尚未掛上頁面就回傳 False。

    ``None`` 會被略過。尚未掛載（建構中、已卸載）的控制項不能更新，會拋 ``RuntimeError``；
    這裡吞掉它，讓資料操作不受畫面狀態影響。
    """
    ok = True
    for control in controls:
        if control is None:
            continue
        try:
            control.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料操作
            log_debug(f"區域更新略過：{exc!r}")
            ok = False
    return ok


def suppress_auto_update() -> None:
    """事件處理函式沒有任何畫面變更時呼叫，讓 Flet 略過事後的自動更新。

    Flet 在事件處理完、沒有人呼叫過 ``update()`` 時，會對最近的更新邊界（通常是整個 Page）
    補一次更新。全域按鍵事件與逐字輸入每個字元都觸發，整頁走訪一次要數十毫秒。
    處理函式確定沒有改動控制項（或已自行更新需要的區塊）時呼叫這個函式。
    """
    try:
        ft.context.mark_update_called()
    except Exception as exc:  # noqa: BLE001 - 不在 Flet 事件脈絡內（單元測試）時沒有影響
        log_debug(f"略過自動更新抑制：{exc!r}")
