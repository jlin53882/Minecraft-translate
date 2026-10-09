"""app/views/icon_preview_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    ShutdownPolicy,
    reserve_page_operation,
)
from app.ui import kit
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.safe_file_picker import SafeFilePicker
from app.ui.snack import show_snack
from app.views.icon_preview.detail_mixin import IconPreviewDetailMixin
from app.views.icon_preview.entries_cache import (
    _compute_source_identity,
    _load_entries_cache_l2,
    _source_entry_data,
)
from app.views.icon_preview.icon_cache import _migrate_old_icon_cache
from app.views.icon_preview.list_mixin import IconPreviewListMixin
from app.views.icon_preview.load_operation import (
    load_icon_preview,
    observe_async_owner,
)
from app.views.icon_preview.progress import _make_progress_callback
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.log_unit import log_error, log_info, log_warning
from translation_tool.utils.path_text import normalize_path_text


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
                        [self.pick_source_btn, self.source_path_input],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [self.pick_review_btn, self.review_path_input],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "Web 模式請輸入執行程式那台電腦上的路徑；選擇按鈕僅適用於支援資料夾對話框的平台。",
                        size=11,
                        color=C.MUTED,
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
        # 每次卸載就遞增：進行中的載入看到世代變了就丟棄結果，不再碰（已卸載的）控制項
        self._load_generation = 0
        # 詳情頁渲染的世代：連續渲染只套用最後一次；卸載時遞增以丟棄進行中的結果
        self._render_generation = 0
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
        self.source_picker = SafeFilePicker(on_upload=self._on_pick_source)
        self.review_picker = SafeFilePicker(on_upload=self._on_pick_review)
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

        self.source_path_input = kit.field(
            label="模組資料夾路徑",
            hint_text=r"例如：C:\Users\使用者\.minecraft\mods",
            dense=True,
            expand=True,
            path_input=True,
            on_change=self._on_source_path_changed,
            on_blur=self._on_source_path_input,
        )
        self.review_path_input = kit.field(
            label="資源包／lang_output 路徑",
            hint_text=r"例如：C:\Users\使用者\resource_pack\lang_output",
            dense=True,
            expand=True,
            path_input=True,
            on_change=self._on_review_path_changed,
            on_blur=self._on_review_path_input,
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
            self.source_path_input.value = str(self.source_root)
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
            self.review_path_input.value = str(self.review_root)
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
            self.source_path_input.value = str(self.source_root)
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
            self.review_path_input.value = str(self.review_root)
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

    def _on_source_path_input(self, e):
        """失焦時同步手動輸入的 mods 路徑並刷新載入按鈕。"""
        value = self._read_path_input_value(e, "_source_path_text")
        if getattr(e.control, "value", None) != value:
            e.control.value = value
        self._set_source_path(value, migrate=True)
        self._update_load_state()

    def _on_source_path_changed(self, e):
        """輸入期間同步來源，但不重繪整個頁面以免 Web 欄位失焦。"""
        value = self._read_path_input_value(e, "_source_path_text")
        self._set_source_path(value)

    def _set_source_path(self, value, *, migrate: bool = False):
        new_root = Path(value).expanduser() if value else None
        if new_root != self.source_root:
            self._entries_cache = None
            self._cache_meta = {}
        self.source_root = new_root
        if migrate and new_root is not None and new_root.is_dir():
            migrated = _migrate_old_icon_cache(new_root)
            if migrated:
                show_snack(
                    self.page,
                    f"✅ 已遷移 {migrated} 個舊版圖示快取",
                    color=C.EM,
                )

    def _on_review_path_input(self, e):
        """失焦時同步校對路徑並刷新載入按鈕。"""
        value = self._read_path_input_value(e, "_review_path_text")
        if getattr(e.control, "value", None) != value:
            e.control.value = value
        self._set_review_path(value)
        self._update_load_state()

    def _on_review_path_changed(self, e):
        """輸入期間同步校對路徑，但避免每個按鍵都觸發整頁更新。"""
        self._set_review_path(self._read_path_input_value(e, "_review_path_text"))

    def _set_review_path(self, value):
        self.review_root = Path(value).expanduser() if value else None

    def _read_path_input_value(self, e, buffer_name: str) -> str:
        """讀取 change 事件完整文字，並在失焦事件資料為空時保留最後值。"""
        event_value = getattr(e, "data", None)
        control_value = getattr(e.control, "value", None)
        if isinstance(event_value, str):
            value = event_value
        elif isinstance(control_value, str) and control_value:
            value = control_value
        else:
            value = getattr(self, buffer_name, "")
        value = normalize_path_text(value)
        setattr(self, buffer_name, value)
        return value

    def _validate_input_paths(self) -> bool:
        """載入前確認兩個路徑都是執行端可讀取的既有資料夾。"""
        source_value = normalize_path_text(self.source_path_input.value)
        review_value = normalize_path_text(self.review_path_input.value)
        # 保留程式內呼叫者與既有測試直接設定 root 的相容性。
        source_value = source_value or str(self.source_root or "")
        review_value = review_value or str(self.review_root or "")

        parsed_paths = []
        for label, value in (
            ("模組資料夾", source_value),
            ("資源包路徑", review_value),
        ):
            if not value:
                show_snack(
                    self.page,
                    f"⚠️ 請輸入{label}路徑",
                    color=C.GOLD,
                    clear_existing=True,
                )
                return False
            path = Path(value).expanduser()
            if not path.is_dir():
                show_snack(
                    self.page,
                    f"⚠️ {label}不存在或不是資料夾：{path}",
                    color=C.GOLD,
                    clear_existing=True,
                )
                return False
            parsed_paths.append(path)

        source_root, review_root = parsed_paths
        if source_root != self.source_root:
            self._entries_cache = None
            self._cache_meta = {}
        _migrate_old_icon_cache(source_root)
        self.source_root = source_root
        self.review_root = review_root
        self.source_path_input.value = str(source_root)
        self.review_path_input.value = str(review_root)
        self._update_load_state()
        log_info(
            f"[IconPreview] 路徑已設定：source_root={source_root}, "
            f"review_root={review_root}"
        )
        return True

    # ==================================================
    # 載入 → 建立模組清單
    # ==================================================
    def will_unmount(self) -> None:
        """換頁／關閉：讓進行中的載入作廢（結果丟棄、不再更新控制項）並取消 debounce（idempotent）。"""
        self._load_generation += 1
        self._render_generation += 1
        self._mod_search_debouncer.cancel()
        self._detail_search_debouncer.cancel()

    def did_mount(self) -> None:
        """重新掛載：作廢的載入已把旗標復位，這裡只同步按鈕狀態。"""
        self._update_load_state()

    def _on_load_clicked(self, e):
        """處理載入按鈕點擊事件（磁碟掃描、讀快取都在背景執行緒，不佔用 event loop）。"""
        if getattr(self, "_loading", False):
            return
        if not self._validate_input_paths():
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

        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            # 沒有 event loop（測試替身）：維持同步流程
            mode = self._detect_source_mode()
            log_info(f"[IconPreview] 偵測到模式: {mode}")
            if self._try_use_cached_entries(mode):
                return
            self._begin_scan(mode)
            return

        operation = reserve_page_operation(
            self.page,
            name="IconPreview 模組載入與掃描",
            owner="icon-preview-load",
            cancellation=CancellationPolicy.BOUNDARY_ONLY,
            commit=CommitPolicy.EPHEMERAL,
            shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
        )
        if not operation.admitted:
            show_snack(self.page, "應用程式正在關閉，未啟動掃描", color=C.GOLD)
            return

        self._loading = True
        self.load_btn.disabled = True
        self.update()
        generation = self._load_generation

        async def _load():
            await self._load_async(generation, operation)

        try:
            future = run_task(_load)
            observe_async_owner(future, operation)
        except Exception as ex:  # noqa: BLE001 - 排程失敗時需結束 owner 並回報 UI
            operation.finish(error=ex)
            self._loading = False
            self._update_load_state()
            log_error(f"[IconPreview] 無法排程載入工作: {ex!r}")

    async def _load_async(self, generation: int, operation=None) -> None:
        """依序：偵測模式 → 查快取 → 計算步數 → 掃描；每個阻塞步驟都在執行緒中執行。

        每次 await 之後檢查世代：View 已卸載（世代變了）就丟棄結果，只復位旗標。
        """
        await load_icon_preview(self, generation, operation)

    def _rebuild_mods(self, entries) -> None:
        """依 entries 重建 modid → entry 清單（dict 轉回 SimpleNamespace，保持屬性存取相容）。"""
        mods = defaultdict(list)
        for entry in entries:
            if isinstance(entry, dict):
                mods[entry["modid"]].append(SimpleNamespace(**entry))
            else:
                mods[entry.modid].append(entry)
        self.mods = dict(mods)

    def _lookup_cached_entries(self, mode: str):
        """查 L1 記憶體／L2 磁碟快取（純讀取，可在背景執行緒）。

        回傳 ``("L1" | "L2", entries)``；沒有可用快取時回傳 None。
        """
        identity = (
            _compute_source_identity(self.source_root, mode)
            if self.source_root is not None
            else None
        )
        cache_valid = (
            self._entries_cache is not None
            and self._cache_meta.get("source_identity") == identity
        )
        if cache_valid:
            return "L1", self._hydrate_entries(self._entries_cache)
        # L2 磁碟快取只在 jar_directory 模式
        if mode == "jar_directory" and self.source_root is not None:
            cached_entries = _load_entries_cache_l2(self.source_root)
            if cached_entries is not None:
                return "L2", self._hydrate_entries(cached_entries)
        return None

    def _apply_cached_entries(self, kind: str, entries, mode: str) -> None:
        """（event loop 上）顯示命中的快取並渲染模組清單。"""
        if kind == "L1":
            log_info("[IconPreview] 使用 L1 快取！")
            label = f"✅ 使用快取（共 {len(entries)} 筆）"
        else:
            log_info("[IconPreview] 使用 L2 磁碟快取！")
            label = f"✅ 使用磁碟快取（共 {len(entries)} 筆）"
            self._entries_cache = [_source_entry_data(entry) for entry in entries]
            self._cache_meta = {
                "source_identity": _compute_source_identity(self.source_root, mode)
            }
        show_snack(self.page, label, color=C.EM, clear_existing=True, duration=3000)
        # 用快取重建 mods dict（dict 轉回 SimpleNamespace，保持屬性存取相容）
        self._rebuild_mods(entries)
        self._render_mod_list()

    def _try_use_cached_entries(self, mode: str) -> bool:
        """（同步流程）命中快取時直接顯示並回傳 True；否則回傳 False。"""
        cached = self._lookup_cached_entries(mode)
        if cached is None:
            return False
        self._apply_cached_entries(cached[0], cached[1], mode)
        return True

    def _count_scan_steps(self, mode: str) -> int:
        """掃描前計算步數（glob／rglob；可在背景執行緒）。"""
        if mode == "jar_directory":
            paths = self.source_root.glob("*.jar")
        elif mode == "extracted_folder":
            paths = self.source_root.rglob("en_us.json")
        else:
            return 0

        count = 0
        for _path in paths:
            raise_if_cancelled()
            count += 1
        raise_if_cancelled()
        return count

    def _show_scan_started(self, mode: str, total_steps: int) -> None:
        """（event loop 上）顯示掃描進度條與提示。"""
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

    def _begin_scan(self, mode: str) -> None:
        """（同步流程，無 event loop 時）掃描 entries 並套用結果。"""
        total_steps = self._count_scan_steps(mode)
        self._show_scan_started(mode, total_steps)
        self._finish_load(self._scan_entries(mode, total_steps), mode)

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
            if not self._loading:  # 載入已結束或已作廢（unmount）：不再套用進度
                return
            self._apply_pending_progress()
            self.update()

        run_task(_update)

    def _scan_entries(self, mode: str, total_steps: int) -> list:
        """讀取翻譯與圖示（不直接刷新畫面，可在背景執行緒執行）。"""
        if mode == "jar_directory":
            entries = self._load_entries_from_jar_directory(
                processed_callback=_make_progress_callback(
                    self, "讀取翻譯內容", total_steps
                )
            )
        elif mode == "extracted_folder":
            entries = self._load_entries()
        else:
            return []
        return self._hydrate_entries(entries)

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

        # L1 stores source-side scan data only; the review translation is
        # hydrated afresh on every load from the currently selected root.
        cache_entries = [_source_entry_data(entry) for entry in entries]
        self._entries_cache = cache_entries
        self._cache_meta = {
            "source_identity": _compute_source_identity(self.source_root, mode),
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
