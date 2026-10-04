"""CacheView 的分片頁：分片 widgets、分片清單／內容、dst 編輯與分頁。

由 ``app/views/cache_view.py`` 拆出（#114），方法內容未改；``CacheView`` 以多重繼承組合這些 mixin。
"""

import json
import re
from pathlib import Path

import flet as ft

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C


class CacheShardMixin:
    """分片頁：分片 widgets、分片清單／內容、dst 編輯與分頁。"""

    # =========================================================
    # Shared helpers
    # =========================================================
    def _dynamic_shard_list_height(self) -> int:
        """計算 shard list height 高度（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 180
        - 正常：int(page.height * 0.24)，clamp 120..360
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            h = 0

        if h <= 0:
            return 180

        # 動態高度：跟視窗高度走，但做上下限保護避免 Flet 撐高異常
        return max(120, min(360, int(h * 0.24)))

    def _dynamic_type_shard_panel_height(self) -> int:
        """計算 type shard panel height（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 260
        - 正常：int(page.height * 0.30)，clamp 180..420
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            h = 0

        if h <= 0:
            return 260

        # 上方分類清單固定高度，避免下方 C1/C2 卡撐掉可視區
        return max(180, min(420, int(h * 0.30)))

    def _dynamic_shard_key_list_height(self) -> int:
        """計算 shard key list height 高度（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 220
        - 正常：int(page.height * 0.30)，clamp 140..420
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            h = 0

        if h <= 0:
            return 220

        return max(140, min(420, int(h * 0.30)))

    def _dynamic_shard_key_panel_width(self) -> int:
        """計算 shard key panel width（跟視窗 width 自適應）。

        規則：
        - 若 page.width 取得失敗或 <= 0 → 360
        - 正常：int(page.width * 0.30)，clamp 280..560
        """
        try:
            w = float(getattr(self.page, "width", 0) or 0)
        except Exception:  # noqa: BLE001
            w = 0

        if w <= 0:
            return 360

        return max(280, min(560, int(w * 0.30)))

        # overview 分類下拉已移除（按鈕維持在分類卡）

    def _load_shard_rows(
        self, cache_type: str, active_shard_id: str, shard_capacity: int
    ) -> list[dict]:
        """載入指定類型的所有分片資料"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return []

        type_dir = Path(root) / cache_type
        if not type_dir.exists():
            return []

        def _sort_key(path: Path):
            """從路徑提取排序用的序列號"""
            stem = path.stem
            m = re.search(r"(\d+)$", stem)
            seq = int(m.group(1)) if m else -1
            return (seq, stem.lower())

        active_filename = (
            f"{cache_type}_{active_shard_id!s}.json"
            if str(active_shard_id or "").strip()
            else ""
        )

        shard_files: list[Path] = []
        for fp in type_dir.glob("*.json"):
            name = fp.name.lower()
            # 排除非 shard 參考檔
            if name == f"{cache_type.lower()}_cache_main.json":
                continue
            shard_files.append(fp)

        rows: list[dict] = []
        for fp in sorted(shard_files, key=_sort_key, reverse=True):
            key_count = 0
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(raw, (dict, list)):
                    key_count = len(raw)
            except Exception:  # noqa: BLE001
                key_count = 0

            rows.append(
                {
                    "filename": fp.name,
                    "key_count": key_count,
                    "is_active": fp.name == active_filename,
                    "capacity": shard_capacity,
                }
            )
        return rows

    def _load_shard_keys(self, cache_type: str, filename: str) -> list[str]:
        """從分片檔案載入所有鍵值"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return []

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return []

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

        if isinstance(raw, dict):
            return sorted([str(k) for k in raw])

        if isinstance(raw, list):
            out = []
            for idx, item in enumerate(raw):
                if isinstance(item, dict) and item.get("key"):
                    out.append(str(item.get("key")))
                else:
                    out.append(f"[{idx}]")
            return out

        return []

    def _set_shard_detail_page(self, page: int):
        """設定分片詳情頁碼並計算總頁數"""
        total = len(self.shard_detail_keys)
        self.shard_detail_total_pages = max(
            1, (total + self.shard_detail_page_size - 1) // self.shard_detail_page_size
        )
        self.shard_detail_page = max(1, min(page, self.shard_detail_total_pages))

    def _render_shard_detail_keys(self):
        """渲染分片詳情的鍵值列表 UI"""
        if not hasattr(self, "shard_detail_key_list"):
            return

        self.shard_detail_key_list.controls.clear()

        if not self.shard_detail_selected_type or not self.shard_detail_selected_file:
            self._render_shard_keys_unselected()
            return

        all_keys = list(self.shard_detail_keys)
        keyword = (
            str(
                (
                    self.tf_shard_key_filter.value
                    if hasattr(self, "tf_shard_key_filter")
                    else ""
                )
                or ""
            )
            .strip()
            .lower()
        )
        filtered_keys = (
            [k for k in all_keys if keyword in k.lower()] if keyword else all_keys
        )

        total_filtered = len(filtered_keys)
        self.shard_detail_total_pages = max(
            1,
            (total_filtered + self.shard_detail_page_size - 1)
            // self.shard_detail_page_size,
        )
        self.shard_detail_page = max(
            1, min(self.shard_detail_page, self.shard_detail_total_pages)
        )

        start = (self.shard_detail_page - 1) * self.shard_detail_page_size
        end = start + self.shard_detail_page_size
        page_keys = filtered_keys[start:end]

        if (
            self.shard_detail_selected_key
            and self.shard_detail_selected_key not in filtered_keys
        ):
            self.shard_detail_selected_key = ""
        if not self.shard_detail_selected_key and filtered_keys:
            self.shard_detail_selected_key = filtered_keys[0]

        self.shard_detail_meta.value = (
            f"{self.shard_detail_selected_type} / {self.shard_detail_selected_file}"
        )
        if hasattr(self, "shard_workspace_meta"):
            self.shard_workspace_meta.value = f"目前分片：{self.shard_detail_selected_type} / {self.shard_detail_selected_file}"
        if not page_keys:
            if keyword and all_keys:
                self.shard_detail_key_list.controls.append(
                    ft.Text("此篩選條件沒有符合的 key", size=11, color=C.MUTED)
                )
            else:
                self.shard_detail_key_list.controls.append(
                    ft.Text("此分片目前沒有 key", size=11, color=C.MUTED)
                )
        else:
            for idx, key in enumerate(page_keys, start=start + 1):
                self.shard_detail_key_list.controls.append(
                    self._shard_key_row(idx, key)
                )

        self.shard_page_info.value = (
            f"第 {self.shard_detail_page} 頁 / 共 {self.shard_detail_total_pages} 頁"
        )
        if keyword:
            self.shard_total_info.value = f"共 {total_filtered}/{len(all_keys)} keys | 每頁 {self.shard_detail_page_size}"
        else:
            self.shard_total_info.value = (
                f"共 {len(all_keys)} keys | 每頁 {self.shard_detail_page_size}"
            )
        self._render_shard_src_panel()
        self._render_shard_dst_panel()
        self._refresh_disabled_state()

    def _render_shard_keys_unselected(self) -> None:
        """尚未選擇分片時的 key 清單與資訊列。"""
        self.shard_detail_selected_key = ""
        self.shard_detail_meta.value = "尚未選擇分片"
        if hasattr(self, "shard_workspace_meta"):
            self.shard_workspace_meta.value = "尚未選擇分片"
        self.shard_page_info.value = "第 1 頁 / 共 1 頁"
        self.shard_total_info.value = "共 0 keys | 每頁 50"
        self.shard_dst_loaded_sig = None
        self.shard_dst_original = ""
        self.shard_detail_key_list.controls.append(
            ft.Text(
                "請先在上方分片清單點選一個 shard",
                size=11,
                color=C.MUTED,
            )
        )
        self._render_shard_src_panel()
        self._render_shard_dst_panel()
        self._refresh_disabled_state()

    def _shard_key_row(self, idx: int, key: str) -> ft.Container:
        """分片 key 清單中的單一列。"""
        selected = key == self.shard_detail_selected_key
        return ft.Container(
            padding=6,
            border=ft.Border.all(
                1,
                C.DIA if selected else C.LINE,
            ),
            border_radius=6,
            bgcolor=C.DIA_BG if selected else None,
            tooltip=key,
            on_click=lambda e, k=key: self._on_select_shard_key(k),
            content=ft.Text(
                f"{idx}. {key}",
                size=11,
                no_wrap=True,
                overflow=ft.TextOverflow.ELLIPSIS,
                max_lines=1,
            ),
        )

    def _on_shard_key_filter_change(self, e):
        """鍵值篩選條件變更時重新渲染"""
        self.shard_detail_page = 1
        self._render_shard_detail_keys()
        if self.page:
            self.page.update()

    def _set_shard_workspace_visible(self, visible: bool):
        """顯示或隱藏分片工作區面板"""
        show_workspace = bool(visible)
        if hasattr(self, "shard_nav_view"):
            self.shard_nav_view.visible = not show_workspace
        if hasattr(self, "shard_workspace_card"):
            self.shard_workspace_card.visible = show_workspace
        # 防護：確保 page 存在且已完全初始化
        if self.page is not None and hasattr(self, "page"):
            try:
                self.page.update()
            except Exception:  # noqa: BLE001, S110
                pass

    def _open_shard_workspace_tab(self):
        """開啟分片工作區標籤"""
        self._set_shard_workspace_visible(True)

    def _on_back_to_shard_list(self, e):
        """返回分片列表檢視"""
        self._set_shard_workspace_visible(False)

    def _on_select_shard_row(self, cache_type: str, filename: str):
        """選擇分片列時載入該分片的鍵值"""
        self.shard_detail_selected_type = cache_type
        self.shard_detail_selected_file = filename
        self.shard_detail_keys = self._load_shard_keys(cache_type, filename)
        self.shard_detail_selected_key = (
            self.shard_detail_keys[0] if self.shard_detail_keys else ""
        )
        self.shard_detail_src_mode = "preview"
        self.shard_detail_page = 1
        if hasattr(self, "tf_shard_key_filter"):
            self.tf_shard_key_filter.value = ""
        self.shard_dst_loaded_sig = None
        self._render_query_type_shard_page()
        self._open_shard_workspace_tab()
        if self.page:
            self.update()

    def _on_select_shard_key(self, key: str):
        """選擇鍵值時更新詳情面板"""
        if key != self.shard_detail_selected_key:
            self.shard_dst_loaded_sig = None
        self.shard_detail_selected_key = key
        self._render_shard_detail_keys()
        # 如果歷史紀錄視窗已開啟，自動更新
        if hasattr(self, "shard_history_window") and self.shard_history_window.visible:
            self._render_shard_history()
        if self.page:
            self.update()

    def _load_shard_entry(
        self, cache_type: str, filename: str, key: str
    ) -> dict | None:
        """載入單一鍵值的詳細資料"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return None

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return None

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

        if isinstance(raw, dict):
            entry = raw.get(key)
            return entry if isinstance(entry, dict) else None

        return None

    async def _on_shard_dst_copy(self, e):
        """複製目標內容到剪貼簿"""
        if not self.shard_detail_selected_key:
            self._notify("請先選擇 key", "warn")
            return

        try:
            await ft.Clipboard().set(str(self.shard_dst_field.value or ""))
            self._notify("已複製 C3 DST 內容", "info")
        except Exception:  # noqa: BLE001
            self._notify("複製失敗", "error")

    def _on_shard_page_first(self, e):
        """跳到分片詳情第一頁"""
        if getattr(self, "_is_shard_rendering", False):
            return  # 防止重複點擊
        self._is_shard_rendering = True
        self.shard_detail_page = 1
        self._shard_state.page = 1
        self._render_query_type_shard_page()
        self.update()
        self.update()
        self._is_shard_rendering = False

    def _on_shard_page_prev(self, e):
        """上一頁分片詳情"""
        if getattr(self, "_is_shard_rendering", False):
            return
        self._is_shard_rendering = True
        self.shard_detail_page -= 1
        self._shard_state.page = self.shard_detail_page
        self._render_query_type_shard_page()
        self.update()
        self.update()
        self._is_shard_rendering = False

    def _on_shard_page_next(self, e):
        """下一頁分片詳情"""
        if getattr(self, "_is_shard_rendering", False):
            return
        self._is_shard_rendering = True
        self.shard_detail_page += 1
        self._shard_state.page = self.shard_detail_page
        self._render_query_type_shard_page()
        self.update()
        self.update()
        self._is_shard_rendering = False

    def _on_shard_page_last(self, e):
        """跳到分片詳情最後一頁"""
        if getattr(self, "_is_shard_rendering", False):
            return
        self._is_shard_rendering = True
        self.shard_detail_page = self.shard_detail_total_pages
        self._shard_state.page = self.shard_detail_page
        self._render_query_type_shard_page()
        self.update()
        self.update()
        self._is_shard_rendering = False

    def _render_query_type_shard_page(self):
        """渲染類型分片列表"""
        if not hasattr(self, "query_type_shard_col"):
            return

        self.query_type_shard_col.controls.clear()

        pairs = list(self._iter_type_states(self._last_overview_data))
        if not pairs:
            self.query_type_shard_col.controls.append(
                ft.Text("目前沒有分類資料", color=C.MUTED)
            )
            self._reset_shard_selection()
            self._render_shard_detail_keys()
            return

        valid_selection_pairs = set()
        shard_panel_height = self._dynamic_type_shard_panel_height()

        for ctype, st in pairs:
            self.query_type_shard_col.controls.append(
                self._type_shard_card(
                    ctype, st, shard_panel_height, valid_selection_pairs
                )
            )

        if (
            self.shard_detail_selected_type,
            self.shard_detail_selected_file,
        ) not in valid_selection_pairs:
            self._reset_shard_selection()

        self._render_shard_detail_keys()

    def _type_shard_card(
        self, ctype: str, st: dict, shard_panel_height: int, valid_selection_pairs: set
    ) -> ft.Container:
        """查詢頁的單一類型分片卡片（並登記有效的 shard 選取）。"""
        entries_count = st.get("entries_count", 0)
        shard = st.get("active_shard_id", "-")
        shard_entries = int(st.get("active_shard_entries", 0) or 0)
        shard_capacity = int(st.get("shard_capacity", 2500) or 2500)
        dirty = "dirty" if bool(st.get("is_dirty", False)) else "clean"

        shard_rows = self._load_shard_rows(ctype, str(shard), shard_capacity)
        shard_controls = []
        if not shard_rows:
            shard_controls.append(
                ft.Text("目前沒有可讀取的 shard 檔案", size=11, color=C.MUTED)
            )
        else:
            for row in shard_rows:
                valid_selection_pairs.add((ctype, row["filename"]))
                shard_controls.append(self._shard_row_tile(ctype, row))
        shard_list_container = ft.Container(
            height=shard_panel_height,
            padding=4,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            bgcolor=C.PANEL,
            alignment=ft.alignment.Alignment(-1, -1),
            content=ft.ListView(
                expand=True,
                spacing=4,
                auto_scroll=False,
                controls=shard_controls,
            ),
        )

        return ft.Container(
            padding=8,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            bgcolor=C.PANEL,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(ctype, size=13, weight=ft.FontWeight.BOLD),
                            ft.TextButton(
                                "切換查詢",
                                icon=ft.Icons.MANAGE_SEARCH,
                                on_click=lambda e, t=ctype: self._on_jump_to_query_type(
                                    t
                                ),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Text(
                        f"分片: {shard} | 狀態: {dirty}",
                        size=11,
                        color=C.MUTED,
                    ),
                    ft.Text(
                        f"筆數: {entries_count} | shard 使用: {shard_entries}/{shard_capacity}",
                        size=11,
                    ),
                    ft.ExpansionTile(
                        title=ft.Text(
                            f"分片清單（{len(shard_rows)}）",
                            weight=ft.FontWeight.BOLD,
                        ),
                        controls=[
                            ft.Container(
                                alignment=ft.alignment.Alignment(-1, -1),
                                content=shard_list_container,
                            )
                        ],
                    ),
                ],
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    def _shard_row_tile(self, ctype: str, row: dict) -> ft.Container:
        """類型卡片內的單一 shard 檔案列。"""
        selected = (
            self.shard_detail_selected_type == ctype
            and self.shard_detail_selected_file == row["filename"]
        )
        mark = " | active" if row["is_active"] else ""
        return ft.Container(
            padding=6,
            border=ft.Border.all(
                1,
                C.DIA if selected else C.LINE,
            ),
            border_radius=8,
            bgcolor=C.DIA_BG if selected else None,
            on_click=lambda e, t=ctype, f=row["filename"]: self._on_select_shard_row(
                t, f
            ),
            content=ft.Column(
                [
                    ft.Text(row["filename"], size=11, selectable=True),
                    ft.Text(
                        f"keys: {row['key_count']}/{row['capacity']}{mark}",
                        size=10,
                        color=C.DIA if row["is_active"] else C.MUTED,
                    ),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    def _reset_shard_selection(self) -> None:
        """清除分片選取（類型、檔案、key、SRC 模式、key 清單與分頁）並收起工作區。"""
        self.shard_detail_selected_type = ""
        self.shard_detail_selected_file = ""
        self.shard_detail_selected_key = ""
        self.shard_detail_src_mode = "preview"
        self.shard_detail_keys = []
        self.shard_detail_page = 1
        self._set_shard_workspace_visible(False)

    def _active_shard_filename(self, cache_type: str) -> str:
        """取得指定類型的活躍分片檔案名"""
        for ctype, st in self._iter_type_states(self._last_overview_data):
            if ctype == cache_type:
                sid = st.get("active_shard_id")
                if sid:
                    return f"{cache_type}_{sid}.json"
        return "-"
