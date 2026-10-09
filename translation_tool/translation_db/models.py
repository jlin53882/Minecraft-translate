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
    review_status: str | None = None
    created_at: str | None = None
    translation_created_at: str | None = None
    effective_updated_at: str | None = None
    last_manual_at: str | None = None
    quality_state: str = "unknown_source"
    quality_issues: tuple[str, ...] = ()
    whitespace_note: str = ""

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
    review_status: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class SameKeyRow:
    """同一個 (類型, 模組, 鍵值) 在其他版本的資料。"""

    entry_id: int
    mc_version: str
    en_us: str
    same_text: bool  # 原文與目前條目相同（手動儲存時會被一併取代）
    zh_tw: str
    source: int | None
    review_status: str | None = None


@dataclass(frozen=True)
class SameTextRow:
    """原文相同、但鍵值或模組不同的條目。"""

    entry_id: int
    mc_version: str
    mod_id: str
    key: str
    zh_tw: str
    source: int | None
    review_status: str | None = None


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
    prev_manual: str | None = None
    prev_checker: str | None = None
    prev_review_status: str | None = None
    new_checker: str | None = None
    new_review_status: str | None = None
    prev_revision: int | None = None
    new_revision: int | None = None


@dataclass(frozen=True)
class QualityFilter:
    """Filter the current effective translation using the shared token analyzer."""

    status: str = "all"
    direction: str = "all"
    token_category: str = "all"

    def __post_init__(self) -> None:
        if self.status not in {
            "all",
            "mismatch",
            "consistent",
            "unknown_source",
            "missing_translation",
            "whitespace",
        }:
            raise ValueError(f"未知翻譯品質狀態：{self.status}")
        if self.direction not in {"all", "missing", "extra"}:
            raise ValueError(f"未知 token 不一致方向：{self.direction}")
        if self.token_category not in {
            "all",
            "placeholder",
            "minecraft",
            "patchouli",
            "newline",
        }:
            raise ValueError(f"未知 token 類別：{self.token_category}")

    @property
    def active(self) -> bool:
        return (
            self.status != "all"
            or self.direction != "all"
            or self.token_category != "all"
        )


@dataclass(frozen=True)
class TimeFilter:
    """UTC half-open time range over one explicitly selected timestamp meaning."""

    kind: str
    start_utc: str | None = None
    end_utc: str | None = None
    unknown_policy: str = "include"
    action: str = "all"

    def __post_init__(self) -> None:
        if self.kind not in {
            "entry_created",
            "translation_created",
            "effective_updated",
            "manual_activity",
        }:
            raise ValueError(f"未知時間類型：{self.kind}")
        if self.unknown_policy not in {"include", "exclude", "only"}:
            raise ValueError(f"未知時間未知值策略：{self.unknown_policy}")
        if self.action not in {"all", "edit", "review", "batch", "revert"}:
            raise ValueError(f"未知歷史操作類型：{self.action}")
        if self.start_utc and self.end_utc and self.start_utc >= self.end_utc:
            raise ValueError("時間區間無效：起始時間必須早於結束時間")


@dataclass(frozen=True)
class EntryFilter:
    """Immutable resolved query shared by entries, count, paging and batch preview."""

    version: str
    mod_id: str | None = None
    kind: str | None = None
    state: str = "all"
    query: str = ""
    source: int | None = None
    review_status: str | None = None
    entry_ids: tuple[int, ...] | None = None
    time: TimeFilter | None = None
    quality: QualityFilter = field(default_factory=QualityFilter)
    sort_by: str = "default"
    include_ids: tuple[int, ...] | None = None
    exclude_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"all", "none", "diff", "changed", "manual", "ok", "same"}:
            raise ValueError(f"未知條目狀態：{self.state}")
        if self.review_status not in (None, "unreviewed", "reviewed", "legacy_unknown"):
            raise ValueError(f"未知人工審核狀態：{self.review_status}")
        if self.sort_by not in {
            "default",
            "entry_newest",
            "entry_oldest",
            "effective_updated_newest",
            "manual_activity_newest",
        }:
            raise ValueError(f"未知條目排序：{self.sort_by}")
        for name in ("entry_ids", "include_ids", "exclude_ids"):
            values = getattr(self, name)
            if values is not None:
                object.__setattr__(
                    self, name, tuple(dict.fromkeys(int(x) for x in values))
                )


@dataclass(frozen=True)
class ReviewPreviewItem:
    entry_id: int
    mc_version: str
    text: str
    source: int | None
    review_status: str | None
    checker: str | None
    effective_revision: int | None
    manual_text: str | None
    manual_checker: str | None
    manual_review_status: str | None
    manual_revision: int | None
    included: bool
    reason: str


@dataclass(frozen=True)
class BatchReplaceChange:
    entry_id: int
    kind: str
    mc_version: str
    mod_id: str
    key: str
    en_us: str
    old_zh_tw: str
    new_zh_tw: str
    effective_source: int
    effective_checker: str
    effective_review_status: str | None
    effective_revision: int | None
    effective_updated_at: str | None
    manual_zh_tw: str | None
    manual_checker: str | None
    manual_review_status: str | None
    manual_revision: int | None
    is_extra_version: bool = False
    root_entry_id: int | None = None
    old_quality_issues: tuple[str, ...] = ()
    new_quality_issues: tuple[str, ...] = ()
    old_whitespace_note: str = ""
    new_whitespace_note: str = ""
    quality_deltas: tuple[QualityIssueDelta, ...] = ()

    @property
    def quality_worsened(self) -> bool:
        return any(delta.after > delta.before for delta in self.quality_deltas) or bool(
            self.new_whitespace_note and not self.old_whitespace_note
        )

    @property
    def quality_improved(self) -> bool:
        return any(delta.after < delta.before for delta in self.quality_deltas) or bool(
            self.old_whitespace_note and not self.new_whitespace_note
        )

    @property
    def quality_mixed(self) -> bool:
        return self.quality_worsened and self.quality_improved

    @property
    def quality_change_kind(self) -> str:
        if self.quality_mixed:
            return "mixed"
        if self.quality_worsened:
            return "worsened"
        if self.quality_improved:
            return "improved"
        return "unchanged"


@dataclass(frozen=True)
class QualityIssueDelta:
    """Count change for one missing/extra source token in a replacement."""

    token: str
    direction: str
    before: int
    after: int


@dataclass(frozen=True)
class BatchReplaceSkipped:
    entry_id: int
    mc_version: str
    key: str
    reason: str
    is_extra_version: bool = False


@dataclass(frozen=True)
class BatchReplacePlan:
    database_identity: str
    criteria: EntryFilter
    find_text: str
    replace_text: str
    propagate: bool
    root_ids: tuple[int, ...]
    changes: tuple[BatchReplaceChange, ...]
    skipped: tuple[BatchReplaceSkipped, ...]
    confirmed_quality_worsening: bool = False
    total_unique_entries: int = 0
    extra_version_count: int = 0
    extra_candidate_count: int = 0
    conflict_count: int = 0
    quality_mixed_count: int = 0
    quality_worsened_count: int = 0
    root_changes: tuple[BatchReplaceChange, ...] = ()
    skipped_count_value: int | None = None
    skipped_details_loaded: bool = True

    @property
    def update_count(self) -> int:
        return len(self.changes)

    @property
    def skipped_count(self) -> int:
        if self.skipped_count_value is not None:
            return self.skipped_count_value
        return len(self.skipped)


@dataclass(frozen=True)
class BatchReplaceResult:
    batch_id: str
    updated: int
    skipped: int
    total: int


@dataclass(frozen=True)
class BatchRevertResult:
    reverted: int
    skipped: int
    total: int


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


@dataclass(frozen=True)
class EffectiveSourceStat:
    """One effective-source bucket for a game version."""

    mc_version: str
    source: int | None
    review_status: str | None
    count: int
