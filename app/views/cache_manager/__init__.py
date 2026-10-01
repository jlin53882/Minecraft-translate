"""快取管理器模組。

將 CacheView 拆分為 controller / presenter，提升可維護性。
"""

from app.views.cache_manager.cache_controller import CacheController
from app.views.cache_manager.cache_presenter import CachePresenter

__all__ = [
    "CacheController",
    "CachePresenter",
]
