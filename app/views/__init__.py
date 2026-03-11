"""app.views 相容層（舊 import path alias）。

目的：維持歷史 import 路徑可用，同時把 cache 相關模組集中到
`app/views/cache_manager/` 下面，避免 app/views 根目錄越長越亂。

維護注意：
- 這裡用 sys.modules 做 alias。
- 僅提供相容性，不應再新增新的 feature 到這個 alias 層。
"""

from __future__ import annotations

import sys

from .cache_manager import cache_controller as _cache_controller
from .cache_manager import cache_presenter as _cache_presenter
from .cache_manager import cache_types as _cache_types

# Legacy module aliases used by tests / older callers.
sys.modules[__name__ + ".cache_controller"] = _cache_controller
sys.modules[__name__ + ".cache_presenter"] = _cache_presenter
sys.modules[__name__ + ".cache_types"] = _cache_types
