"""資料庫清理：找出目前規則判定「不是要翻譯的內容」的舊條目，先匯出檢查檔，確認後才刪除。

流程：``export_cleanup_report`` 寫出 JSON 檢查檔 → 使用者檢查（刪掉不想清的項目，或把
``delete`` 改成 ``false``）→ ``preview_cleanup`` 預覽（得到 ``CleanupPlan.token``）→
``apply_cleanup(path, expected_token)`` 刪除。套用時在寫入交易內重新計算同一份計畫，
檢查檔、版本、資料庫或任何一筆的資料與預覽時不同，token 就對不上，拒絕刪除並要求重新預覽。

一般檢查檔只清沒有人動過的條目：有任何譯文（含 jar 自帶繁中）、歷史紀錄或生效譯文的條目一律不碰。
這些「符合規則但已有譯文或歷史」的條目另外用 ``export_protected_report`` 匯出：項目標記
``has_translation``，列出所有來源的譯文與歷史摘要，並帶著匯出當下完整資料狀態的指紋，
套用時指紋不同（有人又改過）就略過。
刪除前整批存進 ``cleanup_batch``（含關聯的譯文、生效譯文、歷史與原文變更紀錄），
``revert_cleanup`` 可以還原；原位置已有同一條目的項目保留為待還原，不覆蓋、也不標成已完成。
套用時每筆都會依目前的資料與規則重新檢查，檢查檔過期或被改過的項目會略過，不會誤刪。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from translation_tool.core.guide_blocks import NON_TEXT_REASONS, non_text_reason
from translation_tool.core.patchouli_semantics import (
    DISPLAY_NONTRANSLATABLE,
    PATCHOULI_CLASSIFIER_VERSION,
    STRUCTURAL,
    TRANSLATION_REFERENCE,
    UNKNOWN,
    PatchouliSemantic,
)
from translation_tool.core.text_sniffer import (
    book_field,
    classify_book_string,
    is_advancement_literal_text,
    is_field_name_text,
    is_placeholder_text,
    lang_non_text_reason,
    value_shape_reason,
)
from translation_tool.translation_db.schema import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
    SRC_MANUAL,
)

REPORT_FORMAT = "mod-db-cleanup-v2"
_OLD_REPORT_FORMATS = ("mod-db-cleanup-v1",)
MODE_RULE = "rule"  # 一般檢查檔：只列沒有譯文也沒有歷史的條目
MODE_PROTECTED = "protected"  # 已有譯文或歷史的條目
_MODES = (MODE_RULE, MODE_PROTECTED)
_MAX_REPORT_BYTES = 256 * 1024 * 1024
_LOWERCASE = re.compile(r"[a-z]")
_CJK = re.compile(r"[一-鿿]")
_CHUNK = 500
_PREVIEW_CHARS = 300
_HISTORY_SUMMARY = 3
_DB_ID_KEY = "cleanup_db_id"
_RESTORED_KEY = "cleanup_restored:"

CLEANUP_CATEGORIES: dict[str, str] = {
    **NON_TEXT_REASONS,
    "multiblock": "多方塊結構圖案列",
    "not_text": "手冊中不是顯示文字的字串",
    "lang_key": "手冊裡指向語言檔鍵值的字串",
    "no_letters": "沒有文字的字串（只有符號或數字，不含格式碼）",
    "field_name": "內容就是欄位名稱（佔位字）",
    "meta_key": "語言檔的翻譯者備註、語言代碼",
    "code_value": "語言檔裡的色碼、識別字、常數或網址",
}
# 看結構就能確定不是文字的類別：已有 jar／第三方中文譯文時仍預設刪除；
# 其他類別是看內容形狀推測的，只要有中文譯文或中文歷史就預設不刪，要人工確認
STRONG_CATEGORIES = frozenset({"multiblock", "meta_key", "placeholder", "lang_key"})
_CLEANUP_KINDS = (KIND_GUIDE, KIND_PATCHOULI, KIND_ADVANCEMENT, KIND_LANG)


def entry_cleanup_category(
    kind: str,
    key: str,
    en_us: str,
    zh_text: str = "",
    *,
    semantic: PatchouliSemantic | None = None,
    conservative: bool = False,
) -> str | None:
    """依目前規則判斷這個條目屬於哪一類「不該收」；正常條目回傳 None。

    語言檔條目沒有原文（翻譯 ZIP 先建立的）時，改用鍵與 ``zh_text``（目前譯文）判斷。
    """
    if not en_us or not en_us.strip():
        if kind != KIND_LANG:
            return None  # 其他類型沒有原文就沒有可判斷的內容
        en_us = zh_text
        if not en_us.strip():
            return "meta_key" if lang_non_text_reason(key, "") == "meta_key" else None
    if kind == KIND_GUIDE:
        return non_text_reason(en_us)
    if kind == KIND_PATCHOULI:
        if semantic is not None:
            return _patchouli_semantic_category(key, en_us, semantic)
        if conservative:
            return _patchouli_conservative_category(key, en_us)
        return _patchouli_category(key, en_us)
    if kind in (KIND_LANG, KIND_ADVANCEMENT):
        # 語言檔與進度只用不看內容語意的保守規則（小寫單字如 ``on``／``off`` 是正常譯文）
        if is_placeholder_text(en_us):
            return "placeholder"
        if kind == KIND_LANG and (reason := lang_non_text_reason(key, en_us)):
            return reason
        if reason := value_shape_reason(en_us):
            return reason
        if kind == KIND_ADVANCEMENT:
            if is_field_name_text(key, en_us):
                return "field_name"
            # Advancement 的 fallback／with／extra 不是 Patchouli 欄位，
            # 必須和 advancement_texts 共用專用 literal 分類器；否則合法短字
            # 會被舊的未知欄位保守規則誤列為清理候選。
            if is_advancement_literal_text(en_us, field=book_field(key)):
                return None
            verdict = classify_book_string(en_us, book_field(key))
            if verdict != "text":
                return "lang_key" if verdict == "lang_key" else "not_text"
    return None


def _patchouli_semantic_category(
    key: str, en_us: str, semantic: PatchouliSemantic
) -> str | None:
    """依共用分類器決定清理資格；模糊或玩家可見的值一律保留。"""

    if semantic.kind in {
        UNKNOWN,
        TRANSLATION_REFERENCE,
        DISPLAY_NONTRANSLATABLE,
    }:
        return None
    if semantic.kind != STRUCTURAL or not semantic.cleanup_delete_allowed:
        return None
    if "multiblock.pattern" in key and not _LOWERCASE.search(en_us):
        return "multiblock"
    return "not_text"


def _patchouli_conservative_category(key: str, en_us: str) -> str | None:
    """沒有原始 JSON context 時，只接受資料庫鍵本身能證明的結構值。

    舊資料庫只保存 ``file_key#JSON.path`` 與原文，無法安全還原 custom page、
    book i18n 或 template processor 的用途。多方塊 pattern 與 mapping 是
    Patchouli 固定的結構子樹，才保留既有清理類別；其他形狀推測（placeholder、
    resource id、短字等）一律降級為 unknown，不列入自動清理。
    """

    normalized = key.lower()
    if "multiblock.pattern" in normalized and not _LOWERCASE.search(en_us):
        return "multiblock"
    if "multiblock.mapping" in normalized and re.fullmatch(
        r"#?[a-z0-9_.-]+:[a-z0-9_./-]+", en_us.strip(), re.IGNORECASE
    ):
        return "not_text"
    return None


def _patchouli_conservative_metadata(
    key: str, en_us: str
) -> tuple[str, str, tuple[str, ...], bool, str] | None:
    category = _patchouli_conservative_category(key, en_us)
    if category == "multiblock":
        return (
            STRUCTURAL,
            "legacy_key_path_high_confidence",
            ("key_path:multiblock.pattern",),
            False,
            PATCHOULI_CLASSIFIER_VERSION,
        )
    if category == "not_text":
        return (
            STRUCTURAL,
            "legacy_key_path_high_confidence",
            ("key_path:multiblock.mapping",),
            False,
            PATCHOULI_CLASSIFIER_VERSION,
        )
    return None


def _patchouli_category(key: str, en_us: str) -> str | None:
    if "multiblock.pattern" in key and not _LOWERCASE.search(en_us):
        return "multiblock"
    if is_placeholder_text(en_us):
        return "placeholder"
    verdict = classify_book_string(en_us, book_field(key))
    if verdict == "text":
        return None
    return "lang_key" if verdict == "lang_key" else "not_text"


def _item_identity(item: dict[str, Any]) -> tuple[Any, Any, Any]:
    """檢查檔項目的 ``(類型, 模組, 鍵值)``；用來確認 id 指到的還是同一個條目。"""
    return item.get("kind"), item.get("mod"), item.get("key")


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _digest(payload: Any) -> str:
    """任意 JSON 結構的完整 SHA-256（排序鍵值、固定格式）。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _clip(text: Any) -> tuple[str, bool]:
    value = "" if text is None else str(text)
    return value[:_PREVIEW_CHARS], len(value) > _PREVIEW_CHARS


def _positive_int(value: Any) -> int | None:
    """檢查檔的 ``id`` 只接受正整數（排除 bool、小數、字串）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


class CleanupPlanChanged(ValueError):
    """套用時重新計算的清理範圍和預覽（token）不一致；要重新預覽。"""


@dataclass(frozen=True)
class CleanupCandidate:
    entry_id: int
    kind: str
    mod_id: str
    key: str
    en_us: str
    category: str
    semantic_kind: str = ""
    semantic_reason: str = ""
    semantic_evidence: tuple[str, ...] = ()
    source_available: bool = False
    classifier_version: str = ""


@dataclass
class CleanupReport:
    """``export_cleanup_report``／``export_protected_report`` 的結果。"""

    version: str
    path: Path
    counts: dict[str, int] = field(default_factory=dict)  # 類別 → 檢查檔列出的筆數
    protected: dict[str, int] = field(
        default_factory=dict
    )  # 類別 → 有譯文或歷史而未列入
    sources: dict[str, int] = field(
        default_factory=dict
    )  # 來源名稱 → 有該來源譯文的筆數
    default_selected: int = 0  # 預設 delete 為 true 的筆數
    review_required: int = 0  # 預設 delete 為 false、要人工確認的筆數

    @property
    def exported_total(self) -> int:
        return sum(self.counts.values())


@dataclass
class CleanupPlan:
    """檢查檔套用前的預覽，或套用後的結果。"""

    version: str = ""
    mode: str = MODE_RULE
    requested: int = 0  # 檢查檔中標記要刪除的筆數
    valid_ids: list[int] = field(default_factory=list)  # 現在仍可安全刪除
    skipped: Counter = field(default_factory=Counter)  # 略過原因 → 筆數
    by_category: Counter = field(default_factory=Counter)
    with_translation: int = 0  # 可刪除的項目中，已有譯文或歷史的筆數
    batch_id: str = ""
    token: str = ""  # 預覽範圍的指紋；apply_cleanup 要帶回同一個值

    @property
    def deletable(self) -> int:
        return len(self.valid_ids)


@dataclass(frozen=True)
class CleanupBatchInfo:
    batch_id: str
    version: str
    created_at: str
    count: int
    reverted_at: str | None
    restored: int = 0  # 已經還原的條目數（部分還原時小於 count）

    @property
    def complete(self) -> bool:
        return bool(self.reverted_at)

    @property
    def pending(self) -> int:
        return 0 if self.complete else self.count - self.restored


@dataclass
class RevertResult:
    """``revert_cleanup`` 的結果；``complete`` 為 False 時批次仍可再還原。"""

    restored: int = 0
    pending: int = 0  # 還沒還原的條目數
    remapped: int = 0  # 原編號已被別的資料用掉、改用新編號的條目數
    conflicts: list[str] = field(
        default_factory=list
    )  # 原位置已有同一條目：kind/mod/key
    complete: bool = True


_PROTECTED_SQL = (
    (
        "SELECT DISTINCT entry_id FROM translation WHERE entry_id IN ({m}) "
        "AND (zh_tw <> '' OR zh_cn <> '')"
    ),
    "SELECT DISTINCT entry_id FROM effective WHERE entry_id IN ({m}) AND zh_tw <> ''",
    "SELECT DISTINCT entry_id FROM history WHERE entry_id IN ({m})",
)
# 資料表 → (關聯條目的欄位, 排序)；狀態指紋與快照都照這份清單
_STATE_TABLES = (
    ("entry", "id", "id"),
    ("translation", "entry_id", "entry_id, source, id"),
    ("effective", "entry_id", "entry_id"),
    ("history", "entry_id", "entry_id, id"),
)
_SNAPSHOT_TABLES = {table: column for table, column, _order in _STATE_TABLES}
_SRC_CHANGE_FROM = (
    "FROM src_change sc JOIN entry e ON e.kind = sc.kind AND e.mc_version = sc.mc_version "
    "AND e.mod_id = sc.mod_id AND e.key = sc.key WHERE e.id IN ({m})"
)


def _chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


def _marks(count: int) -> str:
    return ",".join("?" * count)


def _write_report(path: str | Path, payload: dict[str, Any]) -> Path:
    """先寫暫存檔再整個換過去；中途失敗不會留下寫一半的檢查檔，也不動到舊檔。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def _report_item(candidate: CleanupCandidate) -> dict[str, Any]:
    en_us, truncated = _clip(candidate.en_us)
    item = {
        "id": candidate.entry_id,
        "delete": True,
        "category": candidate.category,
        "kind": candidate.kind,
        "mod": candidate.mod_id,
        "key": candidate.key,
        "en_us": en_us,
        "en_us_truncated": truncated,
        "en_us_hash": _text_hash(candidate.en_us),
    }
    if candidate.semantic_kind:
        item.update(
            {
                "semantic_kind": candidate.semantic_kind,
                "semantic_reason": candidate.semantic_reason,
                "semantic_evidence": list(candidate.semantic_evidence),
                "source_available": candidate.source_available,
                "classifier_version": candidate.classifier_version,
            }
        )
    return item


def _item_problem(row: tuple | None, item: dict[str, Any], version: str) -> str | None:
    """檢查檔項目和資料庫目前的條目還對得上嗎；對得上回傳 None，否則回傳略過原因。

    ``row`` 是 ``(id, kind, mc_version, mod_id, key, en_us, 目前譯文)``。
    """
    if row is None:
        return "條目已不存在"
    if row[2] != version or row[1] not in _CLEANUP_KINDS:
        return "版本或類型與檢查檔不符"
    if (row[1], row[3], row[4]) != _item_identity(item):
        return "類型、模組或鍵值與檢查檔不符"
    if _text_hash(row[5]) != item.get("en_us_hash"):
        return "原文已變動"
    category = entry_cleanup_category(row[1], row[4], row[5], row[6], conservative=True)
    if category is None:
        return "目前規則判為正常內容"
    if category != item.get("category"):
        return "清理類別已變動，請重新匯出"
    return None


def _record_identity(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        record["kind"],
        record["mc_version"],
        record["mod_id"],
        record["key"],
    )


def _has_cjk(*texts: Any) -> bool:
    return any(_CJK.search(t) for t in texts if t)


@dataclass
class _EntryDetail:
    """匯出已有譯文的檢查檔時，某個條目要給人看的譯文與歷史資料。"""

    sources: list[dict[str, Any]] = field(default_factory=list)
    effective: tuple[str, int] | None = None
    history_count: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    any_cjk: bool = False  # 任何來源的譯文或歷史含中文
    human_cjk: bool = False  # 人工來源的譯文或歷史含中文

    def default_delete(self, category: str) -> bool:
        if category in STRONG_CATEGORIES:
            return not self.human_cjk
        return not self.any_cjk

    def review_note(self, category: str) -> str:
        if category in STRONG_CATEGORIES:
            return "人工譯文或歷史紀錄含中文，可能是需要翻譯的文字；確認要刪再把 delete 改成 true"
        return "已有中文譯文或歷史，這個類別是看內容形狀判斷的，可能是被誤判的文字；確認要刪再把 delete 改成 true"


class CleanupMixin:
    """``TranslationDB`` 的清理方法（使用其連線與交易）。"""

    # ------------------------------------------------------------------ 一致讀取與身分
    @contextmanager
    def _read_snapshot(self) -> Iterator[None]:
        """讓區塊內的多次讀取看到同一份資料（其他連線的寫入不會插進來）。"""
        with self._lock:
            if self._conn.in_transaction:
                yield
                return
            self._conn.execute("BEGIN")
            try:
                yield
            finally:
                self._conn.rollback()

    def _read_db_id(self) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", (_DB_ID_KEY,))
        return row[0] if row else None

    def _ensure_db_id(self) -> str:
        """這個資料庫的身分；檢查檔記錄它，避免拿別的資料庫的檢查檔來刪（編號碰巧相同）。"""
        existing = self._read_db_id()
        if existing:
            return existing
        value = uuid.uuid4().hex
        try:
            with self._tx(bump_generation_on_noop=False) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                    (_DB_ID_KEY, value),
                )
        except sqlite3.Error as exc:
            # 唯讀資料庫寫不進去：沒有身分就不能產生可套用的檢查檔
            raise ValueError("資料庫無法寫入，不能匯出清理檢查檔") from exc
        stored = self._read_db_id()
        if not stored:
            raise ValueError("資料庫無法記錄身分，不能匯出清理檢查檔")
        return stored

    # ------------------------------------------------------------------ 找出
    def _protected_ids(self, ids: Sequence[int]) -> set[int]:
        """有譯文、生效譯文或歷史紀錄的條目編號（這些不清理）。"""
        found: set[int] = set()
        for chunk in _chunks(list(ids)):
            marks = _marks(len(chunk))
            for sql in _PROTECTED_SQL:
                found.update(
                    int(r[0]) for r in self._q(sql.format(m=marks), tuple(chunk))
                )
        return found

    def _translation_states(self, ids: Sequence[int]) -> dict[int, str]:
        """條目編號 → 條目、所有來源譯文、生效譯文與歷史的完整 SHA-256 指紋。

        每個表的全部欄位（含審核狀態、checker、列編號、時間、歷史內容）照固定順序納入，
        任何一個欄位改動指紋就不同。
        """
        states: dict[int, str] = {}
        for chunk in _chunks(list(ids)):
            marks = _marks(len(chunk))
            parts: dict[int, dict[str, list[Any]]] = defaultdict(
                lambda: defaultdict(list)
            )
            for table, column, order in _STATE_TABLES:
                names, fetched = self._select_with_columns(
                    f"SELECT * FROM {table} WHERE {column} IN ({marks}) ORDER BY {order}",
                    tuple(chunk),
                )
                owner = names.index(column)
                for row in fetched:
                    parts[int(row[owner])][table].append(list(row))
            names, fetched = self._select_with_columns(
                f"SELECT e.id AS entry_id, sc.* {_SRC_CHANGE_FROM.format(m=marks)} "
                "ORDER BY e.id, sc.id",
                tuple(chunk),
            )
            for row in fetched:  # 沒有外鍵的原文變更紀錄也會隨條目刪除，要納入指紋
                parts[int(row[0])]["src_change"].append(list(row[1:]))
            for entry_id in chunk:
                states[int(entry_id)] = _digest(parts.get(int(entry_id), {}))
        return states

    def _select_with_columns(
        self, sql: str, params: tuple = ()
    ) -> tuple[list[str], list[tuple]]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [d[0] for d in cursor.description], cursor.fetchall()

    def _flagged_candidates(self, version: str) -> list[CleanupCandidate]:
        """目前規則判定「不該收」的條目（不看有沒有譯文）。

        規則要逐條目在 Python 判斷（數十萬筆約數秒），結果通常只有少數幾筆：依資料世代快取，
        資料沒變時（重開清理對話框、切換版本再切回）不重掃；寫入交易內一律重算。
        """
        rows = self._cached(
            f"cleanup_flagged:{PATCHOULI_CLASSIFIER_VERSION}:{version}",
            lambda: self._scan_flagged(version),
        )
        # 快取以 JSON 保存，tuple 欄位讀回時是 list
        return [CleanupCandidate(*row[:8], tuple(row[8]), *row[9:]) for row in rows]

    def _scan_flagged(self, version: str) -> list[list[Any]]:
        flagged: list[list[Any]] = []
        for entry_id, kind, mod_id, key, en_us, zh_text in self._q(
            "SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, COALESCE(f.zh_tw, '') "
            "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            f"WHERE e.mc_version = ? AND e.kind IN ({_marks(len(_CLEANUP_KINDS))}) "
            "ORDER BY e.kind, e.mod_id, e.key",
            (version, *_CLEANUP_KINDS),
        ):
            category = entry_cleanup_category(
                kind, key, en_us, zh_text, conservative=True
            )
            if category is not None:
                metadata = (
                    _patchouli_conservative_metadata(key, en_us)
                    if kind == KIND_PATCHOULI
                    else None
                )
                flagged.append(
                    [
                        entry_id,
                        kind,
                        mod_id,
                        key,
                        en_us,
                        category,
                        *(metadata or ("", "", (), False, "")),
                    ]
                )
        return flagged

    def cleanup_candidates(
        self, version: str
    ) -> tuple[list[CleanupCandidate], Counter]:
        """``(可清理的條目, 因有譯文或歷史而略過的類別計數)``。"""
        with self._read_snapshot():
            flagged = self._flagged_candidates(version)
            protected = self._protected_ids([c.entry_id for c in flagged])
        skipped = Counter(c.category for c in flagged if c.entry_id in protected)
        return [c for c in flagged if c.entry_id not in protected], skipped

    # ------------------------------------------------------------------ 匯出
    def export_cleanup_report(self, version: str, path: str | Path) -> CleanupReport:
        """把可清理的條目寫成 JSON 檢查檔（不刪任何東西）。"""
        db_id = self._ensure_db_id()
        candidates, protected = self.cleanup_candidates(version)
        counts = Counter(c.category for c in candidates)
        payload: dict[str, Any] = {
            "format": REPORT_FORMAT,
            "mode": MODE_RULE,
            "version": version,
            "db_id": db_id,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "how_to_use": (
                "請檢查 items。不想刪除的項目：整個移除，或把 delete 改成 false。"
                "檢查完回到程式按「依檢查檔清理」；刪除前會再依目前資料重新檢查，"
                "並且可以還原。"
            ),
            "summary": {
                cid: {
                    "label": label,
                    "count": counts.get(cid, 0),
                    "default_delete": counts.get(cid, 0),
                    "skipped_has_translation_or_history": protected.get(cid, 0),
                }
                for cid, label in CLEANUP_CATEGORIES.items()
                if counts.get(cid) or protected.get(cid)
            },
            "items": [_report_item(c) for c in candidates],
        }
        target = _write_report(path, payload)
        return CleanupReport(
            version,
            target,
            dict(counts),
            dict(protected),
            default_selected=len(candidates),
        )

    def _entry_details(self, ids: Sequence[int]) -> dict[int, _EntryDetail]:
        """每個條目所有來源的譯文、生效來源與歷史摘要（匯出已有譯文的檢查檔用）。"""
        details = {int(i): _EntryDetail() for i in ids}
        label = self.source_catalog.label
        for chunk in _chunks(list(ids)):
            marks = _marks(len(chunk))
            effective_source: dict[int, int] = {}
            for entry_id, source in self._q(
                f"SELECT entry_id, source FROM effective WHERE entry_id IN ({marks})",
                tuple(chunk),
            ):
                effective_source[int(entry_id)] = int(source)
            for entry_id, source, zh_tw, zh_cn, checker, status in self._q(
                "SELECT entry_id, source, zh_tw, zh_cn, checker, review_status "
                f"FROM translation WHERE entry_id IN ({marks}) "
                "ORDER BY entry_id, source",
                tuple(chunk),
            ):
                detail = details[int(entry_id)]
                zh_tw_text, tw_cut = _clip(zh_tw)
                zh_cn_text, cn_cut = _clip(zh_cn)
                is_effective = effective_source.get(int(entry_id)) == int(source)
                detail.sources.append(
                    {
                        "code": int(source),
                        "label": label(source),
                        "effective": is_effective,
                        "zh_tw": zh_tw_text,
                        "zh_cn": zh_cn_text,
                        "checker": checker or "",
                        "review_status": status,
                        "truncated": tw_cut or cn_cut,
                    }
                )
                if is_effective:
                    detail.effective = (zh_tw_text, int(source))
                if _has_cjk(zh_tw, zh_cn):
                    detail.any_cjk = True
                    detail.human_cjk |= int(source) == SRC_MANUAL
            for entry_id, at, actor, action, old, new in self._q(
                "SELECT entry_id, at, actor, action, old_zh_tw, new_zh_tw FROM history "
                f"WHERE entry_id IN ({marks}) ORDER BY entry_id, id",
                tuple(chunk),
            ):
                detail = details[int(entry_id)]
                detail.history_count += 1
                detail.history.append(
                    {
                        "at": at,
                        "actor": actor,
                        "action": action,
                        "old_zh_tw": _clip(old)[0][:100],
                        "new_zh_tw": _clip(new)[0][:100],
                    }
                )
                del detail.history[:-_HISTORY_SUMMARY]
                if _has_cjk(old, new):
                    detail.any_cjk = detail.human_cjk = True
        return details

    def export_protected_report(self, version: str, path: str | Path) -> CleanupReport:
        """把「符合清理規則但已有譯文或歷史」的條目寫成 JSON 檢查檔。

        一般檢查檔不會列入這些條目。每筆列出刪除時會一起丟掉的全部資料：每個來源的
        譯文（含來源代碼、簡繁、checker、審核狀態）與歷史摘要，並帶著完整資料狀態的指紋：
        匯出後有人改過譯文或審核，套用時就略過。預設 ``delete`` 採保守策略：
        非確定性類別只要有中文譯文或中文歷史、確定性類別有人工中文，就預設不刪。
        """
        db_id = self._ensure_db_id()
        with self._read_snapshot():
            flagged = self._flagged_candidates(version)
            protected_ids = self._protected_ids([c.entry_id for c in flagged])
            candidates = [c for c in flagged if c.entry_id in protected_ids]
            ids = [c.entry_id for c in candidates]
            states = self._translation_states(ids)
            details = self._entry_details(ids)
        counts: Counter = Counter()
        defaults: Counter = Counter()
        sources: Counter = Counter()
        items = []
        for candidate in candidates:
            detail = details[candidate.entry_id]
            effective_zh, effective_code = detail.effective or ("", None)
            for source in detail.sources:
                if source["zh_tw"] or source["zh_cn"]:
                    sources[source["label"]] += 1
            item = {
                **_report_item(candidate),
                "has_translation": True,
                "zh_tw": effective_zh,
                "source": (
                    self.source_catalog.label(effective_code)
                    if effective_code is not None
                    else ""
                ),
                "source_code": effective_code,
                "sources": detail.sources,
                "history_count": detail.history_count,
                "history": detail.history,
                "state_hash": states[candidate.entry_id],
            }
            counts[candidate.category] += 1
            if detail.default_delete(candidate.category):
                defaults[candidate.category] += 1
            else:
                item["delete"] = False
                item["review"] = detail.review_note(candidate.category)
            items.append(item)
        selected = sum(defaults.values())
        payload: dict[str, Any] = {
            "format": REPORT_FORMAT,
            "mode": MODE_PROTECTED,
            "version": version,
            "db_id": db_id,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "how_to_use": (
                "這些條目符合清理規則（不是要翻譯的內容），但已經有譯文或歷史紀錄。"
                "sources 列出每個來源的譯文（zh_tw／zh_cn 超過長度會截斷，truncated 為 true），"
                "history 是最近幾筆歷史。刪除會連同所有來源的譯文與全部歷史一起移除；"
                "不想刪除的項目：整個移除，或把 delete 改成 false。"
                "有 review 欄位的項目預設 delete 為 false，確認後才改成 true。"
                "匯出後譯文或歷史有變動的項目會略過，並且可以還原。"
            ),
            "summary": {
                cid: {
                    "label": label,
                    "count": counts[cid],
                    "default_delete": defaults[cid],
                    "review": counts[cid] - defaults[cid],
                }
                for cid, label in CLEANUP_CATEGORIES.items()
                if counts.get(cid)
            },
            "items": items,
        }
        target = _write_report(path, payload)
        return CleanupReport(
            version,
            target,
            dict(counts),
            {},
            dict(sources),
            default_selected=selected,
            review_required=len(items) - selected,
        )

    # ------------------------------------------------------------------ 預覽與套用
    @staticmethod
    def _load_report(path: str | Path) -> tuple[dict[str, Any], str]:
        """讀檢查檔；回傳 ``(內容, 檔案內容的 SHA-256)``。格式不對就丟 ``ValueError``。"""
        try:
            target = Path(path)
            if target.stat().st_size > _MAX_REPORT_BYTES:
                raise ValueError("檢查檔過大")
            raw = target.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"無法讀取檢查檔：{exc}") from exc
        if isinstance(data, dict) and data.get("format") in _OLD_REPORT_FORMATS:
            raise ValueError("這是舊版檢查檔，請重新匯出")
        if not isinstance(data, dict) or data.get("format") != REPORT_FORMAT:
            raise ValueError("這不是資料庫清理的檢查檔（格式標記不符）")
        if data.get("mode") not in _MODES:
            raise ValueError("檢查檔缺少類型（mode）")
        if not isinstance(data.get("version"), str) or not data["version"]:
            raise ValueError("檢查檔缺少版本")
        if (
            not isinstance(data.get("db_id"), str)
            or not data["db_id"]
            or not isinstance(data.get("items"), list)
        ):
            raise ValueError("檢查檔缺少資料庫身分或項目清單")
        return data, hashlib.sha256(raw).hexdigest()

    def _wanted_items(
        self, data: dict[str, Any], plan: CleanupPlan
    ) -> dict[int, dict[str, Any]]:
        """檢查檔裡明確標記要刪除（``delete`` 為 true）且格式正確的項目。"""
        wanted: dict[int, dict[str, Any]] = {}
        for item in data["items"]:
            if not isinstance(item, dict):
                plan.requested += 1
                plan.skipped["項目格式錯誤"] += 1
                continue
            flag = item.get("delete")
            if flag is False:
                continue
            plan.requested += 1
            if flag is not True:
                plan.skipped["delete 欄位不是 true／false"] += 1
                continue  # 只有明確的 true 才刪；缺欄位或字串 "false" 一律略過
            entry_id = _positive_int(item.get("id"))
            if entry_id is None:
                plan.skipped["項目格式錯誤"] += 1
            elif entry_id in wanted:
                plan.skipped["重複的 id"] += 1
            else:
                wanted[entry_id] = item
        return wanted

    def _validate_cleanup_items(
        self, data: dict[str, Any], report_sha: str
    ) -> CleanupPlan:
        """逐筆依目前資料與規則重新檢查檔案裡標記要刪除的項目，並算出 token。"""
        version, mode = data["version"], data["mode"]
        db_id = self._read_db_id()
        if not db_id or data["db_id"] != db_id:
            raise ValueError("這份檢查檔不是從目前的資料庫匯出的")
        plan = CleanupPlan(version=version, mode=mode)
        wanted = self._wanted_items(data, plan)
        ids = list(wanted)
        rows: dict[int, tuple] = {}
        for chunk in _chunks(ids):
            for row in self._q(
                "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
                "COALESCE(f.zh_tw, '') FROM entry e "
                "LEFT JOIN effective f ON f.entry_id = e.id "
                f"WHERE e.id IN ({_marks(len(chunk))})",
                tuple(chunk),
            ):
                rows[int(row[0])] = row
        states = self._translation_states(sorted(rows))
        passed: list[int] = []
        translated: set[int] = set()
        for entry_id in ids:
            item = wanted[entry_id]
            reason = _item_problem(rows.get(entry_id), item, version)
            if reason is None and item.get("has_translation") is True:
                if mode != MODE_PROTECTED:
                    reason = "一般檢查檔不能標記已有譯文的項目"
                elif states[entry_id] != item.get("state_hash"):
                    reason = "譯文或歷史已變動"
                else:
                    translated.add(entry_id)
            if reason:
                plan.skipped[reason] += 1
            else:
                passed.append(entry_id)
        protected = self._protected_ids([i for i in passed if i not in translated])
        for entry_id in passed:
            if entry_id in protected:
                plan.skipped["已有譯文或歷史"] += 1
                continue
            plan.valid_ids.append(entry_id)
            plan.with_translation += entry_id in translated
            row = rows[entry_id]
            plan.by_category[
                entry_cleanup_category(
                    row[1], row[4], row[5], row[6], conservative=True
                )
            ] += 1
        plan.token = _digest(
            {
                "report": report_sha,
                "db": db_id,
                "version": version,
                "mode": mode,
                "entries": [[i, states[i]] for i in sorted(plan.valid_ids)],
                "with_translation": plan.with_translation,
            }
        )
        return plan

    def preview_cleanup(self, path: str | Path) -> CleanupPlan:
        """讀檢查檔並預覽：現在實際會刪幾筆、哪些會略過（不寫入）。"""
        data, report_sha = self._load_report(path)
        with self._read_snapshot():
            return self._validate_cleanup_items(data, report_sha)

    def apply_cleanup(self, path: str | Path, expected_token: str) -> CleanupPlan:
        """刪除預覽過的範圍；刪除前整批存進 ``cleanup_batch`` 供還原。

        ``expected_token`` 是 ``preview_cleanup`` 回傳的 ``token``。在寫入交易內重新計算，
        檢查檔、版本、資料庫或任何一筆資料和預覽時不同就丟 ``CleanupPlanChanged``，不刪任何東西。
        """
        data, report_sha = self._load_report(path)
        with self._tx():
            plan = self._validate_cleanup_items(data, report_sha)
            if not expected_token or plan.token != expected_token:
                raise CleanupPlanChanged("檢查檔或資料已和預覽時不同，請重新預覽")
            if not plan.valid_ids:
                return plan
            plan.batch_id = uuid.uuid4().hex
            snapshot = self._snapshot_entries(plan.valid_ids)
            self._conn.execute(
                "INSERT INTO cleanup_batch (id, mc_version, created_at, entry_count, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    plan.batch_id,
                    plan.version,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    len(plan.valid_ids),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            for chunk in _chunks(plan.valid_ids):
                marks = _marks(len(chunk))
                self._conn.execute(
                    f"DELETE FROM src_change WHERE id IN "
                    f"(SELECT sc.id {_SRC_CHANGE_FROM.format(m=marks)})",
                    tuple(chunk),
                )
                self._conn.execute(
                    f"DELETE FROM entry WHERE id IN ({marks})", tuple(chunk)
                )
        return plan

    # ------------------------------------------------------------------ 還原
    def _snapshot_entries(self, ids: Sequence[int]) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        for table, id_column in _SNAPSHOT_TABLES.items():
            columns: list[str] = []
            rows: list[list[Any]] = []
            for chunk in _chunks(list(ids)):
                cursor = self._conn.execute(
                    f"SELECT * FROM {table} WHERE {id_column} IN ({_marks(len(chunk))})",
                    tuple(chunk),
                )
                columns = [d[0] for d in cursor.description]
                rows.extend(list(r) for r in cursor.fetchall())
            snapshot[table] = {"columns": columns, "rows": rows}
        # 原文變更紀錄沒有外鍵，依 (類型, 版本, 模組, 鍵值) 對到條目；條目刪除時一起存、一起刪
        columns, rows = [], []
        for chunk in _chunks(list(ids)):
            cursor = self._conn.execute(
                f"SELECT sc.* {_SRC_CHANGE_FROM.format(m=_marks(len(chunk)))}",
                tuple(chunk),
            )
            columns = [d[0] for d in cursor.description]
            rows.extend(list(r) for r in cursor.fetchall())
        snapshot["src_change"] = {"columns": columns, "rows": rows}
        return snapshot

    def cleanup_batches(self) -> list[CleanupBatchInfo]:
        """所有清理批次，新的在前（含已完成、部分還原的）。"""
        restored = {
            key[len(_RESTORED_KEY) :]: len(json.loads(value))
            for key, value in self._q(
                "SELECT key, value FROM meta WHERE key LIKE ?", (_RESTORED_KEY + "%",)
            )
        }
        return [
            CleanupBatchInfo(*row, restored.get(row[0], 0))
            for row in self._q(
                "SELECT id, mc_version, created_at, entry_count, reverted_at "
                "FROM cleanup_batch ORDER BY created_at DESC, rowid DESC"
            )
        ]

    def last_cleanup_batch(self) -> CleanupBatchInfo | None:
        """最近一次清理（含已還原的）；沒有清理紀錄回傳 None。"""
        batches = self.cleanup_batches()
        return batches[0] if batches else None

    def _free_ids(self, table: str, ids: Sequence[int]) -> set[int]:
        """``ids`` 之中，``table`` 目前沒有被別的資料用掉的編號。"""
        used: set[int] = set()
        for chunk in _chunks(list(ids)):
            used.update(
                int(r[0])
                for r in self._conn.execute(
                    f"SELECT id FROM {table} WHERE id IN ({_marks(len(chunk))})",
                    tuple(chunk),
                )
            )
        return set(ids) - used

    def _insert_rows(
        self, table: str, columns: Sequence[str], rows: Sequence[Sequence]
    ) -> dict[int, int]:
        """寫入列；原編號沒被用掉就沿用，被用掉就換新編號。回傳 原編號 → 實際編號。"""
        id_index = list(columns).index("id")
        free = self._free_ids(table, [int(r[id_index]) for r in rows])
        kept = [r for r in rows if int(r[id_index]) in free]
        moved = [r for r in rows if int(r[id_index]) not in free]
        mapping: dict[int, int] = {}
        for (
            values
        ) in kept:  # 先寫沿用原編號的，再讓其餘的自動取新編號，才不會搶到還沒寫的原編號
            self._insert_row(table, columns, values)
            mapping[int(values[id_index])] = int(values[id_index])
        names = [c for c in columns if c != "id"]
        for values in moved:
            rest = [v for c, v in zip(columns, values, strict=True) if c != "id"]
            cursor = self._conn.execute(
                f"INSERT INTO {table} ({','.join(names)}) VALUES ({_marks(len(names))})",
                tuple(rest),
            )
            mapping[int(values[id_index])] = int(cursor.lastrowid)
        return mapping

    def _restored_identities(self, batch_id: str) -> set[tuple[str, str, str, str]]:
        """已還原條目的 ``(類型, 版本, 模組, 鍵值)``；用身分而不是編號，編號之後可能被重用。"""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_RESTORED_KEY + batch_id,)
        ).fetchone()
        return {tuple(item) for item in json.loads(row[0])} if row else set()

    def _entry_exists(self, identity: tuple[str, str, str, str]) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM entry WHERE kind = ? AND mc_version = ? "
                "AND mod_id = ? AND key = ?",
                identity,
            ).fetchone()
            is not None
        )

    def revert_cleanup(self, batch_id: str) -> RevertResult:
        """還原一個批次；原位置已有同一條目的項目保留為待還原，批次要全部還原才算完成。

        不覆蓋較新的資料。原編號被別的資料用掉時改用新編號，譯文、歷史與審核狀態照樣恢復。
        """
        with self._tx():
            row = self._conn.execute(
                "SELECT payload, reverted_at FROM cleanup_batch WHERE id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise ValueError("找不到這次清理紀錄")
            if row[1]:
                raise ValueError("這次清理已經還原過了")
            snapshot = json.loads(row[0])
            columns = snapshot["entry"]["columns"]
            records = sorted(
                (dict(zip(columns, v, strict=True)) for v in snapshot["entry"]["rows"]),
                key=lambda r: int(r["id"]),
            )
            done = self._restored_identities(batch_id)
            result = RevertResult()
            todo: list[dict[str, Any]] = []
            for record in records:
                identity = _record_identity(record)
                # 之前還原過、現在條目還在的才算完成；已經不見的重新當成待還原
                if identity in done and self._entry_exists(identity):
                    continue
                done.discard(identity)
                if self._entry_exists(identity):
                    result.conflicts.append(
                        f"{record['kind']}/{record['mod_id']}/{record['key']}"
                    )
                else:
                    todo.append(record)
            entry_map = self._insert_rows(
                "entry", columns, [[r[c] for c in columns] for r in todo]
            )
            result.restored = len(entry_map)
            result.remapped = sum(1 for old, new in entry_map.items() if old != new)
            self._restore_children(snapshot, entry_map, todo)
            done |= {_record_identity(r) for r in todo if int(r["id"]) in entry_map}
            result.pending = len(records) - len(done)
            result.complete = result.pending == 0
            if result.complete:
                self._conn.execute(
                    "UPDATE cleanup_batch SET reverted_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(timespec="seconds"), batch_id),
                )
                self._conn.execute(
                    "DELETE FROM meta WHERE key = ?", (_RESTORED_KEY + batch_id,)
                )
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (_RESTORED_KEY + batch_id, json.dumps(sorted(done))),
                )
        return result

    def _restore_children(
        self,
        snapshot: dict[str, Any],
        entry_map: dict[int, int],
        restored_entries: Sequence[dict[str, Any]],
    ) -> None:
        """把快照裡已還原條目的譯文、歷史與原文變更紀錄寫回，生效譯文依目前優先序重算。"""
        for table in ("translation", "history"):
            part = snapshot[table]
            columns = part["columns"]
            owner = columns.index("entry_id")
            rows = []
            for values in part["rows"]:
                if int(values[owner]) in entry_map:
                    remapped = list(values)
                    remapped[owner] = entry_map[int(values[owner])]
                    rows.append(remapped)
            mapping = self._insert_rows(table, columns, rows)
            if table == "translation":
                self._restore_revisions(columns, rows, mapping)
        self._refresh(self._conn, entry_map.values())
        part = snapshot.get("src_change") or {"columns": [], "rows": []}
        restored_keys = {
            (r["kind"], r["mc_version"], r["mod_id"], r["key"])
            for r in restored_entries
            if int(r["id"]) in entry_map
        }
        columns = [c for c in part["columns"] if c != "id"]
        for values in part["rows"]:
            record = dict(zip(part["columns"], values, strict=True))
            if (
                record["kind"],
                record["mc_version"],
                record["mod_id"],
                record["key"],
            ) in restored_keys:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO src_change ({','.join(columns)}) "
                    f"VALUES ({_marks(len(columns))})",
                    tuple(record[c] for c in columns),
                )

    def _restore_revisions(
        self,
        columns: Sequence[str],
        rows: Sequence[Sequence],
        mapping: dict[int, int],
    ) -> None:
        """寫入譯文的觸發器會重新編 revision；改回快照當時的值，歷史裡記的 revision 才對得上。"""
        id_index = list(columns).index("id")
        revision_index = list(columns).index("revision")
        for values in rows:
            self._conn.execute(
                "UPDATE translation SET revision = ? WHERE id = ? AND revision <> ?",
                (
                    values[revision_index],
                    mapping[int(values[id_index])],
                    values[revision_index],
                ),
            )

    def _insert_row(self, table: str, columns: Sequence[str], values: Sequence) -> None:
        self._conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({_marks(len(columns))})",
            tuple(values),
        )
