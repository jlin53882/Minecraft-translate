"""app/views/icon_preview_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from app.ui import design, kit
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.icon_preview.detail_mixin import IconPreviewDetailMixin
from app.views.icon_preview.icon_cache import (
    _load_entries_cache_l2,
    _make_progress_callback,
    _migrate_old_icon_cache,
)
from app.views.icon_preview.list_mixin import IconPreviewListMixin
from translation_tool.utils.log_unit import log_error, log_info, log_warning


class IconPreviewView(IconPreviewDetailMixin, IconPreviewListMixin, ft.Column):
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

    @property
    def page(self):
        return self._page
