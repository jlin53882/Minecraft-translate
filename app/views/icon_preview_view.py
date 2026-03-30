"""app/views/icon_preview_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
import json
import os
import hashlib
import platform
import zipfile
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from app.ui import theme
from translation_tool.utils.log_unit import log_info, log_warning, log_error
from types import SimpleNamespace

from translation_tool.utils.safe_json_loader import load_json_auto_encoding
from translation_tool.core.lang_item_row import LangItemRow

import unicodedata

# ==================================================
# 實驗性功能開關
# ==================================================
_ENABLE_JAR_ICON = False  # TODO: 找回 icon→key 的對應方式後啟用

# ==================================================
# JAR Icon 提取輔助函式
# ==================================================
def _extract_jar_icon(jar_path: Path, modid: str, icon_cache_root: Path) -> Path | None:
    """從 JAR 中提取 mod icon 並快取到磁碟。

    支援：
        - Fabric: assets/<modid>/icon.png
        - NeoForge: neoforge.mods.toml → logoFile

    參數：
        jar_path: JAR 檔案路徑
        modid: mod ID
        icon_cache_root: icon 快取根目錄（如 _icon_preview/jar_icons）

    回傳：
        提取後的圖示路徑，或 None（找不到或提取失敗）
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = zf.namelist()

            # ----- Fabric: assets/<modid>/icon.png -----
            fabric_icon = f"assets/{modid}/icon.png"
            if fabric_icon in names:
                icon_data = zf.read(fabric_icon)
                icon_cache_root.mkdir(parents=True, exist_ok=True)
                safe_jar_name = jar_path.stem
                out_path = icon_cache_root / f"{modid}_{safe_jar_name}.png"
                out_path.write_bytes(icon_data)
                log_info(f"[IconPreview] 提取 Fabric icon: {modid} → {out_path.name}")
                return out_path

            # ----- Fabric: assets/<modid>/textures/**/*.png -----
            import re
            textures_pattern = re.compile(r"^assets/" + re.escape(modid) + r"/textures/.+\.png$")
            texture_files = sorted(n for n in names if textures_pattern.match(n))
            if texture_files:
                # 取第一個找到的 texture PNG
                icon_path = texture_files[0]
                icon_data = zf.read(icon_path)
                icon_cache_root.mkdir(parents=True, exist_ok=True)
                safe_jar_name = jar_path.stem
                out_path = icon_cache_root / f"{modid}_{safe_jar_name}.png"
                out_path.write_bytes(icon_data)
                log_info(f"[IconPreview] 提取 Fabric texture icon: {modid} → {icon_path}")
                return out_path

            # ----- NeoForge: neoforge.mods.toml → logoFile -----
            neoforge_toml = "META-INF/neoforge.mods.toml"
            if neoforge_toml in names:
                try:
                    toml_content = zf.read(neoforge_toml).decode("utf-8")
                except UnicodeDecodeError:
                    toml_content = None

                if toml_content:
                    import re
                    # 解析 logoFile="xxx.png"（可能在 sections[[]] 裡）
                    logo_match = re.search(r'logoFile\s*=\s*"([^"]+\.png)"', toml_content)
                    if logo_match:
                        logo_path = logo_match.group(1)
                        # logoFile 通常相對於 JAR 根目錄
                        if logo_path in names:
                            icon_data = zf.read(logo_path)
                            icon_cache_root.mkdir(parents=True, exist_ok=True)
                            safe_jar_name = jar_path.stem
                            out_path = icon_cache_root / f"{modid}_{safe_jar_name}.png"
                            out_path.write_bytes(icon_data)
                            log_info(f"[IconPreview] 提取 NeoForge logo: {modid} → {out_path.name}")
                            return out_path
    except Exception as ex:
        log_warning(f"[IconPreview] 提取 JAR icon 失敗: {jar_path.name} / {modid} → {ex}")
    return None


# ==================================================
# L2 磁碟快取工具函式
# ==================================================
def _get_cache_dir() -> Path:
    """取得 L2 快取目錄（專案根目錄）。"""
    return Path(__file__).parent.parent.parent / ".icon_cache"


def _compute_cache_key(source_root: Path) -> str:
    """計算快取 key：只看 JAR 檔案名稱，不算內容。

    注意：key 只包含 JAR 的檔名。這樣：
    - 新增/移除 JAR → key 改變 → 快取失效
    - JAR 內容變了但檔名不變 → 不會自動失效（已知限制）
    """
    jar_files = sorted([j.name for j in source_root.glob("*.jar")])
    key_str = str(source_root.resolve()) + ":" + ",".join(jar_files)
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


def _load_entries_cache_l2(source_root: Path) -> list | None:
    """讀取 L2 磁碟快取。回傳 None 表示快取失效。

    失效條件：
    - 快取檔案不存在
    - JSON 解析失敗
    - version 不為 1
    - source_root 不符
    """
    cache_dir = _get_cache_dir()
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None  # 損壞的快取視為失效

    # 版本檢查
    if data.get("version") != 1:
        return None

    # 路徑檢查
    if data.get("source_root") != str(source_root):
        return None

    return data.get("entries", [])


def _save_entries_cache_l2(source_root: Path, entries: list):
    """寫入 L2 磁碟快取（atomic write）。"""
    import tempfile

    cache_dir = _get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    # Atomic write：用 tmp 檔再 rename
    tmp = cache_dir / f"{cache_file.stem}.tmp"
    # 將 entries 轉為可序列化格式
    serializable_entries = []
    for e in entries:
        if hasattr(e, "__dict__"):
            serializable_entries.append(e.__dict__)
        elif isinstance(e, dict):
            serializable_entries.append(e)
        else:
            serializable_entries.append({"modid": str(e.modid), "key": str(e.key), "en": str(e.en), "zh_tw": str(e.zh_tw), "source_jar": getattr(e, "source_jar", "")})

    data = {
        "version": 1,
        "source_root": str(source_root),
        "entries": serializable_entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(cache_file)  # POSIX atomic on most systems


# ==================================================
# Phase 進度條輔助
# ==================================================
def _make_progress_callback(obj, phase: str, total: int):
    """建立 Phase 進度 callback。

    參數：
        obj: IconPreviewView 例項（需有 progress_bar, progress_text, update 方法）
        phase: Phase 顯示文字
        total: 總步數
    """
    def callback(processed: int, total: int):
        # 安全檢查：測試環境或 UI 未初始化時不拋例外
        if not hasattr(obj, 'progress_text') or not hasattr(obj, 'progress_bar'):
            return
        obj.progress_text.value = f"[{phase}] {processed} / {total}"
        obj.progress_bar.value = processed / total if total > 0 else 0
        obj.update()
    return callback


def _show_progress_phase(obj, phase: str, current: int, total: int):
    """更新 Phase 進度顯示（並墊底一次）。"""
    # 安全檢查：測試環境或 UI 未初始化時不拋例外
    if not hasattr(obj, 'progress_text') or not hasattr(obj, 'progress_bar'):
        return
    obj.progress_text.value = f"[{phase}] {current} / {total}"
    obj.progress_bar.value = current / total if total > 0 else 0
    obj.progress_bar.visible = True
    obj.update()


def to_halfwidth(text):
    """
    將字串正規化為半形（NFKC）
    - 只處理 str
    - 非 str 原樣返回（安全）
    """
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)

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
        self.page = page

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
        self._entries_cache: list | None = None   # 緩存的 entries（dict 格式）
        self._cache_meta: dict = {}              # source_root, mode

        self._current_zh_file: Path | None = None
        self._zh_data: dict[str, str] = {}

        # =========================
        # Folder Picker
        # =========================
        self.source_picker = ft.FilePicker(on_result=self._on_pick_source)
        self.review_picker = ft.FilePicker(on_result=self._on_pick_review)
        self.page.overlay.extend([self.source_picker, self.review_picker])

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
        # ===== 模組清單分頁 =====
        self.mod_page_size = 50
        self.mod_current_page = 0
        self.mod_total_pages = 0

        # =========================
        # UI 元件
        # =========================
        self.header = ft.Text("🧩 模組清單", size=20, weight=ft.FontWeight.BOLD)

        self.back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            visible=False,
            tooltip="返回模組清單",
            on_click=self._go_back,
        )

        self.pick_source_btn = ft.ElevatedButton(
            "選擇原文資料夾（en_us + textures）",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e: self.source_picker.get_directory_path(),
        )

        self.pick_review_btn = ft.ElevatedButton(
            "選擇校對資料夾（zh_tw）",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e: self.review_picker.get_directory_path(),
        )

        self.source_label = ft.Text("原文資料夾：尚未選擇", size=12)
        self.review_label = ft.Text("校對資料夾：尚未選擇", size=12)

        self.load_btn = ft.ElevatedButton(
            "載入模組清單",
            icon=ft.Icons.PLAY_ARROW,
            disabled=True,
            on_click=self._on_load_clicked,
        )

        self.save_btn = ft.ElevatedButton(
            "💾 儲存翻譯",
            icon=ft.Icons.SAVE,
            visible=False,
            on_click=self._save_current_zh,
        )

        self.list_view = ft.ListView(expand=True, spacing=8)

        # 進度條
        self.progress_bar = ft.ProgressBar(visible=False, width=500)
        self.progress_text = ft.Text("準備就緒", size=12, color=theme.GREY_600)

        self.controls = [
            ft.Row([self.back_btn, self.header], alignment=ft.MainAxisAlignment.START),
            self.pick_source_btn,
            self.source_label,
            self.pick_review_btn,
            self.review_label,
            self.load_btn,
            # 進度條：置於「載入模組清單」按鈕下方，掃描時才顯示
            self.progress_bar,
            self.progress_text,
            self.save_btn,
            self.page_bar,
            ft.Divider(),
            self.list_view,
        ]

    # ==================================================
    # Folder picker callbacks
    # ==================================================
    def _on_pick_source(self, e: ft.FilePickerResultEvent):
        """處理來源目錄選擇結果"""
        if e.path:
            self.source_root = Path(e.path)
            self.source_label.value = f"原文資料夾：{self.source_root}"
            # 快取失效：source_root 改變
            self._entries_cache = None
            self._cache_meta = {}
            self._update_load_state()
            log_info(f"[IconPreview] 原文資料夾已設定: {self.source_root}")
            self._show_snack("✅ 原文資料夾已設定", color=theme.GREEN_600)
        else:
            log_warning("[IconPreview] 原文資料夾選擇已取消")
            self._show_snack("⚠️ 原文資料夾選擇已取消", color=theme.WARNING)

    def _on_pick_review(self, e: ft.FilePickerResultEvent):
        """處理校對目錄選擇結果"""
        if e.path:
            self.review_root = Path(e.path)
            self.review_label.value = f"校對資料夾：{self.review_root}"
            self._update_load_state()
            log_info(f"[IconPreview] 校對資料夾已設定: {self.review_root}")
            self._show_snack("✅ 校對資料夾已設定", color=theme.GREEN_600)
        else:
            log_warning("[IconPreview] 校對資料夾選擇已取消")
            self._show_snack("⚠️ 校對資料夾選擇已取消", color=theme.WARNING)

    def _update_load_state(self):
        """更新載入按鈕的啟用狀態"""
        self.load_btn.disabled = not (self.source_root and self.review_root)
        self.update()

    # ==================================================
    # 載入 → 建立模組清單
    # ==================================================
    def _on_load_clicked(self, e):
        """處理載入按鈕點擊事件"""
        log_info("[IconPreview] 開始掃描模組...")
        self._show_snack("⏳ 掃描模組中...", color=theme.BLUE_600)
        self.update()

        mode = self._detect_source_mode()
        log_info(f"[IconPreview] 偵測到模式: {mode}")

        # === 快取檢查（L1 in-memory）===
        cache_valid = (
            self._entries_cache is not None
            and self._cache_meta.get("source_root") == str(self.source_root)
            and self._cache_meta.get("mode") == mode
        )

        if cache_valid:
            log_info("[IconPreview] 使用 L1 快取！")
            self._show_snack(f"✅ 使用快取（共 {len(self._entries_cache)} 筆）", color=theme.GREEN_600)
            # 用快取重建 mods dict（dict 轉回 SimpleNamespace，保持屬性存取相容）
            mods = defaultdict(list)
            for entry in self._entries_cache:
                if isinstance(entry, dict):
                    mods[entry["modid"]].append(SimpleNamespace(**entry))
                else:
                    mods[entry.modid].append(entry)
            self.mods = dict(mods)
            self._render_mod_list()
            return

        # === L2 磁碟快取檢查（只在 jar_directory 模式）===
        if mode == "jar_directory":
            cached_entries = _load_entries_cache_l2(self.source_root)
            if cached_entries is not None:
                log_info("[IconPreview] 使用 L2 磁碟快取！")
                self._show_snack(f"✅ 使用磁碟快取（共 {len(cached_entries)} 筆）", color=theme.GREEN_600)
                self._entries_cache = cached_entries
                self._cache_meta = {
                    "source_root": str(self.source_root),
                    "mode": mode,
                }
                # 重建 mods dict
                mods = defaultdict(list)
                for entry in cached_entries:
                    if isinstance(entry, dict):
                        mods[entry["modid"]].append(SimpleNamespace(**entry))
                    else:
                        mods[entry.modid].append(entry)
                self.mods = dict(mods)
                self._render_mod_list()
                return

        # === 快取 miss ===
        
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

        processed = 0

        if mode == "jar_directory":
            log_info("[IconPreview] 使用 JAR 目錄模式掃描")
            self._show_snack("📦 JAR 目錄模式：從 JAR 讀取 en_us.json...", color=theme.BLUE_600)
            jar_files = list(self.source_root.glob("*.jar"))
            total_steps = len(jar_files)
            # Phase 3/3：實際讀取翻譯
            entries = self._load_entries_from_jar_directory(
                processed_callback=_make_progress_callback(self, "讀取翻譯內容", total_steps)
            )
        elif mode == "extracted_folder":
            log_info("[IconPreview] 使用解包資料夾模式掃描")
            entries = self._load_entries()
        else:
            log_warning("[IconPreview] 無法識別資料夾模式，或資料夾為空")
            self._show_snack("❌ 無法識別模式，請確認資料夾內容", color=theme.RED_700)
            entries = []

        if not entries:
            log_warning("[IconPreview] 掃描結果為空，確認 en_us.json 是否存在")
            self._show_snack("❌ 掃描結果為空，請確認 en_us.json 是否存在", color=theme.RED_700)
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
        log_info(f"[IconPreview] 載入完成，共 {len(self.mods)} 個模組，{len(entries)} 筆翻譯")
        self._show_snack(f"✅ 載入完成（共 {len(self.mods)} 個模組）", color=theme.GREEN_600)
        
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
        self.current_modid = None
        self.back_btn.visible = False
        self.save_btn.visible = False
        self.header.value = "🧩 模組清單"

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
                ft.ListTile(
                    title=ft.Text(modid, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(f"總數 {total_count} ｜ 未翻譯 {untranslated}"),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                    on_click=lambda e, m=modid: self._open_mod_detail(m),
                )
            )

        self._update_page_bar_for_mods()
        self.update()

    def _update_page_bar_for_mods(self):
        """更新分頁資訊顯示"""
        self.page_info.value = (
            f"模組清單｜第 {self.mod_current_page + 1} / {self.mod_total_pages} 頁"
        )
        self.prev_page_btn.disabled = self.mod_current_page <= 0
        self.next_page_btn.disabled = self.mod_current_page >= self.mod_total_pages - 1

    def _prev_page(self, e):
        """處理上一頁按鈕點擊"""
        if self.current_modid:
            # 第二層（item）
            if self.current_page > 0:
                self.current_page -= 1
                self._render_current_page()
        else:
            # 第一層（模組）
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
            if self.mod_current_page < self.mod_total_pages - 1:
                self.mod_current_page += 1
                self._render_mod_list()

    # ==================================================
    # 第二層：單一模組 detail
    # ==================================================
    def _open_mod_detail(self, modid: str):
        """開啟模組詳情畫面"""
        self.current_modid = modid
        self.current_page = 0  # ⭐ 重設頁碼
        self.back_btn.visible = True
        self.save_btn.visible = True
        self.header.value = f"📦 {modid}"
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
        self.current_modid = None
        self.current_page = 0
        self.page_info.value = ""
        self.list_view.controls.clear()
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
        self._show_snack("💾 儲存翻譯中...", color=theme.BLUE_600)
        self.update()

        if not self._current_zh_file:
            log_error(f"[IconPreview] 儲存失敗：找不到 zh_tw.json (modid={self.current_modid})")
            self._show_snack("❌ 找不到 zh_tw.json", color=theme.RED_700)
            return

        try:
            self._current_zh_file.parent.mkdir(parents=True, exist_ok=True)
            self._current_zh_file.write_text(
                json.dumps(self._zh_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log_info(f"[IconPreview] 儲存成功：{self._current_zh_file} ({len(self._zh_data)} 筆翻譯)")
            self._show_snack(f"✅ 翻譯已儲存 ({len(self._zh_data)} 筆)", color=theme.GREEN_600)
        except Exception as ex:
            log_error(f"[IconPreview] 儲存失敗：{ex}")
            self._show_snack(f"❌ 儲存失敗：{ex}", color=theme.RED_700)

    # ==================================================
    # 輔助：SnackBar
    # ==================================================
    def _show_snack(
        self,
        message: str,
        color: str = theme.GREEN_600,
    ):
        """
        統一 SnackBar 顯示（Flet Desktop 穩定版）
        - 使用 page.overlay
        - 不會被 ListView / update 吃掉
        """
        log_info(f"[UI] SnackBar: {message}")
        # 清除累積的舊 SnackBar，避免 overlay 無限膨脹
        # Flet 0.28.3 的 page.overlay 是唯讀屬性（無 setter），需 in-place 修改
        for i in range(len(self.page.overlay) - 1, -1, -1):
            if isinstance(self.page.overlay[i], ft.SnackBar):
                del self.page.overlay[i]
        snack = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=color,
            duration=3000,
        )

        # ⚠️ 關鍵：一定要加在 overlay
        self.page.overlay.append(snack)

        snack.open = True
        self.page.update()

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
        found_paths = set(str(direct) for modid in modid_set for direct in [self.review_root / modid / "lang" / "zh_tw.json"] if direct.exists())
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
            except Exception:
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
            log_info(f"[IconPreview] 偵測為解包資料夾模式（{extracted_count} 個 en_us.json）")
            return "extracted_folder"
        else:
            log_warning(f"[IconPreview] 無法識別模式：JAR={jar_count}, en_us={extracted_count}")
            return "empty"

    def _load_entries_from_jar_directory(self, processed_callback=None) -> list:
        """從 JAR 目錄讀取所有 en_us.json（不改磁碟，直接讀 ZIP 內容）。

        流程：
            1. Phase 1/3：收集 modid 清單
            2. Phase 2/3：建立 zh_tw 對照表（雙軌制）
            3. Phase 3/3：建立 entries
        """
        jar_files = list(self.source_root.glob("*.jar"))
        total_steps = len(jar_files)
        failed_jars = []

        # ===== Phase 1/3：收集所有 modid =====
        _show_progress_phase(self, "收集模組資訊", 0, total_steps)
        all_modids = set()
        for jar_path in jar_files:
            try:
                with zipfile.ZipFile(jar_path, 'r') as zf:
                    for name in zf.namelist():
                        if not name.endswith("lang/en_us.json"):
                            continue
                        parts = name.split('/')
                        if len(parts) < 3 or parts[-2] != 'lang' or parts[-1] != 'en_us.json':
                            continue
                        modid = parts[1]
                        all_modids.add(modid)
            except Exception:
                pass
        _show_progress_phase(self, "收集模組資訊", total_steps, total_steps)

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
            found_paths = set(str(self.review_root / modid / "lang" / "zh_tw.json") for modid in all_modids)
            for zh_file in self.review_root.rglob("zh_tw.json"):
                if str(zh_file) not in found_paths:
                    data = load_json_auto_encoding(zh_file)
                    if isinstance(data, dict):
                        zh_map.update(data)
                        log_warning(f"[IconPreview] JAR雙軌-rglob補漏: {zh_file}")

            log_info(f"[IconPreview] 已建立 zh_tw 對照表，共 {len(zh_map)} 筆")
        _show_progress_phase(self, "建立翻譯對照表", 1, 1)

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

        from translation_tool.utils.jar_browser import scan_jars

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
                    log_warning(f"[IconPreview] JAR 解析 JSON 失敗: {jar_path.name} / {name}")
                    failed_jars.append(jar_path.name)
                    continue

                if not isinstance(data, dict):
                    continue

                jar_entries_count = 0
                for key, en_text in data.items():
                    zh_tw_raw = zh_map.get(key, "")
                    if not isinstance(zh_tw_raw, str):
                        zh_tw_raw = ""
                    entries.append(SimpleNamespace(
                        modid=modid,
                        key=key,
                        en=en_text,
                        zh_tw=zh_tw_raw.strip(),
                        source_jar=jar_path.name,
                    ))
                    jar_entries_count += 1

                log_info(f"[IconPreview] {jar_path.name}: 找到 {jar_entries_count} 筆翻譯")

        log_info(f"[IconPreview] JAR 目錄掃描完成：共 {len(entries)} 筆翻譯")

        # ===== JAR Icon 掃描：提取 mod icons =====
        icon_cache_root = self.source_root / "_icon_preview" / "jar_icons"
        # 按 source_jar 分組（減少重複開啟同一個 JAR）
        jar_to_modids: dict[str, set[str]] = defaultdict(set)
        for e in entries:
            if hasattr(e, "source_jar") and e.source_jar:
                jar_to_modids[e.source_jar].add(e.modid)

        # ===== Phase 4/4：提取模組圖示（實驗性，預設關閉）=====
        if _ENABLE_JAR_ICON:
            icon_total = sum(len(m) for m in jar_to_modids.values())
            icon_processed = 0
            _show_progress_phase(self, "提取模組圖示", 0, icon_total)

            for jar_name, modids in jar_to_modids.items():
                jar_path = self.source_root / jar_name
                if not jar_path.exists():
                    continue
                for modid in modids:
                    icon_path = _extract_jar_icon(jar_path, modid, icon_cache_root)
                    if icon_path:
                        for e in entries:
                            if e.modid == modid and getattr(e, "source_jar", "") == jar_name:
                                e.icon_path = str(icon_path)
                    icon_processed += 1
                    _show_progress_phase(self, "提取模組圖示", icon_processed, icon_total)

        # ===== 寫入 L2 磁碟快取 =====
        _save_entries_cache_l2(self.source_root, entries)
        log_info(f"[IconPreview] 已寫入 L2 磁碟快取")

        return entries

    def _render_current_page(self):
        """渲染當前頁面的項目列表"""
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
                    preview_root=self.source_root / "_icon_preview",
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
