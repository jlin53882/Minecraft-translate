"""Compatibility aliases for legacy cache imports.

Keep historical import paths alive while cache-related modules remain
physically grouped under ``app.views.cache_manger``.
"""

from __future__ import annotations

import sys

from .cache_manger import cache_controller as _cache_controller
from .cache_manger import cache_presenter as _cache_presenter
from .cache_manger import cache_types as _cache_types

# Legacy module aliases used by tests / older callers.
sys.modules[__name__ + ".cache_controller"] = _cache_controller
sys.modules[__name__ + ".cache_presenter"] = _cache_presenter
sys.modules[__name__ + ".cache_types"] = _cache_types
