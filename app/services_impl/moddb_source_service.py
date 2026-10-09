"""Source identity helpers shared by Mod DB services and views."""

from __future__ import annotations

from app.services_impl.moddb_service import SRC_AI, SRC_JAR_CN, SRC_MANUAL
from translation_tool.translation_db.source_catalog import (
    DEFAULT_SOURCE_CATALOG,
    SourceCatalog,
)

MANUAL_REVIEW_LABELS = {
    "unreviewed": "人工-未審核",
    "reviewed": "人工-已審核",
    "legacy_unknown": "人工（歷史狀態待確認）",
}


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
    """譯文來源的色調：人工紫、簡中轉繁金、AI 中性、其餘藍。"""
    if source == SRC_MANUAL:
        return "ench"
    if source == SRC_JAR_CN:
        return "gold"
    if source == SRC_AI or source is None:
        return "neutral"
    return "dia"
