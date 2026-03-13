"""View registry for main.py entrypoint."""

from __future__ import annotations

import flet as ft

from app.ui.view_wrapper import wrap_view


DEFAULT_WINDOW_SIZE = (1280, 960)
VIEW_WINDOW_SIZES = {
    'config': (1280, 960),
    'rules': (1280, 960),
    'cache': (1360, 940),
    'translation': (1280, 960),
    'extractor': (1280, 900),
    'lm': (1280, 920),
    'merge': (1280, 920),
}


# Lazy import map - only import when needed
_VIEW_IMPORT_MAP = {
    'config': ('app.views.config_view', 'ConfigView'),
    'rules': ('app.views.rules_view', 'RulesView'),
    'cache': ('app.views.cache_view', 'CacheView'),
    'translation': ('app.views.translation_view', 'TranslationView'),
    'extractor': ('app.views.extractor_view', 'ExtractorView'),
    'lm': ('app.views.lm_view', 'LMView'),
    'merge': ('app.views.merge_view', 'MergeView'),
}


def _lazy_import_view(view_key: str, page: ft.Page, file_picker: ft.FilePicker):
    """Lazy import view class."""
    module_name, class_name = _VIEW_IMPORT_MAP[view_key]
    module = __import__(module_name, fromlist=[class_name])
    view_class = getattr(module, class_name)
    return view_class(page, file_picker) if file_picker else view_class(page)


def build_view_registry(page: ft.Page, file_picker: ft.FilePicker):
    # Lazy import all views
    registry = [
        {'key': 'config', 'icon': ft.Icons.SETTINGS, 'label': '設定', 'view': wrap_view(_lazy_import_view('config', page, file_picker))},
        {'key': 'rules', 'icon': ft.Icons.RULE, 'label': '規則', 'view': wrap_view(_lazy_import_view('rules', page, file_picker))},
        {'key': 'cache', 'icon': ft.Icons.STORAGE, 'label': '快取管理', 'view': wrap_view(_lazy_import_view('cache', page, file_picker))},
        {'key': 'translation', 'icon': ft.Icons.TRANSLATE, 'label': '任務 翻譯工具', 'view': wrap_view(_lazy_import_view('translation', page, file_picker))},
        {'key': 'extractor', 'icon': ft.Icons.UNARCHIVE, 'label': 'jar 提取', 'view': wrap_view(_lazy_import_view('extractor', page, file_picker))},
        {'key': 'lm', 'icon': ft.Icons.AUTO_AWESOME, 'label': '機器翻譯', 'view': wrap_view(_lazy_import_view('lm', page, file_picker))},
        {'key': 'merge', 'icon': ft.Icons.CALL_MERGE, 'label': '檔案合併', 'view': wrap_view(_lazy_import_view('merge', page, file_picker))},
    ]
    return registry


def get_window_size(view_key: str):
    return VIEW_WINDOW_SIZES.get(view_key, DEFAULT_WINDOW_SIZE)


def build_navigation_destinations(registry):
    return [ft.NavigationRailDestination(icon=item['icon'], selected_icon=item['icon'], label=item['label']) for item in registry]
