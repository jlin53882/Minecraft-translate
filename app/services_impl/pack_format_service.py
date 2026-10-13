"""資源包 ``pack.mcmeta`` 規則的 UI 入口：View 一律從這裡取用，不直接 import 核心層。"""

from __future__ import annotations

from translation_tool.core.pack_format_lookup import (
    QUICK_VERSIONS,
    explain_version_input,
    resolve_max_input,
    resolve_version_input,
)
from translation_tool.core.pack_mcmeta import DEFAULT_PACK_FORMAT, build_pack_mcmeta

__all__ = [
    "DEFAULT_PACK_FORMAT",
    "QUICK_VERSIONS",
    "build_pack_mcmeta",
    "explain_version_input",
    "resolve_max_input",
    "resolve_version_input",
]
