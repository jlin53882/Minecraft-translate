"""Web 模式安全的 ``FilePicker``，以及「輸出資料夾自動建立」的共用檢查。

Flet Web 不支援 ``FilePicker.get_directory_path()``（會拋 ``FletUnsupportedPlatformException``），
過去這會變成背景任務裡未捕捉的例外、使用者看不到任何回應。這裡集中處理：不支援時
記一筆警告、顯示提示並回傳 ``None``（等同取消），欄位仍可手動輸入路徑。
Web 模式輸入的是「執行程式那台電腦」上的路徑，不是瀏覽器所在電腦的路徑。
"""

from __future__ import annotations

import os

import flet as ft
from flet.controls.exceptions import FletUnsupportedPlatformException

from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_warning

WEB_UNSUPPORTED_HINT = (
    "⚠️ Web 模式不支援選擇資料夾／檔案對話框，請直接輸入「執行程式那台電腦」上的路徑"
)


class SafeFilePicker(ft.FilePicker):
    """不支援的平台（Flet Web）改為提示並回傳 ``None``，其餘行為與 ``ft.FilePicker`` 相同。"""

    def _notify_unsupported(self, method: str, exc: Exception) -> None:
        log_warning(f"[FilePicker] {method} 在目前平台不支援：{exc!r}")
        try:
            show_snack(self.page, WEB_UNSUPPORTED_HINT, color=ft.Colors.AMBER_700)
        except Exception as ex:  # noqa: BLE001 - 控制項未掛載時只能記錄
            log_warning(f"[FilePicker] 無法顯示不支援提示：{ex!r}")

    async def get_directory_path(self, *args, **kwargs):
        try:
            return await super().get_directory_path(*args, **kwargs)
        except FletUnsupportedPlatformException as exc:
            self._notify_unsupported("get_directory_path", exc)
            return None

    async def pick_files(self, *args, **kwargs):
        try:
            return await super().pick_files(*args, **kwargs)
        except FletUnsupportedPlatformException as exc:
            self._notify_unsupported("pick_files", exc)
            return None

    def _is_web(self) -> bool:
        try:
            return bool(getattr(self.page, "web", False))
        except Exception:  # noqa: BLE001 - 控制項尚未掛載：視為非 Web，交給原本的行為
            return False

    async def save_file(self, *args, **kwargs):
        # Flet Web 的 save_file 要求 src_bytes（瀏覽器下載模式），沒有時拋 ValueError，
        # 不是 FletUnsupportedPlatformException；本專案的 Web 設計是手動輸入伺服器端路徑，
        # 所以進 super() 之前直接判斷，不吃掉桌面版真正的 ValueError。
        if self._is_web():
            self._notify_unsupported(
                "save_file", RuntimeError("Web 模式不支援儲存檔案對話框")
            )
            return None
        try:
            return await super().save_file(*args, **kwargs)
        except FletUnsupportedPlatformException as exc:
            self._notify_unsupported("save_file", exc)
            return None


def ensure_output_dir(path: str) -> str | None:
    """輸出資料夾不需事先存在：自動建立。回傳錯誤訊息（可顯示），成功回傳 ``None``。"""
    path = (path or "").strip()
    if not path:
        return "請輸入輸出目錄"
    if os.path.exists(path) and not os.path.isdir(path):
        return "輸出目錄路徑是檔案，不是資料夾"
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        log_warning(f"[輸出目錄] 無法建立 {path!r}：{exc!r}")
        return "輸出目錄無法建立"
    return None


def check_output_dir(path: str) -> str | None:
    """只檢查輸出路徑「能不能用」，**不建立**資料夾。回傳錯誤訊息，沒問題回傳 ``None``。

    會被取消、並在取消時清掉新建輸出的流程（例如語系合併）必須自己建立輸出資料夾：
    服務要在建立之前記錄「這個資料夾原本存不存在」，只有原本不存在才能在取消時刪除；
    如果對話框先建立了，服務看到的就是「已存在」，取消後會把新建的半成品留下來。
    """
    path = (path or "").strip()
    if not path:
        return "請輸入輸出目錄"
    if os.path.exists(path):
        return None if os.path.isdir(path) else "輸出目錄路徑是檔案，不是資料夾"
    # 找最近一個存在的上層：必須是可寫的資料夾，之後才建得起來
    parent = os.path.dirname(os.path.abspath(path))
    while parent and not os.path.exists(parent):
        up = os.path.dirname(parent)
        if up == parent:
            break
        parent = up
    if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
        return "輸出目錄無法建立"
    return None
