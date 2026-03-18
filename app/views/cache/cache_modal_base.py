# Cache Modal 基底類
# 提供通用的 open/close/callback 機制

import flet as ft


class CacheModalBase(ft.Container):
    """Modal 對話框基底類"""

    def __init__(self, page, on_complete=None, on_error=None, **kwargs):
        self._page_ref = page
        self.on_complete = on_complete
        self.on_error = on_error
        self._dialog = None
        self._is_open = False
        super().__init__(**kwargs)

    def open(self):
        """開啟 Modal"""
        if self._is_open:
            return
        self._dialog = ft.AlertDialog(
            content=self,
            actions=[
                ft.TextButton("取消", on_click=lambda e: self.close()),
                ft.ElevatedButton("確認", on_click=lambda e: self._on_confirm()),
            ],
        )
        self._page_ref.overlay.append(self._dialog)
        self._dialog.open = True
        self._is_open = True
        self._page_ref.update()

    def close(self):
        """關閉 Modal"""
        if not self._is_open:
            return
        try:
            self._page_ref.close(self._dialog)
        except Exception:
            self._dialog.open = False
            if self._dialog in self._page_ref.overlay:
                self._page_ref.overlay.remove(self._dialog)
            self._page_ref.update()
        self._is_open = False
        self._dialog = None
        self._on_close()

    def _on_close(self):
        """關閉時的回調（可覆寫）"""
        pass

    def _on_confirm(self):
        """確認時的回調（可覆寫）"""
        self.close()

    def _do_complete(self, data):
        """完成並回傳資料"""
        if self.on_complete:
            self.on_complete(data)
        self.close()

    def _do_error(self, error):
        """錯誤處理"""
        if self.on_error:
            self.on_error(error)
        self.close()
