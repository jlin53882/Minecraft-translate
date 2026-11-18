"""審核對話框的「模組」選單：只列在目前範圍內有結果的模組。

同文一致性審核與版本差異審核的結果都可以用模組篩選；選單若列出版本內的所有模組，
選到沒有結果的模組只會看到空清單。這裡記住最近一次「不限模組」載入得到的有結果模組，
範圍鍵（版本、模組包、原文等其他條件）改變時需要重新以不限模組的結果計算。

以單一模組載入時，那個模組的結果會更新清單（例如套用後已無結果就移除）；
目前選取的模組一律保留在選項內，避免選單顯示空白。
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable


class ResultModOptions:
    """有結果的模組清單與它對應的範圍鍵。"""

    def __init__(self) -> None:
        self._key: Hashable | None = None
        self._mods: set[str] = set()

    def needs_full_scan(self, key: Hashable, selected_mod: str | None) -> bool:
        """以單一模組載入、且清單不是這個範圍算出的：需要另外取不限模組的結果。"""
        return selected_mod is not None and key != self._key

    def update(
        self,
        key: Hashable,
        selected_mod: str | None,
        *,
        has_results: bool,
        full_mods: Iterable[str] | None = None,
    ) -> None:
        """以這次載入的結果更新清單。

        ``full_mods`` 是不限模組結果中有結果的模組；沒有提供時，只以目前選取模組的結果增減。
        """
        if full_mods is not None:
            self._key = key
            self._mods = set(full_mods)
            return
        if selected_mod is None or key != self._key:
            return
        if has_results:
            self._mods.add(selected_mod)
        else:
            self._mods.discard(selected_mod)

    def options(self, selected_mod: str | None) -> list[str]:
        """選單內容（排序）；目前選取的模組即使已無結果也保留。"""
        mods = set(self._mods)
        if selected_mod is not None:
            mods.add(selected_mod)
        return sorted(mods)
