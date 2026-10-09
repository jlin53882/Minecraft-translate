"""Mod 翻譯資料庫：分版本、可掃描 jar、與翻譯流程整合的 SQLite 翻譯記憶庫。"""

from translation_tool.translation_db.models import (
    AITranslationReplaceResult,
    EntryDetail,
    EntryRow,
    Impact,
    IngestStats,
    SameSourceAIEntry,
    ScanItem,
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

__all__ = [
    "DEFAULT_PRIORITY",
    "KIND_LANG",
    "KIND_PATCHOULI",
    "SOURCE_NAMES",
    "AITranslationReplaceResult",
    "DbSettings",
    "EntryDetail",
    "EntryRow",
    "Impact",
    "IngestStats",
    "SameSourceAIEntry",
    "ScanItem",
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
