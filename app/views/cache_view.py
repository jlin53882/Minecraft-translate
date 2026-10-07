"""app/views/cache_view.py（快取管理頁）

本頁是快取系統的 UI 入口，功能包含：
- 總覽：各 cache_type 的統計、重載、儲存、新分片/補滿舊檔、輪替分片
- 查詢：依 key / dst / 關鍵字搜尋（含全文索引）
- 編輯：針對單筆 dst 做調整並寫回快取
- 歷史：查看並套用歷史版本（查詢區與分片區共用一套浮動視窗）

維護注意：
- 這個檔案偏大是歷史因素；PR 期間已把「總覽頁 UI 組裝」抽到
  `app.views.cache_manager.cache_overview_panel` 以降低閱讀負擔。
- 本頁大量事件回呼會觸發背景操作；若要避免舊任務覆蓋新狀態，
  需配合 CacheController 的 action_id 機制。
- 本輪只補註解/docstring，不改任何 UI 行為。
"""

import asyncio
import traceback

import flet as ft

from app.services_impl.cache.cache_services import (
    cache_get_overview_service,
)
from app.ui import kit

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.cache_manager.cache_view_history import CacheHistoryMixin
from app.views.cache_manager.cache_view_overview import CacheOverviewMixin
from app.views.cache_manager.cache_view_query import CacheQueryMixin
from app.views.cache_manager.cache_view_query_widgets import CacheQueryWidgetsMixin
from app.views.cache_manager.cache_view_shard import CacheShardMixin
from app.views.cache_manager.cache_view_shard_detail import CacheShardDetailMixin
from app.views.cache_manager.cache_view_shard_widgets import CacheShardWidgetsMixin
from translation_tool.utils.log_unit import log_error, log_info, log_warning


class CacheView(
    CacheOverviewMixin,
    CacheQueryMixin,
    CacheQueryWidgetsMixin,
    CacheShardMixin,
    CacheShardDetailMixin,
    CacheShardWidgetsMixin,
    CacheHistoryMixin,
    ft.Column,
):
    """快取管理器（UI）。

    本類別是 UI 組裝與事件處理的集中點。

    維護重點（避免踩坑）：
    - 任何會跑背景任務的操作（reload/save/rebuild index/search）都應
      更新 ui_busy/busy_reason，並透過統一的 log 訊息回饋給使用者。
    - 查詢結果的顯示/分頁/選取狀態彼此耦合，改動時要注意同步更新
      `query_results/query_selected_result/query_page`。
    - 歷史視窗同時支援 query 與 shard 兩種來源，靠 history_window_source
      切換；擴充時避免分叉出兩套近似 UI。
    """

    def __init__(self, page: ft.Page):
        """初始化 CacheView。

        參數：
            page: Flet Page 物件
        """
        # Initialize attributes BEFORE super().__init__() to avoid _notify errors
        self._all_logs: list[str] = []
        self._page = page
        super().__init__(expand=True, spacing=10)

        # -------------------- 效能優化：髒標記機制 --------------------
        # PR5-7 整合：減少 update() 呼叫次數，避免 UI 卡顿
        self._dirty_flags = {
            "overview": False,
            "query": False,
            "shard": False,
            "logs": False,
        }
        self._update_debouncer = Debouncer(lambda: self.page, 0.1)

        # -------------------- Global state --------------------
        self.ui_busy = False
        self.busy_reason = ""
        self._all_logs: list[str] = []
        self._only_error = True  # UI 預設只看 WARN+
        self._last_overview_data: dict = {}

        # -------------------- Overview state --------------------
        self.overview_text = ft.Text("", selectable=True)
        self.overview_status = ft.Text(
            "狀態：就緒", color=C.EM, weight=ft.FontWeight.BOLD
        )
        self.overview_trace = ft.Text(
            "trace: init", size=11, color=C.MUTED, selectable=True
        )

        # top actions（總覽區先統一成共用按鈕樣式）
        self.btn_reload_all = kit.button(
            "重新載入全部",
            "primary",
            icon=ft.Icons.REFRESH,
            tooltip="重新載入各類型 cache",
            on_click=self._on_reload_all,
        )
        self.btn_refresh_stats = kit.button(
            "刷新統計",
            "secondary",
            icon=ft.Icons.ANALYTICS,
            tooltip="更新總覽統計數據",
            on_click=self._on_refresh_stats,
        )
        self.btn_rebuild_index = kit.button(
            "重建搜尋索引",
            "secondary",
            icon=ft.Icons.SEARCH,
            tooltip="重建全文搜尋索引（提升搜尋速度）",
            on_click=self._on_rebuild_index,
        )

        # 「補滿舊檔」是覆寫既有分片的高風險動作：勾選後才會執行（_on_save_*_fill 會檢查）
        self.chk_danger_confirm = ft.Checkbox(
            label="我了解「補滿舊檔」會覆寫既有分片（高風險）",
            value=False,
        )

        # list + log controls
        self.type_list = ft.ListView(expand=True, spacing=6, auto_scroll=True)
        self.log_list = ft.ListView(expand=True, spacing=2, auto_scroll=True)
        self.btn_log_clear = ft.TextButton(
            "清空", icon=ft.Icons.DELETE_SWEEP, on_click=lambda e: self._clear_logs()
        )
        self.btn_log_copy = ft.TextButton(
            "複製全部", icon=ft.Icons.CONTENT_COPY, on_click=self._copy_logs
        )
        self.sw_log_only_error = ft.Switch(
            label="只看警告以上", value=True, on_change=self._on_log_filter_changed
        )

        # 總覽頂端的統計卡（載入資料後由 _refresh_overview_ui 更新）
        self.stat_total = kit.stat_card(
            "快取總筆數", "—", icon=ft.Icons.STORAGE_OUTLINED, tone="ench", expand=1
        )
        self.stat_dirty = kit.stat_card(
            "有變更的類型", "—", icon=ft.Icons.EDIT_NOTE, tone="gold", expand=1
        )
        self.stat_types = kit.stat_card(
            "快取類型", "—", icon=ft.Icons.CATEGORY_OUTLINED, tone="dia", expand=1
        )
        self.stat_root = kit.stat_card(
            "最近儲存", "—", icon=ft.Icons.SAVE_OUTLINED, tone="em", expand=1
        )

        self._build_query_widgets()

    # =========================================================
    # 效能優化：髒標記機制（PR5-7 整合）
    # =========================================================
    def _mark_dirty(self, area: str):
        """標記某區域需要更新"""
        if area not in self._dirty_flags:
            return
        self._dirty_flags[area] = True
        self._schedule_update()

    def _schedule_update(self):
        """排程更新（debounce 100ms）"""
        # 防呆：確保已在 page 上
        if not hasattr(self, "page") or self.page is None:
            return

        self._update_debouncer.call(self._do_update)

    def _do_update(self):
        """批次更新所有髒區域"""
        # 防呆：確保已在 page 上
        if not hasattr(self, "page") or self.page is None:
            return
        if not any(self._dirty_flags.values()):
            return
        try:
            # 根據髒標記決定要更新的區域
            if self._dirty_flags.get("overview"):
                pass  # overview 由背景任務觸發更新
            if self._dirty_flags.get("query"):
                pass  # query 由背景任務觸發更新
            if self._dirty_flags.get("shard"):
                pass  # shard 由背景任務觸發更新
            if self._dirty_flags.get("logs"):
                pass  # logs 由背景任務觸發更新

            self.update()
            for k in self._dirty_flags:
                self._dirty_flags[k] = False
        except (AttributeError, AssertionError):
            # 控件尚未添加到 page，略過
            pass
        except Exception as e:  # noqa: BLE001
            log_error(f"[CacheView] 更新失敗: {e!r}")

    def _batch_refresh(self):
        """批量刷新所有區域（用於初始載入）"""
        try:
            self.update()
        except Exception as e:  # noqa: BLE001
            log_error(f"[CacheView] 批量刷新失敗: {e!r}")

    # =========================================================
    # Lifecycle
    # =========================================================
    def will_unmount(self):
        """換頁／關閉：取消尚未執行的 debounce 更新（idempotent）。"""
        self._update_debouncer.cancel()

    def did_mount(self):
        """元件載入完成後初始化資料與 UI。

        總覽需要載入整個快取（大量資料時數秒），有 event loop 時改在執行緒讀取，
        頁面先顯示「載入中」，不再阻塞切換頁面。
        """
        if self.page is not None:
            self.page.on_resized = self._on_page_resized
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._finish_mount(self._fetch_overview())
            return

        try:
            self._set_state(True, "LOADING", "trace: 載入快取總覽…")
        except Exception:  # noqa: BLE001, S110 - 尚未完成掛載時略過
            pass

        async def _load():
            fetched = await asyncio.to_thread(self._fetch_overview)
            self._finish_mount(fetched)

        run_task(_load)

    def _fetch_overview(self):
        """讀取快取總覽（可在背景執行緒執行）；回傳 (data, error, traceback)。"""
        try:
            data = cache_get_overview_service()
            self._warm_shard_cache(
                data
            )  # 在背景執行緒預熱分片摘要，渲染時不必解析 JSON
            return data, None, None
        except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
            return {}, ex, traceback.format_exc()

    def _finish_mount(self, fetched):
        """（event loop 上）套用總覽並渲染頁面。"""
        try:
            data, error, tb = fetched
            if error is not None:
                self._append_log(f"[WARN] 讀取總覽失敗：{error}")
                self._append_log(tb)
            if self.ui_busy and self.busy_reason == "LOADING":
                self.ui_busy = False
                self.busy_reason = "READY"
                self.overview_status.value = "狀態：就緒"
                self.overview_status.color = C.EM
                self.overview_trace.value = "trace: 總覽載入完成"
            self._refresh_overview_ui(data)
            self._refresh_query_type_options()
            self._render_query_type_shard_page()
            self._render_query_results()
            self._render_query_detail()
            self._refresh_disabled_state()
            # PR5-7: 使用批量刷新優化初始載入
            self._batch_refresh()
        except Exception as ex:  # noqa: BLE001
            log_error(f"CacheView did_mount failed: {ex!r}")
            log_error(traceback.format_exc())
            self.overview_status.value = "狀態：初始化失敗"
            self.overview_status.color = C.RED
            self.overview_trace.value = f"trace: did_mount error -> {ex}"
            try:
                self.update()
            except Exception:  # noqa: BLE001, S110
                pass

    def _on_page_resized(self, e):
        # 重繪分類/分片與 C1 KeyListCard，讓大小可跟視窗動態變更
        """頁面大小變更時重新調整元件。"""
        try:
            if hasattr(self, "shard_key_column"):
                self.shard_key_column.width = self._dynamic_shard_key_panel_width()
            self._render_query_type_shard_page()
            self._render_shard_detail_keys()
            self.update()
        except Exception:  # noqa: BLE001, S110
            pass

    def _set_state(self, busy: bool, reason: str, trace: str):
        """更新忙碌狀態與原因"""
        self.ui_busy = busy
        self.busy_reason = reason

        if busy:
            if reason == "RELOADING":
                label = "重新載入中"
            elif reason == "SAVING":
                label = "儲存中"
            elif reason == "ROTATING":
                label = "輪替分片中"
            elif reason == "LOADING":
                label = "載入中"
            else:
                label = "處理中"
            self.overview_status.value = f"狀態：{label}..."
            self.overview_status.color = C.DIA
        else:
            self.overview_status.value = "狀態：就緒"
            self.overview_status.color = C.EM

        self.overview_trace.value = trace
        if hasattr(self, "query_status"):
            self._recalc_query_status()
        self._refresh_disabled_state()
        self.update()

    def _refresh_disabled_state(self):
        """根據忙碌狀態更新按鈕啟用/禁用"""
        self._refresh_overview_disabled_state()

        self._refresh_query_disabled_state()

        self._refresh_shard_disabled_state()

    def _refresh_overview_disabled_state(self) -> None:
        """總覽頁按鈕的啟用／禁用。"""
        if hasattr(self, "btn_reload_all"):
            self.btn_reload_all.disabled = self.ui_busy
        if hasattr(self, "btn_refresh_stats"):
            self.btn_refresh_stats.disabled = self.ui_busy
        if hasattr(self, "btn_rebuild_index"):
            self.btn_rebuild_index.disabled = self.ui_busy

    def _refresh_query_disabled_state(self) -> None:
        """查詢頁按鈕與分頁的啟用／禁用。"""
        if hasattr(self, "btn_query_refresh_index"):
            self.btn_query_refresh_index.disabled = self.ui_busy
        if hasattr(self, "btn_query_search"):
            self.btn_query_search.disabled = self.ui_busy
        if hasattr(self, "btn_query_clear"):
            self.btn_query_clear.disabled = self.ui_busy
        if hasattr(self, "btn_page_first"):
            self.btn_page_first.disabled = (
                self.ui_busy or getattr(self, "query_page", 1) <= 1
            )
        if hasattr(self, "btn_page_prev"):
            self.btn_page_prev.disabled = (
                self.ui_busy or getattr(self, "query_page", 1) <= 1
            )
        if hasattr(self, "btn_page_next"):
            self.btn_page_next.disabled = self.ui_busy or getattr(
                self, "query_page", 1
            ) >= getattr(self, "query_total_pages", 1)
        if hasattr(self, "btn_page_last"):
            self.btn_page_last.disabled = self.ui_busy or getattr(
                self, "query_page", 1
            ) >= getattr(self, "query_total_pages", 1)
        if hasattr(self, "dd_page_size"):
            self.dd_page_size.disabled = self.ui_busy
        if hasattr(self, "tf_page_jump"):
            self.tf_page_jump.read_only = self.ui_busy
        if hasattr(self, "btn_apply_dst"):
            self.btn_apply_dst.disabled = (
                self.ui_busy or getattr(self, "query_selected_result", None) is None
            )
        if hasattr(self, "btn_revert_dst"):
            self.btn_revert_dst.disabled = (
                self.ui_busy or getattr(self, "query_selected_result", None) is None
            )
        if hasattr(self, "btn_apply_history_old"):
            self.btn_apply_history_old.disabled = (
                self.ui_busy
                or getattr(self, "query_history_selected_event", None) is None
            )
        if hasattr(self, "btn_back_to_shard_list"):
            self.btn_back_to_shard_list.disabled = self.ui_busy

    def _refresh_shard_disabled_state(self) -> None:
        """分片頁按鈕與分頁的啟用／禁用。"""
        if hasattr(self, "btn_shard_page_first"):
            self.btn_shard_page_first.disabled = (
                self.ui_busy or getattr(self, "shard_detail_page", 1) <= 1
            )
        if hasattr(self, "btn_shard_page_prev"):
            self.btn_shard_page_prev.disabled = (
                self.ui_busy or getattr(self, "shard_detail_page", 1) <= 1
            )
        if hasattr(self, "btn_shard_page_next"):
            self.btn_shard_page_next.disabled = self.ui_busy or getattr(
                self, "shard_detail_page", 1
            ) >= getattr(self, "shard_detail_total_pages", 1)
        if hasattr(self, "btn_shard_page_last"):
            self.btn_shard_page_last.disabled = self.ui_busy or getattr(
                self, "shard_detail_page", 1
            ) >= getattr(self, "shard_detail_total_pages", 1)
        if hasattr(self, "tf_shard_key_filter"):
            self.tf_shard_key_filter.read_only = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_file", "")
            )
        if hasattr(self, "btn_shard_src_preview"):
            self.btn_shard_src_preview.disabled = (
                self.ui_busy
                or not bool(getattr(self, "shard_detail_selected_key", ""))
                or getattr(self, "shard_detail_src_mode", "preview") == "preview"
            )
        if hasattr(self, "btn_shard_src_raw"):
            self.btn_shard_src_raw.disabled = (
                self.ui_busy
                or not bool(getattr(self, "shard_detail_selected_key", ""))
                or getattr(self, "shard_detail_src_mode", "preview") == "raw"
            )
        if hasattr(self, "btn_shard_dst_apply"):
            self.btn_shard_dst_apply.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_shard_dst_revert"):
            self.btn_shard_dst_revert.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_shard_dst_copy"):
            self.btn_shard_dst_copy.disabled = (
                self.ui_busy
                or not bool(getattr(self, "shard_detail_selected_key", ""))
                or not bool(
                    str(
                        getattr(getattr(self, "shard_dst_field", None), "value", "")
                        or ""
                    ).strip()
                )
            )
        if hasattr(self, "btn_shard_dst_restore_latest"):
            self.btn_shard_dst_restore_latest.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_open_shard_history_drawer"):
            self.btn_open_shard_history_drawer.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_shard_apply_history_old"):
            self.btn_shard_apply_history_old.disabled = (
                self.ui_busy
                or getattr(self, "shard_history_selected_event", None) is None
            )

    # 與舊測試相容：集中提交 UI 更新
    def commit_ui(self, controls=None):
        """批次更新多個 UI 控制項"""
        try:
            for c in controls or []:
                if hasattr(c, "update"):
                    c.update()
            if hasattr(self, "page") and self.page:
                self.update()
        except Exception as ex:  # noqa: BLE001
            self._append_log(f"[WARN] UI refresh 異常: {ex}")

    def _append_log(self, text: str):
        """新增日誌訊息並根據等級記錄"""
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

    def _notify(self, message: str, level: str = "info"):
        """根據等級顯示訊息並記錄日誌"""
        # Handle boolean level (when called as property setter callback in Flet 0.85+)
        if isinstance(level, bool):
            level = "info"
        # Skip if not fully initialized (during __init__)
        if not hasattr(self, "_all_logs") or not hasattr(self, "log_list"):
            return
        lv = (level or "info").lower()
        if lv == "error":
            self._append_log(f"[ERROR/錯誤] {message}")
            show_snack(self.page, message, C.RED)
        elif lv == "warn":
            self._append_log(f"[WARN/警告] {message}")
            show_snack(self.page, message, C.GOLD)
        else:
            self._append_log(f"[INFO/資訊] {message}")
            show_snack(self.page, message, C.DIA)

    def _normalize_cache_text(self, text: str) -> str:
        """正規化快取文字（移除跳脫序列）"""
        return str(text or "").replace("\\r\\n", "\n").replace("\\n", "\n")

    def _type_dirty_text(self, cache_type: str) -> str:
        """取得指定類型的髒污狀態文字"""
        for ctype, st in self._iter_type_states(self._last_overview_data):
            if ctype == cache_type:
                return "dirty" if bool(st.get("is_dirty", False)) else "clean"
        return "-"

    def _on_tab_change(self, e):
        """Tab 切換時自動關閉歷史紀錄視窗（總覽/管理 ↔ 查詢）"""
        if self.query_history_window.visible:
            self.query_history_window.visible = False
            self.history_window_source = None
        if self.shard_history_window.visible:
            self.shard_history_window.visible = False
            self.history_window_source = None
        self.update()

    @property
    def page(self):
        return self._page
