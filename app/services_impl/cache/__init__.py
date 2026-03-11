"""app.services_impl.cache

此 package 收納提供給 CacheView 的「cache UI services」（cache_*_service wrappers）。

PR16 範圍：
- 先把 services.py 內的 cache UI service wrapper 原樣搬到 `cache_services.py`。
- 不改 `cache_view.py`、不改 `translation_tool.utils.cache_manager` 行為。
- `app/services.py` 仍會 re-export 同名函式，確保既有 import path 相容。
"""

from __future__ import annotations
