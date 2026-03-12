"""View registry for main.py entrypoint."""

from __future__ import annotations

import flet as ft

from app.ui.view_wrapper import wrap_view
from app.views.cache_view import CacheView
from app.views.config_view import ConfigView
from app.views.extractor_view import ExtractorView
from app.views.lm_view import LMView
from app.views.merge_view import MergeView
from app.views.rules_view import RulesView
from app.views.translation_view import TranslationView


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


def build_view_registry(page: ft.Page, file_picker: ft.FilePicker):
    registry = [
        {'key': 'config', 'icon': ft.Icons.SETTINGS, 'label': '設定', 'view': wrap_view(ConfigView(page))},
        {'key': 'rules', 'icon': ft.Icons.RULE, 'label': '規則', 'view': wrap_view(RulesView(page))},
        {'key': 'cache', 'icon': ft.Icons.STORAGE, 'label': '快取管理', 'view': wrap_view(CacheView(page))},
        {'key': 'translation', 'icon': ft.Icons.TRANSLATE, 'label': '任務 翻譯工具', 'view': wrap_view(TranslationView(page, file_picker))},
        {'key': 'extractor', 'icon': ft.Icons.UNARCHIVE, 'label': 'jar 提取', 'view': wrap_view(ExtractorView(page, file_picker))},
        {'key': 'lm', 'icon': ft.Icons.AUTO_AWESOME, 'label': '機器翻譯', 'view': wrap_view(LMView(page, file_picker))},
        {'key': 'merge', 'icon': ft.Icons.CALL_MERGE, 'label': '檔案合併', 'view': wrap_view(MergeView(page, file_picker))},
    ]
    return registry


def get_window_size(view_key: str):
    return VIEW_WINDOW_SIZES.get(view_key, DEFAULT_WINDOW_SIZE)


def build_navigation_destinations(registry):
    return [ft.NavigationRailDestination(icon=item['icon'], selected_icon=item['icon'], label=item['label']) for item in registry]
