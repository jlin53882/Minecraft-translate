"""models.py

資料庫對外回傳的資料結構（上層 UI / 翻譯流程不直接碰 SQL 或資料表欄位）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EntryRow:
    """條目清單的一列。"""

    id: int
    kind: str
    mc_version: str
    mod_id: str
    key: str
    en_us: str
    zh_tw: str  # 生效譯文；沒有則為空字串
    source: int | None  # 生效譯文的來源；沒有則 None
    checker: str
    diff: bool  # 其他版本相同內容的生效譯文與此不同

    @property
    def state(self) -> str:
        """none / diff / manual / ok（與條目清單的色點對應）。"""
        if not self.zh_tw:
            return "none"
        if self.diff:
            return "diff"
        from translation_tool.translation_db.schema import SRC_MANUAL

        return "manual" if self.source == SRC_MANUAL else "ok"


@dataclass(frozen=True)
class SameSourceAIEntry:
    """目前生效來源為 AI，且譯文與非空原文完全相同的條目。"""

    entry_id: int
    kind: str
    mod_id: str
    key: str
    en_us: str
    current_ai_translation: str
    mc_version: str


@dataclass(frozen=True)
class AITranslationReplaceResult:
    """專用 AI 譯文 compare-and-set 的結果。"""

    status: str  # updated / unchanged / skipped_changed


@dataclass(frozen=True)
class TranslationRow:
    source: int
    zh_tw: str
    zh_cn: str
    checker: str
    updated_at: str


@dataclass(frozen=True)
class SameKeyRow:
    """同一個 (類型, 模組, 鍵值) 在其他版本的資料。"""

    entry_id: int
    mc_version: str
    en_us: str
    same_text: bool  # 原文與目前條目相同（手動儲存時會被一併取代）
    zh_tw: str
    source: int | None


@dataclass(frozen=True)
class SameTextRow:
    """原文相同、但鍵值或模組不同的條目。"""

    entry_id: int
    mc_version: str
    mod_id: str
    key: str
    zh_tw: str
    source: int | None


@dataclass(frozen=True)
class HistoryRow:
    id: int
    batch: str
    at: str
    actor: str
    action: str
    old_zh_tw: str
    new_zh_tw: str
    note: str


@dataclass
class EntryDetail:
    entry: EntryRow
    translations: list[TranslationRow] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    same_key: list[SameKeyRow] = field(default_factory=list)
    same_text: list[SameTextRow] = field(default_factory=list)
    history: list[HistoryRow] = field(default_factory=list)
    src_changes: list[SrcChangeRow] = field(default_factory=list)


@dataclass(frozen=True)
class SrcChangeRow:
    """掃描時發現、尚未採用的原文變動（資料庫仍保留舊原文）。"""

    old_en: str
    new_en: str
    detected_at: str


@dataclass(frozen=True)
class Impact:
    """手動儲存會影響的條目。"""

    entry_id: int
    mc_version: str
    old_zh_tw: str
    old_source: int | None
    is_self: bool


@dataclass(frozen=True)
class ScanItem:
    """掃描 jar 得到的一個項目（原文 + jar 自帶的繁／簡中譯文）。"""

    kind: str
    mod_id: str
    key: str
    en_us: str  # 空字串＝原文未知（例如只匯入了 zh_tw 的翻譯 ZIP），之後掃描 jar 會補上
    zh_tw: str = ""
    zh_cn: str = ""
    source: int | None = None  # zh_tw 的來源代碼；None＝模組自帶繁中


@dataclass(frozen=True)
class WriteBackItem:
    """翻譯流程要寫回資料庫的譯文。"""

    kind: str
    mod_id: str
    key: str
    en_us: str
    zh_tw: str


@dataclass
class IngestStats:
    new_entries: int = 0
    existing: int = 0  # 條目已存在、原文相同、沒有新增任何譯文
    added_translations: int = 0  # 為既有條目補入新來源譯文
    en_changed: int = 0  # 原文已變動（略過）
    adopted: int = 0  # 原文原本未知，這次補上

    def add(self, other: IngestStats) -> None:
        self.new_entries += other.new_entries
        self.existing += other.existing
        self.added_translations += other.added_translations
        self.en_changed += other.en_changed
        self.adopted += other.adopted


@dataclass
class WriteBackStats:
    written: int = 0  # 目標版本新增的 AI 譯文
    filled_other: int = 0  # 其他版本相同內容的空白被補上
    skipped: int = 0  # 已有 AI 來源譯文或原文不符而略過


@dataclass(frozen=True)
class VersionStat:
    mc_version: str
    total: int
    manual: int
    jar: int  # 自帶繁中 / 町宮 / i18n / 自訂
    converted: int  # 簡中轉繁
    ai: int
    untranslated: int
