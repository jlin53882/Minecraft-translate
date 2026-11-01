"""模組包遊戲版本的 UI 入口：偵測與版本選項都經由這裡取用，View 不直接 import 核心層。"""

from __future__ import annotations

from translation_tool.core.pack_version_detect import (
    detect_minecraft_version,
    known_minecraft_versions,
)

__all__ = ["detect_minecraft_version", "known_minecraft_versions"]
