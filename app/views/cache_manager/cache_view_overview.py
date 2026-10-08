"""CacheView 的總覽頁：統計卡、類型清單、批次操作與日誌。

由 ``app/views/cache_view.py`` 拆出（#114），方法內容未改；``CacheView`` 以多重繼承組合這些 mixin。
"""

import asyncio
import time
import traceback

import flet as ft

from app.services_impl.cache.cache_services import (
    cache_get_overview_service,
    cache_rebuild_index_service,  # A3 搜尋功能
    cache_reload_service,
    cache_reload_type_service,
    cache_rotate_service,
    cache_save_all_service,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    ShutdownPolicy,
    reserve_page_operation,
)
from app.ui import design, kit

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.ui.snack import show_snack
from app.views.cache_manager.cache_actions import run_cache_action
from app.views.cache_manager.cache_overview_panel import build_overview_page
from translation_tool.utils.log_unit import log_warning


class CacheOverviewMixin:
    """總覽頁：統計卡、類型清單、批次操作與日誌。"""

    # =========================================================
    # Overview page
    # =========================================================
    def _build_overview_page(self):
        """總覽頁組裝（非查詢區）。

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
            btn_rebuild_index=self.btn_rebuild_index,  # A3 搜尋功能
            chk_danger_confirm=self.chk_danger_confirm,
            sw_log_only_error=self.sw_log_only_error,
            btn_log_copy=self.btn_log_copy,
            btn_log_clear=self.btn_log_clear,
            log_list=self.log_list,
            page=self.page,
            stat_cards=[
                self.stat_total,
                self.stat_dirty,
                self.stat_types,
                self.stat_root,
            ],
        )

    def _render_logs(self):
        """渲染日誌列表 UI"""
        self.log_list.controls.clear()
        rows = self._all_logs
        if self._only_error:
            rows = [x for x in rows if ("[ERROR" in x or "[WARN" in x)]
        # PR refactor/unified-log-view: 用 theme token 統一顏色
        # 從字串前綴判 level（cache_view 保留 event-driven 架構，不接 LogView）
        for line in rows[-800:]:
            if line.startswith("[ERROR"):
                text_color = C.RED
            elif line.startswith("[WARN"):
                text_color = C.GOLD
            elif line.startswith(("[系統", "[SYS")):
                text_color = C.EM
            else:
                text_color = C.MUTED
            self.log_list.controls.append(
                ft.Text(line, size=12, color=text_color, selectable=True)
            )
        # PR5-7: 使用局部更新替代全頁更新
        self.update()

    def _on_log_filter_changed(self, e):
        """日誌篩選條件變更時重新渲染"""
        self._only_error = bool(self.sw_log_only_error.value)
        self._render_logs()

    def _clear_logs(self):
        """清除所有日誌記錄"""
        self._all_logs.clear()
        self._render_logs()

    async def _copy_logs(self, e=None):
        """複製所有日誌到剪貼簿"""
        txt = "\n".join(self._all_logs)
        try:
            await ft.Clipboard().set(txt)
            show_snack(self.page, "已複製日誌", C.DIA)
        except Exception:  # noqa: BLE001
            show_snack(self.page, "複製失敗", C.RED)

    def _iter_type_states(self, data: dict):
        """迭代所有快取類型與其狀態。

        回傳值：
        - list of (ctype, state_dict): 正常迭代
        - None: 讀取失敗（資料結構異常，區分「無資料」）
        """
        if not isinstance(data, dict):
            log_warning(
                f"[_iter_type_states] 預期 dict，收到 {type(data).__name__}，回傳 None 表示讀取失敗"
            )
            return None

        raw_types = data.get("types")
        if raw_types is None:
            # 完全沒有 types 欄位 → 視為讀取失敗（區分「有 types 但是空的」）
            log_warning("[_iter_type_states] data 缺少 'types' 欄位，視為讀取失敗")
            return None

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

    def _iter_type_states_safe(self, data: dict):
        """_iter_type_states 的安全包裝：None 時回傳空列表，保持迭代語意一致。"""
        result = self._iter_type_states(data)
        return result if result is not None else []

    def _render_type_list(self, data: dict):
        """渲染快取類型列表 UI"""
        self.type_list.controls.clear()

        type_states = self._iter_type_states(data)
        if type_states is None:
            # 讀取失敗：顯示錯誤狀態
            self.type_list.controls.append(
                kit.empty_state(
                    icon=ft.Icons.ERROR_OUTLINE,
                    title="讀取失敗",
                    subtitle="無法載入快取類型，請檢查日誌或重新整理",
                )
            )
            self.update()
            return

        for ctype, st in type_states:
            self.type_list.controls.append(self._type_card(ctype, st))

        if not self.type_list.controls:
            self.type_list.controls.append(
                kit.empty_state(
                    icon=ft.Icons.INVENTORY_2_OUTLINED,
                    title="沒有分類資料",
                    subtitle="請先建立快取或重新載入",
                )
            )

        # PR5-7: 使用局部更新替代全頁更新
        self.update()

    def _type_card(self, ctype: str, st: dict) -> ft.Container:
        """單一快取類型的狀態卡片。"""
        entries_count = st.get("entries_count", 0)
        new_count = st.get("session_new_count", 0)
        dirty = bool(st.get("is_dirty", False))
        shard = st.get("active_shard_id", "-")
        shard_entries = int(st.get("active_shard_entries", 0) or 0)
        shard_capacity = int(st.get("shard_capacity", 2500) or 2500)
        usage_ratio = (
            min(1.0, shard_entries / shard_capacity) if shard_capacity > 0 else 0.0
        )

        usage_tone = (
            "red" if usage_ratio >= 1.0 else "gold" if usage_ratio >= 0.9 else "dia"
        )
        status_chip = kit.chip(
            "有變更" if dirty else "無變更", "gold" if dirty else "em"
        )

        actions = self._type_actions_row(ctype)

        return ft.Container(
            border=ft.Border.all(1, C.LINE),
            bgcolor=C.PANEL2,
            border_radius=design.RADIUS_CONTROL + 2,
            padding=14,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                ctype,
                                weight=ft.FontWeight.BOLD,
                                color=C.TEXT,
                                font_family=design.FONT_MONO,
                            ),
                            ft.Container(expand=True),
                            status_chip,
                        ]
                    ),
                    ft.Text(
                        f"筆數 {entries_count:,}　新增 {new_count:,}　分片 {shard}",
                        size=12,
                        color=C.MUTED,
                    ),
                    ft.Text(
                        f"分片使用率 {shard_entries:,} / {shard_capacity:,}",
                        size=11,
                        color=get_tone(usage_tone).fg,
                    ),
                    kit.progress_bar(usage_ratio, usage_tone, height=6),
                    actions,
                ],
                spacing=8,
            ),
        )

    def _type_actions_row(self, ctype: str) -> ft.Row:
        """單一類型的操作按鈕列。"""
        return ft.Row(
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
                ft.TextButton(
                    "切換查詢",
                    icon=ft.Icons.MANAGE_SEARCH,
                    on_click=lambda e, t=ctype: self._on_jump_to_query_type(t),
                ),
            ],
            wrap=True,
        )

    def _refresh_overview_ui(self, data: dict):
        """更新總覽區域的統計資料顯示"""
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
        self._update_stat_cards(data or {})
        self._render_type_list(data)

    def _update_stat_cards(self, data: dict) -> None:
        """總覽頂端統計卡。"""
        types = data.get("types") or {}
        dirty = int(data.get("dirty_type_count", 0) or 0)
        self.stat_total.set_value(f"{int(data.get('total_entries', 0) or 0):,}")
        self.stat_dirty.set_value(
            str(dirty),
            delta="尚未寫入磁碟" if dirty else "全部已儲存",
            delta_tone="gold" if dirty else "em",
        )
        self.stat_types.set_value(str(len(types)))
        saved = data.get("last_save_at") or "—"
        self.stat_root.set_value(
            str(saved), delta=str(data.get("cache_root") or ""), delta_tone="neutral"
        )

    def _load_overview(self):
        """從服務載入快取總覽資料"""
        try:
            data = cache_get_overview_service()
        except Exception as ex:  # noqa: BLE001
            self._append_log(f"[WARN] 讀取總覽失敗：{ex}")
            self._append_log(traceback.format_exc())
            data = {}

        self._refresh_overview_ui(data)
        self._refresh_query_type_options()
        self._render_query_type_shard_page()

    def _run_action(
        self, reason: str, work_fn, success_msg: str, show_progress: bool = False
    ):
        """包裝非同步工作函式的執行與狀態更新"""
        return run_cache_action(
            self, reason, work_fn, success_msg, show_progress=show_progress
        )

    # top actions
    def _on_reload_all(self, e):
        """觸發重新載入所有快取（長時間操作）"""
        self._run_action(
            "RELOADING",
            lambda: cache_reload_service(),
            "已重新載入全部快取",
            show_progress=True,
        )

    def _on_save_all_new(self, e):
        """觸發儲存所有新分片（長時間操作）"""
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(write_new_shard=True),
            "已儲存全部新分片",
            show_progress=True,
        )

    def _on_save_all_fill(self, e):
        """觸發補滿所有活躍分片（高風險，長時間操作）"""
        if not self.chk_danger_confirm.value:
            self._notify("尚未勾選高風險確認", "warn")
            return
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(write_new_shard=False),
            "已補滿活躍分片",
            show_progress=True,
        )

    def _on_refresh_stats(self, e):
        """刷新快取統計資料"""
        self._load_overview()
        self._notify("已刷新統計", "info")

    def _on_rebuild_index(self, e):
        """重建搜尋索引（A3 功能）"""
        if self.ui_busy:
            self._notify("目前正在處理，請稍候", "warn")
            return

        self._set_state(True, "INDEXING", "trace: 正在重建搜尋索引...")

        def work():
            try:
                return cache_rebuild_index_service(), None
            except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
                return None, (ex, traceback.format_exc())

        def finish(result, error):
            try:
                if error is not None:
                    ex, tb = error
                    self._append_log(f"[ERROR] 重建索引異常: {ex}")
                    self._append_log(tb)
                    self._notify(f"重建失敗: {ex}", "error")
                elif result.get("success"):
                    msg = result.get("message", "重建完成")
                    self._append_log(f"[INFO] {msg}")
                    self._notify(msg, "info")
                else:
                    err = result.get("error", "未知錯誤")
                    self._append_log(f"[ERROR] 重建索引失敗: {err}")
                    self._notify(f"重建失敗: {err}", "error")
            finally:
                self._set_state(False, "READY", "trace: 重建完成")

        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            finish(*work())
            return

        operation = reserve_page_operation(
            self.page,
            name="快取搜尋索引重建",
            owner="cache-index-rebuild",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            commit=CommitPolicy.PARTIAL_ALLOWED,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
        )
        if not operation.admitted:
            finish(None, RuntimeError("應用程式正在關閉，未啟動索引重建"))
            return
        result = {}

        def rebuild():
            result["value"] = work()

        if not operation.launch(rebuild):
            operation.finish(
                error=RuntimeError("index rebuild worker was not launched")
            )
            finish(None, RuntimeError("無法啟動索引重建"))
            return

        async def _rebuild():
            while not operation.done_event.is_set():
                await asyncio.sleep(0.02)
            finish(*result.get("value", (None, RuntimeError("索引重建無結果"))))

        run_task(_rebuild)

    # overview 集中操作已移除，功能保留在分類卡按鈕

    # per-type actions
    def _on_reload_one(self, cache_type: str):
        """重新載入指定類型的快取"""
        self._run_action(
            "RELOADING",
            lambda: cache_reload_type_service(cache_type),
            f"已重新載入單一分類：{cache_type}",
        )

    def _on_save_one_new(self, cache_type: str):
        """儲存指定類型的新分片"""
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(
                write_new_shard=True, only_types=[cache_type]
            ),
            f"已儲存新分片：{cache_type}",
        )

    def _on_save_one_fill(self, cache_type: str):
        """補滿指定類型的活躍分片（高風險）"""
        if not self.chk_danger_confirm.value:
            self._notify("尚未勾選高風險確認", "warn")
            return
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(
                write_new_shard=False, only_types=[cache_type]
            ),
            f"已補滿舊檔：{cache_type}",
        )

    def _on_rotate_one(self, cache_type: str):
        """處理單一快取類型的分片輪替（Rotation）動作。"""

        def _work():
            """執行分片輪替的工作函式"""
            ok = cache_rotate_service(cache_type)
            if not ok:
                raise RuntimeError(f"輪替失敗: {cache_type}")
            return cache_get_overview_service()

        self._run_action("ROTATING", _work, f"已輪替分片：{cache_type}")

    def _on_analyze_one(self, cache_type: str):
        """分析指定類型的快取內容"""
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

    def _on_jump_to_query_type(self, cache_type: str):
        # 切到查詢頁 -> 查詢區，並預先設定 KEY + 指定分類
        """跳轉到查詢類型頁面。"""
        if hasattr(self, "main_tabs"):
            self.main_tabs.selected_index = 1
        if hasattr(self, "query_sub_tabs"):
            self.query_sub_tabs.selected_index = 0

        if hasattr(self, "dd_query_mode"):
            self.dd_query_mode.value = "KEY"

        if hasattr(self, "dd_query_type"):
            options = [str(opt.key) for opt in (self.dd_query_type.options or [])]
            self.dd_query_type.value = cache_type if cache_type in options else "ALL"

        self.query_search_hint.value = (
            f"已切換到查詢區：模式=KEY，分類={self.dd_query_type.value or 'ALL'}"
        )
        self.query_search_hint.color = C.DIA
        self.update()
