"""IconPreviewView 的模組清單、分頁與搜尋（由 icon_preview_view.py 拆出，#114）。"""

import asyncio

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.views.icon_preview.mod_row import ModRow


class IconPreviewListMixin:
    """IconPreviewView 的模組清單、分頁與搜尋（由 icon_preview_view.py 拆出，#114）。"""

    def _render_mod_list(self):
        """渲染模組清單畫面"""
        # PR61 Issue 1：清除搜尋結果狀態
        self._mod_search_matched = []
        self._mod_search_page = 0
        self._mod_search_total = 0

        chrome_before = self._list_chrome()
        self.current_modid = None
        self._switch_list(detail=False)
        self.back_btn.visible = False
        self.save_btn.visible = False
        self.header.value = "🧩 JAR 圖示預覽"

        # Phase 2: 顯示 mod 清單搜尋框
        self.mod_search_tf.visible = True
        self.mod_search_status.visible = True
        # 確保 detail 搜尋框隱藏
        if hasattr(self, "detail_search_tf"):
            self.detail_search_tf.visible = False
            self.detail_search_status.visible = False

        if self._list_chrome() != chrome_before:
            self.update()  # 從詳情回來等版面變動；單純換頁不必比對整棵樹

        mod_ids = sorted(self.mods.keys())
        total = len(mod_ids)

        self.mod_total_pages = max(
            1, (total + self.mod_page_size - 1) // self.mod_page_size
        )

        start = self.mod_current_page * self.mod_page_size
        end = start + self.mod_page_size
        visible_mods = mod_ids[start:end]

        self._show_mod_page(visible_mods)
        self._prefetch_mod_pages(mod_ids, start, end)

    @property
    def list_view(self) -> ft.ListView:
        """目前顯示的那一層清單（詳情頁或模組清單）。"""
        return self.detail_list_view if self.current_modid else self.mod_list_view

    def _list_chrome(self) -> tuple:
        """標題、按鈕與搜尋框的顯示狀態；變動時才需要整個 view 重新送出。"""
        detail_search = getattr(self, "detail_search_tf", None)
        return (
            self.header.value,
            self.back_btn.visible,
            self.save_btn.visible,
            self.mod_search_tf.visible,
            self.mod_search_status.visible,
            detail_search.visible if detail_search is not None else None,
            self.mod_list_view.visible,
            self.detail_list_view.visible,
        )

    def _update_list_area(self, list_view) -> None:
        """只刷新清單、分頁列與搜尋狀態文字，不重新比對整個 view 的控制項樹。"""
        list_view.update()
        self.page_bar.update()
        self.mod_search_status.update()
        detail_status = getattr(self, "detail_search_status", None)
        if detail_status is not None and detail_status in self.controls:
            detail_status.update()  # 只在詳情頁（已掛載）時存在於版面

    def _switch_list(self, *, detail: bool, ready: bool = True) -> None:
        """切換顯示模組清單或詳情清單；``ready=False`` 時詳情清單先隱藏，等列綁好再顯示。"""
        self.mod_list_view.visible = not detail
        self.detail_list_view.visible = detail and ready

    def _prefetch_mod_pages(self, ordered: list[str], start: int, end: int) -> None:
        """在背景執行緒先載入前後兩頁的模組項目，下一次換頁時列表直接用記憶體裡的資料。"""
        warm = getattr(self.mods, "warm", None)
        if warm is None:
            return
        size = end - start
        neighbors = ordered[end : end + size] + ordered[max(0, start - size) : start]
        run_task = getattr(self.page, "run_task", None)
        if neighbors and run_task is not None:

            async def _warm():
                await asyncio.to_thread(warm, neighbors)

            run_task(_warm)

    def _show_mod_page(self, modids) -> None:
        """把這一頁的模組綁到可重用的清單列並刷新（列控制項不重建）。"""
        pool = self._mod_row_pool
        while len(pool) < len(modids):
            pool.append(ModRow(self._open_mod_detail))
        for row, modid in zip(pool, modids, strict=False):
            entries = self.mods[modid]
            row.bind(
                modid, len(entries), sum(1 for e in entries if not e.zh_tw.strip())
            )
        self.mod_list_view.controls = pool[: len(modids)]
        self._update_page_bar_for_mods()
        self._update_list_area(self.mod_list_view)

    def _update_page_bar_for_mods(self):
        """更新分頁資訊顯示（同時支援一般清單與搜尋結果分頁）"""
        if self._mod_search_matched:
            # 搜尋結果分頁模式（PR61 Issue 1）
            self.page_info.value = f"搜尋結果｜第 {self._mod_search_page + 1} / {self._mod_search_total} 頁"
            self.prev_page_btn.disabled = self._mod_search_page <= 0
            self.next_page_btn.disabled = (
                self._mod_search_page >= self._mod_search_total - 1
            )
        else:
            # 一般模組清單分頁模式
            self.page_info.value = (
                f"模組清單｜第 {self.mod_current_page + 1} / {self.mod_total_pages} 頁"
            )
            self.prev_page_btn.disabled = self.mod_current_page <= 0
            self.next_page_btn.disabled = (
                self.mod_current_page >= self.mod_total_pages - 1
            )

    def _on_page_size_change(self, e: ft.ControlEvent):
        """處理每頁顯示數量變更（PR61 Issue 1）"""
        size = int(e.control.value)
        if self.current_modid:
            # 詳情頁：改的是每頁項目數；模組清單的分頁設定不動
            self.page_size = size
            self.current_page = 0
            self._render_current_page()
            return
        self.mod_page_size = size
        self.mod_current_page = 0  # 重設回第一頁
        self._mod_search_page = 0  # 搜尋結果也重設
        if self._mod_search_matched:
            # 重新渲染搜尋結果的目前頁
            self._render_mod_search_page()
        else:
            self._render_mod_list()

    def _prev_page(self, e):
        """處理上一頁按鈕點擊"""
        if self.current_modid:
            # 第二層（item）
            if self.current_page > 0:
                self.current_page -= 1
                self._render_current_page()
        else:
            # 第一層（模組）
            if self._mod_search_matched:
                # 搜尋結果分頁模式（PR61 Issue 1）
                if self._mod_search_page > 0:
                    self._mod_search_page -= 1
                    self._render_mod_search_page()
            else:
                if self.mod_current_page > 0:
                    self.mod_current_page -= 1
                    self._render_mod_list()

    def _next_page(self, e):
        """處理下一頁按鈕點擊"""
        if self.current_modid:
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                self._render_current_page()
        else:
            if self._mod_search_matched:
                # 搜尋結果分頁模式（PR61 Issue 1）
                if self._mod_search_page < self._mod_search_total - 1:
                    self._mod_search_page += 1
                    self._render_mod_search_page()
            else:
                if self.mod_current_page < self.mod_total_pages - 1:
                    self.mod_current_page += 1
                    self._render_mod_list()

    # ==================================================
    # 即時搜尋（Phase 2）：Debounce 輔助
    # ==================================================
    def _cancel_mod_search_debounce(self):
        """取消之前的 mod 搜尋 debounce timer"""
        self._mod_search_debouncer.cancel()

    def _cancel_detail_search_debounce(self):
        """取消之前的 detail 搜尋 debounce timer"""
        self._detail_search_debouncer.cancel()

    def _on_mod_search_change(self, e: ft.ControlEvent):
        """Mod 清單搜尋輸入 on_change（debounce 150ms）"""
        self._mod_search_text = e.control.value or ""
        self._mod_search_debouncer.call(self._do_mod_search)

    def _do_mod_search(self):
        """實際執行 mod 清單搜尋（在 debounce 延遲後執行）"""
        keyword = self._mod_search_text.strip().lower()
        if not keyword:
            self.mod_search_status.value = ""
            self._mod_search_matched = []  # 清除搜尋結果
            self._mod_search_page = 0
            self._mod_search_total = 0
            # 恢復模組清單搜尋框
            self.mod_search_tf.visible = True
            self._render_mod_list()
            return

        all_modids = sorted(self.mods.keys())
        matched = [m for m in all_modids if keyword in m.lower()]
        total = len(all_modids)

        if not matched:
            self.mod_search_status.value = "無符合結果"
            self._mod_search_matched = []
            self._mod_search_page = 0
            self._mod_search_total = 0
            self.list_view.controls.clear()
            self.list_view.controls.append(
                ft.ListTile(
                    title=ft.Text("無符合結果", color=C.MUTED),
                    subtitle=ft.Text("嘗試不同的關鍵字"),
                )
            )
            self.page_info.value = ""
            self.mod_current_page = 0
        else:
            # PR61 Issue 1：儲存搜尋結果並計算分頁
            self._mod_search_matched = matched
            self._mod_search_page = 0
            self._mod_search_total = max(
                1, (len(matched) + self.mod_page_size - 1) // self.mod_page_size
            )
            self.mod_search_status.value = f"符合 {len(matched)} / {total} 個模組"
            self._render_mod_search_page()
            return

        self._update_list_area(self.mod_list_view)

    def _render_mod_search_page(self):
        """渲染搜尋結果的目前頁（PR61 Issue 1）"""
        matched = self._mod_search_matched

        start = self._mod_search_page * self.mod_page_size
        end = start + self.mod_page_size
        visible_mods = matched[start:end]

        self._show_mod_page(visible_mods)
        self._prefetch_mod_pages(matched, start, end)

    def _on_detail_search_change(self, e: ft.ControlEvent):
        """Mod 詳情頁搜尋 on_change（debounce 150ms）"""
        self._detail_search_text = e.control.value or ""
        self._detail_search_debouncer.call(self._do_detail_search)

    def _do_detail_search(self):
        """實際執行 detail 搜尋（在 debounce 延遲後執行）"""
        keyword = self._detail_search_text.strip().lower()
        entries = self.mods.get(self.current_modid, [])
        total = len(entries)

        if not keyword:
            self._detail_filtered_entries = None  # 無篩選，顯示全部
            if hasattr(self, "detail_search_status"):
                self.detail_search_status.value = ""
        else:
            filtered = [
                e
                for e in entries
                if keyword in e.key.lower()
                or keyword in (e.en or "").lower()
                or keyword in (e.zh_tw or "").lower()
            ]
            self._detail_filtered_entries = filtered
            if hasattr(self, "detail_search_status"):
                self.detail_search_status.value = f"符合 {len(filtered)} / {total} 筆"
            if not filtered and hasattr(self, "detail_search_status"):
                self.detail_search_status.value = f"無符合結果（{total} 筆）"

        # 重設到第一頁再渲染
        self.current_page = 0
        self._render_current_page()

    def _update_detail_search_controls(self, visible: bool):
        """切換 detail 搜尋 UI 的顯示/隱藏"""
        self._init_detail_search_widgets()
        self.detail_search_tf.visible = visible
        self.detail_search_status.visible = visible

        # 從 controls 中移除再重新加入（確保順序正確：搜尋框在最上方）
        self.controls = [
            c
            for c in self.controls
            if c not in [self.detail_search_tf, self.detail_search_status]
        ]
        if visible:
            idx = (
                self.controls.index(self.mod_list_view)
                if self.mod_list_view in self.controls
                else len(self.controls)
            )
            self.controls.insert(idx, self.detail_search_tf)
            self.controls.insert(idx + 1, self.detail_search_status)
        self.update()

    # ==================================================
    # 第二層：單一模組 detail
    # ==================================================

    # Mod 詳情頁搜尋框（Phase 2）- 初始化於 __init__
    def _init_detail_search_widgets(self):
        """初始化 Mod 詳情頁的搜尋 UI（只在需要時建立）"""
        if not hasattr(self, "detail_search_tf"):
            self.detail_search_tf = kit.field(
                label="搜尋 key + value",
                hint_text="搜尋 key + value",
                dense=True,
                on_change=self._on_detail_search_change,
                visible=False,
            )
            self.detail_search_status = ft.Text("", size=11, color=C.MUTED)
