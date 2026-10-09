"""Mod 翻譯資料庫：分版本、可掃描 jar、與翻譯流程整合的 SQLite 翻譯記憶庫。"""

from translation_tool.translation_db.models import (
    AITranslationReplaceResult,
    BatchReplaceChange,
    BatchReplacePlan,
    BatchReplaceResult,
    BatchReplaceSkipped,
    BatchRevertResult,
    EffectiveSourceStat,
    EntryDetail,
    EntryFilter,
    EntryRow,
    Impact,
    IngestStats,
    QualityFilter,
    ReviewPreviewItem,
    SameSourceAIEntry,
    ScanItem,
    TimeFilter,
    VersionStat,
    WriteBackItem,
    WriteBackStats,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.resolver import (
    TranslationResolver,
    WriteBackBuffer,
    split_items_by_db,
)
from translation_tool.translation_db.schema import (
    DEFAULT_PRIORITY,
    KIND_LANG,
    KIND_PATCHOULI,
    SOURCE_NAMES,
)
from translation_tool.translation_db.settings import (
    DbSettings,
    load_db_settings,
    open_db,
)
from translation_tool.translation_db.source_catalog import SourceCatalog

__all__ = [
    "DEFAULT_PRIORITY",
    "KIND_LANG",
    "KIND_PATCHOULI",
    "SOURCE_NAMES",
    "AITranslationReplaceResult",
    "BatchReplaceChange",
    "BatchReplacePlan",
    "BatchReplaceResult",
    "BatchReplaceSkipped",
    "BatchRevertResult",
    "DbSettings",
    "EffectiveSourceStat",
    "EntryDetail",
    "EntryFilter",
    "EntryRow",
    "Impact",
    "IngestStats",
    "QualityFilter",
    "ReviewPreviewItem",
    "SameSourceAIEntry",
    "ScanItem",
    "SourceCatalog",
    "TimeFilter",
    "TranslationDB",
    "TranslationResolver",
    "VersionStat",
    "WriteBackBuffer",
    "WriteBackItem",
    "WriteBackStats",
    "load_db_settings",
    "open_db",
    "split_items_by_db",
]
