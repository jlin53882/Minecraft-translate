"""app/views/moddb/source_filter.py：條目校對的「譯文來源」篩選下拉。"""

from __future__ import annotations

from collections.abc import Callable

from app.services_impl.moddb_service import SOURCE_NAMES, current_settings
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
            options=[],
            on_select=lambda _e: on_change(),
        )
        self.refresh()

    def refresh(self) -> None:
        """選項順序跟隨設定的 translation_db.priority（每次切到條目校對頁籤重讀）。"""
        order = current_settings().priority
        kit.set_dropdown_options(
            self.dropdown,
            [
                (ALL_SOURCES, "全部來源"),
                *((str(c), SOURCE_NAMES.get(c, f"來源 {c}")) for c in order),
            ],
        )

    def reset(self) -> None:
        """回到「全部來源」。"""
        self.dropdown.value = ALL_SOURCES

    @property
    def code(self) -> int | None:
        """目前選的來源代碼；全部來源為 None。"""
        value = self.dropdown.value
        return None if value in (None, "", ALL_SOURCES) else int(value)
