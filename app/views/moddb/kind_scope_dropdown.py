"""「類型」下拉選單：批次機翻範圍用來只處理某一種條目類型（Lang、Guide、FTB Quests…）。

選項是某個遊戲版本資料庫裡實際出現過的類型（``TranslationDB.kinds``），顯示名稱用 ``kind_label``；
換版本後選值不在新清單裡時退回「全部類型」。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.ui import kit
from app.views.moddb.formatting import kind_label

ALL_KINDS = "__all_kinds__"
ALL_KINDS_LABEL = "全部類型"


class KindScopeDropdown:
    """類型選單與它的選項、選值。"""

    def __init__(self, on_select: Callable, *, width: float = 200) -> None:
        self.control = kit.dropdown(
            label="類型", dense=True, width=width, on_select=on_select
        )
        self.set_kinds(())

    @property
    def kind(self) -> str | None:
        """目前選的類型；「全部類型」為 ``None``。"""
        value = self.control.value
        return None if value in (None, "", ALL_KINDS) else str(value)

    def select(self, kind: str | None) -> None:
        self.control.value = kind or ALL_KINDS

    def set_kinds(self, kinds: Sequence[str]) -> bool:
        """換成新的類型清單；選值不在清單裡時退回「全部」。回傳選項是否改變。"""
        kinds = list(kinds)
        changed = kit.set_dropdown_options(
            self.control,
            [(ALL_KINDS, ALL_KINDS_LABEL), *((k, kind_label(k)) for k in kinds)],
        )
        if self.kind not in kinds:
            self.select(None)
        return changed
