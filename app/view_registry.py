"""View registry for main.py entrypoint."""

from __future__ import annotations

import flet as ft

from app.ui.view_wrapper import wrap_view

DEFAULT_WINDOW_SIZE = (1280, 960)
VIEW_WINDOW_SIZES = {
    "config": (1280, 960),
    "rules": (1280, 960),
    "cache": (1360, 940),
    "qc": (1280, 960),
    "lookup": (1280, 960),
    "icon_preview": (1280, 960),
    "bundler": (1280, 960),
    "translation": (1280, 960),
    "extractor": (1280, 900),
    "lm": (1280, 920),
    "merge": (1280, 920),
    "arnold": (1000, 950),
}

# Lazy import map - 延遲載入 view 的對應表
# 格式：{'key': (module_name, class_name, needs_file_picker)}
_VIEW_IMPORT_MAP = {
    "config": ("app.views.config_view", "ConfigView", False),
    "rules": ("app.views.rules_view", "RulesView", False),
    "cache": ("app.views.cache_view", "CacheView", False),
    "qc": ("app.views.qc_view", "QCView", True),
    "lookup": ("app.views.lookup_view", "LookupView", False),
    "icon_preview": ("app.views.icon_preview_view", "IconPreviewView", False),
    "bundler": ("app.views.bundler_view", "BundlerView", True),
    "translation": ("app.views.translation_view", "TranslationView", True),
    "extractor": ("app.views.extractor_view", "ExtractorView", True),
    "lm": ("app.views.lm_view", "LMView", True),
    "merge": ("app.views.merge_view", "MergeView", True),
    "pipeline": ("app.views.pipeline.pipeline_view", "PipelineView", True),
}


def _lazy_import_view(view_key: str, page: ft.Page, file_picker: ft.FilePicker):
    """Lazy import view 類別（PR67 優化）。

    Args:
        view_key: View 的 key（如 'config', 'cache'）
        page: Flet Page 物件
        file_picker: Flet FilePicker 物件（部分 view 需要）

    Returns:
        View 實例
    """
    module_name, class_name, needs_file_picker = _VIEW_IMPORT_MAP[view_key]
    module = __import__(module_name, fromlist=[class_name])
    view_class = getattr(module, class_name)
    if needs_file_picker:
        return view_class(page, file_picker)
    return view_class(page)


class LazyViewItem(dict):
    """registry 項目：第一次取用 item["view"] 時才建立頁面。

    原本啟動時一次建立全部 12 個頁面（main() 阻塞約 1.2 秒）；
    改為切換到該頁（或其他程式需要它）時才建立。
    """

    def __init__(self, builder, **fields):
        super().__init__(**fields)
        self._builder = builder
        self._build_hooks: list = []

    @property
    def is_built(self) -> bool:
        return dict.__contains__(self, "view")

    def built_view(self):
        """已建立的頁面；尚未建立時回傳 None（不會觸發建立）。"""
        return dict.get(self, "view")

    def on_build(self, hook) -> None:
        """頁面建立後呼叫 hook(view)；已建立時立即呼叫。"""
        if self.is_built:
            hook(dict.__getitem__(self, "view"))
        else:
            self._build_hooks.append(hook)

    def _ensure_built(self):
        if not self.is_built:
            view = self._builder()
            dict.__setitem__(self, "view", view)
            hooks, self._build_hooks = self._build_hooks, []
            for hook in hooks:
                hook(view)
        return dict.__getitem__(self, "view")

    def __getitem__(self, key):
        if key == "view":
            return self._ensure_built()
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "view":
            return self._ensure_built()
        return super().get(key, default)


def built_view(item):
    """取得 registry 項目「已建立」的頁面；尚未建立時回傳 None。"""
    if isinstance(item, LazyViewItem):
        return item.built_view()
    return item.get("view")


_VIEW_NAV = [
    ("config", ft.Icons.SETTINGS, "設定"),
    ("rules", ft.Icons.RULE, "規則"),
    ("cache", ft.Icons.STORAGE, "快取管理"),
    ("qc", ft.Icons.CHECK_CIRCLE, "QC 檢驗"),
    ("lookup", ft.Icons.SEARCH, "查詢"),
    ("icon_preview", ft.Icons.IMAGE, "JAR 圖示預覽"),
    ("bundler", ft.Icons.FOLDER_ZIP, "打包"),
    ("translation", ft.Icons.TRANSLATE, "任務 翻譯工具"),
    ("extractor", ft.Icons.UNARCHIVE, "jar 提取"),
    ("lm", ft.Icons.AUTO_AWESOME, "機器翻譯"),
    ("merge", ft.Icons.CALL_MERGE, "語系比對合併"),
    ("pipeline", ft.Icons.TERMINAL, "模組流水線翻譯打包"),
]


def build_view_registry(page: ft.Page, file_picker: ft.FilePicker):
    """建立 view 註冊表（頁面在第一次取用時才建立）。

    Args:
        page: Flet Page 物件
        file_picker: Flet FilePicker 物件

    Returns:
        View 註冊表列表（LazyViewItem）
    """

    def _builder(key):
        return lambda: wrap_view(_lazy_import_view(key, page, file_picker))

    return [
        LazyViewItem(_builder(key), key=key, icon=icon, label=label)
        for key, icon, label in _VIEW_NAV
    ]


def get_window_size(view_key: str) -> tuple:
    """取得 view 的視窗大小。

    Args:
        view_key: View 的 key

    Returns:
        (寬, 高) 元組
    """
    return VIEW_WINDOW_SIZES.get(view_key, DEFAULT_WINDOW_SIZE)


def build_navigation_destinations(registry):
    """從 registry 建立導航目的地。

    Args:
        registry: View 註冊表

    Returns:
        NavigationRailDestination 列表
    """
    return [
        ft.NavigationRailDestination(
            icon=item["icon"], selected_icon=item["icon"], label=item["label"]
        )
        for item in registry
    ]
