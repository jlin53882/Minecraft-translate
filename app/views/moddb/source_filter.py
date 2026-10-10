"""app/views/moddb/source_filter.py：條目校對的「目前生效來源」篩選下拉。"""

from __future__ import annotations

from collections.abc import Callable

from app.services_impl.moddb_service import current_settings
from app.services_impl.moddb_source_service import (
    CUSTOM_SOURCE_BASE,
    MANUAL_REVIEW_LABELS,
    builtin_source_codes,
)
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

    def refresh(self, effective_source_codes=(), *, catalog=None) -> None:
        """Show built-ins and custom sources used by this version's effective rows."""
        settings = current_settings()
        catalog = catalog or settings.source_catalog
        active_custom_codes = {
            int(code)
            for code in effective_source_codes
            if int(code) >= CUSTOM_SOURCE_BASE
        }
        visible_codes = set(builtin_source_codes()) | active_custom_codes
        order = [
            code
            for code in catalog.ordered_codes(settings.priority)
            if code in visible_codes
        ]
        order.extend(code for code in sorted(active_custom_codes) if code not in order)
        selected = self.dropdown.value
        kit.set_dropdown_options(
            self.dropdown,
            [
                (ALL_SOURCES, "全部來源"),
                *((str(code), catalog.label(code)) for code in order),
            ],
        )
        if selected not in {ALL_SOURCES, *(str(code) for code in order)}:
            self.dropdown.value = ALL_SOURCES

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
