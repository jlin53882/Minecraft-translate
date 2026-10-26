"""IconPreviewView 的單一模組詳情、載入 entries 與翻譯儲存（由 icon_preview_view.py 拆出，#114）。"""

import asyncio
import json
import threading
from types import SimpleNamespace

from app.services_impl.icon_service import IconResult
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    reserve_page_operation,
)
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.icon_preview.entries_cache import (
    _source_entry_data,
)
from app.views.icon_preview.icon_cache import (
    _get_icon_cache_dir,
    to_halfwidth,
)
from app.views.icon_preview.render_operation import render_current_page
from app.views.icon_preview_row import LangItemRow, prepare_row_icon
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.log_unit import log_error, log_info, log_warning
from translation_tool.utils.safe_json_loader import load_json_auto_encoding


class IconPreviewDetailMixin:
    """IconPreviewView 的單一模組詳情、載入 entries 與翻譯儲存（由 icon_preview_view.py 拆出，#114）。"""

    def _open_mod_detail(self, modid: str):
        """開啟模組詳情畫面"""
        self.current_modid = modid
        self.current_page = 0  # ⭐ 重設頁碼
        self._detail_search_text = ""  # 重設 detail 搜尋
        self._detail_filtered_entries = None  # None = 無篩選，顯示全部
        self.back_btn.visible = True
        self.save_btn.visible = True
        self._switch_list(detail=True, ready=False)
        self.header.value = f"📦 {modid}"

        # ===== Phase 2：顯示 Mod 詳情頁搜尋框 =====
        self._init_detail_search_widgets()
        self.detail_search_tf.value = ""
        self.detail_search_status.value = ""

        # ===== Phase 2：隱藏模組清單搜尋框，避免在 Detail View 中誤觸 =====
        self.mod_search_tf.visible = False
        self.mod_search_status.visible = False

        # 更新 controls：將 detail 搜尋元件插入 list_view 前（同時送出上面的版面變更）
        self._update_detail_search_controls(visible=True)

        log_info(f"[IconPreview] 開啟模組詳情: {modid}")

        # zh_tw.json 的搜尋（含 rglob fallback）與讀取在背景執行緒，不佔用 event loop
        self._current_zh_file = None
        self._zh_data = {}
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._current_zh_file, self._zh_data = self._find_zh_file(modid)
            self._render_current_page()
            return

        generation = self._render_generation

        operation = reserve_page_operation(
            self.page,
            name=f"IconPreview 載入 {modid} 詳情",
            owner="icon-preview-detail",
            cancellation=CancellationPolicy.BOUNDARY_ONLY,
            commit=CommitPolicy.EPHEMERAL,
            shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
        )
        if not operation.admitted:
            show_snack(self.page, "應用程式正在關閉，未啟動詳情載入", color=C.GOLD)
            return
        result = {}

        def find_file():
            try:
                result["found"] = self._find_zh_file(modid)
            except Exception as ex:  # noqa: BLE001 - UI coroutine 顯示錯誤
                result["error"] = ex

        if not operation.launch(find_file):
            operation.finish(error=RuntimeError("detail load worker was not launched"))
            return

        async def _open():
            while not operation.done_event.is_set():
                await asyncio.sleep(0.02)
            if "error" in result:
                log_error(f"[IconPreview] 載入 {modid} 詳情失敗: {result['error']!r}")
                show_snack(self.page, f"載入詳情失敗：{result['error']}", color=C.RED)
                return
            if operation.handle and operation.handle.cancel_requested:
                return
            if generation != self._render_generation or self.current_modid != modid:
                return  # 期間已換頁／返回／卸載：丟棄結果
            self._current_zh_file, self._zh_data = result["found"]
            self._render_current_page()

        try:
            run_task(_open)
        except Exception as ex:  # noqa: BLE001 - 排程失敗時需結束 owner 並回報 UI
            log_error(f"[IconPreview] 無法排程詳情載入: {ex!r}")

    def _find_zh_file(self, modid: str):
        """找出並讀取 ``{modid}/lang/zh_tw.json``（磁碟 I/O，可在背景執行緒）。

        回傳 ``(path | None, data)``。
        """
        # Track 1：直接路徑（快速）
        direct = self.review_root / modid / "lang" / "zh_tw.json"
        if direct.exists():
            log_info(f"[IconPreview] 直接路徑: {direct}")
            return direct, load_json_auto_encoding(direct) or {}
        # Track 2：rglob fallback（容錯）
        zh_files = list(self.review_root.rglob(f"{modid}/lang/zh_tw.json"))
        found = zh_files[0] if zh_files else None
        if found and found.exists():
            log_info(f"[IconPreview] rglob fallback: {found}")
            return found, load_json_auto_encoding(found) or {}
        log_warning(f"[IconPreview] 找不到 zh_tw.json for mod: {modid}")
        return found, {}

    def _go_back(self, e):
        """處理返回按鈕，返回模組清單"""
        self._cancel_detail_search_debounce()  # P1 fix: 取消 pending debounce timer，避免返回後覆蓋列表
        self.current_modid = None
        self.current_page = 0
        self.page_info.value = ""
        self._detail_search_text = ""
        self._detail_filtered_entries = None
        self._switch_list(detail=False)
        # 隱藏 detail 搜尋 UI
        self._update_detail_search_controls(visible=False)

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

        target = self._current_zh_file
        payload = json.dumps(self._zh_data, ensure_ascii=False, indent=2)
        count = len(self._zh_data)
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._finish_save(self._write_zh_file(target, payload), target, count)
            return

        operation = reserve_page_operation(
            self.page,
            name=f"IconPreview 儲存 {target.name}",
            owner="icon-preview-save",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            commit=CommitPolicy.PARTIAL_ALLOWED,
            durability=DurabilityPolicy.USER_ACTION,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
        )
        if not operation.admitted:
            show_snack(self.page, "應用程式正在關閉，未開始儲存", color=C.GOLD)
            return
        saved = {"error": None}
        worker_done = threading.Event()

        def write_file():
            try:
                saved["error"] = self._write_zh_file(target, payload)
                if saved["error"] is not None:
                    operation.handle.record_error(saved["error"])
            finally:
                worker_done.set()

        if not operation.launch(write_file):
            operation.finish(error=RuntimeError("save worker was not launched"))
            show_snack(self.page, "無法啟動儲存工作", color=C.RED)
            return

        async def _save():
            while not worker_done.is_set():
                await asyncio.sleep(0.02)
            error = saved["error"]
            self._finish_save(error, target, count)

        run_task(_save)

    @staticmethod
    def _write_zh_file(target, payload: str):
        """寫入 zh_tw.json（磁碟 I/O，可在背景執行緒）；成功回傳 None，失敗回傳例外。"""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        except Exception as ex:  # noqa: BLE001 - 錯誤由呼叫端顯示在 UI
            return ex
        return None

    def _finish_save(self, error, target, count: int) -> None:
        """（event loop 上）顯示儲存結果。"""
        if error is None:
            log_info(f"[IconPreview] 儲存成功：{target} ({count} 筆翻譯)")
            show_snack(
                self.page,
                f"✅ 翻譯已儲存 ({count} 筆)",
                color=C.EM,
                clear_existing=True,
                duration=3000,
            )
            return
        log_error(f"[IconPreview] 儲存失敗：{error}")
        show_snack(
            self.page,
            f"❌ 儲存失敗：{error}",
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
        - 翻譯在載入完成時，依目前的 review_root 補入
        - 只建立索引，不處理 icon
        """
        entries = []

        if not self.source_root:
            return entries

        # 掃描 en_us
        for en_file in self.source_root.rglob("en_us.json"):
            raise_if_cancelled()
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
                raise_if_cancelled()
                entries.append(
                    SimpleNamespace(
                        modid=modid,
                        key=key,
                        en=en_text,
                        zh_tw="",
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

        jar_count = 0
        for _jar_path in self.source_root.glob("*.jar"):
            raise_if_cancelled()
            jar_count += 1

        extracted_count = 0
        for _lang_path in self.source_root.rglob("en_us.json"):
            raise_if_cancelled()
            extracted_count += 1

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

    def _build_zh_tw_lookup(self, all_modids):
        """Phase 2/3：建立 zh_tw 對照表（雙軌制）。"""
        zh_map = {}
        if self.review_root and all_modids:
            # Track 1：直接路徑
            for modid in all_modids:
                raise_if_cancelled()
                direct = self.review_root / modid / "lang" / "zh_tw.json"
                if direct.exists():
                    data = load_json_auto_encoding(direct)
                    if isinstance(data, dict):
                        for key, value in data.items():
                            raise_if_cancelled()
                            zh_map[key] = value
                        log_info(f"[IconPreview] JAR雙軌-直接: {direct}")

            # Track 2：rglob fallback
            found_paths = {
                str(self.review_root / modid / "lang" / "zh_tw.json")
                for modid in all_modids
            }
            for zh_file in self.review_root.rglob("zh_tw.json"):
                raise_if_cancelled()
                if str(zh_file) not in found_paths:
                    data = load_json_auto_encoding(zh_file)
                    if isinstance(data, dict):
                        for key, value in data.items():
                            raise_if_cancelled()
                            zh_map[key] = value
                        log_warning(f"[IconPreview] JAR雙軌-rglob補漏: {zh_file}")

            log_info(f"[IconPreview] 已建立 zh_tw 對照表，共 {len(zh_map)} 筆")
        return zh_map

    def _hydrate_entries(self, entries):
        """從目前選取的 review_root 補上 zh_tw，來源快取本身不含譯文。"""
        source_entries = [_source_entry_data(entry) for entry in entries]
        modids = {str(entry.get("modid", "")) for entry in source_entries}
        zh_map = self._build_zh_tw_lookup(modids)

        hydrated = []
        for entry in source_entries:
            raise_if_cancelled()
            zh_tw = zh_map.get(entry.get("key"), "")
            if not isinstance(zh_tw, str):
                zh_tw = ""
            hydrated.append(SimpleNamespace(**entry, zh_tw=zh_tw.strip()))
        return hydrated

    def _render_current_page(self):
        """渲染當前頁面；I/O orchestration 位於獨立 operation helper。"""
        render_current_page(self)

    @staticmethod
    def _prepare_row_icons(entries, icon_context) -> list:
        """替每個項目準備圖示（磁碟／ZIP／圖片 I/O，在背景執行緒）。"""
        assets_root, preview_root = icon_context
        prepared = []
        for entry in entries:
            raise_if_cancelled()
            prepared.append(
                prepare_row_icon(
                    entry.key,
                    assets_root,
                    preview_root,
                    getattr(entry, "icon_path", None),
                )
            )
        return prepared

    def _fill_rows(self, page_entries, prepared) -> None:
        """（event loop 上）把這一頁的項目綁到可重用的列並刷新；``prepared`` 為 None 時同步計算。"""
        pool = self._detail_row_pool
        if prepared is None:
            prepared = self._prepare_row_icons(
                page_entries, (self.source_root / "assets", _get_icon_cache_dir())
            )
        while len(pool) < len(page_entries):
            pool.append(self._new_detail_row())
        for row, entry, icon in zip(pool, page_entries, prepared, strict=False):
            row.bind(entry.key, entry.en, self._zh_data.get(entry.key, ""), icon)
        self.detail_list_view.controls = pool[: len(page_entries)]
        self.detail_list_view.visible = True
        self._update_list_area(self.detail_list_view)

    def _new_detail_row(self) -> LangItemRow:
        """建立一列尚未綁定項目的校對列（之後以 ``bind`` 換內容）。"""
        return LangItemRow(
            lang_key="",
            en_text="",
            zh_text="",
            assets_root=self.source_root / "assets",
            preview_root=_get_icon_cache_dir(),
            on_value_changed=self._on_value_changed,
            prepared_icon=(IconResult(icon_path=None, reason="", risk=None), None),
        )
