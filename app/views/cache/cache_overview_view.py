"""app/views/cache/cache_overview_view.py（快取總覽頁）

本頁是快取系統的「總覽/管理」功能區塊，功能包含：
- 總覽：各 cache_type 的統計、重載、儲存、新分片/補滿舊檔、輪替分片

本檔案由 cache_view.py 拆分出來，獨立運作。
"""

import time
import traceback
from datetime import datetime

import flet as ft

from app.ui.components import primary_button, secondary_button

from app.views.cache_manager.cache_overview_panel import build_overview_page
from app.services_impl.cache.cache_services import (
    cache_get_overview_service,
    cache_reload_service,
    cache_reload_type_service,
    cache_rotate_service,
    cache_save_all_service,
    cache_rebuild_index_service,
)
from translation_tool.utils.log_unit import log_error, log_info, log_warning


class CacheOverviewView(ft.Column):
    """快取總覽頁（UI）。

    本類別處理總覽區塊的 UI 組裝與事件處理。
    """

    def __init__(self, page: ft.Page):
        """初始化總覽頁。

        - 主要包裝：`__init__`, `Text`

        回傳：None
        """
        super().__init__(expand=True, spacing=10)
        self.page = page

        # -------------------- Global state --------------------
        self.ui_busy = False
        self.busy_reason = ""
        self._all_logs: list[str] = []
        self._only_error = True  # UI 預設只看 WARN+
        self._last_overview_data: dict = {}

        # -------------------- Overview state --------------------
        self.overview_text = ft.Text("", selectable=True)
        self.overview_status = ft.Text(
            "狀態：就緒", color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD
        )
        self.overview_trace = ft.Text(
            "trace: init", size=11, color=ft.Colors.GREY_700, selectable=True
        )

        # top actions（總覽區先統一成共用按鈕樣式）
        self.btn_reload_all = primary_button(
            "重新載入全部",
            icon=ft.Icons.REFRESH,
            tooltip="重新載入各類型 cache",
            on_click=self._on_reload_all,
        )
        self.btn_refresh_stats = secondary_button(
            "刷新統計",
            icon=ft.Icons.ANALYTICS,
            tooltip="更新總覽統計數據",
            on_click=self._on_refresh_stats,
        )
        self.btn_rebuild_index = secondary_button(
            "重建搜尋索引",
            icon=ft.Icons.SEARCH,
            tooltip="重建全文搜尋索引（提升搜尋速度）",
            on_click=self._on_rebuild_index,
        )

        # list + log controls
        self.type_list = ft.ListView(expand=True, spacing=6, auto_scroll=True)
        self.log_list = ft.ListView(expand=True, spacing=2, auto_scroll=True)
        self.btn_log_clear = ft.TextButton(
            "清空", icon=ft.Icons.DELETE_SWEEP, on_click=lambda e: self._clear_logs()
        )
        self.btn_log_copy = ft.TextButton(
            "複製全部", icon=ft.Icons.CONTENT_COPY, on_click=lambda e: self._copy_logs()
        )
        self.sw_log_only_error = ft.Switch(
            label="只看警告以上", value=True, on_change=self._on_log_filter_changed
        )

        # 組裝頁面
        self.overview_page = self._build_overview_page()

        self.controls = [
            ft.Container(
                padding=ft.padding.only(bottom=6),
                content=ft.Row(
                    [
                        ft.Text(
                            "快取總覽 (Cache Overview)",
                            size=20,
                            weight=ft.FontWeight.BOLD,
                        )
                    ]
                ),
            ),
            self.overview_page,
        ]

    # =========================================================
    # Lifecycle
    # =========================================================
    def did_mount(self):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_load_overview`

        回傳：None
        """
        try:
            self._load_overview()
            self.page.update()
        except Exception as ex:
            log_error(f"CacheOverviewView did_mount failed: {ex}")
            log_error(traceback.format_exc())
            self.overview_status.value = "狀態：初始化失敗"
            self.overview_status.color = ft.Colors.RED_700
            self.overview_trace.value = f"trace: did_mount error -> {ex}"
            try:
                self.page.update()
            except Exception:
                pass

    # =========================================================
    # UI Building
    # =========================================================
    def _build_overview_page(self):
        """總覽頁組裝。

        已抽到 cache_manager/cache_overview_panel.py：
        - 本檔保留事件路由與資料狀態
        - 大段 UI 結構移到 panel，降低主檔閱讀負擔
        """
        return build_overview_page(
            overview_text=self.overview_text,
            type_list=self.type_list,
            overview_status=self.overview_status,
            overview_trace=self.overview_trace,
            btn_reload_all=self.btn_reload_all,
            btn_refresh_stats=self.btn_refresh_stats,
            btn_rebuild_index=self.btn_rebuild_index,
            sw_log_only_error=self.sw_log_only_error,
            btn_log_copy=self.btn_log_copy,
            btn_log_clear=self.btn_log_clear,
            log_list=self.log_list,
        )

    # =========================================================
    # Log methods
    # =========================================================
    def _render_logs(self):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`clear`

        回傳：None
        """
        self.log_list.controls.clear()
        rows = self._all_logs
        if self._only_error:
            rows = [x for x in rows if ("[ERROR" in x or "[WARN" in x)]
        for line in rows[-800:]:
            self.log_list.controls.append(ft.Text(line, size=12, selectable=True))
        self.page.update()

    def _on_log_filter_changed(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`bool`, `_render_logs`

        回傳：None
        """
        self._only_error = bool(self.sw_log_only_error.value)
        self._render_logs()

    def _clear_logs(self):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`clear`, `_render_logs`

        回傳：None
        """
        self._all_logs.clear()
        self._render_logs()

    def _copy_logs(self):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`join`, `set_clipboard`

        回傳：None
        """
        txt = "\n".join(self._all_logs)
        try:
            self.page.set_clipboard(txt)
            self._show_snack_bar("已複製日誌", ft.Colors.BLUE_400)
        except Exception:
            self._show_snack_bar("複製失敗", ft.Colors.RED_400)

    def _append_log(self, text: str):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_render_logs`

        回傳：None
        """
        if text.startswith("[ERROR"):
            log_error(text)
        elif text.startswith("[WARN"):
            log_warning(text)
        else:
            log_info(text)

        self._all_logs.append(text)
        if len(self._all_logs) > 1500:
            self._all_logs = self._all_logs[-1500:]
        self._render_logs()

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_400):
        """
        顯示底部的快訊通知 (SnackBar)
        :param message: 要顯示的文字訊息
        :param color: SnackBar 的背景顏色，預設為 RED_400
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _notify(self, message: str, level: str = "info"):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：None
        """
        lv = (level or "info").lower()
        if lv == "error":
            self._append_log(f"[ERROR/錯誤] {message}")
            self._show_snack_bar(message, ft.Colors.RED_400)
        elif lv == "warn":
            self._append_log(f"[WARN/警告] {message}")
            self._show_snack_bar(message, ft.Colors.AMBER_700)
        else:
            self._append_log(f"[INFO/資訊] {message}")
            self._show_snack_bar(message, ft.Colors.BLUE_400)

    # =========================================================
    # Overview rendering
    # =========================================================
    def _iter_type_states(self, data: dict):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
        raw_types = data.get("types") or {}
        if isinstance(raw_types, dict):
            return raw_types.items()
        if isinstance(raw_types, list):
            pairs = []
            for item in raw_types:
                if isinstance(item, dict):
                    ctype = (
                        item.get("cache_type") or item.get("type") or item.get("name")
                    )
                    if ctype:
                        pairs.append((ctype, item))
            return pairs
        return []

    def _render_type_list(self, data: dict):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`clear`, `_iter_type_states`

        回傳：None
        """
        self.type_list.controls.clear()

        for ctype, st in self._iter_type_states(data):
            entries_count = st.get("entries_count", 0)
            new_count = st.get("session_new_count", 0)
            dirty = bool(st.get("is_dirty", False))
            shard = st.get("active_shard_id", "-")
            shard_entries = int(st.get("active_shard_entries", 0) or 0)
            shard_capacity = int(st.get("shard_capacity", 2500) or 2500)
            usage_ratio = (
                min(1.0, shard_entries / shard_capacity) if shard_capacity > 0 else 0.0
            )

            if usage_ratio >= 1.0:
                usage_color = ft.Colors.RED_500
                usage_text_color = ft.Colors.RED_700
            elif usage_ratio >= 0.9:
                usage_color = ft.Colors.AMBER_500
                usage_text_color = ft.Colors.AMBER_800
            else:
                usage_color = ft.Colors.BLUE_400
                usage_text_color = ft.Colors.BLUE_700

            status_chip = ft.Container(
                padding=ft.padding.symmetric(horizontal=8, vertical=2),
                border_radius=20,
                bgcolor=ft.Colors.AMBER_100 if dirty else ft.Colors.GREEN_100,
                content=ft.Text("有變更" if dirty else "無變更", size=11),
            )

            actions = ft.Row(
                [
                    ft.TextButton(
                        "重新載入",
                        icon=ft.Icons.REFRESH,
                        on_click=lambda e, t=ctype: self._on_reload_one(t),
                    ),
                    ft.TextButton(
                        "新分片",
                        icon=ft.Icons.SAVE,
                        on_click=lambda e, t=ctype: self._on_save_one_new(t),
                    ),
                    ft.TextButton(
                        "補滿舊檔",
                        icon=ft.Icons.SAVE_AS,
                        on_click=lambda e, t=ctype: self._on_save_one_fill(t),
                    ),
                    ft.TextButton(
                        "輪替分片",
                        icon=ft.Icons.ROTATE_RIGHT,
                        on_click=lambda e, t=ctype: self._on_rotate_one(t),
                    ),
                    ft.TextButton(
                        "分析",
                        icon=ft.Icons.INSIGHTS,
                        on_click=lambda e, t=ctype: self._on_analyze_one(t),
                    ),
                ],
                wrap=True,
            )

            self.type_list.controls.append(
                ft.Container(
                    border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=10,
                    padding=10,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(ctype, weight=ft.FontWeight.BOLD),
                                    ft.Container(expand=True),
                                    status_chip,
                                ]
                            ),
                            ft.Text(
                                f"筆數: {entries_count} | 新增: {new_count} | 分片: {shard}",
                                size=12,
                                color=ft.Colors.GREY_700,
                            ),
                            ft.Text(
                                f"分片使用率: {shard_entries}/{shard_capacity}",
                                size=11,
                                color=usage_text_color,
                            ),
                            ft.ProgressBar(
                                value=usage_ratio,
                                height=6,
                                color=usage_color,
                                bgcolor=ft.Colors.BLUE_50,
                            ),
                            actions,
                        ],
                        spacing=6,
                    ),
                )
            )

        if not self.type_list.controls:
            self.type_list.controls.append(
                ft.Text("目前沒有可顯示的分類資料", color=ft.Colors.GREY_600)
            )

        self.page.update()

    def _refresh_overview_ui(self, data: dict):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`strftime`, `_render_type_list`

        回傳：None
        """
        self._last_overview_data = data or {}
        ts = time.strftime("%H:%M:%S")
        self.overview_text.value = (
            f"總筆數: {data.get('total_entries', 0)} | "
            f"有變更的類型: {data.get('dirty_type_count', 0)} | "
            f"最近重新載入: {data.get('last_reload_at', '-') or '-'} | "
            f"最近儲存: {data.get('last_save_at', '-') or '-'} | "
            f"快取根目錄: {data.get('cache_root', '-') or '-'} | "
            f"UI更新: {ts}"
        )
        self._render_type_list(data)

    def _load_overview(self):
        """載入此函式的工作（細節以程式碼為準）。

        - 主要包裝：`cache_get_overview_service`, `_refresh_overview_ui`

        回傳：None
        """
        try:
            data = cache_get_overview_service()
        except Exception as ex:
            self._append_log(f"[WARN] 讀取總覽失敗：{ex}")
            self._append_log(traceback.format_exc())
            data = {}

        self._refresh_overview_ui(data)

    # =========================================================
    # UI State
    # =========================================================
    def _set_state(self, busy: bool, reason: str, trace: str):
        """設定此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_refresh_disabled_state`

        回傳：None
        """
        self.ui_busy = busy
        self.busy_reason = reason

        if busy:
            if reason == "RELOADING":
                label = "重新載入中"
            elif reason == "SAVING":
                label = "儲存中"
            elif reason == "ROTATING":
                label = "輪替分片中"
            else:
                label = "處理中"
            self.overview_status.value = f"狀態：{label}..."
            self.overview_status.color = ft.Colors.BLUE_700
        else:
            self.overview_status.value = "狀態：就緒"
            self.overview_status.color = ft.Colors.GREEN_700

        self.overview_trace.value = trace
        self._refresh_disabled_state()
        self.page.update()

    def _refresh_disabled_state(self):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：None
        """
        if hasattr(self, "btn_reload_all"):
            self.btn_reload_all.disabled = self.ui_busy
        if hasattr(self, "btn_refresh_stats"):
            self.btn_refresh_stats.disabled = self.ui_busy
        if hasattr(self, "btn_rebuild_index"):
            self.btn_rebuild_index.disabled = self.ui_busy

    # =========================================================
    # Action execution
    # =========================================================
    def _run_action(self, reason: str, work_fn, success_msg: str):
        """執行此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_append_log`, `_set_state`, `work_fn`

        回傳：None
        """
        if self.ui_busy:
            self._notify("目前正在處理，請稍候", "warn")
            return

        action_id = int(time.time() * 1000) % 1000000
        self._append_log(f"[ACTION#{action_id}] start {reason}")
        self._set_state(True, reason, f"trace: ACTION#{action_id} start {reason}")

        try:
            data = work_fn()
            self._refresh_overview_ui(data)
            self._append_log(f"[ACTION#{action_id}] success {reason}")
            self._notify(success_msg, "info")
        except Exception as ex:
            self._append_log(f"[ACTION#{action_id}] error {reason}: {ex}")
            self._append_log(traceback.format_exc())
            self._notify(f"{reason} 失敗: {ex}", "error")
        finally:
            self._append_log(f"[ACTION#{action_id}] finish READY")
            self._set_state(False, "READY", f"trace: ACTION#{action_id} ready")
            self._append_log(f"[STATE] {self.overview_status.value}")

    # =========================================================
    # Event handlers - Overview actions
    # =========================================================
    def _on_reload_all(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：None
        """
        self._run_action(
            "RELOADING", lambda: cache_reload_service(), "已重新載入全部快取"
        )

    def _on_save_all_new(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：None
        """
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(write_new_shard=True),
            "已儲存全部新分片",
        )

    def _on_save_all_fill(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：None
        """
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(write_new_shard=False),
            "已補滿活躍分片",
        )

    def _on_refresh_stats(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_load_overview`, `_notify`

        回傳：None
        """
        self._load_overview()
        self._notify("已刷新統計", "info")

    def _on_rebuild_index(self, e):
        """重建搜尋索引（A3 功能）"""
        if self.ui_busy:
            self._notify("目前正在處理，請稍候", "warn")
            return

        self._set_state(True, "INDEXING", "trace: 正在重建搜尋索引...")

        try:
            result = cache_rebuild_index_service()

            if result.get("success"):
                msg = result.get("message", "重建完成")
                self._append_log(f"[INFO] {msg}")
                self._notify(msg, "info")
            else:
                error = result.get("error", "未知錯誤")
                self._append_log(f"[ERROR] 重建索引失敗: {error}")
                self._notify(f"重建失敗: {error}", "error")

        except Exception as ex:
            self._append_log(f"[ERROR] 重建索引異常: {ex}")
            self._append_log(traceback.format_exc())
            self._notify(f"重建失敗: {ex}", "error")

        finally:
            self._set_state(False, "READY", "trace: 重建完成")

    # per-type actions
    def _on_reload_one(self, cache_type: str):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：None
        """
        self._run_action(
            "RELOADING",
            lambda: cache_reload_type_service(cache_type),
            f"已重新載入單一分類：{cache_type}",
        )

    def _on_save_one_new(self, cache_type: str):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：None
        """
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(
                write_new_shard=True, only_types=[cache_type]
            ),
            f"已儲存新分片：{cache_type}",
        )

    def _on_save_one_fill(self, cache_type: str):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：None
        """
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(
                write_new_shard=False, only_types=[cache_type]
            ),
            f"已補滿舊檔：{cache_type}",
        )

    def _on_rotate_one(self, cache_type: str):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_run_action`

        回傳：依函式內 return path。
        """

        def _work():
            """處理此函式的工作（細節以程式碼為準）。

            - 主要包裝：`cache_rotate_service`, `cache_get_overview_service`

            回傳：依函式內 return path。
            """
            ok = cache_rotate_service(cache_type)
            if not ok:
                raise RuntimeError(f"輪替失敗: {cache_type}")
            return cache_get_overview_service()

        self._run_action("ROTATING", _work, f"已輪替分片：{cache_type}")

    def _on_analyze_one(self, cache_type: str):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_iter_type_states`

        回傳：None
        """
        target = None
        for ctype, st in self._iter_type_states(self._last_overview_data):
            if ctype == cache_type:
                target = st
                break

        if not target:
            self._notify(f"找不到分類資料：{cache_type}", "warn")
            return

        entries_count = target.get("entries_count", 0)
        new_count = target.get("session_new_count", 0)
        dirty = "有變更" if bool(target.get("is_dirty", False)) else "無變更"
        shard = target.get("active_shard_id", "-")
        shard_entries = int(target.get("active_shard_entries", 0) or 0)
        shard_capacity = int(target.get("shard_capacity", 2500) or 2500)
        message = f"分析 {cache_type}：筆數={entries_count}，新增={new_count}，狀態={dirty}，分片={shard}，使用率={shard_entries}/{shard_capacity}"
        self._append_log(f"[ANALYZE] {message}")
        self._notify(message, "info")
