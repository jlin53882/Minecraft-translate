"""Compatibility-friendly config access helpers."""

from __future__ import annotations

from pathlib import Path

from translation_tool.utils.config_manager import load_config, resolve_project_path


def get_runtime_config() -> dict:
    """Explicit runtime config accessor for non-legacy callers."""
    return load_config()


def resolve_runtime_path(path_like: str | Path | None) -> Path:
    """Explicit project-relative path resolver for runtime helpers."""
    return resolve_project_path(path_like)
