"""View registry for main.py entrypoint."""

from __future__ import annotations

import flet as ft

from app.ui.view_wrapper import wrap_view


DEFAULT_WINDOW_SIZE = (1280, 960)
VIEW_WINDOW_SIZES = {
    'config': (1280, 960),
    'rules': (1280, 960),
    'cache': (1360, 940),
    'qc': (1280, 960),
    'translation': (1280, 960),
    'extractor': (1280, 900),
    'lm': (1280, 920),
    'merge': (1280, 920),
}


# Lazy import map - 延遲載入 view 的對應表
# 格式：{'key': (module_name, class_name, needs_file_picker)}
_VIEW_IMPORT_MAP = {
    'config': ('app.views.config_view', 'ConfigView', False),  # 不需要 file_picker
    'rules': ('app.views.rules_view', 'RulesView', False),
    'cache': ('app.views.cache_view', 'CacheView', False),
    'qc': ('app.views.qc_view', 'QCView', True),  # 需要 file_picker
    'translation': ('app.views.translation_view', 'TranslationView', True),  # 需要 file_picker
    'extractor': ('app.views.extractor_view', 'ExtractorView', True),
    'lm': ('app.views.lm_view', 'LMView', True),
    'merge': ('app.views.merge_view', 'MergeView', True),
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


def build_view_registry(page: ft.Page, file_picker: ft.FilePicker):
    """建立 view 註冊表（Lazy import 優化）。

    使用 lazy import 按需載入 view，減少啟動時間。

    Args:
        page: Flet Page 物件
        file_picker: Flet FilePicker 物件

    Returns:
        View 註冊表列表
    """
    # Lazy import all views
    registry = [
        {'key': 'config', 'icon': ft.Icons.SETTINGS, 'label': '設定', 'view': wrap_view(_lazy_import_view('config', page, file_picker))},
        {'key': 'rules', 'icon': ft.Icons.RULE, 'label': '規則', 'view': wrap_view(_lazy_import_view('rules', page, file_picker))},
        {'key': 'cache', 'icon': ft.Icons.STORAGE, 'label': '快取管理', 'view': wrap_view(_lazy_import_view('cache', page, file_picker))},
        {'key': 'qc', 'icon': ft.Icons.CHECK_CIRCLE, 'label': 'QC 檢驗', 'view': wrap_view(_lazy_import_view('qc', page, file_picker))},
        {'key': 'translation', 'icon': ft.Icons.TRANSLATE, 'label': '任務 翻譯工具', 'view': wrap_view(_lazy_import_view('translation', page, file_picker))},
        {'key': 'extractor', 'icon': ft.Icons.UNARCHIVE, 'label': 'jar 提取', 'view': wrap_view(_lazy_import_view('extractor', page, file_picker))},
        {'key': 'lm', 'icon': ft.Icons.AUTO_AWESOME, 'label': '機器翻譯', 'view': wrap_view(_lazy_import_view('lm', page, file_picker))},
        {'key': 'merge', 'icon': ft.Icons.CALL_MERGE, 'label': '檔案合併', 'view': wrap_view(_lazy_import_view('merge', page, file_picker))},
    ]
    return registry


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
    return [ft.NavigationRailDestination(icon=item['icon'], selected_icon=item['icon'], label=item['label']) for item in registry]
