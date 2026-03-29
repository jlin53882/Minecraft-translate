"""相容性友好的設定存取輔助模組。"""

from __future__ import annotations

from pathlib import Path

from translation_tool.utils.config_manager import load_config, resolve_project_path

def get_runtime_config() -> dict:
    """取得執行期設定。"""
    return load_config()

def resolve_runtime_path(path_like: str | Path | None) -> Path:
    """解析執行期的相對路徑。"""
    return resolve_project_path(path_like)
