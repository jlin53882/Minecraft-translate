"""app/views/moddb/source_filter.py：條目校對的「目前生效來源」篩選下拉。"""

from __future__ import annotations

from collections.abc import Callable

from app.services_impl.moddb_service import current_settings
from app.services_impl.moddb_source_service import (
    CUSTOM_SOURCE_BASE,
    REVIEW_STATUS_LABELS,
    builtin_source_codes,
)
from app.ui import kit

ALL_SOURCES = "__all__"


def _normalise_source_codes(codes) -> set[int]:
    """把快照中的來源代碼轉成整數，略過空值與無法辨識的值。"""
    normalised: set[int] = set()
    for code in codes or ():
        if code in (None, ""):
            continue
        try:
            normalised.add(int(code))
        except (TypeError, ValueError):
            continue
    return normalised


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

    def refresh(
        self,
        effective_source_codes=(),
        *,
        catalog=None,
        available_only: bool = False,
    ) -> None:
        """更新來源選項，可選擇只顯示目前篩選結果實際使用的來源。"""
        settings = current_settings()
        catalog = catalog or settings.source_catalog
        available_codes = _normalise_source_codes(effective_source_codes)
        active_custom_codes = {
            code for code in available_codes if code >= CUSTOM_SOURCE_BASE
        }
        if available_only:
            visible_codes = available_codes
        else:
            visible_codes = set(builtin_source_codes()) | active_custom_codes
        order = [
            code
            for code in catalog.ordered_codes(settings.priority)
            if code in visible_codes
        ]
        fallback_codes = available_codes if available_only else active_custom_codes
        order.extend(code for code in sorted(fallback_codes) if code not in order)
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
            label="審核狀態",
            dense=True,
            width=190,
            value="__all__",
            options=[],
            on_select=lambda _e: on_change(),
        )
        kit.set_dropdown_options(
            self.dropdown,
            [
                ("__all__", "全部狀態"),
                *(
                    (status, label)
                    for status, label in REVIEW_STATUS_LABELS.items()
                    if status != "legacy_unknown"  # 與「人工已審核」同名，併入該選項
                ),
            ],
        )
