"""條目校對右側的「學名」建議：顯示或查詢目前條目原文對應的中文名稱。"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_service import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    SpeciesLookup,
    cached_species_lookup,
    is_species_candidate,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.species_bulk_lookup import (
    bulk_lookup_for,
    parent_issue_text,
    parent_reference_text,
)
from app.views.moddb.species_bulk_lookup import safe_lookup as _safe_lookup
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.species_names import (
    has_species_key_hint,
    normalize_species_name,
)

TAB_KEY = "species"
TAB_LABEL = "學名"


class SpeciesSuggestion:
    """「學名」建議分頁的內容與背景查詢；套用只會把名稱帶入譯文框，不會儲存。"""

    def __init__(
        self,
        page: ft.Page,
        on_apply: Callable[[str], None],
        on_change: Callable[[], None],
    ):
        self.page = page
        self.on_apply = on_apply
        self.on_change = on_change
        self._results: dict[str, SpeciesLookup] = {}
        # 每筆自己查詢完成時快取裡的結果；快取之後變了才代表有更新的資料。
        self._cache_seen: dict[str, SpeciesLookup | None] = {}
        self._running: set[str] = set()
        # 背景批次查詢每查到一筆就寫入快取；重繪讓目前條目的結果即時出現。
        bulk_lookup_for(page).add_listener(self._on_bulk_event)

    def _on_bulk_event(self, _event: str, _entry_id: int | None) -> None:
        self.on_change()

    # ------------------------------------------------------------------ 畫面
    def _pick_lookup(self, name: str) -> SpeciesLookup | None:
        """決定分頁顯示自己的查詢結果，還是快取中較新的結果。

        自己的查詢（包含「再查一次」失敗）優先；只有快取在那次查詢完成後又被更新
        （例如背景批次查到名稱或補齊上一層物種），且更新後的結果查到名稱，或已
        完整而自己的結果還不完整時，才改用快取。
        """
        cached = cached_species_lookup(name)
        fresh = self._results.get(name)
        if fresh is None:
            return cached
        updated = name not in self._cache_seen or cached != self._cache_seen[name]
        if (
            cached is not None
            and updated
            and (
                cached.status == STATUS_FOUND
                or (cached.complete and not fresh.complete)
            )
        ):
            return cached
        return fresh

    def build(self, entry) -> list[ft.Control]:
        value = entry.en_us or ""
        if not is_species_candidate(entry.key, value, require_key_hint=False):
            return [
                kit.hint_text(
                    "這個條目的原文不像學名（學名格式為「屬名 種小名」，"
                    "例如 Olea europaea）。"
                )
            ]
        name = normalize_species_name(value)
        lookup = self._pick_lookup(name)
        controls: list[ft.Control] = [self._card(entry, name, lookup)]
        if not has_species_key_hint(entry.key):
            controls.append(
                kit.hint_text(
                    "這個條目的 key 沒有學名提示（如 latin），"
                    "只是格式像學名，請確認它真的是學名。"
                )
            )
        controls.append(
            kit.hint_text(
                "「套用」只會把名稱帶入譯文框，不會自動儲存；查到的名稱會記在本機學名快取。"
            )
        )
        return controls

    def _card(self, entry, name: str, lookup: SpeciesLookup | None) -> ft.Control:
        running = name in self._running
        if running:
            body, color = "查詢中…", C.MUTED
        elif lookup is None:
            body, color = "尚未查詢", C.MUTED
        elif lookup.status == STATUS_FOUND:
            body, color = lookup.value or "", C.TEXT
        elif lookup.status == STATUS_NOT_FOUND:
            body, color = "維基百科找不到對應條目", C.GOLD
        elif lookup.status == STATUS_RATE_LIMITED:
            body, color = f"請求過於頻繁，稍後再試（{lookup.detail}）", C.RED
        else:
            body, color = f"查詢失敗（{lookup.detail}）", C.RED

        found = bool(lookup and lookup.status == STATUS_FOUND and not running)
        chips: list[ft.Control] = [kit.chip("維基百科", "neutral")]
        if found and lookup.value == entry.zh_tw:
            chips.append(kit.chip("與目前譯文相同", "em"))
        actions = [] if running else self._actions(entry, name, lookup)
        parent_lines = (
            self._parent_lines(lookup)
            if lookup and lookup.status == STATUS_NOT_FOUND and not running
            else []
        )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
            content=ft.Column(
                [
                    ft.Text(name, size=12, color=C.DIM, selectable=True),
                    ft.Text(
                        body,
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=color,
                        selectable=True,
                    ),
                    *parent_lines,
                    ft.Row(chips, spacing=6, wrap=True),
                    ft.Row(actions, spacing=8, wrap=True),
                ],
                spacing=6,
            ),
        )

    def _actions(
        self, entry, name: str, lookup: SpeciesLookup | None
    ) -> list[ft.Control]:
        """依查詢結果決定這張卡片提供哪些操作（套用、查詢／重試、再查一次）。"""
        actions: list[ft.Control] = []
        if lookup and lookup.status == STATUS_FOUND and lookup.value != entry.zh_tw:
            actions.append(
                kit.button(
                    "套用",
                    "primary",
                    size="sm",
                    icon=ft.Icons.CHECK,
                    on_click=lambda _e, text=lookup.value: self.on_apply(text),
                )
            )
        if not (lookup and lookup.complete):
            if lookup is None:
                label, icon = "查詢維基百科", ft.Icons.SEARCH
            elif lookup.definitive:  # 原名稱已確定查無，只差上一層物種
                label, icon = "補查上一層", ft.Icons.REFRESH
            else:
                label, icon = "重試", ft.Icons.REFRESH
            actions.append(
                kit.button(
                    label,
                    "secondary",
                    size="sm",
                    icon=icon,
                    on_click=lambda _e, item=name: self.query(item),
                )
            )
        if lookup and lookup.status == STATUS_NOT_FOUND:
            if parent_reference_text(lookup) and lookup.parent_value != entry.zh_tw:
                actions.append(
                    kit.button(
                        "套用參考名稱",
                        "secondary",
                        size="sm",
                        icon=ft.Icons.CHECK,
                        on_click=lambda _e, text=lookup.parent_value: self.on_apply(
                            text
                        ),
                    )
                )
            actions.append(
                kit.button(
                    "再查一次",
                    "ghost",
                    size="sm",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda _e, item=name: self.query(item, refresh=True),
                )
            )
        return actions

    @staticmethod
    def _parent_lines(lookup: SpeciesLookup) -> list[ft.Control]:
        """查無時的上一層物種資訊：查到的參考名稱，或上一層查詢失敗的說明。"""
        if reference := parent_reference_text(lookup):
            return [
                ft.Text(reference, size=13, color=C.TEXT, selectable=True),
                kit.hint_text(
                    "變種／品種的正確譯名通常不同，這只是上一層物種的名稱，僅供參考。"
                ),
            ]
        if issue := parent_issue_text(lookup):
            return [
                ft.Text(
                    f"{issue}，可按「補查上一層」。",
                    size=13,
                    color=C.RED if lookup.parent_status else C.MUTED,
                    selectable=True,
                )
            ]
        return []

    # ------------------------------------------------------------------ 查詢
    def query(self, name: str, *, refresh: bool = False) -> None:
        """查詢學名；``refresh`` 略過快取裡的查無紀錄重新連線。"""
        if name in self._running:
            return
        self._running.add(name)
        self._results.pop(name, None)
        self._cache_seen.pop(name, None)
        self.on_change()

        def work() -> None:
            lookup = _safe_lookup(name, refresh=refresh)
            self._post(self._finish, name, lookup)

        if not self._launch(work):
            self._running.discard(name)
            self.on_change()

    def _finish(self, name: str, lookup: SpeciesLookup) -> None:
        self._running.discard(name)
        self._results[name] = lookup
        self._cache_seen[name] = cached_species_lookup(name)
        if not lookup.definitive:
            show_snack(self.page, f"學名查詢失敗：{lookup.detail}", C.GOLD)
        self.on_change()

    def _launch(self, work) -> bool:
        try:
            handle = launch_page_operation(
                self.page,
                work,
                name="條目校對學名查詢",
                owner="moddb-species-suggestion",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=CommitPolicy.EPHEMERAL,
                durability=DurabilityPolicy.RECOMPUTABLE,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except Exception as exc:  # noqa: BLE001 - restore UI after launch failure
            show_snack(self.page, f"學名查詢無法啟動：{exc}", C.RED)
            return False
        if handle is False:
            show_snack(self.page, "學名查詢無法啟動：應用程式正在關閉。", C.GOLD)
            return False
        return True

    def _post(self, callback, *args) -> None:
        run_task = getattr(self.page, "run_task", None)
        try:
            if callable(run_task):

                async def runner() -> None:
                    callback(*args)

                run_task(runner)
            else:
                callback(*args)
        except Exception as exc:  # noqa: BLE001 - disposed page
            log_warning("學名查詢結果無法排回畫面：%r", exc)


__all__ = ["TAB_KEY", "TAB_LABEL", "SpeciesSuggestion"]
