"""「模組包版本」下拉選單：條目校對、批次機翻、批量審核與學名掃描共用。

選項的值是模組包身分（資料夾名稱@路徑雜湊），顯示文字是匯入時所選資料夾（或 ZIP）的名稱。
選了遊戲版本才有清單；該版本沒有 KubeJS／FTB Quests 內容時只有「全部模組包版本」並停用。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import flet as ft

from app.services_impl.moddb_service import (
    NO_PACK_SCOPE_HINT,
    PACK_SCOPE_HINT,
    mods_in_pack,
    pack_scope_options,
    pack_scopes_of,
)
from app.ui import kit

ALL_PACKS = "__all_packs__"
ALL_PACKS_LABEL = "全部模組包版本"


class PackScopeDropdown:
    """模組包版本選單與它的選項、選值、停用規則。"""

    def __init__(
        self,
        on_select: Callable,
        *,
        initial: str | None = None,
        width: float = 220,
    ) -> None:
        self._scopes: tuple[str, ...] = ()
        self._busy = False
        self.control = kit.dropdown(
            label="模組包版本", dense=True, width=width, on_select=on_select
        )
        # 完整選項由呼叫端查到後再補上；先放預填值，讓它在載入前就顯示正確的選擇
        initial = (initial or "").strip() or None
        self._set_options((initial,) if initial else ())
        self.control.value = initial or ALL_PACKS
        self._sync_availability()

    @property
    def scope(self) -> str | None:
        """目前選的模組包身分；「全部」為 ``None``。"""
        value = self.control.value
        return None if value in (None, "", ALL_PACKS) else str(value)

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._scopes

    def select(self, scope: str | None) -> None:
        self.control.value = scope or ALL_PACKS

    def set_scopes(self, scopes: Sequence[str]) -> bool:
        """換成新的模組包清單；選值不在清單裡時退回「全部」。回傳選項或可用狀態是否改變。"""
        was_disabled = self.control.disabled
        self._scopes = tuple(scopes)
        changed = self._set_options(self._scopes)
        if self.scope not in self._scopes:
            self.select(None)
        self._sync_availability()
        return changed or was_disabled != self.control.disabled

    def set_busy(self, busy: bool) -> None:
        """背景工作進行中暫時停用；結束後依是否有選項恢復。"""
        self._busy = busy
        self._sync_availability()

    def _set_options(self, scopes: Sequence[str]) -> bool:
        return kit.set_dropdown_options(
            self.control, [(ALL_PACKS, ALL_PACKS_LABEL), *pack_scope_options(scopes)]
        )

    def _sync_availability(self) -> None:
        self.control.disabled = self._busy or not self._scopes
        self.control.tooltip = PACK_SCOPE_HINT if self._scopes else NO_PACK_SCOPE_HINT


class PackModScope:
    """模組包版本與模組兩個選單的連動：由某個遊戲版本的完整模組清單在畫面端換算，不查資料庫。

    選了模組包版本後，模組選單只剩它的模組；範圍（:meth:`mod_ids`）是選到的模組，
    只選模組包版本時是該模組包的所有模組，都沒選為全部（空 tuple）。
    """

    def __init__(
        self,
        pack_filter: PackScopeDropdown,
        mod_dd: ft.Dropdown,
        *,
        all_mods: str,
        all_mods_label: str = "全部模組",
    ) -> None:
        self.pack_filter = pack_filter
        self.mod_dd = mod_dd
        self._all_mods = all_mods
        self._all_mods_label = all_mods_label
        self._version_mods: list[str] = []

    def set_version_mods(self, mods: Sequence[str]) -> list[ft.Control]:
        """換成另一個遊戲版本的模組清單；回傳選項有變的下拉選單。"""
        self._version_mods = list(mods)
        return self.refresh()

    def refresh(self) -> list[ft.Control]:
        """依目前的模組包版本選擇更新兩個選單；選值已不在選項裡時退回「全部」。"""
        changed: list[ft.Control] = []
        if self.pack_filter.set_scopes(pack_scopes_of(self._version_mods)):
            changed.append(self.pack_filter.control)
        mods = mods_in_pack(self._version_mods, self.pack_filter.scope)
        options = [(self._all_mods, self._all_mods_label), *((m, m) for m in mods)]
        if kit.set_dropdown_options(self.mod_dd, options):
            changed.append(self.mod_dd)
        if self.mod_dd.value not in [self._all_mods, *mods]:
            self.mod_dd.value = self._all_mods
        return changed

    def mod_ids(self) -> tuple[str, ...]:
        value = self.mod_dd.value
        if value and value != self._all_mods:
            return (str(value),)
        scope = self.pack_filter.scope
        return tuple(mods_in_pack(self._version_mods, scope)) if scope else ()


def pack_scope_label(scope: str) -> str:
    """單一模組包身分的顯示名稱（資料夾名稱）。"""
    return pack_scope_options([scope])[0][1]
