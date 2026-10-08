"""LM-specific database enablement and inherited/manual target-version choice."""

from __future__ import annotations

from dataclasses import dataclass, replace

import flet as ft

from app.services_impl import moddb_service
from app.services_impl.moddb_service import (
    DbSettings,
    database_version_choices,
    summarize_database,
)
from app.ui import kit
from app.ui.design import C
from app.views.moddb.version_picker import set_target_version, target_version_dropdown
from translation_tool.utils.log_unit import log_warning


@dataclass(frozen=True)
class LmDbRunSnapshot:
    """Resolved database identity and behavior frozen for one LM operation."""

    use_db: bool
    version: str
    source: str
    warning: str
    database_settings: DbSettings


class LmDbOptions:
    """Keep global inheritance explicit while preserving LM's manual-version rule."""

    def __init__(
        self,
        page_update,
        *,
        settings: DbSettings | None = None,
        on_missing_database=None,
    ) -> None:
        self._page_update = page_update
        self._on_missing_database = on_missing_database
        self._settings = settings or moddb_service.load_db_settings()
        self._enabled_touched = False
        self._version_touched = False
        self._summary: dict | None = None
        self._versions: list[str] = []

        self.use_db_switch = ft.Switch(
            label="使用 Mod 資料庫",
            value=self._settings.enabled,
            on_change=self._on_enabled_changed,
        )
        self.inherit_version_switch = ft.Switch(
            label=self._inherit_label(self._settings.version),
            value=True,
            on_change=self._on_inherit_changed,
        )
        self.version_field = target_version_dropdown(
            value=self._settings.version,
            label="Mod 資料庫目標版本",
            hint="選擇版本或輸入新目標版本",
            on_change=self._on_version_changed,
            on_focus=self._on_version_focus,
        )
        self.version_field.disabled = True
        self.info = ft.Text("", size=11.5, color=C.DIM, selectable=True)
        self._refresh_database_state(self._settings)
        self.refresh_info()
        self.card = kit.section_card(
            "Mod 資料庫（LM 翻譯）",
            ft.Column(
                [
                    self.use_db_switch,
                    self.inherit_version_switch,
                    self.version_field,
                    self.info,
                ],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            icon=ft.Icons.DATASET_OUTLINED,
            tone="dia",
        )

    @staticmethod
    def _inherit_label(version: str) -> str:
        return f"沿用全域目標版本（目前：{version or '未指定'}）"

    def _on_enabled_changed(self, _event=None) -> None:
        self._enabled_touched = True
        self.refresh_info()
        self._update()

    def _on_inherit_changed(self, event=None) -> None:
        self._version_touched = True
        control = getattr(event, "control", None)
        inherited = bool(getattr(control, "value", self.inherit_version_switch.value))
        self.version_field.disabled = inherited
        if inherited:
            self._settings = moddb_service.load_db_settings()
            set_target_version(self.version_field, self._settings.version)
        self.refresh_info()
        self._update()

    def _on_version_changed(self, _event=None) -> None:
        self._version_touched = True
        self.inherit_version_switch.value = False
        self.version_field.disabled = False
        self.refresh_info()
        self._update()

    def _on_version_focus(self, _event=None) -> None:
        self.sync_from_global()
        if self._summary is None and self._on_missing_database is not None:
            self._on_missing_database()

    def _update(self) -> None:
        try:
            self._page_update()
        except Exception as exc:  # noqa: BLE001 - control may be temporarily detached
            log_warning(f"LM 資料庫選項畫面更新略過：{exc!r}")

    def _refresh_database_state(self, settings: DbSettings) -> None:
        try:
            self._versions = database_version_choices(settings)
        except Exception as exc:  # noqa: BLE001 - LM still permits a manual new target
            log_warning(f"讀取 LM 資料庫版本失敗：{exc!r}")
            self._versions = []
        try:
            self._summary = summarize_database(settings)
        except Exception as exc:  # noqa: BLE001 - keep the page usable and expose warning
            log_warning(f"讀取 LM 資料庫摘要失敗：{exc!r}")
            self._summary = {"problem": f"讀取摘要失敗（{exc}），詳情請看後台 log"}

    def sync_from_global(self, settings: DbSettings | None = None) -> None:
        """Refresh untouched fields; preserve any explicit page-level choice."""
        current = settings or moddb_service.load_db_settings()
        self._settings = current
        self._refresh_database_state(current)
        if not self._enabled_touched:
            self.use_db_switch.value = current.enabled
        if not self._version_touched or self.inherit_version_switch.value:
            self.inherit_version_switch.value = True
            self.inherit_version_switch.label = self._inherit_label(current.version)
            set_target_version(self.version_field, current.version)
            self.version_field.disabled = True
        self.refresh_info()
        self._update()

    def snapshot_for_run(
        self, global_settings: DbSettings | None = None
    ) -> LmDbRunSnapshot:
        """Resolve against one global-settings snapshot and freeze path/priority too."""
        settings = global_settings or moddb_service.load_db_settings()
        # Refresh untouched controls and their visible effective-value summary
        # from the same settings object that will be frozen into the operation.
        self.sync_from_global(settings)
        enabled = (
            bool(self.use_db_switch.value)
            if self._enabled_touched
            else settings.enabled
        )
        inherited = bool(self.inherit_version_switch.value)
        target = (
            str(settings.version or "").strip()
            if inherited
            else str(self.version_field.value or self.version_field.text or "").strip()
        )
        source = "global" if inherited else "page"
        path = str(settings.resolved_path().resolve())
        if not enabled:
            frozen = replace(settings, enabled=False, path=path, version=target)
            return LmDbRunSnapshot(False, target, source, "Mod 資料庫已停用", frozen)
        if not target:
            warning = "未指定 Mod 資料庫目標版本；本次會略過資料庫，不會改用其他版本"
            frozen = replace(settings, enabled=False, path=path, version="")
            return LmDbRunSnapshot(False, "", source, warning, frozen)

        frozen = replace(settings, enabled=True, path=path, version=target)
        warning = ""
        if self._summary is None:
            warning = f"資料庫尚未建立（{path}）；本次會略過資料庫查詢／寫回"
        elif self._summary.get("problem"):
            warning = f"資料庫目前無法使用：{self._summary['problem']}"
        return LmDbRunSnapshot(True, target, source, warning, frozen)

    def restore_snapshot(self, settings: DbSettings) -> None:
        """Restore an interrupted operation's exact DB identity and controls."""
        self._settings = settings
        self._enabled_touched = True
        self._version_touched = True
        self._refresh_database_state(settings)
        self.use_db_switch.value = settings.enabled
        self.inherit_version_switch.value = False
        self.inherit_version_switch.label = "沿用全域版本（續跑固定使用上次快照）"
        self.version_field.disabled = False
        set_target_version(self.version_field, settings.version)
        self.refresh_info()
        self._update()

    def refresh_info(self) -> None:
        if self._summary is None:
            text = "尚未建立 Mod 資料庫；啟用時仍會執行 LM，但資料庫查詢／寫回會略過。"
        elif self._summary.get("problem"):
            text = f"⚠ 資料庫無法使用：{self._summary['problem']}"
        else:
            text = (
                f"資料庫 {self._summary['entries']:,} 條目（已翻譯 "
                f"{self._summary['progress']}%）・已有版本："
                f"{'、'.join(self._versions[:6]) or '—'}"
            )
        if not self.use_db_switch.value:
            text += "\n本次停用資料庫，不查詢也不寫回。"
        else:
            inherited = bool(self.inherit_version_switch.value)
            target = (
                str(self._settings.version or "").strip()
                if inherited
                else str(
                    self.version_field.value or self.version_field.text or ""
                ).strip()
            )
            source = "沿用全域設定" if inherited else "頁面指定"
            text += f"\n本次預計使用：{target or '未指定（略過 DB）'}（{source}）"
            if not target:
                text += "\n⚠ 尚未指定目標版本；不會回退到其他版本。"
            elif target not in self._versions:
                text += "\n此版本目前不在資料庫中；若資料庫可用，LM 仍可依設定建立新版本譯文。"
        self.info.value = text
