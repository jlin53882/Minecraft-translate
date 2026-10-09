"""Source identity helpers shared by Mod DB services and views."""

from __future__ import annotations

from app.services_impl.moddb_service import SRC_AI, SRC_JAR_CN, SRC_MANUAL
from translation_tool.translation_db.schema import CUSTOM_SOURCE_BASE, SRC_CUSTOM
from translation_tool.translation_db.settings import (
    DbSettings,
    clean_source_names,
    normalize_db_path,
    read_custom_sources,
    serialize_priority_lines,
)
from translation_tool.translation_db.source_catalog import (
    DEFAULT_SOURCE_CATALOG,
    SourceCatalog,
)

MANUAL_REVIEW_LABELS = {
    "unreviewed": "人工-未審核",
    "reviewed": "人工-已審核",
    "legacy_unknown": "人工（歷史狀態待確認）",
}


def normalize_priority_config(config: dict) -> None:
    """Persist registered priorities as database-scoped collision-proof tokens."""
    db_config = config.setdefault("translation_db", {})
    path = DbSettings(path=normalize_db_path(db_config.get("path"))).resolved_path()
    catalog = SourceCatalog.from_registry(read_custom_sources(path))
    db_config["priority"] = serialize_priority_lines(db_config.get("priority"), catalog)


def priority_display_lines(names, database_path: str | None = None) -> list[str]:
    """Render stored source identity tokens as readable labels for the settings UI."""
    path = DbSettings(path=normalize_db_path(database_path)).resolved_path()
    catalog = SourceCatalog.from_registry(read_custom_sources(path))
    display_lines = []
    for name in clean_source_names(names):
        code = catalog.resolve(name)
        display_lines.append(catalog.label(code) if code is not None else name)
    return display_lines


def source_catalog_for(db) -> SourceCatalog:
    """Return the database-scoped source catalog, or built-ins without a DB."""
    return db.source_catalog if db is not None else DEFAULT_SOURCE_CATALOG


def source_label(
    source: int | None,
    catalog: SourceCatalog | None = None,
    review_status: str | None = None,
) -> str:
    catalog = catalog or DEFAULT_SOURCE_CATALOG
    if source == SRC_MANUAL and review_status:
        return MANUAL_REVIEW_LABELS.get(review_status, catalog.label(source))
    return catalog.label(source)


def source_tone(source: int | None) -> str:
    """Give custom source identities a repeatable palette for overview charts."""
    if source == SRC_MANUAL:
        return "ench"
    if source == SRC_JAR_CN:
        return "gold"
    if source == SRC_AI or source is None:
        return "neutral"
    if source >= CUSTOM_SOURCE_BASE:
        palette = ("dia", "red", "em", "gold", "ench", "neutral")
        return palette[(int(source) - CUSTOM_SOURCE_BASE) % len(palette)]
    if source == SRC_CUSTOM:
        return "red"
    return "dia"
