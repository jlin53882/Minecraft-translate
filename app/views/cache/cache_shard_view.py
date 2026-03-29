"""app/views/cache/cache_shard_view.py（快取分片頁）

本頁是快取系統的「分片編輯」功能區塊，功能包含：
- 分類/分片清單導航
- Key 列表檢視與分頁
- SRC 預覽（可切換原始碼模式）
- DST 編輯與寫回
- 歷史紀錄浮動視窗

本檔案由 cache_view.py 拆分出來，獨立運作。
"""

import json
import re
from pathlib import Path

import flet as ft

from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_get_overview_service,
    cache_update_dst_service,
    cache_save_all_service,
)
from translation_tool.utils.log_unit import log_error, log_warning


class CacheShardView(ft.Column):
    """快取分片頁（UI）。

    本類別處理分片區塊的 UI 組裝與事件處理。
    """

    def __init__(self, page: ft.Page):
        """初始化分片頁。"""
        super().__init__(expand=True, spacing=10)
        self.page = page

        # Global state
        self.ui_busy = False
        self.busy_reason = ""
        self._last_overview_data: dict = {}

        # Shard state - C1: KeyListCard
        self.shard_detail_selected_type = ""
        self.shard_detail_selected_file = ""
        self.shard_detail_selected_key = ""
        self.shard_detail_keys: list[str] = []
        self.shard_detail_page = 1
        self.shard_detail_page_size = 50
        self.shard_detail_total_pages = 1

        # C2: SRC 預覽模式
        self.shard_detail_src_mode = "preview"

        # C3: DST 編輯
        self.shard_dst_loaded_sig: tuple[str, str, str] | None = None
        self.shard_dst_original = ""

        # Shard UI components - 分片導航
        self.query_type_shard_hint = ft.Text(
            "分類 / 分片清單", size=11, color=ft.Colors.GREY_700
        )
        self.query_type_shard_col = ft.Column(
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )
        self.query_type_shard_list_container = ft.Container(
            expand=True,
            padding=8,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=ft.Colors.WHITE,
            alignment=ft.alignment.top_left,
            content=self.query_type_shard_col,
        )

        # C1: Key List
        self.shard_detail_meta = ft.Text(
            "尚未選擇分片", size=11, color=ft.Colors.GREY_700
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

        self.shard_detail_key_list_container = ft.Container(
            expand=True,
            padding=6,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=ft.Colors.WHITE,
            alignment=ft.alignment.top_left,
            content=self.shard_detail_key_list,
        )

        # C2: SRC
        self.shard_src_meta = ft.Text(
            "SRC：請先選擇 key", size=11, color=ft.Colors.GREY_700
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
        self.shard_src_container = ft.Container(
            expand=True,
            padding=6,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=ft.Colors.WHITE,
            alignment=ft.alignment.top_left,
            content=self.shard_src_field,
        )

        # C3: DST
        self.shard_dst_meta = ft.Text(
            "DST：請先選擇 key", size=11, color=ft.Colors.GREY_700
        )
        self.shard_dst_field = ft.TextField(
            value="",
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_align=ft.TextAlign.LEFT,
            text_style=ft.TextStyle(font_family="Consolas", size=12, height=1.45),
        )
        self.btn_shard_dst_apply = ft.ElevatedButton(
            "套用 DST", icon=ft.Icons.SAVE, on_click=self._on_shard_dst_apply
        )
        self.btn_shard_dst_revert = ft.OutlinedButton(
            "還原", icon=ft.Icons.UNDO, on_click=self._on_shard_dst_revert
        )
        self.btn_shard_dst_copy = ft.OutlinedButton(
            "複製", icon=ft.Icons.CONTENT_COPY, on_click=self._on_shard_dst_copy
        )

        self.shard_dst_container = ft.Container(
            expand=True,
            padding=6,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=ft.Colors.WHITE,
            alignment=ft.alignment.top_left,
            content=self.shard_dst_field,
        )

        # 組裝頁面
        self.query_type_shard_card = self._build_query_type_shard_card()

        self.controls = [
            ft.Container(
                padding=ft.padding.only(bottom=6),
                content=ft.Row(
                    [
                        ft.Text(
                            "快取分片編輯 (Cache Shard)",
                            size=20,
                            weight=ft.FontWeight.BOLD,
                        )
                    ]
                ),
            ),
            self.query_type_shard_card,
        ]

    # Lifecycle
    def did_mount(self):
        """頁面載入後初始化。"""
        try:
            self._load_overview()
            self._render_query_type_shard_page()
            self._render_shard_detail_keys()
            self.page.update()
        except Exception as ex:
            log_error(f"CacheShardView did_mount failed: {ex}")

    # UI Building
    def _dynamic_shard_key_panel_width(self) -> int:
        """計算 shard key panel width。"""
        try:
            w = float(getattr(self.page, "width", 0) or 0)
        except Exception:
            w = 0
        if w <= 0:
            return 360
        return max(280, min(560, int(w * 0.30)))

    def _build_query_type_shard_card(self):
        """建立分片卡片。"""
        self.shard_nav_column = ft.Container(
            expand=True,
            padding=10,
            content=ft.Column(
                [
                    ft.Text("分類 / 分片", size=15, weight=ft.FontWeight.BOLD),
                    self.query_type_shard_hint,
                    self.query_type_shard_list_container,
                ],
                spacing=8,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self.shard_nav_view = ft.Container(
            expand=True,
            visible=True,
            content=self.shard_nav_column,
        )

        self.shard_key_column = ft.Container(
            width=self._dynamic_shard_key_panel_width(),
            padding=10,
            border=ft.border.only(
                right=ft.border.BorderSide(1, ft.Colors.OUTLINE_VARIANT)
            ),
            content=ft.Column(
                [
                    ft.Text("C1 KeyListCard", weight=ft.FontWeight.BOLD),
                    self.shard_detail_meta,
                    self.tf_shard_key_filter,
                    self.shard_detail_key_list_container,
                    ft.Row(
                        [
                            self.btn_shard_page_first,
                            self.btn_shard_page_prev,
                            self.shard_page_info,
                            self.btn_shard_page_next,
                            self.btn_shard_page_last,
                            ft.Text("|", size=12, color=ft.Colors.GREY_500),
                            self.shard_total_info,
                        ],
                        wrap=True,
                        spacing=6,
                    ),
                ],
                spacing=8,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self.shard_editor_column = ft.Container(
            expand=True,
            padding=12,
            content=ft.Column(
                [
                    ft.Text("編輯工作區", size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "右側：上 SRC（唯讀）/ 下 DST（可編輯）",
                        size=11,
                        color=ft.Colors.GREY_700,
                    ),
                    ft.Text("C2 SRC 預覽", weight=ft.FontWeight.BOLD),
                    self.shard_src_meta,
                    ft.Row(
                        [self.btn_shard_src_preview, self.btn_shard_src_raw],
                        wrap=True,
                        spacing=6,
                    ),
                    self.shard_src_container,
                    ft.Divider(height=8),
                    ft.Text("C3 DST 編輯", weight=ft.FontWeight.BOLD),
                    self.shard_dst_meta,
                    self.shard_dst_container,
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
            ),
        )

        self.shard_workspace_card = ft.Container(
            expand=True,
            visible=False,
            padding=0,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10,
            bgcolor=ft.Colors.WHITE,
            content=ft.Column(
                [
                    ft.Container(
                        padding=ft.padding.symmetric(horizontal=10, vertical=8),
                        border=ft.border.only(
                            bottom=ft.border.BorderSide(1, ft.Colors.OUTLINE_VARIANT)
                        ),
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(
                                            "C1 / C2 / C3 工作區",
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                        self.shard_detail_meta,
                                    ],
                                    spacing=2,
                                    tight=True,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.START,
                        ),
                    ),
                    ft.Row(
                        expand=True,
                        spacing=0,
                        controls=[
                            self.shard_key_column,
                            self.shard_editor_column,
                        ],
                    ),
                ],
                expand=True,
                spacing=0,
            ),
        )

        return ft.Container(
            expand=True,
            padding=0,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10,
            bgcolor=ft.Colors.WHITE,
            content=ft.Column(
                controls=[self.shard_nav_view, self.shard_workspace_card],
                expand=True,
                spacing=0,
            ),
        )

    # Helper methods
    def _iter_type_states(self, data: dict):
        """迭代類型狀態。"""
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

    def _load_overview(self):
        """載入總覽資料。"""
        try:
            self._last_overview_data = cache_get_overview_service()
        except Exception as ex:
            log_warning(f"讀取總覽失敗：{ex}")
            self._last_overview_data = {}

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_400):
        """顯示 SnackBar。"""
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _notify(self, message: str, level: str = "info"):
        """顯示通知。"""
        lv = (level or "info").lower()
        if lv == "error":
            self._show_snack_bar(message, ft.Colors.RED_400)
        elif lv == "warn":
            self._show_snack_bar(message, ft.Colors.AMBER_700)
        else:
            self._show_snack_bar(message, ft.Colors.BLUE_400)

    # Shard loading
    def _load_shard_rows(
        self, cache_type: str, active_shard_id: str, shard_capacity: int
    ) -> list[dict]:
        """載入分片列表。"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return []

        type_dir = Path(root) / cache_type
        if not type_dir.exists():
            return []

        def _sort_key(path: Path):
            stem = path.stem
            m = re.search(r"(\d+)$", stem)
            seq = int(m.group(1)) if m else -1
            return (seq, stem.lower())

        active_filename = (
            f"{cache_type}_{str(active_shard_id)}.json"
            if str(active_shard_id or "").strip()
            else ""
        )

        shard_files: list[Path] = []
        for fp in type_dir.glob("*.json"):
            name = fp.name.lower()
            if name == f"{cache_type.lower()}_cache_main.json":
                continue
            shard_files.append(fp)

        rows: list[dict] = []
        for fp in sorted(shard_files, key=_sort_key, reverse=True):
            key_count = 0
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    key_count = len(raw)
                elif isinstance(raw, list):
                    key_count = len(raw)
            except Exception:
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
        """載入分片 keys。"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return []

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return []

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return []

        if isinstance(raw, dict):
            return sorted([str(k) for k in raw.keys()])

        if isinstance(raw, list):
            out = []
            for idx, item in enumerate(raw):
                if isinstance(item, dict) and item.get("key"):
                    out.append(str(item.get("key")))
                else:
                    out.append(f"[{idx}]")
            return out

        return []

    def _load_shard_entry(
        self, cache_type: str, filename: str, key: str
    ) -> dict | None:
        """載入分片 entry。"""
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return None

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return None

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return None

        if isinstance(raw, dict):
            entry = raw.get(key)
            return entry if isinstance(entry, dict) else None

        return None

    # Shard rendering
    def _render_query_type_shard_page(self):
        """渲染分類/分片頁面。"""
        self.query_type_shard_col.controls.clear()

        for ctype, st in self._iter_type_states(self._last_overview_data):
            shard = st.get("active_shard_id", "-")
            shard_capacity = int(st.get("shard_capacity", 2500) or 2500)

            rows = self._load_shard_rows(ctype, shard, shard_capacity)

            if not rows:
                continue

            self.query_type_shard_col.controls.append(
                ft.Text(
                    f"{ctype} (分片: {shard})",
                    weight=ft.FontWeight.BOLD,
                    size=13,
                )
            )

            for row in rows[:5]:
                fname = row.get("filename", "")
                key_count = row.get("key_count", 0)
                is_active = row.get("is_active", False)

                bg = ft.Colors.BLUE_50 if is_active else None
                border = ft.Colors.BLUE_300 if is_active else ft.Colors.OUTLINE_VARIANT

                self.query_type_shard_col.controls.append(
                    ft.Container(
                        padding=6,
                        border=ft.border.all(1, border),
                        border_radius=6,
                        bgcolor=bg,
                        on_click=lambda e, ct=ctype, fn=fname: self._on_select_shard_row(ct, fn),
                        content=ft.Row(
                            [
                                ft.Text(
                                    fname,
                                    size=11,
                                    weight=ft.FontWeight.BOLD if is_active else None,
                                ),
                                ft.Container(expand=True),
                                ft.Text(
                                    f"{key_count} keys",
                                    size=10,
                                    color=ft.Colors.GREY_700,
                                ),
                            ],
                            spacing=4,
                        ),
                    )
                )

        if not self.query_type_shard_col.controls:
            self.query_type_shard_col.controls.append(
                ft.Text("沒有可顯示的分片", color=ft.Colors.GREY_600)
            )

        self._refresh_disabled_state()

    def _set_shard_detail_page(self, page: int):
        """設定分片頁碼。"""
        total = len(self.shard_detail_keys)
        self.shard_detail_total_pages = max(
            1, (total + self.shard_detail_page_size - 1) // self.shard_detail_page_size
        )
        self.shard_detail_page = max(1, min(page, self.shard_detail_total_pages))

    def _render_shard_detail_keys(self):
        """渲染 key 列表。"""
        if not hasattr(self, "shard_detail_key_list"):
            return

        self.shard_detail_key_list.controls.clear()

        if not self.shard_detail_selected_type or not self.shard_detail_selected_file:
            self.shard_detail_selected_key = ""
            self.shard_detail_meta.value = "尚未選擇分片"
            self.shard_page_info.value = "第 1 頁 / 共 1 頁"
            self.shard_total_info.value = "共 0 keys | 每頁 50"
            self.shard_dst_loaded_sig = None
            self.shard_dst_original = ""
            self.shard_detail_key_list.controls.append(
                ft.Text(
                    "請先在上方分片清單點選一個 shard",
                    size=11,
                    color=ft.Colors.GREY_600,
                )
            )
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._refresh_disabled_state()
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

        if not page_keys:
            if keyword and all_keys:
                self.shard_detail_key_list.controls.append(
                    ft.Text(
                        "此篩選條件沒有符合的 key", size=11, color=ft.Colors.GREY_600
                    )
                )
            else:
                self.shard_detail_key_list.controls.append(
                    ft.Text("此分片目前沒有 key", size=11, color=ft.Colors.GREY_600)
                )
        else:
            for idx, key in enumerate(page_keys, start=start + 1):
                selected = key == self.shard_detail_selected_key
                self.shard_detail_key_list.controls.append(
                    ft.Container(
                        padding=6,
                        border=ft.border.all(
                            1,
                            ft.Colors.BLUE_300
                            if selected
                            else ft.Colors.OUTLINE_VARIANT,
                        ),
                        border_radius=6,
                        bgcolor=ft.Colors.BLUE_50 if selected else None,
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

    def _format_shard_src_text(self, src_text: str, mode: str) -> str:
        """格式化 SRC 文字。"""
        src = str(src_text or "")
        if mode == "raw":
            return json.dumps(src, ensure_ascii=False)
        return src.replace("\\r\\n", "\n").replace("\\n", "\n")

    def _render_shard_src_panel(self):
        """渲染 SRC 面板。"""
        if not hasattr(self, "shard_src_field"):
            return

        if (
            not self.shard_detail_selected_type
            or not self.shard_detail_selected_file
            or not self.shard_detail_selected_key
        ):
            self.shard_src_meta.value = "SRC：請先選擇 key"
            self.shard_src_field.value = ""
            self._refresh_disabled_state()
            return

        ctype = self.shard_detail_selected_type
        key = self.shard_detail_selected_key
        filename = self.shard_detail_selected_file

        entry = cache_get_entry_service(ctype, key)
        if not isinstance(entry, dict):
            entry = self._load_shard_entry(ctype, filename, key)

        src_text = ""
        if isinstance(entry, dict):
            src_text = str(entry.get("src", ""))

        mode_text = (
            "👁️ 預覽" if self.shard_detail_src_mode == "preview" else "</> 原始碼"
        )
        self.shard_src_meta.value = f"SRC：{key} | 模式：{mode_text}"
        self.shard_src_field.value = self._format_shard_src_text(
            src_text, self.shard_detail_src_mode
        )
        self._refresh_disabled_state()

    def _normalize_cache_text(self, text: str) -> str:
        """正規化快取文字。"""
        return str(text or "").replace("\\r\\n", "\n").replace("\\n", "\n")

    def _render_shard_dst_panel(self):
        """渲染 DST 面板。"""
        if not hasattr(self, "shard_dst_field"):
            return

        ctype = str(self.shard_detail_selected_type or "")
        filename = str(self.shard_detail_selected_file or "")
        key = str(self.shard_detail_selected_key or "")

        if not ctype or not filename or not key:
            self.shard_dst_loaded_sig = None
            self.shard_dst_original = ""
            self.shard_dst_meta.value = "DST：請先選擇 key"
            self.shard_dst_field.value = ""
            self._refresh_disabled_state()
            return

        current_sig = (ctype, filename, key)
        if self.shard_dst_loaded_sig != current_sig:
            entry = cache_get_entry_service(ctype, key)
            if not isinstance(entry, dict):
                entry = self._load_shard_entry(ctype, filename, key)

            dst_text = ""
            if isinstance(entry, dict):
                dst_text = self._normalize_cache_text(str(entry.get("dst", "")))

            self.shard_dst_original = dst_text
            self.shard_dst_field.value = dst_text
            self.shard_dst_loaded_sig = current_sig

        self.shard_dst_meta.value = f"DST：{key}"
        self._refresh_disabled_state()

    def _refresh_disabled_state(self):
        """刷新禁用狀態。"""
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

    def _set_shard_workspace_visible(self, visible: bool):
        """設定工作區可見性。"""
        show_workspace = bool(visible)
        if hasattr(self, "shard_nav_view"):
            self.shard_nav_view.visible = not show_workspace
        if hasattr(self, "shard_workspace_card"):
            self.shard_workspace_card.visible = show_workspace

    # Event handlers - Shard navigation
    def _on_shard_key_filter_change(self, e):
        """處理 key 過濾變更。"""
        self.shard_detail_page = 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_select_shard_row(self, cache_type: str, filename: str):
        """處理選擇分片列。"""
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
        self._set_shard_workspace_visible(True)
        self.page.update()

    def _on_select_shard_key(self, key: str):
        """處理選擇 key。"""
        if key != self.shard_detail_selected_key:
            self.shard_dst_loaded_sig = None
        self.shard_detail_selected_key = key
        self._render_shard_detail_keys()
        self.page.update()

    # Event handlers - SRC
    def _on_shard_src_preview_mode(self, e):
        """處理 SRC 預覽模式。"""
        self.shard_detail_src_mode = "preview"
        self._render_shard_src_panel()
        self.page.update()

    def _on_shard_src_raw_mode(self, e):
        """處理 SRC 原始碼模式。"""
        self.shard_detail_src_mode = "raw"
        self._render_shard_src_panel()
        self.page.update()

    # Event handlers - DST
    def _on_shard_dst_apply(self, e):
        """套用 DST 變更。"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        filename = str(self.shard_detail_selected_file or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not filename or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        old_dst = str(self.shard_dst_original or "")
        new_dst = str(self.shard_dst_field.value or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            self.shard_dst_original = new_dst
            if self.shard_dst_loaded_sig != (ctype, filename, key):
                self.shard_dst_loaded_sig = (ctype, filename, key)

            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._notify("已套用 C3 DST 並寫入快取", "info")
            self.page.update()
        except Exception as ex:
            self._notify(f"套用 DST 失敗：{ex}", "error")

    def _on_shard_dst_revert(self, e):
        """還原 DST 到原始值。"""
        if not self.shard_detail_selected_key:
            self._show_snack_bar("請先選擇 key", ft.Colors.AMBER_700)
            return

        self.shard_dst_field.value = str(self.shard_dst_original or "")
        self._show_snack_bar("已還原到原始值", ft.Colors.BLUE_400)
        self._refresh_disabled_state()
        self.page.update()

    def _on_shard_dst_copy(self, e):
        """複製 DST 內容。"""
        if not self.shard_detail_selected_key:
            self._notify("請先選擇 key", "warn")
            return

        try:
            self.page.set_clipboard(str(self.shard_dst_field.value or ""))
            self._notify("已複製 C3 DST 內容", "info")
        except Exception:
            self._notify("複製失敗", "error")

    # Event handlers - Pagination
    def _on_shard_page_first(self, e):
        """第一頁。"""
        self.shard_detail_page = 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_prev(self, e):
        """上一頁。"""
        self.shard_detail_page -= 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_next(self, e):
        """下一頁。"""
        self.shard_detail_page += 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_last(self, e):
        """最後一頁。"""
        self.shard_detail_page = self.shard_detail_total_pages
        self._render_shard_detail_keys()
        self.page.update()
