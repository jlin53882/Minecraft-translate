"""app/views/moddb/source_filter.py：條目校對的「譯文來源」篩選下拉。"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_service import SOURCE_NAMES
from app.ui import kit

ALL_SOURCES = "__all__"


class SourceFilter:
    """下拉選單：全部來源，或只看「含某個來源譯文」的條目。"""

    def __init__(self, on_change: Callable[[], None]):
        self._on_change = on_change
        self.dropdown = kit.dropdown(
            label="譯文來源（含）",
            dense=True,
            width=200,
            value=ALL_SOURCES,
            options=[ft.dropdown.Option(key=ALL_SOURCES, text="全部來源")]
            + [ft.dropdown.Option(key=str(c), text=n) for c, n in SOURCE_NAMES.items()],
            on_select=lambda _e: on_change(),
        )

    @property
    def code(self) -> int | None:
        """目前選的來源代碼；全部來源為 None。"""
        value = self.dropdown.value
        return None if value in (None, "", ALL_SOURCES) else int(value)
