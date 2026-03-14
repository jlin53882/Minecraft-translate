"""translation_tool/utils/cache_search_facade.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .cache_search import SearchOrchestrator

class CacheSearchFacade:
    """CacheSearchFacade 類別。

    用途：封裝與 CacheSearchFacade 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, cache_root_getter: Callable[[], Path], logger: logging.Logger):
        """初始化 CacheSearchFacade。

        參數：
            cache_root_getter: 取得快取根目錄的回調函數
            logger: 日誌記錄器
        """
        self._cache_root_getter = cache_root_getter
        self._logger = logger
        self._orchestrator: Optional[SearchOrchestrator] = None
        self._lock = threading.Lock()

    def _get_orchestrator(self) -> SearchOrchestrator:
        """取得搜尋協調器（延遲初始化）。"""
        if self._orchestrator is None:
            with self._lock:
                if self._orchestrator is None:
                    self._orchestrator = SearchOrchestrator(self._cache_root_getter)
        return self._orchestrator

    def get_search_engine(self):
        """Get the search engine instance, initializing if needed."""
        try:
            return self._get_orchestrator().get_engine()
        except Exception as e:
            self._logger.error(f"Failed to initialize search engine: {e}", exc_info=True)
            return None

    def rebuild_search_index(
        self, cache_types: list[str], translation_cache: dict[str, dict[str, Any]]
    ) -> None:
        """重建搜尋索引。"""
        try:
            self._logger.info("🔄 開始重建搜尋索引...")
            total_indexed = self._get_orchestrator().rebuild_search_index(
                cache_types, translation_cache
            )
            self._logger.info(f"✅ 搜尋索引重建完成，共索引 {total_indexed} 條翻譯")
        except Exception as e:
            self._logger.error(f"❌ 重建搜尋索引失敗: {e}", exc_info=True)

    def rebuild_search_index_for_type(
        self,
        cache_type: str,
        cache_types: list[str],
        translation_cache: dict[str, dict[str, Any]],
    ) -> None:
        """重建特定類型的搜尋索引。"""
        if cache_type not in cache_types:
            return
        try:
            indexed = self._get_orchestrator().rebuild_search_index_for_type(
                cache_type, translation_cache
            )
            self._logger.info(f"✅ {cache_type} 索引重建完成（{indexed} 條）")
        except Exception as e:
            self._logger.error(f"❌ {cache_type} 索引重建失敗: {e}", exc_info=True)

    def search_cache(
        self,
        *,
        query: str,
        cache_type: str | None = None,
        limit: int = 50,
        use_fuzzy: bool = True,
    ) -> list:
        """Search the cache for translations matching the query."""
        try:
            return self._get_orchestrator().search_cache(
                query=query,
                cache_type=cache_type,
                limit=limit,
                use_fuzzy=use_fuzzy,
            )
        except Exception as e:
            self._logger.error(f"Search failed: {e}", exc_info=True)
            return []

    def find_similar_translations(
        self,
        *,
        text: str,
        cache_type: str | None = None,
        threshold: float = 0.6,
        limit: int = 20,
    ) -> list:
        """

    
        """
        try:
            return self._get_orchestrator().find_similar_translations(
                text=text,
                cache_type=cache_type,
                threshold=threshold,
                limit=limit,
            )
        except Exception as e:
            self._logger.error(f"相似翻譯搜尋失敗: {e}", exc_info=True)
            return []
