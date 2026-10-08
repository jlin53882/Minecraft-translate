"""IconPreviewView 的單一模組詳情、載入 entries 與翻譯儲存（由 icon_preview_view.py 拆出，#114）。"""

import asyncio
import json
import threading
from collections import defaultdict
from types import SimpleNamespace

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
    _save_entries_cache_l2,
    _source_entry_data,
)
from app.views.icon_preview.icon_cache import (
    _batch_extract_jar_icons,
    _get_icon_cache_dir,
    to_halfwidth,
)
from app.views.icon_preview.progress import _show_progress_phase
from app.views.icon_preview.render_operation import render_current_page
from app.views.icon_preview_row import LangItemRow, prepare_row_icon
from translation_tool.utils.jar_browser import scan_jars
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

        # zh_tw.json 的搜尋（含 rglob fallback）與讀取在背景執行緒，不佔用 event loop
        self._current_zh_file = None
        self._zh_data = {}
        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            self._current_zh_file, self._zh_data = self._find_zh_file(modid)
            self._render_current_page()
            return

        self.list_view.controls.clear()
        self.update()
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

        entries 只包含來源資料；zh_tw 會在共用載入完成階段依目前的
        review_root 補入，確保 L1/L2 命中與新掃描行為一致。
        """
        if self.source_root is None:
            return []
        jar_files = list(self.source_root.glob("*.jar"))
        total_steps = len(jar_files)

        entries = self._scan_jar_entries(processed_callback, total_steps)
        self._extract_and_cache_icons(entries)
        return entries

    def _build_zh_tw_lookup(self, all_modids):
        """Phase 2/3：建立 zh_tw 對照表（雙軌制）。"""
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
        return zh_map

    def _hydrate_entries(self, entries):
        """從目前選取的 review_root 補上 zh_tw，來源快取本身不含譯文。"""
        source_entries = [_source_entry_data(entry) for entry in entries]
        modids = {str(entry.get("modid", "")) for entry in source_entries}
        zh_map = self._build_zh_tw_lookup(modids)

        hydrated = []
        for entry in source_entries:
            zh_tw = zh_map.get(entry.get("key"), "")
            if not isinstance(zh_tw, str):
                zh_tw = ""
            hydrated.append(SimpleNamespace(**entry, zh_tw=zh_tw.strip()))
        return hydrated

    def _scan_jar_entries(self, processed_callback, total_steps):
        """掃描 JAR 內的 en_us.json 並建立來源 entries。"""
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
                    entries.append(
                        SimpleNamespace(
                            modid=modid,
                            key=key,
                            en=en_text,
                            zh_tw="",
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
        """渲染當前頁面；I/O orchestration 位於獨立 operation helper。"""
        render_current_page(self)

    @staticmethod
    def _prepare_row_icons(entries, icon_context) -> list:
        """替每個項目準備圖示（磁碟／ZIP／圖片 I/O，在背景執行緒）。"""
        assets_root, preview_root = icon_context
        return [
            prepare_row_icon(
                entry.key, assets_root, preview_root, getattr(entry, "icon_path", None)
            )
            for entry in entries
        ]

    def _fill_rows(self, page_entries, prepared) -> None:
        """（event loop 上）依準備好的圖示建構列並刷新；``prepared`` 為 None 時同步計算。"""
        self.list_view.controls.clear()
        for index, entry in enumerate(page_entries):
            self.list_view.controls.append(
                LangItemRow(
                    lang_key=entry.key,
                    en_text=entry.en,
                    zh_text=self._zh_data.get(entry.key, ""),
                    assets_root=self.source_root / "assets",
                    preview_root=_get_icon_cache_dir(),
                    on_value_changed=self._on_value_changed,
                    icon_path=getattr(entry, "icon_path", None),
                    prepared_icon=None if prepared is None else prepared[index],
                )
            )
        self.update()
