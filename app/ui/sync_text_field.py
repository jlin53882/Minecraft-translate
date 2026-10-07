"""輸入值會即時同步回後端的 ``TextField``。

背景：Flet Web 的輸入事件到了 Python，但控制項的 ``.value`` 可能還是舊值
（一鍵流程 ``input=[]``、機器翻譯頁用舊路徑掃描、打包輸出 ZIP 仍用預設路徑）。

行為（``app/`` 內一律用 ``SyncTextField``，``tests/test_text_field_value_sync.py`` 的 AST
契約測試強制，不要直接建立 ``ft.TextField``）：

* 可編輯單行欄位，沒有指定 handler：自動掛 ``on_change``（同步輸入值）與 ``on_blur``
  （同步並記一行診斷 log）。
* 呼叫端自己指定了 ``on_change`` / ``on_blur``：**先同步、再呼叫它**（單行欄位的 ``on_blur``
  也一樣）。呼叫端 handler 直接讀 ``e.control.value`` 才會是新值；保留 sync／async／零參數寫法，
  重複套用不會再包一層。
* 多行、密碼欄位：只有呼叫端自己指定了 ``on_change`` 時才先同步再呼叫；沒有指定就不掛
  （每個按鍵都往返，對大段文字不划算）。
* 唯讀欄位：不掛（使用者改不了）。

``change`` 事件只更新 Flet 控制項的後端快取（``_values``），**不標記 dirty**：Flet 的差異計算
只看 ``_dirty``，所以不會把值再推回瀏覽器；直接 ``control.value = …`` 會觸發反向更新，使用者
還在打字時，較早的值可能蓋掉較新的值（長路徑只剩中間或尾端）。
"""

from __future__ import annotations

import inspect

import flet as ft

from translation_tool.utils.log_unit import log_debug, log_info
from translation_tool.utils.path_text import normalize_path_text, strip_path_quotes


def _sync_value(e) -> None:
    """把 Web 事件中的最新值寫回後端控制項。"""
    if e is not None and getattr(e, "control", None) is not None:
        value = getattr(e, "data", None)
        current = getattr(e.control, "value", "")
        event_name = getattr(e, "name", None)
        # 兩種情況寫回事件資料：
        # 1. change 事件：Web 可能已帶回新文字、Python 控制項卻還是舊值；change 資料是
        #    這次輸入的完整值，必須優先寫回。
        # 2. 其他事件（例如 blur）：有些 renderer 只送空資料，不能因此清掉原本的值；
        #    只有控制項仍為空時，才用非空事件資料補救。
        if (event_name == "change" and isinstance(value, str)) or (
            not current and value
        ):
            e.control.value = value


def _clean_path_input(control, *, final: bool) -> None:
    """路徑欄位（``path_input=True``）：去掉貼上時帶的引號（Windows「複製為路徑」）。

    輸入中（change）只去引號，不動空白（路徑中間可能還在打字）；離開欄位（blur）
    再整理前後空白。只有內容真的需要改時才寫回並更新畫面，所以平常打字不會觸發反向更新。
    """
    if not getattr(control, "path_input", False):
        return
    current = getattr(control, "value", "") or ""
    cleaned = normalize_path_text(current) if final else strip_path_quotes(current)
    if cleaned == current:
        return
    control.value = cleaned
    try:
        control.update()
    except Exception as exc:  # noqa: BLE001 - 尚未掛上頁面時不影響輸入
        log_debug(f"路徑欄位更新略過：{exc}")


def _sync_change(e) -> None:
    """把 ``on_change`` 的完整文字寫入後端，但不要反向推回瀏覽器。

    Flet Web 的輸入事件可能在使用者還在打字時連續抵達。若這裡直接設定
    ``control.value``，Flet 會把每次同步再當成一次 UI 更新送回瀏覽器；來回
    更新競速時，較早的值可能覆蓋較新的值，造成長路徑只剩中間或尾端文字。
    ``_values`` 是 Flet 控制項的後端快取；只更新快取、不標記 dirty，讓呼叫端
    能立即讀到新值，同時避免每個按鍵觸發反向更新。
    """
    control = getattr(e, "control", None)
    value = getattr(e, "data", None)
    if control is None or not isinstance(value, str):
        return

    values = getattr(control, "_values", None)
    if isinstance(values, dict):
        if value:
            values["value"] = value
        else:
            values.pop("value", None)
    else:
        # 測試替身或未使用 Flet Prop 的控制項仍維持可讀行為。
        control.value = value
    _clean_path_input(control, final=False)


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
    control = getattr(e, "control", None)
    if control is not None:
        _clean_path_input(control, final=True)


# 內建的同步 handler 本身就是「已同步」：init 再次確認時不可再包一層
_sync_value._sync_wrapped = True
_sync_change._sync_wrapped = True
_sync_blur._sync_wrapped = True


def _takes_event(handler) -> bool:
    """handler 是否接收事件參數（Flet 也支援零參數的 handler）。"""
    try:
        params = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        p.kind in (p.VAR_POSITIONAL, p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        for p in params
    )


def _synced(user_handler, sync):
    """先同步事件值，再呼叫呼叫端自己的 handler。

    Web 的 ``e.data`` 已是新值、``e.control.value`` 卻可能還是舊值；許多呼叫端的
    ``on_change`` 直接讀 ``e.control.value``，所以同步一定要排在它前面，不能二選一。
    保留呼叫端 handler 的 sync／async 與零參數寫法。
    """
    if user_handler is None:
        return sync
    if getattr(user_handler, "_sync_wrapped", False):
        return user_handler  # 已包過（例如 init 再次確認）：不重複包
    takes_event = _takes_event(user_handler)

    if inspect.iscoroutinefunction(user_handler):

        async def wrapper(e):
            sync(e)
            await (user_handler(e) if takes_event else user_handler())

    else:

        def wrapper(e):
            sync(e)
            return user_handler(e) if takes_event else user_handler()

    wrapper._sync_wrapped = True
    wrapper.__wrapped__ = user_handler
    return wrapper


class SyncTextField(ft.TextField):
    def __init__(self, *args, **kwargs):
        """在控制項建立前就註冊 Web 同步事件（並與呼叫端自己的 handler 串起來）。"""
        # Flet 會在基底建構子內準備事件註冊資料；要在 super() 前傳入，
        # 才能確保 Web renderer 真的把 handler 發佈到前端，而不是只改到
        # Python 物件上的屬性。
        path_input = bool(kwargs.pop("path_input", False))
        editable = not kwargs.get("read_only", False)
        single_line = editable and not (
            kwargs.get("multiline", False) or kwargs.get("password", False)
        )
        if editable and (single_line or kwargs.get("on_change") is not None):
            # 有呼叫端 handler 時任何型態的欄位都要先同步（多行也一樣）；沒有時只補單行
            kwargs["on_change"] = _synced(kwargs.get("on_change"), _sync_change)
        if single_line:
            kwargs["on_blur"] = _synced(kwargs.get("on_blur"), _sync_blur)
        super().__init__(*args, **kwargs)
        # 路徑欄位：貼上帶引號的路徑（檔案總管「複製為路徑」）會自動去掉引號
        self.path_input = path_input
        self._ensure_sync_handlers()

    def _ensure_sync_handlers(self) -> None:
        """為可編輯單行欄位註冊輸入與失焦同步事件。"""
        if self.read_only:
            return
        if self.on_change is not None or not (self.multiline or self.password):
            self.on_change = _synced(self.on_change, _sync_change)
        if not (self.multiline or self.password):
            self.on_blur = _synced(self.on_blur, _sync_blur)

    def init(self):
        super().init()
        # Flet 生命週期可能在不同 renderer 以不同順序呼叫 init；再次確保
        # handler 存在，避免 Web renderer 在第一次 build 時遺漏事件註冊。
        self._ensure_sync_handlers()
