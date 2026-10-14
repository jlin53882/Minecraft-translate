"""資料庫清理：找出目前規則判定「不是要翻譯的內容」的舊條目，先匯出檢查檔，確認後才刪除。

流程：``export_cleanup_report`` 寫出 JSON 檢查檔 → 使用者檢查（刪掉不想清的項目，或把
``delete`` 改成 ``false``）→ ``preview_cleanup`` 預覽 → ``apply_cleanup`` 刪除。

只清沒有人動過的條目：有任何譯文（含 jar 自帶繁中）、歷史紀錄或生效譯文的條目一律不碰。
刪除前整批存進 ``cleanup_batch``（含關聯的譯文、生效譯文與歷史），``revert_cleanup`` 可以整批還原。
套用時每筆都會依目前的資料與規則重新檢查，檢查檔過期或被改過的項目會略過，不會誤刪。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from translation_tool.core.guide_blocks import NON_TEXT_REASONS, non_text_reason
from translation_tool.core.text_sniffer import classify_book_string, is_placeholder_text
from translation_tool.translation_db.schema import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
)

REPORT_FORMAT = "mod-db-cleanup-v1"
_LOWERCASE = re.compile(r"[a-z]")
_LETTER = re.compile(r"[^\W\d_]")
_FORMAT_TOKEN = re.compile(
    r"%(?:\d+\$)?[sdfxXeEgGcb%]|\{\d*\}|§[0-9a-fk-orA-FK-OR]|\$\([^)]*\)"
)
_KEY_SEGMENT = re.compile(r"[#./\[\]]+")
_FIELD_NAMES = frozenset({"description", "title", "name", "text", "summary"})
_CHUNK = 500
_PREVIEW_CHARS = 300

CLEANUP_CATEGORIES: dict[str, str] = {
    **NON_TEXT_REASONS,
    "multiblock": "多方塊結構圖案列",
    "not_text": "手冊中不是顯示文字的字串",
    "no_letters": "沒有文字的字串（只有符號或數字，不含格式碼）",
    "field_name": "內容就是欄位名稱（佔位字）",
}
_CLEANUP_KINDS = (KIND_GUIDE, KIND_PATCHOULI, KIND_ADVANCEMENT, KIND_LANG)


def _has_real_letter(text: str) -> bool:
    """去掉 ``%s``、``{0}``、``§c``、``$(…)`` 這類格式碼後，還有沒有任何文字（含中日韓字元）。"""
    return _LETTER.search(_FORMAT_TOKEN.sub("", text)) is not None


def _is_field_name(key: str, en_us: str) -> bool:
    """內容就是它所在的欄位名稱（例如進度的 ``description`` 欄位內容是 ``Description``）。"""
    word = en_us.strip().lower()
    return word in _FIELD_NAMES and word in {
        part.lower() for part in _KEY_SEGMENT.split(key)
    }


def entry_cleanup_category(kind: str, key: str, en_us: str) -> str | None:
    """依目前規則判斷這個條目屬於哪一類「不該收」；正常條目回傳 None。"""
    if not en_us or not en_us.strip():
        return None  # 沒有原文的條目（只有譯文來源）不在這裡處理
    if kind == KIND_GUIDE:
        return non_text_reason(en_us)
    if kind == KIND_PATCHOULI:
        return _patchouli_category(key, en_us)
    if kind in (KIND_LANG, KIND_ADVANCEMENT):
        # 語言檔與進度只用不看內容語意的保守規則（小寫單字如 ``on``／``off`` 是正常譯文）
        if is_placeholder_text(en_us):
            return "placeholder"
        if not _has_real_letter(en_us) and not _FORMAT_TOKEN.search(en_us):
            return "no_letters"  # 含 %s 之類的格式碼要保留：中文語序可能不同，需要翻譯
        if kind == KIND_ADVANCEMENT and _is_field_name(key, en_us):
            return "field_name"
    return None


def _patchouli_category(key: str, en_us: str) -> str | None:
    if "multiblock.pattern" in key and not _LOWERCASE.search(en_us):
        return "multiblock"
    if is_placeholder_text(en_us):
        return "placeholder"
    return "not_text" if classify_book_string(en_us) == "not" else None


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CleanupCandidate:
    entry_id: int
    kind: str
    mod_id: str
    key: str
    en_us: str
    category: str


@dataclass
class CleanupReport:
    """``export_cleanup_report`` 的結果。"""

    version: str
    path: Path
    counts: dict[str, int] = field(default_factory=dict)  # 類別 → 可清理筆數
    protected: dict[str, int] = field(default_factory=dict)  # 類別 → 有譯文或歷史而略過

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class CleanupPlan:
    """檢查檔套用前的預覽，或套用後的結果。"""

    version: str = ""
    requested: int = 0  # 檢查檔中標記要刪除的筆數
    valid_ids: list[int] = field(default_factory=list)  # 現在仍可安全刪除
    skipped: Counter = field(default_factory=Counter)  # 略過原因 → 筆數
    by_category: Counter = field(default_factory=Counter)
    batch_id: str = ""

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


_PROTECTED_SQL = (
    (
        "SELECT DISTINCT entry_id FROM translation WHERE entry_id IN ({m}) "
        "AND (zh_tw <> '' OR zh_cn <> '')"
    ),
    "SELECT DISTINCT entry_id FROM effective WHERE entry_id IN ({m}) AND zh_tw <> ''",
    "SELECT DISTINCT entry_id FROM history WHERE entry_id IN ({m})",
)
_SNAPSHOT_TABLES = {
    "entry": "id",
    "translation": "entry_id",
    "effective": "entry_id",
    "history": "entry_id",
}


def _chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


def _marks(count: int) -> str:
    return ",".join("?" * count)


class CleanupMixin:
    """``TranslationDB`` 的清理方法（使用其連線與交易）。"""

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

    def cleanup_candidates(
        self, version: str
    ) -> tuple[list[CleanupCandidate], Counter]:
        """``(可清理的條目, 因有譯文或歷史而略過的類別計數)``。"""
        flagged: list[CleanupCandidate] = []
        for entry_id, kind, mod_id, key, en_us in self._q(
            "SELECT id, kind, mod_id, key, en_us FROM entry "
            f"WHERE mc_version = ? AND kind IN ({_marks(len(_CLEANUP_KINDS))}) "
            "ORDER BY kind, mod_id, key",
            (version, *_CLEANUP_KINDS),
        ):
            category = entry_cleanup_category(kind, key, en_us)
            if category is not None:
                flagged.append(
                    CleanupCandidate(entry_id, kind, mod_id, key, en_us, category)
                )
        protected = self._protected_ids([c.entry_id for c in flagged])
        skipped = Counter(c.category for c in flagged if c.entry_id in protected)
        return [c for c in flagged if c.entry_id not in protected], skipped

    # ------------------------------------------------------------------ 匯出
    def export_cleanup_report(self, version: str, path: str | Path) -> CleanupReport:
        """把可清理的條目寫成 JSON 檢查檔（只讀資料庫，不刪任何東西）。"""
        candidates, protected = self.cleanup_candidates(version)
        counts = Counter(c.category for c in candidates)
        payload: dict[str, Any] = {
            "format": REPORT_FORMAT,
            "version": version,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "how_to_use": (
                "請檢查 items。不想刪除的項目：整個移除，或把 delete 改成 false。"
                "檢查完回到程式按「依檢查檔清理」；刪除前會再依目前資料重新檢查，"
                "並且可以整批還原。"
            ),
            "summary": {
                cid: {
                    "label": label,
                    "count": counts.get(cid, 0),
                    "skipped_has_translation_or_history": protected.get(cid, 0),
                }
                for cid, label in CLEANUP_CATEGORIES.items()
                if counts.get(cid) or protected.get(cid)
            },
            "items": [
                {
                    "id": c.entry_id,
                    "delete": True,
                    "category": c.category,
                    "kind": c.kind,
                    "mod": c.mod_id,
                    "key": c.key,
                    "en_us": c.en_us[:_PREVIEW_CHARS],
                    "en_us_hash": _text_hash(c.en_us),
                }
                for c in candidates
            ],
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return CleanupReport(version, target, dict(counts), dict(protected))

    # ------------------------------------------------------------------ 預覽與套用
    @staticmethod
    def _load_report(path: str | Path) -> dict[str, Any]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"無法讀取檢查檔：{exc}") from exc
        if not isinstance(data, dict) or data.get("format") != REPORT_FORMAT:
            raise ValueError("這不是資料庫清理的檢查檔（格式標記不符）")
        if not isinstance(data.get("items"), list) or not data.get("version"):
            raise ValueError("檢查檔缺少版本或項目清單")
        return data

    def _validate_cleanup_items(self, data: dict[str, Any]) -> CleanupPlan:
        """逐筆依目前資料與規則重新檢查檢查檔裡標記要刪除的項目。"""
        version = str(data["version"])
        plan = CleanupPlan(version=version)
        wanted: dict[int, dict[str, Any]] = {}
        for item in data["items"]:
            if not isinstance(item, dict) or item.get("delete") is False:
                continue
            try:
                wanted[int(item["id"])] = item
            except (KeyError, TypeError, ValueError):
                plan.skipped["項目格式錯誤"] += 1
        plan.requested = len(wanted) + plan.skipped["項目格式錯誤"]
        rows: dict[int, tuple] = {}
        ids = list(wanted)
        for chunk in _chunks(ids):
            for row in self._q(
                "SELECT id, kind, mc_version, mod_id, key, en_us FROM entry "
                f"WHERE id IN ({_marks(len(chunk))})",
                tuple(chunk),
            ):
                rows[int(row[0])] = row
        passed: list[int] = []
        for entry_id in ids:
            row = rows.get(entry_id)
            if row is None:
                plan.skipped["條目已不存在"] += 1
            elif row[2] != version or row[1] not in _CLEANUP_KINDS:
                plan.skipped["版本或類型與檢查檔不符"] += 1
            elif _text_hash(row[5]) != wanted[entry_id].get("en_us_hash"):
                plan.skipped["原文已變動"] += 1
            elif entry_cleanup_category(row[1], row[4], row[5]) is None:
                plan.skipped["目前規則判為正常內容"] += 1
            else:
                passed.append(entry_id)
        protected = self._protected_ids(passed)
        for entry_id in passed:
            if entry_id in protected:
                plan.skipped["已有譯文或歷史"] += 1
                continue
            plan.valid_ids.append(entry_id)
            row = rows[entry_id]
            plan.by_category[entry_cleanup_category(row[1], row[4], row[5])] += 1
        return plan

    def preview_cleanup(self, path: str | Path) -> CleanupPlan:
        """讀檢查檔並預覽：現在實際會刪幾筆、哪些會略過（不寫入）。"""
        return self._validate_cleanup_items(self._load_report(path))

    def apply_cleanup(self, path: str | Path) -> CleanupPlan:
        """刪除檢查檔中仍然安全的項目；刪除前整批存進 ``cleanup_batch`` 供還原。"""
        data = self._load_report(path)
        with self._tx():
            plan = self._validate_cleanup_items(data)
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
                self._conn.execute(
                    f"DELETE FROM entry WHERE id IN ({_marks(len(chunk))})",
                    tuple(chunk),
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
        return snapshot

    def last_cleanup_batch(self) -> CleanupBatchInfo | None:
        """最近一次清理（含已還原的）；沒有清理紀錄回傳 None。"""
        row = self._one(
            "SELECT id, mc_version, created_at, entry_count, reverted_at "
            "FROM cleanup_batch ORDER BY created_at DESC, rowid DESC LIMIT 1"
        )
        return None if row is None else CleanupBatchInfo(*row)

    def revert_cleanup(self, batch_id: str) -> tuple[int, int]:
        """整批還原；回傳 ``(還原筆數, 因原位置已有同一條目而略過的筆數)``。"""
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
            entry_table = snapshot["entry"]
            columns = entry_table["columns"]
            restored = skipped = 0
            kept_ids: set[int] = set()
            for values in entry_table["rows"]:
                record = dict(zip(columns, values, strict=True))
                clash = self._conn.execute(
                    "SELECT 1 FROM entry WHERE id = ? OR (kind = ? AND mc_version = ? "
                    "AND mod_id = ? AND key = ?)",
                    (
                        record["id"],
                        record["kind"],
                        record["mc_version"],
                        record["mod_id"],
                        record["key"],
                    ),
                ).fetchone()
                if clash:
                    skipped += 1  # 重新掃描後已有同一條目，不覆蓋
                    continue
                self._insert_row("entry", columns, values)
                kept_ids.add(int(record["id"]))
                restored += 1
            for table in ("translation", "effective", "history"):
                part = snapshot[table]
                id_index = part["columns"].index("entry_id")
                for values in part["rows"]:
                    if int(values[id_index]) in kept_ids:
                        self._insert_row(table, part["columns"], values)
            self._conn.execute(
                "UPDATE cleanup_batch SET reverted_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"), batch_id),
            )
        return restored, skipped

    def _insert_row(self, table: str, columns: Sequence[str], values: Sequence) -> None:
        self._conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({_marks(len(columns))})",
            tuple(values),
        )
