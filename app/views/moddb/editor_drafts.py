"""Mod DB 編輯框草稿的 resident state 與資料庫值比較契約。"""

from __future__ import annotations

from app.services_impl.moddb_service import EntryRow


class EditorDraftStore:
    """以條目 id 保存尚未寫入資料庫的編輯框內容。"""

    def __init__(self) -> None:
        self._values: dict[int, str] = {}

    def clear(self) -> None:
        """資料庫切換時清除舊資料庫的草稿。"""
        self._values.clear()

    def remember(self, entry: EntryRow | None, value: str | None) -> None:
        """只保存與目前資料庫譯文不同的內容，避免把已儲存值當成草稿。"""
        if entry is None:
            return
        current = value or ""
        saved = entry.zh_tw or ""
        if current == saved:
            self._values.pop(entry.id, None)
        else:
            self._values[entry.id] = current

    def get(self, entry_id: int) -> str | None:
        """取得指定條目的未儲存內容。"""
        return self._values.get(entry_id)

    def discard(self, entry_id: int) -> None:
        """放棄指定條目的未儲存內容。"""
        self._values.pop(entry_id, None)
