"""語系合併頁的「Mod 資料庫補譯」選項卡（開關＋目標版本）。

預設值取自設定（``translation_db.merge_enabled`` / ``translation_db.version``）；
使用者在頁面上動過之後，這一頁的選擇優先於設定，不會被設定覆寫。沒動過時，
每次回到這一頁都會重新讀設定，所以在設定頁改的值會跟著變。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import flet as ft

from app.services_impl.moddb_service import (
    DbSettings,
    load_db_settings,
    summarize_database,
)
from app.ui import kit
from app.ui.design import C
from app.views.moddb import version_picker
from translation_tool.utils.log_unit import log_debug, log_warning


@dataclass(frozen=True)
class MergeDbRunSnapshot:
    """Immutable database choice resolved at the moment a merge is admitted."""

    use_db: bool
    version: str
    warning: str = ""
    database_settings: DbSettings | None = None


class MergeDbOptions:
    """Merge-specific DB switch and version override, separate from shared pickers."""

    def __init__(self, page_update, on_missing_database=None, *, settings=None) -> None:
        self._page_update = page_update
        self._on_missing_database = on_missing_database
        self._switch_touched = False
        self._version_override: str | None = None
        self._stale_override: str | None = None
        self._database_missing = False
        settings = settings or load_db_settings()
        self._settings = settings
        self._database_versions: list[str] = []
        self._summary: dict | None = None
        self._refresh_database_state(settings)
        row = kit.SwitchRow(
            "使用 Mod 資料庫補譯",
            "純英文條目先向資料庫找相同（模組、鍵值、原文）的譯文；只補沒有譯文的條目",
            settings.merge_enabled,
            divider=False,
        )
        self.switch = row.switch
        self.switch.on_change = self._on_changed
        self.version_field = version_picker.merge_target_version_dropdown(
            global_version=settings.version,
            on_select=self._on_version_changed,
            on_focus=self._on_version_focus,
        )
        self.version_warning = ft.Text(
            "",
            size=11.5,
            color=C.GOLD,
            visible=False,
        )
        self.info = ft.Text("", size=11.5, color=C.DIM)
        self.refresh_info()
        self.card = kit.section_card(
            "Mod 資料庫補譯",
            ft.Column(
                [row, self.version_field, self.version_warning, self.info],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            icon=ft.Icons.DATASET_OUTLINED,
            tone="dia",
        )

    @property
    def use_db(self) -> bool:
        return bool(self.switch.value)

    @property
    def version(self) -> str | None:
        """Explicit page override; ``None`` means inherit the current global setting."""
        return self._version_override

    def sync_from_config(self) -> None:
        """Refresh global hints/options while preserving independent user overrides."""
        self._settings = load_db_settings()
        self._refresh_database_state(self._settings)
        if not self._switch_touched:
            self.switch.value = self._settings.merge_enabled
        self._refresh_version_control()
        self.refresh_info()

    def _on_version_focus(self, _e=None) -> None:
        self._settings = load_db_settings()
        self._refresh_database_state(self._settings)
        self._refresh_version_control()
        self.refresh_info()
        if self._database_missing and self._on_missing_database:
            self._on_missing_database()
        try:
            self._page_update()
        except Exception as exc:  # noqa: BLE001 - 頁面尚未掛載時略過即時更新
            log_debug(f"語系合併版本選項更新略過：{exc}")

    def _on_changed(self, _e=None) -> None:
        self._switch_touched = True
        self.refresh_info()
        try:
            self._page_update()
        except Exception as exc:  # noqa: BLE001 - 頁面尚未掛載時只是不即時更新提示
            log_debug(f"語系合併資料庫提示更新略過：{exc}")

    def _on_version_changed(self, event=None) -> None:
        """Record only an explicit database option; the sentinel means inherit."""
        selected = getattr(getattr(event, "control", None), "value", None)
        if selected == version_picker.MERGE_INHERIT_VERSION:
            self._version_override = None
            self._stale_override = None
        elif selected in self._database_versions:
            self._version_override = str(selected)
            self._stale_override = None
        elif selected:
            # A stale programmatic/UI selection must fail closed, not become inherit.
            self._version_override = str(selected)
            self._stale_override = str(selected)
        self._refresh_version_control()
        self.refresh_info()
        try:
            self._page_update()
        except Exception as exc:  # noqa: BLE001 - 頁面尚未掛載時只是不即時更新提示
            log_debug(f"語系合併資料庫版本提示更新略過：{exc}")

    def _refresh_database_state(self, settings: DbSettings | None = None) -> None:
        """Read latest DB candidates without creating a database."""
        try:
            self._database_versions = version_picker.merge_target_version_choices(
                settings
            )
        except Exception as exc:  # noqa: BLE001 - picker 本身也採 fail-closed
            log_warning(f"讀取語系合併資料庫版本失敗：{exc!r}")
            self._database_versions = []
        try:
            self._summary = summarize_database(settings)
        except Exception as exc:  # noqa: BLE001 - 只影響提示文字，不應讓頁面載入失敗
            log_warning(f"讀取 Mod 資料庫摘要失敗：{exc!r}")
            self._summary = {"problem": f"讀取摘要失敗（{exc}），詳情請看後台 log"}
        self._database_missing = self._summary is None
        if self._version_override:
            if self._version_override in self._database_versions:
                self._stale_override = None
            else:
                self._stale_override = self._version_override

    def _refresh_version_control(self) -> None:
        selected = (
            self._version_override
            if self._version_override in self._database_versions
            else None
        )
        if self._stale_override:
            self.version_warning.value = (
                f"⚠️ 頁面原指定版本 {self._stale_override} 已不存在於目前資料庫；"
                "請重新選擇資料庫版本或改為沿用全域設定。"
            )
            self.version_warning.visible = True
        else:
            self.version_warning.value = ""
            self.version_warning.visible = False
        version_picker.refresh_merge_target_version_options(
            self.version_field,
            self._settings.version,
            selected,
            choices=self._database_versions,
        )

    def _resolved_choice(self) -> tuple[str, str, str]:
        """Return (version, source, warning), validating against current DB contents."""
        if self._stale_override:
            value = self._stale_override
            return (
                "",
                "page",
                f"頁面指定版本 {value} 不存在於目前資料庫，本次略過補譯",
            )
        if self._version_override is not None:
            target, source = self._version_override, "page"
        else:
            target, source = str(self._settings.version or "").strip(), "global"
        if not target:
            if source == "global":
                return "", source, "全域未指定版本，本次略過資料庫補譯"
            return "", source, "未指定目標版本，本次略過資料庫補譯"
        if not self._database_versions:
            if self._summary is None:
                return "", source, "尚未建立資料庫，本次略過補譯"
            if self._summary.get("problem"):
                return "", source, "目前資料庫無法使用，本次略過補譯"
            return "", source, "目前資料庫沒有有效版本，本次略過補譯"
        if target not in self._database_versions:
            label = "頁面指定版本" if source == "page" else "全域設定版本"
            return "", source, f"{label} {target} 不存在於目前資料庫，本次略過補譯"
        return target, source, ""

    def snapshot_for_run(self, global_settings=None) -> MergeDbRunSnapshot:
        """Resolve the effective target on the UI thread and freeze it for the worker."""
        self._settings = global_settings or load_db_settings()
        settings = self._settings
        self._refresh_database_state(settings)
        self._refresh_version_control()
        self.refresh_info()
        if not self.use_db:
            return MergeDbRunSnapshot(False, "")
        target, _source, warning = self._resolved_choice()
        frozen_settings = replace(
            settings,
            path=str(settings.resolved_path().resolve()),
            merge_enabled=True,
            version=target,
        )
        return MergeDbRunSnapshot(True, target, warning, frozen_settings)

    def refresh_info(self) -> None:
        """顯示資料庫狀態；沒建立或沒有版本時說明這次會略過補譯。"""
        info = self._summary
        if info is None:
            self._database_missing = True
            text = "尚未建立資料庫：這次合併會略過資料庫補譯（到「Mod 資料庫」頁掃描後即可使用）。"
        elif info.get("problem"):
            self._database_missing = False
            text = f"⚠ 資料庫無法使用：{info['problem']}（這次會略過補譯）"
        else:
            self._database_missing = False
            text = (
                f"資料庫 {info['entries']:,} 條目（已翻譯 {info['progress']}%）・"
                f"版本：{'、'.join(info['versions'][:4]) or '—'}"
            )
        if not self.use_db:
            text += "\n未啟用 Mod 資料庫補譯"
        else:
            target, source, warning = self._resolved_choice()
            if warning:
                text += f"\n⚠ {warning}"
            else:
                source_label = "頁面指定" if source == "page" else "沿用全域設定"
                text += f"\n本次實際使用：{target}（{source_label}）"
        self.info.value = text
