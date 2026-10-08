"""CacheView 的查詢頁：查詢 widgets、搜尋、結果顯示／分頁與單筆編輯。

由 ``app/views/cache_view.py`` 拆出（#114），方法內容未改；``CacheView`` 以多重繼承組合這些 mixin。
"""

import asyncio

import flet as ft

from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_save_all_service,
    cache_search_service,
    cache_update_dst_service,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    ShutdownPolicy,
    current_operation,
    get_page_operation_registry,
    reserve_page_operation,
)
from app.ui import kit

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C
from app.ui.snack import show_snack


class CacheQueryMixin:
    """查詢頁：查詢 widgets、搜尋、結果顯示／分頁與單筆編輯。"""

    # =========================================================
    # Query phase-2: search block
    # =========================================================
    def _refresh_query_type_options(self):
        """更新查詢類型下拉選單的選項"""
        types = sorted(
            [ctype for ctype, _ in self._iter_type_states(self._last_overview_data)]
        )

        if hasattr(self, "dd_query_type"):
            self.dd_query_type.options = [ft.dropdown.Option("ALL", "全部")]
            self.dd_query_type.options.extend([ft.dropdown.Option(t, t) for t in types])
            if not self.dd_query_type.value:
                self.dd_query_type.value = "ALL"

    def _on_query_sub_tab_change(self, e):
        """查詢區內 sub-tab 切換時自動關閉歷史紀錄視窗（查詢區 ↔ 分類/分片）"""
        if self.query_history_window.visible:
            self.query_history_window.visible = False
            self.history_window_source = None
        if self.shard_history_window.visible:
            self.shard_history_window.visible = False
            self.history_window_source = None
        self.update()

    def _render_query_detail(self):
        """渲染查詢詳情面板"""
        row = self.query_selected_result
        if not row:
            self.query_detail_key.value = "Key: -"
            self.query_detail_type.value = "類型: -"
            self.query_detail_shard.value = "Shard: -"
            self.query_detail_status.value = "Cache 狀態: -"
            self.query_detail_src.value = "-"
            self.query_detail_dst.value = ""
            self.query_original_dst = ""
            self._render_query_history()
            return

        ctype = str(row.get("cache_type", ""))
        key = str(row.get("key", ""))
        shard = str(row.get("shard", "-"))

        entry = cache_get_entry_service(ctype, key) or {}
        src = str(entry.get("src", "")).replace("\\r\\n", "\n").replace("\\n", "\n")
        dst = str(entry.get("dst", "")).replace("\\r\\n", "\n").replace("\\n", "\n")

        self.query_detail_key.value = f"Key: {key}"
        self.query_detail_type.value = f"類型: {ctype}"
        self.query_detail_shard.value = f"Shard: {shard}"
        self.query_detail_status.value = f"Cache 狀態: {self._type_dirty_text(ctype)}"
        self.query_detail_src.value = src or "-"
        self.query_detail_dst.value = dst
        self.query_original_dst = dst
        self._render_query_history()

    def _set_query_page(self, page: int):
        """設定查詢結果頁碼並計算總頁數"""
        total = len(self.query_results)
        self.query_total_pages = max(
            1, (total + self.query_page_size - 1) // self.query_page_size
        )
        self.query_page = max(1, min(page, self.query_total_pages))

    def _render_query_results(self):
        """渲染查詢結果列表"""
        if not hasattr(self, "query_result_list"):
            return

        self._set_query_page(self.query_page)
        self.query_result_list.controls.clear()

        total = len(self.query_results)
        start = (self.query_page - 1) * self.query_page_size
        end = start + self.query_page_size
        page_rows = self.query_results[start:end]

        if not page_rows:
            self.query_result_list.controls.append(
                kit.empty_state(
                    icon=ft.Icons.SEARCH_OFF,
                    title="沒有搜尋結果",
                    subtitle="請嘗試其他關鍵字或調整篩選條件",
                )
            )
        else:
            for row in page_rows:
                key = str(row.get("key", ""))
                cache_type = str(row.get("cache_type", "-"))
                shard = str(row.get("shard", "-"))
                preview = str(row.get("preview", ""))
                selected = (
                    self.query_selected_result is not None
                    and self.query_selected_result.get("cache_type") == cache_type
                    and self.query_selected_result.get("key") == key
                )

                self.query_result_list.controls.append(
                    ft.Row(
                        [
                            ft.Container(
                                expand=True,
                                padding=8,
                                border=ft.Border.all(
                                    1,
                                    C.DIA_BG if selected else C.LINE,
                                ),
                                border_radius=8,
                                bgcolor=C.DIA_BG if selected else None,
                                on_click=lambda e, r=row: self._on_select_result(r),
                                content=ft.Column(
                                    [
                                        ft.Text(
                                            f"Key: {key}",
                                            size=12,
                                            weight=ft.FontWeight.BOLD,
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            max_lines=1,
                                        ),
                                        ft.Text(
                                            f"類型: {cache_type} | shard: {shard}",
                                            size=11,
                                            color=C.MUTED,
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            max_lines=1,
                                        ),
                                        ft.Text(
                                            f"預覽: {preview}",
                                            size=11,
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            max_lines=2,
                                        ),
                                    ],
                                    spacing=3,
                                    horizontal_alignment=ft.CrossAxisAlignment.START,
                                ),
                            )
                        ]
                    )
                )

        self.tf_page_jump.value = str(self.query_page)
        self.query_page_info.value = f"頁 / 共 {self.query_total_pages}"
        self.query_total_info.value = f"共 {total} 筆"
        self._refresh_disabled_state()

    def _on_select_result(self, row: dict):
        """選擇查詢結果項目"""
        self.query_selected_result = row
        self._render_query_results()
        self._render_query_detail()
        self.update()

    def _on_page_first(self, e):
        """跳到查詢結果第一頁"""
        self.query_page = 1
        self._query_state.query_page = 1
        self._render_query_results()
        self.update()
        self.update()

    def _on_page_prev(self, e):
        """上一頁查詢結果"""
        self.query_page -= 1
        self._query_state.query_page = self.query_page
        self._render_query_results()
        self.update()
        self.update()

    def _on_page_next(self, e):
        """下一頁查詢結果"""
        self.query_page += 1
        self._query_state.query_page = self.query_page
        self._render_query_results()
        self.update()
        self.update()

    def _on_page_last(self, e):
        """跳到查詢結果最後一頁"""
        self.query_page = self.query_total_pages
        self._query_state.query_page = self.query_page
        self._render_query_results()
        self.update()
        self.update()

    def _on_page_jump(self, e):
        """跳轉到指定頁碼"""
        try:
            p = int((self.tf_page_jump.value or "1").strip())
        except Exception:  # noqa: BLE001
            p = 1
        self.query_page = p
        self._render_query_results()
        self.update()

    def _on_page_size_change(self, e):
        """變更每頁顯示數量"""
        try:
            self.query_page_size = int(self.dd_page_size.value or "50")
        except Exception:  # noqa: BLE001
            self.query_page_size = 50
        self.query_page = 1

    def _on_apply_dst(self, e):
        """套用目標翻譯到選取的查詢結果"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        if not self.query_selected_result:
            self._notify("請先選擇一筆資料", "warn")
            return

        ctype = str(self.query_selected_result.get("cache_type", ""))
        key = str(self.query_selected_result.get("key", ""))
        shard = str(self.query_selected_result.get("shard", "-"))
        new_dst = str(self.query_detail_dst.value or "")
        old_dst = str(self.query_original_dst or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用失敗：找不到目標 key", "error")
                return

            # 寫入快取檔（不是新增，直接覆寫既有 key 的內容）
            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            # 持久化歷史（jsonl + pretty json）
            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": shard,
                "old_dst": old_dst,
                "new_dst": new_dst,
                "action": "apply",
                "actor": "cache_query_ui",
            }
            self._history_append_event(ctype, history_event)

            self.query_original_dst = new_dst
            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._notify("已套用並寫入快取", "info")
            if self.page:
                self.page.update()
        except Exception as ex:  # noqa: BLE001
            self._notify(f"套用失敗：{ex}", "error")

    def _on_revert_dst(self, e):
        """還原 DST 到原始值（查詢區）"""
        if not self.query_selected_result:
            show_snack(self.page, "請先選擇一筆資料", C.GOLD)
            return

        self.query_detail_dst.value = str(self.query_original_dst or "")
        show_snack(self.page, "已還原到原始值", C.DIA)
        if self.page:
            self.page.update()

    def _on_restore_latest_query(self, e):
        """還原最新歷史紀錄（查詢區，不立即寫入快取）"""
        if self.ui_busy:
            show_snack(self.page, "目前忙碌中，暫停還原", C.GOLD)
            return

        if not self.query_selected_result:
            show_snack(self.page, "請先選擇一筆資料", C.GOLD)
            return

        ctype = str(self.query_selected_result.get("cache_type", ""))
        key = str(self.query_selected_result.get("key", ""))
        if not ctype or not key:
            show_snack(self.page, "無法取得 cache_type 或 key", C.RED)
            return

        # 載入最新歷史紀錄
        records = self._history_load_recent(ctype, key, limit=1)
        if not records:
            show_snack(self.page, "此 key 目前沒有歷史紀錄", C.GOLD)
            return

        latest = records[0]
        old_dst = str(latest.get("old_dst", ""))

        # 只填入 DST 輸入框，不寫入快取
        self.query_detail_dst.value = old_dst
        show_snack(
            self.page,
            "已載入最新歷史紀錄到 DST（尚未寫入快取，請點「套用」儲存）",
            C.DIA,
        )
        self.update()

    # PR5-7: 查詢變更提示
    def _on_query_input_change(self, e):
        """偵測輸入框變更"""
        current = self.tf_query_input.value or ""
        hint = "⚠️ 偵測到變更，請重新搜尋" if current != self._last_query_value else ""
        if self.query_change_hint.value == hint:
            return  # 提示沒變就不刷新（原本每按一鍵都整個快取頁 diff）
        self.query_change_hint.value = hint
        self.query_change_hint.color = C.GOLD
        self.query_change_hint.update()

    def _on_query_mode_change(self, e):
        """偵測搜尋模式變更"""
        self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
        self.query_change_hint.color = C.GOLD
        self.update()

    def _on_query_type_change(self, e):
        """偵測分類選擇變更"""
        self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
        self.query_change_hint.color = C.GOLD
        self.update()

    def _on_query_search(self, e):
        """執行關鍵字搜尋快取"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停搜尋", "warn")
            return

        query = (self.tf_query_input.value or "").strip()
        if not query:
            self._notify("請輸入查詢內容", "warn")
            return

        mode = (self.dd_query_mode.value or "ALL").upper()
        target_type = self.dd_query_type.value or "ALL"
        targets = (
            [target_type]
            if target_type != "ALL"
            else [
                ctype for ctype, _ in self._iter_type_states(self._last_overview_data)
            ]
        )

        self.query_search_hint.value = f"搜尋中：{query} …"
        self.query_search_hint.color = C.MUTED
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._apply_query_results(self._compute_query_results(query, mode, targets))
            return

        seq = self._query_seq = getattr(self, "_query_seq", 0) + 1
        self.query_search_hint.update()
        if get_page_operation_registry(self.page) is None:

            async def _search_standalone():
                try:
                    results = await asyncio.to_thread(
                        self._compute_query_results, query, mode, targets
                    )
                except Exception as ex:  # noqa: BLE001 - event loop displays the error
                    self._notify(f"搜尋失敗：{ex}", "error")
                    return
                if seq == self._query_seq:
                    self._apply_query_results(results)

            run_task(_search_standalone)
            return
        operation = reserve_page_operation(
            self.page,
            name="快取查詢",
            owner="cache-query",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
        )
        if not operation.admitted:
            self._notify("應用程式正在關閉，未啟動查詢", "warn")
            return
        result = {}

        def search():
            try:
                result["dedup"] = self._compute_query_results(query, mode, targets)
            except Exception as ex:  # noqa: BLE001 - event loop displays the error
                result["error"] = ex
                current_operation().record_error(ex)

        if not operation.launch(search):
            operation.finish(error=RuntimeError("cache query worker was not launched"))
            self._notify("無法啟動快取查詢", "error")
            return

        async def _search():
            while not operation.done_event.is_set():
                await asyncio.sleep(0.02)
            if "error" in result:
                self._notify(f"搜尋失敗：{result['error']}", "error")
                return
            if seq == self._query_seq:  # 只套用最後一次搜尋
                self._apply_query_results(result.get("dedup", []))

        run_task(_search)

    def _compute_query_results(self, query: str, mode: str, targets: list) -> list:
        """查詢快取（可在背景執行緒執行；不修改任何控制項）。"""
        out = []
        for ctype in targets:
            if mode in ("KEY", "ALL"):
                r = cache_search_service(ctype, query, mode="key", limit=2000)
                for item in r.get("items", []):
                    k = item.get("key", "")
                    entry = cache_get_entry_service(ctype, k) or {}
                    preview_dst = (
                        str(entry.get("dst", ""))
                        .replace("\\r\\n", "\n")
                        .replace("\\n", "\n")
                    )
                    out.append(
                        {
                            "cache_type": ctype,
                            "key": k,
                            "preview": preview_dst,
                            "shard": self._active_shard_filename(ctype),
                        }
                    )

            if mode in ("DST", "ALL"):
                r = cache_search_service(ctype, query, mode="dst", limit=2000)
                for item in r.get("items", []):
                    out.append(
                        {
                            "cache_type": ctype,
                            "key": item.get("key", ""),
                            "preview": item.get("preview", ""),
                            "shard": self._active_shard_filename(ctype),
                        }
                    )

        seen = set()
        dedup = []
        for row in out:
            k = (row.get("cache_type"), row.get("key"))
            if k in seen:
                continue
            seen.add(k)
            dedup.append(row)

        return dedup

    def _apply_query_results(self, dedup: list) -> None:
        """（event loop 上）套用搜尋結果。"""
        self.query_results = dedup
        self.query_page = 1
        self.query_selected_result = (
            self.query_results[0] if self.query_results else None
        )
        # PR5-7: 清除變更提示
        self._last_query_value = self.tf_query_input.value or ""
        self.query_change_hint.value = ""
        self.query_search_hint.value = (
            f"搜尋完成：{len(self.query_results)} 筆（左側點選，右側檢視）"
        )
        self.query_search_hint.color = C.DIA
        self._render_query_results()
        self._render_query_detail()
        self.update()

    def _on_query_clear(self, e):
        """清除搜尋條件與結果"""
        self.tf_query_input.value = ""
        self.query_results = []
        self.query_selected_result = None
        self.query_page = 1
        self.query_search_hint.value = "請輸入關鍵字開始搜尋"
        self.query_search_hint.color = C.MUTED
        self._render_query_results()
        self._render_query_detail()
        self.update()
