"""View registry for main.py entrypoint."""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from app.ui.view_wrapper import wrap_view

# 視窗尺寸：整個 App 共用一個尺寸，切頁時不再改變視窗（側欄 + 頂列的外殼需要穩定的版面）
DEFAULT_WINDOW_SIZE = (1360, 900)
MIN_WINDOW_SIZE = (1100, 720)


@dataclass(frozen=True)
class NavGroup:
    """側欄的分組（工作流程 / 品管與校對 / 資料庫 / 輸出）。"""

    key: str
    label: str
    tone: str  # 對應 design.tone() 的強調色組


@dataclass(frozen=True)
class ViewSpec:
    """一個頁面的全部靜態資訊（側欄、麵包屑、快速跳轉、快捷鍵、延遲載入都從這裡取）。"""

    key: str
    label: str
    icon: str
    group: str  # NavGroup.key；"system" 表示固定在側欄底部
    module: str
    cls: str
    needs_file_picker: bool = False
    shortcut: str | None = None  # Ctrl+<數字>
    keywords: tuple[str, ...] = ()  # 快速跳轉的額外搜尋字


# 側欄由上到下的分組順序（依「工作流程」排序）
NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup("flow", "工作流程", "em"),
    NavGroup("qc", "品管與校對", "ench"),
    NavGroup("data", "資料庫", "dia"),
    NavGroup("out", "輸出", "gold"),
)
SYSTEM_GROUP = NavGroup("system", "系統", "neutral")

# 單一資料來源：新增頁面只需要在這裡加一筆
VIEW_SPECS: tuple[ViewSpec, ...] = (
    ViewSpec(
        "dashboard",
        "工作台",
        ft.Icons.GRID_VIEW_OUTLINED,
        "flow",
        "app.views.dashboard_view",
        "DashboardView",
        False,
        "1",
        ("dashboard", "home", "首頁", "總覽", "狀態"),
    ),
    ViewSpec(
        "pipeline",
        "一鍵流水線",
        ft.Icons.ACCOUNT_TREE_OUTLINED,
        "flow",
        "app.views.pipeline.pipeline_view",
        "PipelineView",
        True,
        "2",
        ("pipeline", "模組流水線", "提取", "翻譯", "打包"),
    ),
    ViewSpec(
        "extractor",
        "JAR 提取",
        ft.Icons.INVENTORY_2_OUTLINED,
        "flow",
        "app.views.extractor_view",
        "ExtractorView",
        True,
        "3",
        ("jar", "extract", "提取"),
    ),
    ViewSpec(
        "merge",
        "語系合併",
        ft.Icons.CALL_MERGE,
        "flow",
        "app.views.merge_view",
        "MergeView",
        True,
        "4",
        ("merge", "比對", "lang"),
    ),
    ViewSpec(
        "lm",
        "機器翻譯",
        ft.Icons.AUTO_AWESOME_OUTLINED,
        "flow",
        "app.views.lm_view",
        "LMView",
        True,
        "5",
        ("gemini", "lm", "ai", "api"),
    ),
    ViewSpec(
        "translation",
        "任務翻譯",
        ft.Icons.TRANSLATE,
        "flow",
        "app.views.translation_view",
        "TranslationView",
        True,
        "6",
        ("task", "翻譯工具"),
    ),
    ViewSpec(
        "qc",
        "QC 檢驗",
        ft.Icons.VERIFIED_USER_OUTLINED,
        "qc",
        "app.views.qc_view",
        "QCView",
        True,
        "7",
        ("qc", "品管", "檢查"),
    ),
    ViewSpec(
        "icon_preview",
        "翻譯校對",
        ft.Icons.SPELLCHECK,
        "qc",
        "app.views.icon_preview_view",
        "IconPreviewView",
        False,
        "8",
        ("review", "圖示", "icon", "校對"),
    ),
    ViewSpec(
        "moddb",
        "Mod 資料庫",
        ft.Icons.DATASET_OUTLINED,
        "data",
        "app.views.moddb_view",
        "ModDbView",
        True,
        None,
        ("moddb", "資料庫", "翻譯記憶", "版本", "jar", "掃描"),
    ),
    ViewSpec(
        "cache",
        "快取管理",
        ft.Icons.STORAGE_OUTLINED,
        "data",
        "app.views.cache_view",
        "CacheView",
        False,
        "9",
        ("cache", "索引"),
    ),
    ViewSpec(
        "rules",
        "替換規則",
        ft.Icons.FIND_REPLACE,
        "data",
        "app.views.rules_view",
        "RulesView",
        False,
        "0",
        ("rules", "規則", "取代"),
    ),
    ViewSpec(
        "lookup",
        "學名查詢",
        ft.Icons.SCIENCE_OUTLINED,
        "data",
        "app.views.lookup_view",
        "LookupView",
        False,
        None,
        ("lookup", "查詢", "字典"),
    ),
    ViewSpec(
        "bundler",
        "資源包打包",
        ft.Icons.FOLDER_ZIP_OUTLINED,
        "out",
        "app.views.bundler_view",
        "BundlerView",
        True,
        None,
        ("bundle", "zip", "打包"),
    ),
    ViewSpec(
        "config",
        "設定",
        ft.Icons.SETTINGS_OUTLINED,
        "system",
        "app.views.config_view",
        "ConfigView",
        False,
        None,
        ("config", "api key", "設定"),
    ),
)

SPECS_BY_KEY: dict[str, ViewSpec] = {spec.key: spec for spec in VIEW_SPECS}
DEFAULT_VIEW_KEY = "dashboard"


def get_spec(view_key: str) -> ViewSpec:
    """依 key 取得 ViewSpec；未知的 key 丟 KeyError。"""
    return SPECS_BY_KEY[view_key]


def get_group(group_key: str) -> NavGroup:
    for group in (*NAV_GROUPS, SYSTEM_GROUP):
        if group.key == group_key:
            return group
    raise KeyError(group_key)


def specs_in_group(group_key: str) -> list[ViewSpec]:
    return [spec for spec in VIEW_SPECS if spec.group == group_key]


def _lazy_import_view(view_key: str, page: ft.Page, file_picker: ft.FilePicker):
    """Lazy import view 類別（PR67 優化）。

    Args:
        view_key: View 的 key（如 'config', 'cache'）
        page: Flet Page 物件
        file_picker: Flet FilePicker 物件（部分 view 需要）

    Returns:
        View 實例
    """
    spec = get_spec(view_key)
    module = __import__(spec.module, fromlist=[spec.cls])
    view_class = getattr(module, spec.cls)
    if spec.needs_file_picker:
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


def build_view_registry(page: ft.Page, file_picker: ft.FilePicker):
    """建立 view 註冊表（頁面在第一次取用時才建立）。順序即側欄順序。

    Args:
        page: Flet Page 物件
        file_picker: Flet FilePicker 物件

    Returns:
        View 註冊表列表（LazyViewItem）
    """

    def _builder(key):
        return lambda: wrap_view(_lazy_import_view(key, page, file_picker))

    return [
        LazyViewItem(
            _builder(spec.key),
            key=spec.key,
            icon=spec.icon,
            label=spec.label,
            group=spec.group,
        )
        for spec in VIEW_SPECS
    ]


def index_of(registry, view_key: str) -> int:
    """view key 在 registry 的位置；找不到回傳 -1。"""
    for index, item in enumerate(registry):
        if item.get("key") == view_key:
            return index
    return -1


def get_window_size(_view_key: str | None = None) -> tuple:
    """視窗大小（整個 App 共用；保留參數是為了相容舊呼叫）。"""
    return DEFAULT_WINDOW_SIZE
