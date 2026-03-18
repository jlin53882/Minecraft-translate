# Cache 分片管理 Modal
# 提供 Key/SRC/DST 三標籤頁面編輯

import flet as ft
from app.views.cache.cache_modal_base import CacheModalBase


class CacheShardModal(CacheModalBase):
    """分片管理 Modal"""

    def __init__(
        self,
        page,
        on_complete=None,
        on_error=None,
        initial_data=None,
        cache_view=None,
    ):
        """初始化分片 Modal

        參數：
            page: Flet Page
            on_complete: 完成回調
            on_error: 錯誤回調
            initial_data: 初始資料
            cache_view: 主 View 引用（用於訪問其分片方法）
        """
        self.initial_data = initial_data
        self.cache_view = cache_view  # 主 View 引用
        self._current_tab = "key"
        # 變更提示
        self._last_shard_type = ""
        super().__init__(
            page=page,
            on_complete=on_complete,
            on_error=on_error,
            width=600,
            height=500,
        )
        self._build_content()

    def _build_content(self):
        """建立分頁 UI"""
        # 分片類型選擇
        self.dd_shard_type = ft.Dropdown(
            width=200,
            label="選擇分類",
            options=[
                ft.dropdown.Option("ALL", "全部"),
            ],
            on_change=self._on_shard_type_change,
        )
        # 提示文字
        self.shard_hint = ft.Text(
            "請選擇分類",
            size=12,
            color=ft.Colors.GREY_700,
        )
        # Tab
        self.tabs = ft.Tabs(
            selected_index=0,
            on_change=self._on_tab_change,
            tabs=[
                ft.Tab(text="Key 列表", content=self._build_key_tab()),
                ft.Tab(text="SRC 編輯", content=self._build_src_tab()),
                ft.Tab(text="DST 編輯", content=self._build_dst_tab()),
            ],
        )
        self.content = ft.Column(
            [
                ft.Text("分片管理", size=20, weight=ft.FontWeight.BOLD),
                ft.Row([self.dd_shard_type, self.shard_hint]),
                self.tabs,
            ]
        )

    def _build_key_tab(self):
        """建立 Key 列表標籤"""
        self.key_list = ft.ListView(expand=True, spacing=5)
        return ft.Container(
            content=ft.Column([
                ft.Text("分片 Key 列表", size=14, weight=ft.FontWeight.BOLD),
                self.key_list,
            ]),
            padding=10,
        )

    def _build_src_tab(self):
        """建立 SRC 編輯標籤"""
        self.src_field = ft.TextField(
            multiline=True,
            min_lines=10,
            label="來源語言",
        )
        return ft.Container(
            content=ft.Column([
                ft.Text("來源語言編輯", size=14, weight=ft.FontWeight.BOLD),
                self.src_field,
            ]),
            padding=10,
        )

    def _build_dst_tab(self):
        """建立 DST 編輯標籤"""
        self.dst_field = ft.TextField(
            multiline=True,
            min_lines=10,
            label="目標語言",
        )
        return ft.Container(
            content=ft.Column([
                ft.Text("目標語言編輯", size=14, weight=ft.FontWeight.BOLD),
                self.dst_field,
            ]),
            padding=10,
        )

    def _on_shard_type_change(self, e):
        """分類選擇變更"""
        shard_type = self.dd_shard_type.value or "ALL"
        if shard_type != self._last_shard_type and self._last_shard_type:
            self.shard_hint.value = "⚠️ 偵測到變更，請重新載入"
            self.shard_hint.color = ft.Colors.ORANGE_700
            self.update()
        # 加載該分類的分片數據
        self._load_shard_data(shard_type)

    def _load_shard_data(self, shard_type):
        """載入分片數據"""
        if not self.cache_view:
            self.shard_hint.value = "無法訪問主視圖"
            self.shard_hint.color = ft.Colors.RED_700
            self.update()
            return
        try:
            # 從主 View 獲取數據
            overview = self.cache_view._last_overview_data
            # 取得 types 結構
            raw_types = overview.get("types", {}) if isinstance(overview, dict) else {}
            
            # 取得所有分類
            type_list = []
            if isinstance(raw_types, dict):
                type_list = list(raw_types.keys())
            elif isinstance(raw_types, list):
                type_list = [t.get("cache_type") or t.get("type") for t in raw_types if isinstance(t, dict)]
            
            # 如果沒有選擇分類，預設選擇第一個
            if shard_type == "ALL":
                shard_type = type_list[0] if type_list else "mods"
            
            # 更新下拉選單
            self.dd_shard_type.options = [
                ft.dropdown.Option(t, t) for t in type_list
            ]
            self.dd_shard_type.value = shard_type
            
            # 取得該分類的數據
            type_data = raw_types.get(shard_type, {}) if isinstance(raw_types, dict) else {}
            
            # 更新 Key 列表
            self.key_list.controls.clear()
            entries = type_data.get("entries_count", 0) if isinstance(type_data, dict) else 0
            self.key_list.controls.append(
                ft.Text(f"{shard_type}: {entries} 筆")
            )
            
            self.shard_hint.value = f"已載入 {shard_type}"
            self.shard_hint.color = ft.Colors.GREEN_700
            self._last_shard_type = shard_type
            self.update()
        except Exception as e:
            self.shard_hint.value = f"載入失敗: {e}"
            self.shard_hint.color = ft.Colors.RED_700
            self.update()

    def _on_tab_change(self, e):
        """切換標籤"""
        self._current_tab = ["key", "src", "dst"][e.control.selected_index]

    def _on_confirm(self):
        """確認回傳當前標籤"""
        data = {
            "tab": self._current_tab,
            "shard_type": self.dd_shard_type.value,
            "src": self.src_field.value if hasattr(self, "src_field") else "",
            "dst": self.dst_field.value if hasattr(self, "dst_field") else "",
        }
        self._do_complete(data)

    def _on_close(self):
        """關閉時清理資源"""
        pass
        """關閉時處理（預留自動儲存）"""
        # TODO: 自動儲存功能（未實作）
        pass
