"""app/views/moddb/source_filter.py：條目校對的「目前生效來源」篩選下拉。"""

from __future__ import annotations

from collections.abc import Callable

from app.services_impl.moddb_service import current_settings
from app.services_impl.moddb_source_service import MANUAL_REVIEW_LABELS
from app.ui import kit

ALL_SOURCES = "__all__"


class SourceFilter:
    """下拉選單：全部來源，或只看目前生效來源為指定來源的條目。"""

    def __init__(self, on_change: Callable[[], None]):
        self._on_change = on_change
        self.dropdown = kit.dropdown(
            label="目前生效來源",
            dense=True,
            width=200,
            value=ALL_SOURCES,
            options=[],
            on_select=lambda _e: on_change(),
        )
        self.refresh()

    def refresh(self) -> None:
        """選項順序跟隨設定的 translation_db.priority（每次切到條目校對頁籤重讀）。"""
        settings = current_settings()
        catalog = settings.source_catalog
        order = catalog.ordered_codes(settings.priority)
        kit.set_dropdown_options(
            self.dropdown,
            [
                (ALL_SOURCES, "全部來源"),
                *((str(c), catalog.label(c)) for c in order),
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


class ReviewStatusFilter:
    """Separate effective-source filtering from manual review-state filtering."""

    def __init__(self, on_change: Callable[[], None]):
        self.dropdown = kit.dropdown(
            label="人工審核狀態",
            dense=True,
            width=190,
            value="__all__",
            options=[],
            on_select=lambda _e: on_change(),
        )
        kit.set_dropdown_options(
            self.dropdown,
            [("__all__", "全部狀態"), *MANUAL_REVIEW_LABELS.items()],
        )
