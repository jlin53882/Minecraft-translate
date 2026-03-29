"""快取管理器模組（PR6 重構）。

將 CacheView 拆分為多個獨立模組，提升可維護性。
"""

# 保留原有的導出
from app.views.cache_manager.cache_controller import CacheController
from app.views.cache_manager.cache_presenter import CachePresenter

# 新增 PR6 的面板導出
from app.views.cache_manager.panels.overview_panel import CacheOverviewPanel
from app.views.cache_manager.panels.query_panel import CacheQueryPanel
from app.views.cache_manager.panels.shard_panel import CacheShardPanel

__all__ = [
    'CacheController',
    'CachePresenter',
    'CacheOverviewPanel',
    'CacheQueryPanel',
    'CacheShardPanel',
]
