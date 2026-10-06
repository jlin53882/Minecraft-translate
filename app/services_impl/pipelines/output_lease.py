"""合併輸出資料夾的獨占租約。

為什麼需要：取消時「清掉本次新建的輸出」只靠「這個資料夾原本存不存在」判斷歸屬並不夠。
兩個任務同時往同一個「還不存在」的輸出資料夾寫，各自看到的都是「原本不存在」，其中一個
被取消就會 ``rmtree`` 掉另一個任務正在寫（甚至已經寫完）的成果。

做法：每個合併任務在開始前取得輸出路徑的獨占租約，結束（成功、失敗、取消、generator 被關閉）
時釋放；同一時間第二個任務碰到相同（或有包含關係）的輸出路徑，直接明確失敗，而不是互相覆蓋。
folder／ZIP 兩個服務共用同一份登記表。
"""

from __future__ import annotations

import os
import threading

__all__ = ["OutputInUseError", "OutputLease", "acquire_output_lease"]

_LOCK = threading.Lock()
_HELD: dict[str, OutputLease] = {}


class OutputInUseError(RuntimeError):
    """輸出資料夾正在被另一個任務使用。"""


def _key(path: str) -> str:
    """正規化：Windows 的大小寫、``..``、符號連結／捷徑都視為同一個輸出。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _overlaps(a: str, b: str) -> bool:
    """相同，或其中一個在另一個底下（取消時 ``rmtree`` 會連子資料夾一起刪）。"""
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


class OutputLease:
    def __init__(self, key: str, path: str) -> None:
        self.key = key
        self.path = path
        self._released = False

    def release(self) -> None:
        """釋放租約（可重複呼叫）。"""
        with _LOCK:
            if not self._released and _HELD.get(self.key) is self:
                del _HELD[self.key]
            self._released = True


def acquire_output_lease(path: str) -> OutputLease:
    """取得輸出路徑的獨占租約；已被另一個任務使用（含包含關係）時拋出 ``OutputInUseError``。"""
    key = _key(path)
    with _LOCK:
        for held_key, holder in _HELD.items():
            if _overlaps(key, held_key):
                raise OutputInUseError(
                    f"輸出資料夾正在被另一個任務使用：{path}"
                    f"（與 {holder.path} 相同或互相包含；同一時間只能有一個合併任務使用同一個輸出）"
                )
        lease = OutputLease(key, path)
        _HELD[key] = lease
    return lease
