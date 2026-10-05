"""CacheView 的歷史：查詢區與分片區共用的歷史視窗與還原。

由 ``app/views/cache_view.py`` 拆出（#114），方法內容未改；``CacheView`` 以多重繼承組合這些 mixin。
"""

from pathlib import Path

import flet as ft

from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_save_all_service,
    cache_update_dst_service,
)

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.cache_manager.cache_history_store import (
    history_active_default,
    history_append_event,
    history_dirs,
    history_load_active,
    history_load_recent,
    history_now_ts,
    history_save_active,
)


class CacheHistoryMixin:
    """歷史：查詢區與分片區共用的歷史視窗與還原。"""

    def _on_select_shard_history_event(self, event: dict):
        """選擇歷史事件"""
        self.shard_history_selected_event = event
        self._render_shard_history()
        self.update()

    def _render_shard_history(self):
        """渲染分類分片的歷史紀錄列表"""
        if not hasattr(self, "shard_history_list"):
            return

        self.shard_history_list.controls.clear()
        self.shard_history_preview.value = ""

        ctype = str(self.shard_detail_selected_type or "")
        key = str(self.shard_detail_selected_key or "")

        if not ctype or not key:
            self.shard_history_records = []
            self.shard_history_selected_event = None
            self.shard_history_key_text.value = "Key: -"
            self.shard_history_selected_text.value = "未選取歷史紀錄"
            self.shard_history_list.controls.append(
                ft.Text("請先選擇 key", size=11, color=C.MUTED)
            )
            self._refresh_disabled_state()
            return

        self.shard_history_key_text.value = f"Key: {key} ({ctype})"

        self.shard_history_records = self._history_load_recent(ctype, key, limit=20)
        if not self.shard_history_records:
            self.shard_history_selected_event = None
            self.shard_history_selected_text.value = "此 key 目前沒有歷史紀錄"
            self.shard_history_list.controls.append(
                ft.Text("尚無歷史紀錄", size=11, color=C.MUTED)
            )
            self._refresh_disabled_state()
            return

        # 若當前選取不在新清單中，就預設第一筆
        def _ev_id(ev: dict):
            """從事件物件取出 ID 組合"""
            return (
                str(ev.get("ts", "")),
                str(ev.get("old_dst", "")),
                str(ev.get("new_dst", "")),
            )

        selected_id = (
            _ev_id(self.shard_history_selected_event)
            if self.shard_history_selected_event
            else None
        )
        found = None
        for ev in self.shard_history_records:
            if selected_id and _ev_id(ev) == selected_id:
                found = ev
                break
        self.shard_history_selected_event = found or self.shard_history_records[0]

        for ev in self.shard_history_records:
            ts = str(ev.get("ts", ""))
            action = str(ev.get("action", "apply"))
            old_dst = str(ev.get("old_dst", ""))
            new_dst = str(ev.get("new_dst", ""))
            is_selected = _ev_id(ev) == _ev_id(self.shard_history_selected_event)

            self.shard_history_list.controls.append(
                ft.Container(
                    padding=6,
                    border=ft.Border.all(
                        1,
                        C.DIA_BG if is_selected else C.LINE,
                    ),
                    border_radius=8,
                    bgcolor=C.DIA_BG if is_selected else None,
                    on_click=lambda e, item=ev: self._on_select_shard_history_event(
                        item
                    ),
                    content=ft.Column(
                        [
                            ft.Text(f"{ts} | {action}", size=10, color=C.MUTED),
                            ft.Text(f"old: {old_dst[:60]}", size=11, no_wrap=False),
                            ft.Text(f"new: {new_dst[:60]}", size=11, no_wrap=False),
                        ],
                        spacing=2,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                )
            )

        self._update_shard_history_preview()
        self._refresh_disabled_state()

    def _update_shard_history_preview(self):
        """更新歷史預覽"""
        ev = self.shard_history_selected_event
        if not ev:
            self.shard_history_selected_text.value = "未選取歷史紀錄"
            self.shard_history_preview.value = ""
            return

        ts = str(ev.get("ts", ""))
        action = str(ev.get("action", "apply"))
        old_dst = str(ev.get("old_dst", ""))
        new_dst = str(ev.get("new_dst", ""))
        self.shard_history_selected_text.value = f"已選取：{ts} | {action}"
        self.shard_history_preview.value = f"old:\n{old_dst}\n\nnew:\n{new_dst}"

    def _on_shard_apply_selected_history(self, e):
        """套用選取的歷史舊值（分類分片版）"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        ev = self.shard_history_selected_event
        if not ev:
            self._notify("請先選擇一筆歷史紀錄", "warn")
            return

        new_dst = str(ev.get("old_dst", ""))
        old_dst_now = str(self.shard_dst_original or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用舊值失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": str(self.shard_detail_selected_file or "-"),
                "old_dst": old_dst_now,
                "new_dst": new_dst,
                "action": "revert_from_shard_history",
                "actor": "cache_shard_detail_ui",
            }
            self._history_append_event(ctype, history_event)

            self.shard_dst_original = new_dst
            self.shard_dst_field.value = new_dst
            if self.shard_dst_loaded_sig != (
                ctype,
                self.shard_detail_selected_file,
                key,
            ):
                self.shard_dst_loaded_sig = (
                    ctype,
                    self.shard_detail_selected_file,
                    key,
                )

            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._render_shard_history()
            self._notify("已套用選取舊值並寫入快取", "info")
            if self.page:
                self.page.update()
        except Exception as ex:  # noqa: BLE001
            self._notify(f"套用舊值失敗：{ex}", "error")

    # -------------------- History storage helpers --------------------
    def _history_now_ts(self) -> str:
        """取得目前時間戳"""
        return history_now_ts()

    def _history_dirs(self, cache_type: str):
        """取得歷史記錄目錄"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_dirs(root, cache_type)

    def _history_active_default(self, cache_type: str) -> dict:
        """取得預設的活跃记录"""
        return history_active_default(cache_type)

    def _history_load_active(self, cache_type: str):
        """載入活跃记录"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_load_active(root, cache_type)

    def _history_save_active(self, active_path: Path, active: dict):
        """儲存活跃记录"""
        return history_save_active(active_path, active)

    def _history_append_event(self, cache_type: str, event: dict):
        """新增歷史事件"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_append_event(root, cache_type, event)

    def _history_load_recent(
        self, cache_type: str, key: str, limit: int = 20
    ) -> list[dict]:
        """載入最近的歷史記錄"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_load_recent(root, cache_type, key, limit)

    def _render_query_history(self):
        """渲染查詢區的歷史記錄列表"""
        if not hasattr(self, "query_history_list"):
            return

        self.query_history_list.controls.clear()
        self.query_history_preview.value = ""

        row = self.query_selected_result
        if not row:
            self.query_history_records = []
            self.query_history_selected_event = None
            self.query_history_key_text.value = "Key: -"
            self.query_history_selected_text.value = "未選取歷史紀錄"
            self.query_history_list.controls.append(
                ft.Text("請先選擇左側結果", size=11, color=C.MUTED)
            )
            self._refresh_disabled_state()
            return

        ctype = str(row.get("cache_type", ""))
        key = str(row.get("key", ""))
        self.query_history_key_text.value = f"Key: {key} ({ctype})"

        self.query_history_records = self._history_load_recent(ctype, key, limit=20)
        if not self.query_history_records:
            self.query_history_selected_event = None
            self.query_history_selected_text.value = "此 key 目前沒有歷史紀錄"
            self.query_history_list.controls.append(
                ft.Text("尚無歷史紀錄", size=11, color=C.MUTED)
            )
            self._refresh_disabled_state()
            return

        # 若當前選取不在新清單中，就預設第一筆
        def _ev_id(ev: dict):
            """從事件物件取出 ID 組合"""
            return (
                str(ev.get("ts", "")),
                str(ev.get("old_dst", "")),
                str(ev.get("new_dst", "")),
            )

        selected_id = (
            _ev_id(self.query_history_selected_event)
            if self.query_history_selected_event
            else None
        )
        found = None
        for ev in self.query_history_records:
            if selected_id and _ev_id(ev) == selected_id:
                found = ev
                break
        self.query_history_selected_event = found or self.query_history_records[0]

        for ev in self.query_history_records:
            ts = str(ev.get("ts", ""))
            action = str(ev.get("action", "apply"))
            old_dst = str(ev.get("old_dst", ""))
            new_dst = str(ev.get("new_dst", ""))
            is_selected = _ev_id(ev) == _ev_id(self.query_history_selected_event)

            self.query_history_list.controls.append(
                ft.Container(
                    padding=6,
                    border=ft.Border.all(
                        1,
                        C.DIA_BG if is_selected else C.LINE,
                    ),
                    border_radius=8,
                    bgcolor=C.DIA_BG if is_selected else None,
                    on_click=lambda e, item=ev: self._on_select_history_event(item),
                    content=ft.Column(
                        [
                            ft.Text(f"{ts} | {action}", size=10, color=C.MUTED),
                            ft.Text(f"old: {old_dst[:60]}", size=11, no_wrap=False),
                            ft.Text(f"new: {new_dst[:60]}", size=11, no_wrap=False),
                        ],
                        spacing=2,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                )
            )

        self._update_history_preview()
        self._refresh_disabled_state()

    def _update_history_preview(self):
        """更新歷史紀錄預覽"""
        ev = self.query_history_selected_event
        if not ev:
            self.query_history_selected_text.value = "未選取歷史紀錄"
            self.query_history_preview.value = ""
            return

        ts = str(ev.get("ts", ""))
        action = str(ev.get("action", "apply"))
        old_dst = str(ev.get("old_dst", ""))
        new_dst = str(ev.get("new_dst", ""))
        self.query_history_selected_text.value = f"已選取：{ts} | {action}"
        self.query_history_preview.value = f"old:\n{old_dst}\n\nnew:\n{new_dst}"

    def _on_open_history_window(self, e, source="query"):
        """打開歷史紀錄浮動視窗（獨立視窗）"""
        # 參數驗證
        if source not in ("query", "shard"):
            self._notify(f"無效的 source 參數：{source}", "error")
            return

        self.history_window_source = source

        # 根據來源開啟對應的獨立視窗
        if source == "query":
            self._render_query_history()
            self.query_history_window.visible = True
            source_text = "查詢區"
        else:  # source == 'shard'
            self._render_shard_history()
            self.shard_history_window.visible = True
            source_text = "分片區"

        show_snack(
            self.page,
            f"歷史紀錄視窗已打開（{source_text}，可拖曳標題列移動）",
            C.DIA,
        )
        if self.page:
            self.page.update()

    def _on_close_history_window(self, e):
        """關閉歷史紀錄浮動視窗"""
        self.query_history_window.visible = False
        self.history_window_source = None
        if self.page:
            self.page.update()

    def _on_query_history_window_drag(self, e: ft.DragUpdateEvent):
        """拖曳歷史紀錄視窗"""
        win = self.query_history_window
        win.top = max(0, win.top + e.delta_y)
        win.left = max(0, win.left + e.delta_x)
        win.update()

    def _on_query_history_window_resize(self, e: ft.DragUpdateEvent):
        """調整歷史紀錄視窗大小"""
        win = self.query_history_window
        win.width = max(300, win.width + e.delta_x)
        win.height = max(350, win.height + e.delta_y)
        win.update()

    def _on_close_shard_history_window(self, e):
        """關閉分片歷史紀錄浮動視窗"""
        self.shard_history_window.visible = False
        self.history_window_source = None
        self.update()

    def _on_shard_history_window_drag(self, e: ft.DragUpdateEvent):
        """拖曳分片歷史紀錄視窗"""
        win = self.shard_history_window
        win.top = max(0, win.top + e.delta_y)
        win.left = max(0, win.left + e.delta_x)
        win.update()

    def _on_shard_history_window_resize(self, e: ft.DragUpdateEvent):
        """調整分片歷史紀錄視窗大小"""
        win = self.shard_history_window
        win.width = max(300, win.width + e.delta_x)
        win.height = max(350, win.height + e.delta_y)
        win.update()

    def _on_select_history_event(self, event: dict):
        """選擇歷史紀錄項目"""
        self.query_history_selected_event = event

    def _on_apply_selected_history(self, e):
        """套用選取的歷史紀錄"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        if not self.query_selected_result:
            self._notify("請先選擇一筆資料", "warn")
            return

        ev = self.query_history_selected_event
        if not ev:
            self._notify("請先選擇一筆歷史紀錄", "warn")
            return

        ctype = str(self.query_selected_result.get("cache_type", ""))
        key = str(self.query_selected_result.get("key", ""))
        new_dst = str(ev.get("old_dst", ""))

        current = cache_get_entry_service(ctype, key) or {}
        old_dst_now = str(current.get("dst", ""))

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用舊值失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": str(self.query_selected_result.get("shard", "-")),
                "old_dst": old_dst_now,
                "new_dst": new_dst,
                "action": "revert_from_history",
                "actor": "cache_query_ui",
            }
            self._history_append_event(ctype, history_event)

            self.query_original_dst = new_dst
            self.query_detail_dst.value = new_dst
            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._notify("已套用選取舊值並寫入快取", "info")
            self.update()
        except Exception as ex:  # noqa: BLE001
            self._notify(f"套用舊值失敗：{ex}", "error")
