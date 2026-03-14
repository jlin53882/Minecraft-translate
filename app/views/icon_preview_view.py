"""app/views/icon_preview_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft
import json
from pathlib import Path
from collections import defaultdict
from app.ui import theme
from types import SimpleNamespace

from translation_tool.utils.safe_json_loader import load_json_auto_encoding
from translation_tool.core.lang_item_row import LangItemRow

import unicodedata

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

        self.controls = [
            ft.Row([self.back_btn, self.header], alignment=ft.MainAxisAlignment.START),
            self.pick_source_btn,
            self.source_label,
            self.pick_review_btn,
            self.review_label,
            self.load_btn,
            self.save_btn,
            self.page_bar,
            ft.Divider(),
            self.list_view,
        ]

    # ==================================================
    # Folder picker callbacks
    # ==================================================
    def _on_pick_source(self, e: ft.FilePickerResultEvent):
        """

        回傳：None
        """
        if e.path:
            self.source_root = Path(e.path)
            self.source_label.value = f"原文資料夾：{self.source_root}"
            self._update_load_state()

    def _on_pick_review(self, e: ft.FilePickerResultEvent):
        """

        回傳：None
        """
        if e.path:
            self.review_root = Path(e.path)
            self.review_label.value = f"校對資料夾：{self.review_root}"
            self._update_load_state()

    def _update_load_state(self):
        """

        回傳：None
        """
        self.load_btn.disabled = not (self.source_root and self.review_root)
        self.update()

    # ==================================================
    # 載入 → 建立模組清單
    # ==================================================
    def _on_load_clicked(self, e):
        """

        回傳：None
        """
        entries = self._load_entries()
        mods = defaultdict(list)

        for entry in entries:
            mods[entry.modid].append(entry)

        self.mods = dict(mods)
        self._render_mod_list()

    # ==================================================
    # 第一層：模組清單
    # ==================================================
    def _render_mod_list(self):
        """

        回傳：None
        """
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
        """

        回傳：None
        """
        self.page_info.value = (
            f"模組清單｜第 {self.mod_current_page + 1} / {self.mod_total_pages} 頁"
        )
        self.prev_page_btn.disabled = self.mod_current_page <= 0
        self.next_page_btn.disabled = self.mod_current_page >= self.mod_total_pages - 1

    def _prev_page(self, e):
        """

        回傳：None
        """
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
        """

        回傳：None
        """
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
        """

        回傳：None
        """
        self.current_modid = modid
        self.current_page = 0  # ⭐ 重設頁碼
        self.back_btn.visible = True
        self.save_btn.visible = True
        self.header.value = f"📦 {modid}"

        # 讀取 zh_tw.json（你原本的邏輯）
        zh_files = list(self.review_root.rglob(f"{modid}/lang/zh_tw.json"))
        self._current_zh_file = zh_files[0] if zh_files else None
        self._zh_data = (
            load_json_auto_encoding(self._current_zh_file)
            if self._current_zh_file and self._current_zh_file.exists()
            else {}
        )

        self._render_current_page()

    def _go_back(self, e):
        """

        回傳：None
        """
        self.current_modid = None
        self.current_page = 0
        self.page_info.value = ""
        self.list_view.controls.clear()
        self._render_mod_list()

    # ==================================================
    # Row → 回報翻譯變更
    # ==================================================
    def _on_value_changed(self, key: str, value: str):
        """

        回傳：None
        """
        self._zh_data[key] = to_halfwidth(value)

    # ==================================================
    # 儲存 zh_tw.json
    # ==================================================
    def _save_current_zh(self, e):
        """

        回傳：None
        """
        if not self._current_zh_file:
            self._show_snack("❌ 找不到 zh_tw.json")
            return

        self._current_zh_file.parent.mkdir(parents=True, exist_ok=True)
        self._current_zh_file.write_text(
            json.dumps(self._zh_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._show_snack("✅ 翻譯已儲存")

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

        # 建立 zh_tw 對照（全域）
        zh_map = {}
        for zh_file in self.review_root.rglob("zh_tw.json"):
            data = load_json_auto_encoding(zh_file)
            if isinstance(data, dict):
                zh_map.update(data)

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
                entries.append(
                    SimpleNamespace(
                        modid=modid,
                        key=key,
                        en=en_text,
                        zh_tw=zh_map.get(key, ""),
                    )
                )

        return entries

    def _render_current_page(self):
        """

        回傳：None
        """
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
                )
            )

        self.page_info.value = (
            f"{self.current_modid}｜第 {self.current_page + 1} / {self.total_pages} 頁"
        )
        self.prev_page_btn.disabled = self.current_page <= 0
        self.next_page_btn.disabled = self.current_page >= self.total_pages - 1

        self.update()
