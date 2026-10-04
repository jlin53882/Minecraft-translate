"""app/views/icon_preview_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import asyncio
import json
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from app.ui import design, kit
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.icon_preview.icon_cache import (
    _ENABLE_JAR_ICON,
    _batch_extract_jar_icons,
    _get_icon_cache_dir,
    _load_entries_cache_l2,
    _make_progress_callback,
    _migrate_old_icon_cache,
    _save_entries_cache_l2,
    _show_progress_phase,
    to_halfwidth,
)
from app.views.icon_preview_row import LangItemRow
from translation_tool.utils.jar_browser import scan_jars
from translation_tool.utils.log_unit import log_error, log_info, log_warning
from translation_tool.utils.safe_json_loader import load_json_auto_encoding


class IconPreviewView(ft.Column):
    """
    Icon / 翻譯校對 View（模組分層版）
    - 第一層：模組清單
    - 第二層：單一模組翻譯 + icon 校對
    """

    def __init__(self, page: ft.Page):
        """初始化 IconPreviewView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=8)
        self._init_icon_preview_state(page)
        self._init_icon_preview_paging_controls()
        self._init_icon_preview_source_controls()

        setup_card = kit.section_card(
            "資料來源",
            ft.Column(
                [
                    ft.Row(
                        [self.pick_source_btn, self.source_label],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [self.pick_review_btn, self.review_label],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row([self.load_btn], spacing=12),
                    # 進度條：置於「載入模組清單」按鈕下方，掃描時才顯示
                    self.progress_bar,
                    self.progress_text,
                ],
                spacing=12,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="dia",
        )
        self.controls = [
            ft.Row(
                [
                    self.back_btn,
                    kit.tone_icon(
                        ft.Icons.SPELLCHECK, "ench", size=22, box=46, radius=13
                    ),
                    self.header,
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
            # Mod 清單搜尋（Phase 2）：搜尋框 + 狀態文字
            self.mod_search_tf,
            self.mod_search_status,
            setup_card,
            self.save_btn,
            self.page_bar,
            self.page_size_selector,
            self.list_view,
        ]

    def _init_icon_preview_state(self, page) -> None:
        """圖示預覽頁的狀態、載入進度與分頁欄。"""
        self._page = page
        self._loading = False  # 掃描進行中（避免重複點擊載入）
        self._last_progress_refresh = 0.0

        # =========================
        # 使用者選擇的資料夾
        # =========================
        self.source_root: Path | None = None  # 原文（en_us + textures）
        self.review_root: Path | None = None  # 校對（zh_tw）

        # =========================
        # 狀態
        # =========================
        self.mods: dict[str, list] = {}
        self.current_modid: str | None = None

        # 快取（防止重複掃描 JAR）
        self._entries_cache: list | None = None  # 緩存的 entries（dict 格式）
        self._cache_meta: dict = {}  # source_root, mode

        self._current_zh_file: Path | None = None
        self._zh_data: dict[str, str] = {}

        # =========================
        # 即時搜尋（Phase 2）
        # =========================
        self._mod_search_text: str = ""
        # debounce 在 event loop 上執行（threading.Timer 會在背景執行緒改控制項）
        self._mod_search_debouncer = Debouncer(lambda: self._page, 0.150)

        self._detail_search_text: str = ""
        self._detail_search_debouncer = Debouncer(lambda: self._page, 0.150)
        self._detail_filtered_entries: list | None = None  # None 表示無搜尋，顯示全部

        # =========================
        # Folder Picker
        # =========================
        self.source_picker = ft.FilePicker(on_upload=self._on_pick_source)
        self.review_picker = ft.FilePicker(on_upload=self._on_pick_review)
        # FilePicker 是 Service，自動通過 init() 註冊，不需要添加到 page.overlay

        # ===== 分頁設定 =====
        self.page_size = 50
        self.current_page = 0
        self.total_pages = 0

        # 設定頁數
        self.page_info = ft.Text("")

        self.prev_page_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT,
            tooltip="上一頁",
            on_click=self._prev_page,
        )

    def _init_icon_preview_paging_controls(self) -> None:
        """模組清單分頁與每頁筆數控制項。"""

        self.next_page_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT,
            tooltip="下一頁",
            on_click=self._next_page,
        )

        self.page_bar = ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                self.prev_page_btn,
                self.page_info,
                self.next_page_btn,
            ],
        )
        # 每頁顯示數量選擇器（PR61 Issue 1）
        self.page_size_selector = ft.Dropdown(
            label="每頁顯示",
            options=[
                ft.dropdown.Option("25", "25"),
                ft.dropdown.Option("50", "50"),
                ft.dropdown.Option("100", "100"),
            ],
            value="50",
            width=120,
        )
        self.page_size_selector.on_select = self._on_page_size_change
        # ===== 模組清單分頁 =====
        self.mod_page_size = 50
        self.mod_current_page = 0
        self.mod_total_pages = 0

        # ===== 模組搜尋分頁狀態（PR61 Issue 1） =====
        self._mod_search_matched: list[str] = []  # 目前搜尋結果（所有 matched modid）
        self._mod_search_page: int = 0  # 目前搜尋結果頁碼
        self._mod_search_total: int = 0  # 搜尋結果總頁數

        # =========================
        # UI 元件
        # =========================
        self.header = ft.Text(
            "🧩 JAR 圖示預覽", size=22, weight=ft.FontWeight.BOLD, color=C.TEXT
        )

        # Mod 清單搜尋框（Phase 2）
        self.mod_search_tf = kit.field(
            label="搜尋模組",
            hint_text="輸入 modid（大小寫不敏感）",
            dense=True,
            on_change=self._on_mod_search_change,
            visible=False,
        )
        self.mod_search_status = ft.Text("", size=11, color=C.MUTED)

        self.back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            visible=False,
            tooltip="返回模組清單",
            on_click=self._go_back,
        )

    def _init_icon_preview_source_controls(self) -> None:
        """來源／審查資料夾選擇與載入按鈕。"""

        self.pick_source_btn = kit.button(
            "選擇模組資料夾（例：mods 資料夾）",
            "secondary",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e: self._page.run_task(self._async_pick_source_dir),
        )

        self.pick_review_btn = kit.button(
            "選擇資源包路徑",
            "secondary",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e: self._page.run_task(self._async_pick_review_dir),
        )

        self.source_label = ft.Text(
            "模組資料夾：尚未選擇", size=12, color=C.MUTED, font_family=design.FONT_MONO
        )
        self.review_label = ft.Text(
            "資源包路徑：尚未選擇", size=12, color=C.MUTED, font_family=design.FONT_MONO
        )

        self.load_btn = kit.button(
            "載入模組清單",
            "primary",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_load_clicked,
        )
        self.load_btn.disabled = True

        self.save_btn = kit.button(
            "💾 儲存翻譯",
            "primary",
            icon=ft.Icons.SAVE,
            on_click=self._save_current_zh,
        )
        self.save_btn.visible = False

        self.list_view = ft.ListView(expand=True, spacing=8)

        # 進度條
        self.progress_bar = kit.progress_bar(0, "em", height=6)
        self.progress_bar.visible = False
        self.progress_text = ft.Text("準備就緒", size=12, color=C.MUTED)

    # ==================================================
    # Folder picker callbacks
    # ==================================================
    async def _async_pick_source_dir(self):
        """選擇模組資料夾（async 實作）。"""
        result = await self.source_picker.get_directory_path()
        if result:
            self.source_root = Path(result)
            self.source_label.value = f"模組資料夾：{self.source_root}"
            migrated = _migrate_old_icon_cache(self.source_root)
            if migrated:
                show_snack(
                    self.page,
                    "🔄 已搬移舊 icon cache 至新路徑",
                    color=C.DIA,
                    clear_existing=True,
                    duration=3000,
                )
            self._entries_cache = None
            self._cache_meta = {}
            self._update_load_state()
            log_info(f"[IconPreview] 模組資料夾已設定: {self.source_root}")
            show_snack(
                self.page,
                "✅ 模組資料夾已設定",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
        else:
            log_warning("[IconPreview] 模組資料夾選擇已取消")
            show_snack(
                self.page,
                "⚠️ 模組資料夾選擇已取消",
                color=C.GOLD,
                clear_existing=True,
                duration=3000,
            )

    async def _async_pick_review_dir(self):
        """選擇資源包路徑（async 實作）。"""
        result = await self.review_picker.get_directory_path()
        if result:
            self.review_root = Path(result)
            self.review_label.value = f"資源包路徑：{self.review_root}"
            self._update_load_state()
            log_info(f"[IconPreview] 資源包路徑已設定: {self.review_root}")
            show_snack(
                self.page,
                "✅ 資源包路徑已設定",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
        else:
            log_warning("[IconPreview] 資源包路徑選擇已取消")
            show_snack(
                self.page,
                "⚠️ 資源包路徑選擇已取消",
                color=C.GOLD,
                clear_existing=True,
                duration=3000,
            )

    def _on_pick_source(self, e: ft.FilePickerUploadEvent):
        """處理來源目錄選擇結果"""
        if e.path:
            self.source_root = Path(e.path)
            self.source_label.value = f"模組資料夾：{self.source_root}"
            migrated = _migrate_old_icon_cache(self.source_root)
            if migrated:
                show_snack(
                    self.page,
                    "🔄 已搬移舊 icon cache 至新路徑",
                    color=C.DIA,
                    clear_existing=True,
                    duration=3000,
                )
            self._entries_cache = None
            self._cache_meta = {}
            self._update_load_state()
            log_info(f"[IconPreview] 模組資料夾已設定: {self.source_root}")
            show_snack(
                self.page,
                "✅ 模組資料夾已設定",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
        else:
            log_warning("[IconPreview] 模組資料夾選擇已取消")
            show_snack(
                self.page,
                "⚠️ 模組資料夾選擇已取消",
                color=C.GOLD,
                clear_existing=True,
                duration=3000,
            )

    def _on_pick_review(self, e: ft.FilePickerUploadEvent):
        """處理校對目錄選擇結果"""
        if e.path:
            self.review_root = Path(e.path)
            self.review_label.value = f"資源包路徑：{self.review_root}"
            self._update_load_state()
            log_info(f"[IconPreview] 資源包路徑已設定: {self.review_root}")
            show_snack(
                self.page,
                "✅ 資源包路徑已設定",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
        else:
            log_warning("[IconPreview] 資源包路徑選擇已取消")
            show_snack(
                self.page,
                "⚠️ 資源包路徑選擇已取消",
                color=C.GOLD,
                clear_existing=True,
                duration=3000,
            )

    def _update_load_state(self):
        """更新載入按鈕的啟用狀態"""
        self.load_btn.disabled = bool(getattr(self, "_loading", False)) or not (
            self.source_root and self.review_root
        )
        self.update()

    # ==================================================
    # 載入 → 建立模組清單
    # ==================================================
    def _on_load_clicked(self, e):
        """處理載入按鈕點擊事件"""
        if getattr(self, "_loading", False):
            return
        log_info("[IconPreview] 開始掃描模組...")
        show_snack(
            self.page,
            "⏳ 掃描模組中...",
            color=C.DIA,
            clear_existing=True,
            duration=3000,
        )
        # PR61 Issue 1：載入新模組時清除搜尋狀態
        self._mod_search_matched = []
        self._mod_search_page = 0
        self._mod_search_total = 0
        self._mod_search_text = ""
        self.update()

        mode = self._detect_source_mode()
        log_info(f"[IconPreview] 偵測到模式: {mode}")

        if self._try_use_cached_entries(mode):
            return

        self._begin_scan(mode)

    def _rebuild_mods(self, entries) -> None:
        """依 entries 重建 modid → entry 清單（dict 轉回 SimpleNamespace，保持屬性存取相容）。"""
        mods = defaultdict(list)
        for entry in entries:
            if isinstance(entry, dict):
                mods[entry["modid"]].append(SimpleNamespace(**entry))
            else:
                mods[entry.modid].append(entry)
        self.mods = dict(mods)

    def _try_use_cached_entries(self, mode: str) -> bool:
        """命中 L1 記憶體快取或 L2 磁碟快取時直接顯示並回傳 True；否則回傳 False。"""
        # === 快取檢查（L1 in-memory）===
        cache_valid = (
            self._entries_cache is not None
            and self._cache_meta.get("source_root") == str(self.source_root)
            and self._cache_meta.get("mode") == mode
        )

        if cache_valid:
            log_info("[IconPreview] 使用 L1 快取！")
            show_snack(
                self.page,
                f"✅ 使用快取（共 {len(self._entries_cache)} 筆）",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
            # 用快取重建 mods dict（dict 轉回 SimpleNamespace，保持屬性存取相容）
            self._rebuild_mods(self._entries_cache)
            self._render_mod_list()
            return True

        # === L2 磁碟快取檢查（只在 jar_directory 模式）===
        if mode == "jar_directory":
            cached_entries = _load_entries_cache_l2(self.source_root)
            if cached_entries is not None:
                log_info("[IconPreview] 使用 L2 磁碟快取！")
                show_snack(
                    self.page,
                    f"✅ 使用磁碟快取（共 {len(cached_entries)} 筆）",
                    color=C.EM,
                    clear_existing=True,
                    duration=3000,
                )
                self._entries_cache = cached_entries
                self._cache_meta = {
                    "source_root": str(self.source_root),
                    "mode": mode,
                }
                # 重建 mods dict
                self._rebuild_mods(cached_entries)
                self._render_mod_list()
                return True
        return False

    def _begin_scan(self, mode: str) -> None:
        """快取 miss：顯示進度並掃描 entries（有事件迴圈時改在背景執行緒執行）。"""
        # 顯示進度條
        if mode == "jar_directory":
            jar_files = list(self.source_root.glob("*.jar"))
            total_steps = len(jar_files)
        elif mode == "extracted_folder":
            en_files = list(self.source_root.rglob("en_us.json"))
            total_steps = len(en_files)
        else:
            total_steps = 0

        if total_steps > 0:
            self.progress_bar.visible = True
            self.progress_bar.value = 0
            self.progress_text.value = f"正在掃描：0 / {total_steps}"
            self.update()

        if mode == "jar_directory":
            log_info("[IconPreview] 使用 JAR 目錄模式掃描")
            show_snack(
                self.page,
                "📦 JAR 目錄模式：從 JAR 讀取 en_us.json...",
                color=C.DIA,
                clear_existing=True,
                duration=3000,
            )
        elif mode == "extracted_folder":
            log_info("[IconPreview] 使用解包資料夾模式掃描")

        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._finish_load(self._scan_entries(mode, total_steps), mode)
            return

        # 掃描（讀 JAR、建 model index、提取圖示）在執行緒執行：
        # 406 個 JAR 首次載入原本會在 event loop 上同步執行約 60 秒，整個 UI 凍結
        self._loading = True
        self.load_btn.disabled = True
        self.update()

        async def _scan():
            try:
                entries = await asyncio.to_thread(self._scan_entries, mode, total_steps)
            except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
                log_error(f"[IconPreview] 掃描失敗: {ex}")
                show_snack(
                    self.page,
                    f"❌ 掃描失敗：{ex}",
                    color=C.RED,
                    clear_existing=True,
                    duration=4000,
                )
                entries = []
            finally:
                self._loading = False
                self._update_load_state()
            self._finish_load(entries, mode)

        run_task(_scan)

    def _set_progress(self, text: str, value: float, visible: bool | None) -> None:
        """（可在背景執行緒呼叫）記下最新進度；實際賦值與刷新交給 event loop。"""
        self._pending_progress = (text, value, visible)
        self._refresh_progress()

    def _apply_pending_progress(self) -> None:
        """在 event loop 上把最新進度寫進控制項。"""
        pending = getattr(self, "_pending_progress", None)
        if pending is None:
            return
        text, value, visible = pending
        self.progress_text.value = text
        self.progress_bar.value = value
        if visible is not None:
            self.progress_bar.visible = visible

    def _refresh_progress(self):
        """（可在背景執行緒呼叫）節流後由 event loop 刷新進度顯示。"""
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._apply_pending_progress()
            self.update()
            return
        now = time.monotonic()
        if now - self._last_progress_refresh < 0.2:
            return
        self._last_progress_refresh = now

        async def _update():
            self._apply_pending_progress()
            self.update()

        run_task(_update)

    def _scan_entries(self, mode: str, total_steps: int) -> list:
        """讀取翻譯與圖示（不直接刷新畫面，可在背景執行緒執行）。"""
        if mode == "jar_directory":
            return self._load_entries_from_jar_directory(
                processed_callback=_make_progress_callback(
                    self, "讀取翻譯內容", total_steps
                )
            )
        if mode == "extracted_folder":
            return self._load_entries()
        return []

    def _finish_load(self, entries: list, mode: str):
        """（event loop 上）套用掃描結果並渲染模組清單。"""
        self._pending_progress = (
            None  # 掃描結束後不再套用延遲的進度（避免重新顯示進度條）
        )
        if mode not in ("jar_directory", "extracted_folder"):
            log_warning("[IconPreview] 無法識別資料夾模式，或資料夾為空")
            show_snack(
                self.page,
                "❌ 無法識別模式，請確認資料夾內容",
                color=C.RED,
                clear_existing=True,
                duration=3000,
            )
            self.progress_bar.visible = False
            self.update()
            return

        if not entries:
            log_warning("[IconPreview] 掃描結果為空，確認 en_us.json 是否存在")
            show_snack(
                self.page,
                "❌ 掃描結果為空，請確認 en_us.json 是否存在",
                color=C.RED,
                clear_existing=True,
                duration=3000,
            )
            self.progress_bar.visible = False
            self.update()
            return

        # 寫入快取（dict 格式，脫離 SimpleNamespace）
        cache_entries = []
        for entry in entries:
            if hasattr(entry, "__dict__"):
                cache_entries.append(entry.__dict__)
            else:
                cache_entries.append(entry)
        self._entries_cache = cache_entries
        self._cache_meta = {
            "source_root": str(self.source_root),
            "mode": mode,
        }

        mods = defaultdict(list)
        for entry in entries:
            mods[entry.modid].append(entry)

        self.mods = dict(mods)
        log_info(
            f"[IconPreview] 載入完成，共 {len(self.mods)} 個模組，{len(entries)} 筆翻譯"
        )
        show_snack(
            self.page,
            f"✅ 載入完成（共 {len(self.mods)} 個模組）",
            color=C.EM,
            clear_existing=True,
            duration=3000,
        )

        # 隱藏進度條
        self.progress_bar.visible = False
        self.progress_text.value = "準備就緒"
        self.update()

        self._render_mod_list()

    def _update_progress(self, current: int, total: int):
        """更新進度條"""
        if total > 0:
            self.progress_bar.value = current / total
            self.progress_text.value = f"正在掃描：{current} / {total}"
            self.update()

    def _render_mod_list(self):
        """渲染模組清單畫面"""
        # PR61 Issue 1：清除搜尋結果狀態
        self._mod_search_matched = []
        self._mod_search_page = 0
        self._mod_search_total = 0

        self.current_modid = None
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

        mod_ids = sorted(self.mods.keys())
        total = len(mod_ids)

        self.mod_total_pages = max(
            1, (total + self.mod_page_size - 1) // self.mod_page_size
        )

        start = self.mod_current_page * self.mod_page_size
        end = start + self.mod_page_size
        visible_mods = mod_ids[start:end]

        self.list_view.controls.clear()

        for modid in visible_mods:
            entries = self.mods[modid]
            total_count = len(entries)
            untranslated = sum(1 for e in entries if not e.zh_tw.strip())

            self.list_view.controls.append(
                self._mod_row(modid, total_count, untranslated)
            )

        self._update_page_bar_for_mods()
        self.update()

    def _mod_row(self, modid: str, total_count: int, untranslated: int) -> ft.Control:
        """模組清單的一列：modid（等寬字）+ 總數 / 未翻譯晶片，點擊進入該模組。"""
        done_ratio = 1.0 - (untranslated / total_count if total_count else 0.0)
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL + 2,
            ink=True,
            on_click=lambda e, m=modid: self._open_mod_detail(m),
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                modid,
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT,
                                font_family=design.FONT_MONO,
                            ),
                            ft.Text(
                                f"總數 {total_count} ｜ 未翻譯 {untranslated}",
                                size=12,
                                color=C.DIM,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                    ft.Container(
                        width=120,
                        content=kit.progress_bar(
                            done_ratio, "em" if untranslated == 0 else "gold", height=5
                        ),
                    ),
                    kit.chip(
                        "完成" if untranslated == 0 else f"未翻譯 {untranslated}",
                        "em" if untranslated == 0 else "gold",
                    ),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=18, color=C.DIM),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

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
        self.mod_page_size = int(e.control.value)
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

        self.update()

    def _render_mod_search_page(self):
        """渲染搜尋結果的目前頁（PR61 Issue 1）"""
        matched = self._mod_search_matched

        start = self._mod_search_page * self.mod_page_size
        end = start + self.mod_page_size
        visible_mods = matched[start:end]

        self.list_view.controls.clear()
        for modid in visible_mods:
            entries = self.mods[modid]
            total_count = len(entries)
            untranslated = sum(1 for e in entries if not e.zh_tw.strip())
            self.list_view.controls.append(
                self._mod_row(modid, total_count, untranslated)
            )

        self._update_page_bar_for_mods()
        self.update()

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
                self.controls.index(self.list_view)
                if self.list_view in self.controls
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

    def _open_mod_detail(self, modid: str):
        """開啟模組詳情畫面"""
        self.current_modid = modid
        self.current_page = 0  # ⭐ 重設頁碼
        self._detail_search_text = ""  # 重設 detail 搜尋
        self._detail_filtered_entries = None  # None = 無篩選，顯示全部
        self.back_btn.visible = True
        self.save_btn.visible = True
        self.header.value = f"📦 {modid}"

        # ===== Phase 2：顯示 Mod 詳情頁搜尋框 =====
        self._init_detail_search_widgets()
        self.detail_search_tf.value = ""
        self.detail_search_status.value = ""

        # 更新 controls：將 detail 搜尋元件插入 list_view 前
        self._update_detail_search_controls(visible=True)

        # ===== Phase 2：隱藏模組清單搜尋框，避免在 Detail View 中誤觸 =====
        self.mod_search_tf.visible = False
        self.mod_search_status.visible = False

        log_info(f"[IconPreview] 開啟模組詳情: {modid}")

        # Track 1：直接路徑（快速）
        direct = self.review_root / modid / "lang" / "zh_tw.json"
        if direct.exists():
            self._current_zh_file = direct
            self._zh_data = load_json_auto_encoding(direct) or {}
            log_info(f"[IconPreview] 直接路徑: {direct}")
        else:
            # Track 2：rglob fallback（容錯）
            zh_files = list(self.review_root.rglob(f"{modid}/lang/zh_tw.json"))
            self._current_zh_file = zh_files[0] if zh_files else None
            if self._current_zh_file and self._current_zh_file.exists():
                self._zh_data = load_json_auto_encoding(self._current_zh_file) or {}
                log_info(f"[IconPreview] rglob fallback: {self._current_zh_file}")
            else:
                self._zh_data = {}
                log_warning(f"[IconPreview] 找不到 zh_tw.json for mod: {modid}")

        self._render_current_page()

    def _go_back(self, e):
        """處理返回按鈕，返回模組清單"""
        self._cancel_detail_search_debounce()  # P1 fix: 取消 pending debounce timer，避免返回後覆蓋列表
        self.current_modid = None
        self.current_page = 0
        self.page_info.value = ""
        self._detail_search_text = ""
        self._detail_filtered_entries = None
        # 隱藏 detail 搜尋 UI
        self._update_detail_search_controls(visible=False)
        self.list_view.controls.clear()

        # ===== Phase 2：恢復模組清單搜尋框 =====
        if hasattr(self, "mod_search_tf"):
            self.mod_search_tf.visible = True
        if hasattr(self, "_mod_search_text") and self._mod_search_text:
            # 如果之前有搜尋文字，重新執行搜尋以顯示過濾後的結果
            self._do_mod_search()
        else:
            # PR61 Issue 1：清除搜尋結果狀態
            if hasattr(self, "_mod_search_matched"):
                self._mod_search_matched = []
            if hasattr(self, "_mod_search_page"):
                self._mod_search_page = 0
            if hasattr(self, "_mod_search_total"):
                self._mod_search_total = 0
            if hasattr(self, "mod_search_status"):
                self.mod_search_status.visible = False
            self._render_mod_list()

    # ==================================================
    # Row → 回報翻譯變更
    # ==================================================
    def _on_value_changed(self, key: str, value: str):
        """處理翻譯值變更事件"""
        self._zh_data[key] = to_halfwidth(value)

    # ==================================================
    # 儲存 zh_tw.json
    # ==================================================
    def _save_current_zh(self, e):
        """儲存目前的翻譯到 zh_tw.json"""
        log_info(f"[IconPreview] 開始儲存翻譯: {self._current_zh_file}")
        show_snack(
            self.page,
            "💾 儲存翻譯中...",
            color=C.DIA,
            clear_existing=True,
            duration=3000,
        )
        self.update()

        if not self._current_zh_file:
            log_error(
                f"[IconPreview] 儲存失敗：找不到 zh_tw.json (modid={self.current_modid})"
            )
            show_snack(
                self.page,
                "❌ 找不到 zh_tw.json",
                color=C.RED,
                clear_existing=True,
                duration=3000,
            )
            return

        try:
            self._current_zh_file.parent.mkdir(parents=True, exist_ok=True)
            self._current_zh_file.write_text(
                json.dumps(self._zh_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log_info(
                f"[IconPreview] 儲存成功：{self._current_zh_file} ({len(self._zh_data)} 筆翻譯)"
            )
            show_snack(
                self.page,
                f"✅ 翻譯已儲存 ({len(self._zh_data)} 筆)",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
        except Exception as ex:  # noqa: BLE001
            log_error(f"[IconPreview] 儲存失敗：{ex}")
            show_snack(
                self.page,
                f"❌ 儲存失敗：{ex}",
                color=C.RED,
                clear_existing=True,
                duration=3000,
            )

    # ==================================================
    # 輔助：SnackBar
    # ==================================================

    # ==================================================
    # 核心資料載入（只處理 JSON）
    # ==================================================
    def _load_entries(self):
        """
        - 以 source_root 的 en_us.json 為主
        - 對照 review_root 的 zh_tw.json
        - 只建立索引，不處理 icon
        """
        entries = []

        if not self.source_root or not self.review_root:
            return entries

        # 改成雙軌
        # 方式 A：直接路徑（需先知道所有 modid）
        # 先從 source_root 掃出 modid 清單
        modid_set = set()
        for en_file in self.source_root.rglob("en_us.json"):
            parts = en_file.parts
            try:
                idx = parts.index("assets")
                modid = parts[idx + 1]
                modid_set.add(modid)
            except (ValueError, IndexError):
                continue

        # Track 1：直接路徑（快速）
        zh_map = {}
        for modid in modid_set:
            direct = self.review_root / modid / "lang" / "zh_tw.json"
            if direct.exists():
                data = load_json_auto_encoding(direct)
                if isinstance(data, dict):
                    zh_map.update(data)
                    log_info(f"[IconPreview] 雙軌-直接: {direct}")

        # Track 2：rglob fallback（容錯，找漏網）
        found_paths = {
            str(direct)
            for modid in modid_set
            for direct in [self.review_root / modid / "lang" / "zh_tw.json"]
            if direct.exists()
        }
        for zh_file in self.review_root.rglob("zh_tw.json"):
            if str(zh_file) not in found_paths:
                data = load_json_auto_encoding(zh_file)
                if isinstance(data, dict):
                    zh_map.update(data)
                    log_warning(f"[IconPreview] 雙軌-rglob補漏: {zh_file}")

        # 掃描 en_us
        for en_file in self.source_root.rglob("en_us.json"):
            data = load_json_auto_encoding(en_file)
            if not isinstance(data, dict):
                continue

            try:
                parts = en_file.parts
                idx = parts.index("assets")
                modid = parts[idx + 1]
            except Exception:  # noqa: BLE001
                modid = "unknown"

            for key, en_text in data.items():
                zh_tw_raw = zh_map.get(key, "")
                if not isinstance(zh_tw_raw, str):
                    zh_tw_raw = ""
                entries.append(
                    SimpleNamespace(
                        modid=modid,
                        key=key,
                        en=en_text,
                        zh_tw=zh_tw_raw.strip(),
                    )
                )

        return entries

    # ==================================================
    # JAR 目錄模式：偵測與掃描
    # ==================================================
    def _detect_source_mode(self) -> str:
        """偵測 source_root 是「JAR 目錄」還是「已解包資料夾」。

        回傳：
            "jar_directory"   - mods 資料夾模式（JAR 檔案優先）
            "extracted_folder" - 傳統解包資料夾（en_us.json 存在）
            "empty"           - 無內容或無法識別
        """
        if not self.source_root:
            return "unknown"

        jar_count = len(list(self.source_root.glob("*.jar")))
        extracted_count = len(list(self.source_root.rglob("en_us.json")))

        if jar_count > 0 and extracted_count == 0:
            log_info(f"[IconPreview] 偵測為 JAR 目錄模式（{jar_count} 個 JAR 檔）")
            return "jar_directory"
        elif extracted_count > 0:
            log_info(
                f"[IconPreview] 偵測為解包資料夾模式（{extracted_count} 個 en_us.json）"
            )
            return "extracted_folder"
        else:
            log_warning(
                f"[IconPreview] 無法識別模式：JAR={jar_count}, en_us={extracted_count}"
            )
            return "empty"

    def _load_entries_from_jar_directory(self, processed_callback=None) -> list:
        """從 JAR 目錄讀取所有 en_us.json（不改磁碟，直接讀 ZIP 內容）。

        流程：
            1. Phase 1/3：收集 modid 清單
            2. Phase 2/3：建立 zh_tw 對照表（雙軌制）
            3. Phase 3/3：建立 entries
        """
        if self.source_root is None:
            return []
        jar_files = list(self.source_root.glob("*.jar"))
        total_steps = len(jar_files)

        all_modids = self._collect_jar_modids(jar_files, total_steps)
        zh_map = self._build_zh_tw_lookup(all_modids)
        entries = self._scan_jar_entries(processed_callback, total_steps, zh_map)
        self._extract_and_cache_icons(entries)
        return entries

    def _collect_jar_modids(self, jar_files, total_steps):
        """Phase 1/3：收集所有 JAR 內的 modid。"""
        # ===== Phase 1/3：收集所有 modid =====
        _show_progress_phase(self, "收集模組資訊", 0, total_steps)
        all_modids = set()
        for jar_path in jar_files:
            try:
                with zipfile.ZipFile(jar_path, "r") as zf:
                    for name in zf.namelist():
                        if not name.endswith("lang/en_us.json"):
                            continue
                        parts = name.split("/")
                        if (
                            len(parts) < 3
                            or parts[-2] != "lang"
                            or parts[-1] != "en_us.json"
                        ):
                            continue
                        modid = parts[1]
                        all_modids.add(modid)
            except Exception:  # noqa: BLE001, S110
                pass
        _show_progress_phase(self, "收集模組資訊", total_steps, total_steps)
        return all_modids

    def _build_zh_tw_lookup(self, all_modids):
        """Phase 2/3：建立 zh_tw 對照表（雙軌制）。"""
        # ===== Phase 2/3：建立 zh_tw 對照表（雙軌制）=====
        _show_progress_phase(self, "建立翻譯對照表", 0, 1)
        zh_map = {}
        if self.review_root and all_modids:
            # Track 1：直接路徑
            for modid in all_modids:
                direct = self.review_root / modid / "lang" / "zh_tw.json"
                if direct.exists():
                    data = load_json_auto_encoding(direct)
                    if isinstance(data, dict):
                        zh_map.update(data)
                        log_info(f"[IconPreview] JAR雙軌-直接: {direct}")

            # Track 2：rglob fallback
            found_paths = {
                str(self.review_root / modid / "lang" / "zh_tw.json")
                for modid in all_modids
            }
            for zh_file in self.review_root.rglob("zh_tw.json"):
                if str(zh_file) not in found_paths:
                    data = load_json_auto_encoding(zh_file)
                    if isinstance(data, dict):
                        zh_map.update(data)
                        log_warning(f"[IconPreview] JAR雙軌-rglob補漏: {zh_file}")

            log_info(f"[IconPreview] 已建立 zh_tw 對照表，共 {len(zh_map)} 筆")
        _show_progress_phase(self, "建立翻譯對照表", 1, 1)
        return zh_map

    def _scan_jar_entries(self, processed_callback, total_steps, zh_map):
        """Phase 3/3：掃描 JAR 內的 en_us.json 並建立 entries。"""
        # ===== Phase 3/3：使用 jar_browser 多執行緒掃描 =====
        entries = []
        failed_jars = []

        # 墊底一次：讓使用者知道掃描啟動了
        # 向後兼容：舊測試使用 0 參數 callback，新設計使用 2 參數 callback
        if processed_callback:
            try:
                processed_callback(0, total_steps)
            except TypeError:
                try:
                    processed_callback()
                except TypeError:
                    pass  # 忽略不兼容的 callback

        # 包裝 callback：同時支援 0 參數（旧測試）和 2 參數（新設計）
        def wrapped_callback(processed: int, total: int):
            if processed_callback:
                try:
                    processed_callback(processed, total)
                except TypeError:
                    try:
                        processed_callback()
                    except TypeError:
                        pass

        results = scan_jars(
            jar_dir=self.source_root,
            patterns=[r"assets/([^/]+)/lang/en_us\.json"],
            processed_callback=wrapped_callback,
        )

        # 建立 entries
        for jar_path, files in results.items():
            for name, content in files.items():
                if not name.endswith("lang/en_us.json"):
                    continue
                if content is None:
                    continue  # binary 檔案（不應該在這裡出現）

                parts = name.split("/")
                modid = parts[1]

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    log_warning(
                        f"[IconPreview] JAR 解析 JSON 失敗: {jar_path.name} / {name}"
                    )
                    failed_jars.append(jar_path.name)
                    continue

                if not isinstance(data, dict):
                    continue

                jar_entries_count = 0
                for key, en_text in data.items():
                    zh_tw_raw = zh_map.get(key, "")
                    if not isinstance(zh_tw_raw, str):
                        zh_tw_raw = ""
                    entries.append(
                        SimpleNamespace(
                            modid=modid,
                            key=key,
                            en=en_text,
                            zh_tw=zh_tw_raw.strip(),
                            source_jar=jar_path.name,
                        )
                    )
                    jar_entries_count += 1

                log_info(
                    f"[IconPreview] {jar_path.name}: 找到 {jar_entries_count} 筆翻譯"
                )

        log_info(f"[IconPreview] JAR 目錄掃描完成：共 {len(entries)} 筆翻譯")
        return entries

    def _extract_and_cache_icons(self, entries) -> None:
        """Phase 4/4：批次提取模組圖示並寫入 L2 磁碟快取。"""
        # ===== JAR Icon 掃描：提取 mod icons =====
        icon_cache_root = _get_icon_cache_dir()

        # ===== Phase 4/4：批次提取模組圖示（每個 JAR 只開一次 ZIP）=====
        if _ENABLE_JAR_ICON:
            # 按 source_jar 分組
            jar_to_entries: dict[str, list] = defaultdict(list)
            for e in entries:
                if getattr(e, "source_jar", None):
                    jar_to_entries[e.source_jar].append(e)

            def _on_icon_progress(done: int, total: int):
                _show_progress_phase(self, "提取模組圖示", done, total)

            _batch_extract_jar_icons(
                jar_to_entries, icon_cache_root, self.source_root, _on_icon_progress
            )

        # ===== 寫入 L2 磁碟快取 =====
        _save_entries_cache_l2(self.source_root, entries)
        log_info("[IconPreview] 已寫入 L2 磁碟快取")

    def _render_current_page(self):
        """渲染當前頁面的項目列表（支援 detail 搜尋過濾）"""
        # Phase 2：搜尋過濾邏輯
        if self._detail_filtered_entries is not None:
            # 有搜尋條件，使用過濾後的 entries
            entries = self._detail_filtered_entries
        else:
            entries = self.mods.get(self.current_modid, [])

        total = len(entries)

        self.total_pages = max(1, (total + self.page_size - 1) // self.page_size)

        start = self.current_page * self.page_size
        end = start + self.page_size

        self.list_view.controls.clear()

        for entry in entries[start:end]:
            self.list_view.controls.append(
                LangItemRow(
                    lang_key=entry.key,
                    en_text=entry.en,
                    zh_text=self._zh_data.get(entry.key, ""),
                    assets_root=self.source_root / "assets",
                    preview_root=_get_icon_cache_dir(),
                    on_value_changed=self._on_value_changed,
                    icon_path=getattr(entry, "icon_path", None),
                )
            )

        self.page_info.value = (
            f"{self.current_modid}｜第 {self.current_page + 1} / {self.total_pages} 頁"
        )
        self.prev_page_btn.disabled = self.current_page <= 0
        self.next_page_btn.disabled = self.current_page >= self.total_pages - 1

        self.update()

    @property
    def page(self):
        return self._page
