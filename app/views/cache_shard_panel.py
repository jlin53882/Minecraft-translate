"""app/views/cache_shard_panel.py 模組。

用途：封裝快取分片管理功能的 UI 元件。
維護注意：本模組依賴 cache_state.CacheShardState 與 cache_services。
"""

import flet as ft

from translation_tool.utils.log_unit import log_info, log_warning
from app.ui import theme
from app.views.cache_manager.cache_state import CacheShardState
from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_update_dst_service,
    cache_save_all_service,
)
from app.views.cache_manager.cache_history_store import (
    history_now_ts,
    history_append_event,
)


class CacheShardPanel(ft.Container):
    """CacheShardPanel 元件。

    用途：封裝快取分片管理的 UI 與邏輯。
    維護注意：此元件處理分類/分片選擇與 key 檢視/編輯。
    """

    def __init__(self, page: ft.Page, state: CacheShardState, last_overview_data: dict):
        """初始化 CacheShardPanel。

        參數：
            page: Flet Page 物件
            state: CacheShardState 實例
            last_overview_data: 快取總覽資料
        """
        # 先建立 UI 元件
        self._build_components()

        # 先呼叫父類初始化
        super().__init__(content=self._build_content())

        # 再設定實例屬性
        self._page = page
        self.state = state
        self.last_overview_data = last_overview_data

    def _build_components(self):
        """建立內部元件"""
        # 分類/分片列表
        self.query_type_shard_hint = ft.Text(
            "分類 / 分片清單", size=11, color=theme.GREY_700
        )
        self.query_type_shard_col = ft.Column(
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        # Key 列表
        self.shard_detail_meta = ft.Text(
            "尚未選擇分片", size=11, color=theme.GREY_700
        )
        self.tf_shard_key_filter = ft.TextField(
            label="過濾 key",
            hint_text="輸入關鍵字快速過濾",
            dense=True,
            on_change=self._on_shard_key_filter_change,
        )
        self.shard_detail_key_list = ft.ListView(
            expand=True,
            spacing=4,
            auto_scroll=False,
        )

        # 分頁控制
        self.btn_shard_page_first = ft.OutlinedButton(
            "<<", on_click=self._on_shard_page_first
        )
        self.btn_shard_page_prev = ft.OutlinedButton(
            "<", on_click=self._on_shard_page_prev
        )
        self.btn_shard_page_next = ft.OutlinedButton(
            ">", on_click=self._on_shard_page_next
        )
        self.btn_shard_page_last = ft.OutlinedButton(
            ">>", on_click=self._on_shard_page_last
        )
        self.shard_page_info = ft.Text("第 1 頁 / 共 1 頁")
        self.shard_total_info = ft.Text("共 0 keys | 每頁 50")

        # SRC 預覽
        self.shard_src_meta = ft.Text(
            "SRC：請先選擇 key", size=11, color=theme.GREY_700
        )
        self.btn_shard_src_preview = ft.OutlinedButton(
            "👁️ 預覽", on_click=self._on_shard_src_preview_mode
        )
        self.btn_shard_src_raw = ft.OutlinedButton(
            "</> 原始碼", on_click=self._on_shard_src_raw_mode
        )
        self.shard_src_field = ft.TextField(
            value="",
            read_only=True,
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_align=ft.TextAlign.LEFT,
            text_style=ft.TextStyle(font_family="Consolas", size=12, height=1.45),
        )

        # DST 編輯
        self.shard_dst_meta = ft.Text(
            "DST：請先選擇 key", size=11, color=theme.GREY_700
        )
        self.shard_dst_field = ft.TextField(
            value="",
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_align=ft.TextAlign.LEFT,
            text_style=ft.TextStyle(font_family="Consolas", size=12, height=1.45),
        )
        self.btn_shard_dst_apply = ft.Button(
            "套用 DST", icon=ft.Icons.SAVE, on_click=self._on_shard_dst_apply
        )
        self.btn_shard_dst_revert = ft.OutlinedButton(
            "還原", icon=ft.Icons.UNDO, on_click=self._on_shard_dst_revert
        )
        self.btn_shard_dst_copy = ft.OutlinedButton(
            "複製", icon=ft.Icons.CONTENT_COPY, on_click=self._on_shard_dst_copy
        )

    def _build_content(self) -> ft.Column:
        """建立 UI 內容"""
        return ft.Column(
            [
                ft.Text("分類 / 分片", size=15, weight=ft.FontWeight.BOLD),
                self.query_type_shard_hint,
                ft.Container(
                    expand=True,
                    padding=8,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    bgcolor=theme.WHITE,
                    alignment=ft.alignment.top_left,
                    content=self.query_type_shard_col,
                ),
                ft.Divider(height=20),
                ft.Text("Key 列表", weight=ft.FontWeight.BOLD),
                self.shard_detail_meta,
                self.tf_shard_key_filter,
                self.shard_detail_key_list,
                ft.Row(
                    [
                        self.btn_shard_page_first,
                        self.btn_shard_page_prev,
                        self.shard_page_info,
                        self.btn_shard_page_next,
                        self.btn_shard_page_last,
                        ft.Text("|", size=12, color=theme.GREY_500),
                        self.shard_total_info,
                    ],
                    wrap=True,
                    spacing=6,
                ),
                ft.Divider(height=20),
                ft.Text("SRC 預覽", weight=ft.FontWeight.BOLD),
                self.shard_src_meta,
                ft.Row(
                    [self.btn_shard_src_preview, self.btn_shard_src_raw],
                    wrap=True,
                    spacing=6,
                ),
                ft.Container(
                    expand=True,
                    padding=6,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    bgcolor=theme.WHITE,
                    alignment=ft.alignment.top_left,
                    content=self.shard_src_field,
                ),
                ft.Divider(height=20),
                ft.Text("DST 編輯", weight=ft.FontWeight.BOLD),
                self.shard_dst_meta,
                ft.Container(
                    expand=True,
                    padding=6,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    bgcolor=theme.WHITE,
                    alignment=ft.alignment.top_left,
                    content=self.shard_dst_field,
                ),
                ft.Row(
                    [
                        self.btn_shard_dst_apply,
                        self.btn_shard_dst_revert,
                        self.btn_shard_dst_copy,
                    ],
                    wrap=True,
                    spacing=6,
                ),
            ],
            spacing=8,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

    def _iter_type_states(self, data: dict):
        """迭代所有快取類型與其狀態"""
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

    def refresh(self):
        """重新渲染分片列表"""
        self._render_query_type_shard_page()
        self._render_shard_detail_keys()

    # ==================== 分片列表渲染 ====================
    def _render_query_type_shard_page(self):
        """渲染類型分片列表"""
        self.query_type_shard_col.controls.clear()

        pairs = list(self._iter_type_states(self.last_overview_data))
        if not pairs:
            self.query_type_shard_col.controls.append(
                ft.Text("目前沒有分類資料", color=theme.GREY_600)
            )
            self.state.selected_type = ""
            self.state.selected_file = ""
            self.state.selected_key = ""
            self.state.keys = []
            return

        for ctype, st in pairs:
            entries_count = st.get("entries_count", 0)
            shard = st.get("active_shard_id", "-")
            dirty = "dirty" if bool(st.get("is_dirty", False)) else "clean"

            self.query_type_shard_col.controls.append(
                ft.Container(
                    padding=8,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    bgcolor=theme.WHITE,
                    content=ft.Column(
                        [
                            ft.Text(ctype, size=13, weight=ft.FontWeight.BOLD),
                            ft.Text(
                                f"分片: {shard} | 狀態: {dirty}",
                                size=11,
                                color=theme.GREY_700,
                            ),
                            ft.Text(
                                f"筆數: {entries_count}",
                                size=11,
                            ),
                        ],
                        spacing=4,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                )
            )

    # ==================== Key 列表渲染 ====================
    def _render_shard_detail_keys(self):
        """渲染 key 列表"""
        self.shard_detail_key_list.controls.clear()

        if not self.state.selected_type or not self.state.selected_file:
            self.state.selected_key = ""
            self.shard_detail_meta.value = "尚未選擇分片"
            self.shard_page_info.value = "第 1 頁 / 共 1 頁"
            self.shard_total_info.value = "共 0 keys"
            self.state.dst_loaded_sig = None
            self.state.dst_original = ""
            self.shard_detail_key_list.controls.append(
                ft.Text("請先在上方選擇分片", size=11, color=theme.GREY_600)
            )
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            return

        # 過濾
        keyword = str(self.tf_shard_key_filter.value or "").strip().lower()
        all_keys = list(self.state.keys)
        filtered_keys = (
            [k for k in all_keys if keyword in k.lower()] if keyword else all_keys
        )

        total_filtered = len(filtered_keys)
        self.state.total_pages = max(
            1,
            (total_filtered + self.state.page_size - 1) // self.state.page_size,
        )
        self.state.page = max(1, min(self.state.page, self.state.total_pages))

        start = (self.state.page - 1) * self.state.page_size
        end = start + self.state.page_size
        page_keys = filtered_keys[start:end]

        if not page_keys:
            self.shard_detail_key_list.controls.append(
                ft.Text("此分片沒有 key", size=11, color=theme.GREY_600)
            )
        else:
            for idx, key in enumerate(page_keys, start=start + 1):
                selected = key == self.state.selected_key
                self.shard_detail_key_list.controls.append(
                    ft.Container(
                        padding=6,
                        border=ft.border.all(
                            1,
                            theme.BLUE_300 if selected else theme.OUTLINE_VARIANT,
                        ),
                        border_radius=6,
                        bgcolor=theme.BLUE_50 if selected else None,
                        on_click=lambda e, k=key: self._on_select_shard_key(k),
                        content=ft.Text(
                            f"{idx}. {key}",
                            size=11,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            max_lines=1,
                        ),
                    )
                )

        self.shard_page_info.value = (
            f"第 {self.state.page} 頁 / 共 {self.state.total_pages} 頁"
        )
        if keyword:
            self.shard_total_info.value = (
                f"共 {total_filtered}/{len(all_keys)} keys | 每頁 {self.state.page_size}"
            )
        else:
            self.shard_total_info.value = (
                f"共 {len(all_keys)} keys | 每頁 {self.state.page_size}"
            )

        self._render_shard_src_panel()
        self._render_shard_dst_panel()

    def _on_select_shard_key(self, key: str):
        """選擇 key"""
        if key != self.state.selected_key:
            self.state.dst_loaded_sig = None
        self.state.selected_key = key
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_key_filter_change(self, e):
        """key 篩選條件變更"""
        self.state.page = 1
        self._render_shard_detail_keys()
        self.page.update()

    # ==================== SRC/DST 面板 ====================
    def _load_shard_entry(self, cache_type: str, filename: str, key: str):
        """從分片檔案載入單一 entry"""
        import json
        from pathlib import Path

        root = str((self.last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return None

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return None

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            log_warning(f"載入 shard 資料失敗: {e}")
            return None

        if isinstance(raw, dict):
            entry = raw.get(key)
            return entry if isinstance(entry, dict) else None

        return None

    def _render_shard_src_panel(self):
        """渲染 SRC 面板"""
        if (
            not self.state.selected_type
            or not self.state.selected_file
            or not self.state.selected_key
        ):
            self.shard_src_meta.value = "SRC：請先選擇 key"
            self.shard_src_field.value = ""
            return

        ctype = self.state.selected_type
        key = self.state.selected_key
        filename = self.state.selected_file

        entry = cache_get_entry_service(ctype, key)
        if not isinstance(entry, dict):
            entry = self._load_shard_entry(ctype, filename, key)

        src_text = ""
        if isinstance(entry, dict):
            src_text = str(entry.get("src", ""))

        mode_text = (
            "👁️ 預覽" if self.state.src_mode == "preview" else "</> 原始碼"
        )
        self.shard_src_meta.value = f"SRC：{key} | 模式：{mode_text}"

        if self.state.src_mode == "raw":
            import json

            self.shard_src_field.value = json.dumps(src_text, ensure_ascii=False)
        else:
            self.shard_src_field.value = src_text.replace("\\r\\n", "\n").replace(
                "\\n", "\n"
            )

    def _render_shard_dst_panel(self):
        """渲染 DST 面板"""
        ctype = str(self.state.selected_type or "")
        filename = str(self.state.selected_file or "")
        key = str(self.state.selected_key or "")

        if not ctype or not filename or not key:
            self.state.dst_loaded_sig = None
            self.state.dst_original = ""
            self.shard_dst_meta.value = "DST：請先選擇 key"
            self.shard_dst_field.value = ""
            return

        current_sig = (ctype, filename, key)
        if self.state.dst_loaded_sig != current_sig:
            entry = cache_get_entry_service(ctype, key)
            if not isinstance(entry, dict):
                entry = self._load_shard_entry(ctype, filename, key)

            dst_text = ""
            if isinstance(entry, dict):
                dst_text = str(entry.get("dst", "")).replace("\\r\\n", "\n").replace(
                    "\\n", "\n"
                )

            self.state.dst_original = dst_text
            self.shard_dst_field.value = dst_text
            self.state.dst_loaded_sig = current_sig

        self.shard_dst_meta.value = f"DST：{key}"

    def _on_shard_src_preview_mode(self, e):
        """切換到預覽模式"""
        self.state.src_mode = "preview"
        self._render_shard_src_panel()
        self.page.update()

    def _on_shard_src_raw_mode(self, e):
        """切換到原始碼模式"""
        self.state.src_mode = "raw"
        self._render_shard_src_panel()
        self.page.update()

    def _on_shard_page_first(self, e):
        """跳到第一頁"""
        self.state.page = 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_prev(self, e):
        """上一頁"""
        self.state.page -= 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_next(self, e):
        """下一頁"""
        self.state.page += 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_last(self, e):
        """跳到最後一頁"""
        self.state.page = self.state.total_pages
        self._render_shard_detail_keys()
        self.page.update()

    # ==================== 編輯 ====================
    def _on_shard_dst_apply(self, e):
        """套用 DST"""
        if not self.state.selected_key:
            self._show_snack_bar("請先選擇 key", theme.AMBER_700)
            return

        ctype = str(self.state.selected_type or "")
        filename = str(self.state.selected_file or "")
        key = str(self.state.selected_key or "")
        new_dst = str(self.shard_dst_field.value or "")
        old_dst = str(self.state.dst_original or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._show_snack_bar("套用失敗：找不到 key", theme.RED_400)
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            # 記錄歷史
            history_event = {
                "ts": history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": filename,
                "old_dst": old_dst,
                "new_dst": new_dst,
                "action": "apply_from_shard_detail",
                "actor": "cache_shard_panel",
            }
            self._history_append_event(ctype, history_event)

            self.state.dst_original = new_dst
            self._show_snack_bar("已套用 DST 並寫入快取", theme.BLUE_400)
            self.page.update()
        except Exception as ex:
            self._show_snack_bar(f"套用失敗：{ex}", theme.RED_400)

    def _on_shard_dst_revert(self, e):
        """還原 DST"""
        if not self.state.selected_key:
            self._show_snack_bar("請先選擇 key", theme.AMBER_700)
            return

        self.shard_dst_field.value = str(self.state.dst_original or "")
        self._show_snack_bar("已還原到原始值", theme.BLUE_400)
        self.page.update()

    def _on_shard_dst_copy(self, e):
        """複製 DST"""
        if not self.state.selected_key:
            self._show_snack_bar("請先選擇 key", theme.AMBER_700)
            return

        try:
            self.page.set_clipboard(str(self.shard_dst_field.value or ""))
            self._show_snack_bar("已複製 DST 內容", theme.BLUE_400)
        except Exception:
            self._show_snack_bar("複製失敗", theme.RED_400)

    def _history_append_event(self, cache_type: str, event: dict):
        """新增歷史事件"""
        root = str((self.last_overview_data or {}).get("cache_root", "") or "").strip()
        history_append_event(root, cache_type, event)

    def _show_snack_bar(self, message: str, color: str):
        """顯示 SnackBar"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
