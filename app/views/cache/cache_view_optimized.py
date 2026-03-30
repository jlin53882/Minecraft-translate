# Cache View 優化版本
# 提供髒標記 + 批次更新機制

import threading

import flet as ft


class CacheViewOptimized(ft.Column):
    """優化的 Cache View，支援區域性更新"""

    def __init__(self, page=None):
        self._page_ref = page
        self._dirty_flags = {
            "query": False,
            "shard": False,
            "overview": False,
        }
        self._update_timer = None
        super().__init__()

    def mark_dirty(self, area: str):
        """標記某區域需要更新"""
        if self._page_ref is None:
            return
        if area not in self._dirty_flags:
            return
        self._dirty_flags[area] = True
        self._schedule_update()

    def _schedule_update(self):
        """Debounce 更新（100ms）"""
        if self._update_timer:
            self._update_timer.cancel()
        self._update_timer = threading.Timer(0.1, self._do_update)
        self._update_timer.start()

    def _do_update(self):
        """批次更新所有髒區域"""
        if not any(self._dirty_flags.values()):
            return
        try:
            self._render_dirty_areas()
            self.update()
            for k in self._dirty_flags:
                self._dirty_flags[k] = False
        except (AttributeError, AssertionError):
            # 控件尚未添加到 page，略過
            pass
        except Exception as e:
            print(f"[CacheViewOptimized] 更新失敗: {e}")

    def _render_dirty_areas(self):
        """渲染所有髒區域（可覆寫）"""
        if self._dirty_flags.get("overview"):
            self._render_overview()
        if self._dirty_flags.get("query"):
            self._render_query_results()
        if self._dirty_flags.get("shard"):
            self._render_shard_results()

    def _render_overview(self):
        """渲染總覽區域"""
        pass

    def _render_query_results(self):
        """渲染搜尋結果"""
        pass

    def _render_shard_results(self):
        """渲染分片結果"""
        pass
