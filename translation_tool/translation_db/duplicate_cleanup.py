"""資料庫掃描迭代留下的路徑別名清理。

舊版掃描器可能把 ``_en_us``／``en_us_0`` 語系目錄留在指南鍵值，
新版掃描器則把它們正規化成同一個檔案鍵。本模組只處理高信心的別名：
同版本、同類型、同模組、同原文，而且翻譯來源沒有衝突；其餘候選只列為
需要人工判斷，不會被自動合併。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from translation_tool.translation_db.identity import canonical_entry_key
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_PATCHOULI

_CHUNK = 500
_MERGE_KINDS = frozenset({KIND_GUIDE, KIND_PATCHOULI})
_BATCH_FORMAT = "mod-db-duplicate-merge-v1"


class DuplicatePlanChanged(ValueError):
    """重複候選在預覽後變動，必須重新掃描／預覽。"""


@dataclass(frozen=True)
class DuplicateEntry:
    entry_id: int
    key: str
    en_us: str
    effective_zh_tw: str
    effective_source: int | None
    translation_count: int
    history_count: int


@dataclass(frozen=True)
class DuplicateGroup:
    kind: str
    mc_version: str
    mod_id: str
    canonical_key: str
    entries: tuple[DuplicateEntry, ...]
    safe: bool
    reason: str

    @property
    def group_id(self) -> str:
        value = (
            f"{self.kind}\x1f{self.mc_version}\x1f{self.mod_id}\x1f{self.canonical_key}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    @property
    def duplicate_count(self) -> int:
        return max(0, len(self.entries) - 1)


@dataclass
class DuplicateMergePlan:
    version: str
    candidates: list[DuplicateGroup] = field(default_factory=list)
    valid_groups: list[DuplicateGroup] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    token: str = ""
    batch_id: str = ""
    mod_id: str | None = None
    kind: str | None = None

    @property
    def group_count(self) -> int:
        return len(self.valid_groups)

    @property
    def duplicate_count(self) -> int:
        return sum(group.duplicate_count for group in self.valid_groups)


@dataclass(frozen=True)
class DuplicateMergeBatchInfo:
    batch_id: str
    version: str
    created_at: str
    group_count: int
    entry_count: int
    reverted_at: str | None

    @property
    def complete(self) -> bool:
        return bool(self.reverted_at)


@dataclass
class DuplicateRevertResult:
    restored_groups: int = 0
    pending_groups: int = 0
    conflicts: list[str] = field(default_factory=list)
    complete: bool = True


def _chunks(values: list[int]):
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def _marks(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _normal_text(value: str) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def _entry_payload(group: DuplicateGroup) -> list[Any]:
    return [
        [entry.entry_id, entry.key, _normal_text(entry.en_us)]
        for entry in group.entries
    ]


def _group_payload(group: DuplicateGroup) -> dict[str, Any]:
    return {
        "id": group.group_id,
        "kind": group.kind,
        "version": group.mc_version,
        "mod": group.mod_id,
        "canonical_key": group.canonical_key,
        "safe": group.safe,
        "reason": group.reason,
        "entries": _entry_payload(group),
    }


def _token(
    candidates: list[DuplicateGroup],
    states: dict[int, str],
    *,
    version: str,
    mod_id: str | None,
    kind: str | None,
) -> str:
    payload = {
        "scope": {"version": version, "mod": mod_id, "kind": kind},
        "candidates": [
            {
                **_group_payload(group),
                "state": [states.get(entry.entry_id, "") for entry in group.entries],
            }
            for group in candidates
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DuplicateCleanupMixin:
    """TranslationDB 的路徑別名掃描、合併與復原方法。"""

    def duplicate_candidates(
        self,
        version: str,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
    ) -> list[DuplicateGroup]:
        """列出指定範圍中可能是同一來源檔的重複條目。只讀、不合併。"""
        if kind is not None and kind not in _MERGE_KINDS:
            return []
        clauses = ["e.mc_version=?", "e.kind IN (?,?)"]
        params: list[str] = [version, KIND_GUIDE, KIND_PATCHOULI]
        if mod_id:
            clauses.append("e.mod_id=?")
            params.append(mod_id)
        if kind:
            clauses.append("e.kind=?")
            params.append(kind)
        rows = self._q(
            "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            "COALESCE(f.zh_tw, ''), f.source "
            "FROM entry e LEFT JOIN effective f ON f.entry_id=e.id "
            "WHERE " + " AND ".join(clauses),
            params,
        )
        if not rows:
            return []
        ids = [int(row[0]) for row in rows]
        translation_counts = self._duplicate_counts("translation", ids)
        history_counts = self._duplicate_counts("history", ids)
        translations = self._duplicate_translations(ids)
        grouped: dict[tuple[str, str, str], list[DuplicateEntry]] = defaultdict(list)
        for row in rows:
            entry = DuplicateEntry(
                int(row[0]),
                row[4],
                row[5],
                row[6],
                int(row[7]) if row[7] is not None else None,
                translation_counts.get(int(row[0]), 0),
                history_counts.get(int(row[0]), 0),
            )
            grouped[(row[1], row[3], canonical_entry_key(row[1], row[4]))].append(entry)
        candidates = []
        for (group_kind, group_mod_id, canonical), entries in grouped.items():
            if len(entries) < 2:
                continue
            entries.sort(key=lambda item: item.entry_id)
            same_text = (
                bool(entries[0].en_us.strip())
                and len({_normal_text(item.en_us) for item in entries}) == 1
            )
            conflict = _has_translation_conflict(
                [translations.get(item.entry_id, ()) for item in entries]
            )
            safe = same_text and not conflict
            reason = (
                "same_original"
                if safe
                else "translation_conflict"
                if same_text
                else "different_original"
            )
            candidates.append(
                DuplicateGroup(
                    group_kind,
                    version,
                    group_mod_id,
                    canonical,
                    tuple(entries),
                    safe,
                    reason,
                )
            )
        return sorted(
            candidates,
            key=lambda group: (group.kind, group.mod_id, group.canonical_key),
        )

    def preview_duplicate_cleanup(
        self,
        version: str,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
    ) -> DuplicateMergePlan:
        """掃描高信心別名並產生 token；不寫入資料庫。"""
        with self._read_snapshot():
            return self._duplicate_plan(version, mod_id=mod_id, kind=kind)

    def apply_duplicate_cleanup(
        self,
        version: str,
        expected_token: str,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
    ) -> DuplicateMergePlan:
        """在單一交易內合併高信心別名，並保存完整快照供復原。"""
        with self._tx(keep_review=True):
            plan = self._duplicate_plan(version, mod_id=mod_id, kind=kind)
            if not expected_token or plan.token != expected_token:
                raise DuplicatePlanChanged("重複候選已變動，請重新掃描與預覽")
            if not plan.valid_groups:
                return plan
            ids = [
                entry.entry_id for group in plan.valid_groups for entry in group.entries
            ]
            snapshots = self._duplicate_snapshots(ids)
            batch_groups = []
            for group in plan.valid_groups:
                survivor = _choose_survivor(group)
                duplicate_ids = [
                    entry.entry_id
                    for entry in group.entries
                    if entry.entry_id != survivor.entry_id
                ]
                for duplicate_id in duplicate_ids:
                    self._merge_duplicate_entry(survivor.entry_id, duplicate_id)
                batch_groups.append(
                    {
                        "id": group.group_id,
                        "survivor_id": survivor.entry_id,
                        "entry_ids": [entry.entry_id for entry in group.entries],
                        "identities": [
                            [group.kind, version, group.mod_id, entry.key]
                            for entry in group.entries
                        ],
                    }
                )
            self._refresh(
                self._conn,
                [item["survivor_id"] for item in batch_groups],
            )
            after = self._duplicate_states(
                [item["survivor_id"] for item in batch_groups]
            )
            for item in batch_groups:
                item["after_state"] = after[item["survivor_id"]]
            plan.batch_id = uuid.uuid4().hex
            payload = {
                "format": _BATCH_FORMAT,
                "version": version,
                "groups": batch_groups,
                "snapshots": snapshots,
                "restored_groups": [],
            }
            self._conn.execute(
                "INSERT INTO duplicate_merge_batch "
                "(id, mc_version, created_at, group_count, entry_count, payload) "
                "VALUES (?,?,?,?,?,?)",
                (
                    plan.batch_id,
                    version,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    len(batch_groups),
                    plan.duplicate_count,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return plan

    def duplicate_merge_batches(self) -> list[DuplicateMergeBatchInfo]:
        rows = self._q(
            "SELECT id, mc_version, created_at, group_count, entry_count, reverted_at "
            "FROM duplicate_merge_batch ORDER BY created_at DESC"
        )
        return [DuplicateMergeBatchInfo(*row) for row in rows]

    def revert_duplicate_cleanup(self, batch_id: str) -> DuplicateRevertResult:
        """只在合併後 survivor 未被改動時復原；有新修改就保留並報告衝突。"""
        with self._tx(keep_review=True):
            row = self._conn.execute(
                "SELECT payload, reverted_at FROM duplicate_merge_batch WHERE id=?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise ValueError("找不到重複合併紀錄")
            if row[1]:
                raise ValueError("這次重複合併已經還原過了")
            payload = json.loads(row[0])
            restored = set(payload.get("restored_groups", []))
            result = DuplicateRevertResult()
            for group in payload["groups"]:
                if group["id"] in restored:
                    continue
                survivor_id = int(group["survivor_id"])
                if (
                    self._duplicate_states([survivor_id]).get(survivor_id)
                    != group["after_state"]
                ):
                    result.conflicts.append(group["id"])
                    continue
                if self._duplicate_identities_reappeared(
                    group["identities"], survivor_id
                ):
                    result.conflicts.append(group["id"])
                    continue
                snapshot = payload["snapshots"][group["id"]]
                self._delete_entry_with_source_changes(survivor_id)
                records = [
                    dict(zip(snapshot["entry"]["columns"], values, strict=True))
                    for values in snapshot["entry"]["rows"]
                ]
                entry_map = self._insert_rows(
                    "entry",
                    snapshot["entry"]["columns"],
                    [
                        [record[column] for column in snapshot["entry"]["columns"]]
                        for record in records
                    ],
                )
                self._restore_children(snapshot, entry_map, records)
                restored.add(group["id"])
                result.restored_groups += 1
            payload["restored_groups"] = sorted(restored)
            result.pending_groups = len(payload["groups"]) - len(restored)
            result.complete = result.pending_groups == 0
            self._conn.execute(
                "UPDATE duplicate_merge_batch SET payload=?, reverted_at=? WHERE id=?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(UTC).isoformat(timespec="seconds")
                    if result.complete
                    else None,
                    batch_id,
                ),
            )
        return result

    def _duplicate_plan(
        self,
        version: str,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
    ) -> DuplicateMergePlan:
        candidates = self.duplicate_candidates(version, mod_id=mod_id, kind=kind)
        valid = [group for group in candidates if group.safe]
        skipped = Counter(group.reason for group in candidates if not group.safe)
        ids = [entry.entry_id for group in candidates for entry in group.entries]
        states = self._duplicate_states(ids)
        return DuplicateMergePlan(
            version,
            candidates,
            valid,
            skipped,
            _token(
                candidates,
                states,
                version=version,
                mod_id=mod_id,
                kind=kind,
            ),
            mod_id=mod_id,
            kind=kind,
        )

    def _duplicate_counts(self, table: str, ids: list[int]) -> dict[int, int]:
        counts: dict[int, int] = {}
        for chunk in _chunks(ids):
            marks = _marks(len(chunk))
            rows = self._q(
                f"SELECT entry_id, COUNT(*) FROM {table} "
                f"WHERE entry_id IN ({marks}) GROUP BY entry_id",
                chunk,
            )
            counts.update({int(entry_id): int(count) for entry_id, count in rows})
        return counts

    def _duplicate_translations(self, ids: list[int]) -> dict[int, list[tuple]]:
        found: dict[int, list[tuple]] = defaultdict(list)
        for chunk in _chunks(ids):
            marks = _marks(len(chunk))
            for row in self._q(
                f"SELECT entry_id, source, zh_tw, zh_cn FROM translation "
                f"WHERE entry_id IN ({marks})",
                chunk,
            ):
                found[int(row[0])].append(row[1:])
        return found

    def _duplicate_snapshots(self, ids: list[int]) -> dict[str, Any]:
        snapshots: dict[str, Any] = {}
        for group in self.duplicate_candidates_for_ids(ids):
            group_ids = [entry.entry_id for entry in group.entries]
            snapshots[group.group_id] = self._snapshot_entries(group_ids)
        return snapshots

    def _duplicate_states(self, ids: list[int]) -> dict[int, str]:
        """只納入合併安全性相關欄位，排除可由 trigger 重算的衍生欄位。"""
        states: dict[int, str] = {}
        for entry_id in ids:
            snapshot = self._snapshot_entries([entry_id])
            parts: dict[str, list[list[Any]]] = {}
            for table, data in snapshot.items():
                columns = list(data["columns"])
                drop = {
                    "qflags",
                    "diff",
                    "upd",
                    "mc_version" if table == "effective" else "__never__",
                }
                keep = [
                    index for index, column in enumerate(columns) if column not in drop
                ]
                rows = [[row[index] for index in keep] for row in data["rows"]]
                parts[table] = sorted(rows, key=lambda row: repr(row))
            encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True)
            states[int(entry_id)] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return states

    def duplicate_candidates_for_ids(self, ids: list[int]) -> list[DuplicateGroup]:
        wanted = set(ids)
        groups = self.duplicate_candidates_for_version_ids(ids)
        return [
            group
            for group in groups
            if wanted.issuperset(entry.entry_id for entry in group.entries)
        ]

    def duplicate_candidates_for_version_ids(
        self, ids: list[int]
    ) -> list[DuplicateGroup]:
        if not ids:
            return []
        versions = self._q(
            f"SELECT DISTINCT mc_version FROM entry WHERE id IN ({_marks(len(ids))})",
            ids,
        )
        return [
            group
            for (version,) in versions
            for group in self.duplicate_candidates(version)
        ]

    def _merge_duplicate_entry(self, survivor_id: int, duplicate_id: int) -> None:
        self._merge_translation_rows(survivor_id, duplicate_id)
        self._copy_history_rows(survivor_id, duplicate_id)
        self._copy_source_changes(survivor_id, duplicate_id)
        self._delete_entry_with_source_changes(duplicate_id)

    def _merge_translation_rows(self, survivor_id: int, duplicate_id: int) -> None:
        survivor = self._rows_by_source("translation", survivor_id)
        duplicate = self._rows_by_source("translation", duplicate_id)
        for source, row in duplicate.items():
            current = survivor.get(source)
            if current is None:
                self._insert_child_row("translation", row, survivor_id)
                continue
            self._merge_translation_row(current, row)

    def _merge_translation_row(
        self, current: dict[str, Any], incoming: dict[str, Any]
    ) -> None:
        current_tw = _normal_text(current.get("zh_tw", ""))
        incoming_tw = _normal_text(incoming.get("zh_tw", ""))
        if current_tw and incoming_tw and current_tw != incoming_tw:
            raise DuplicatePlanChanged("翻譯來源衝突，請人工處理後再合併")
        updates: dict[str, Any] = {}
        if not current_tw and incoming_tw:
            updates["zh_tw"] = incoming["zh_tw"]
        if not current.get("zh_cn", "") and incoming.get("zh_cn", ""):
            updates["zh_cn"] = incoming["zh_cn"]
        if (
            incoming.get("review_status") == "reviewed"
            and current.get("review_status") != "reviewed"
        ):
            updates["review_status"] = "reviewed"
            updates["checker"] = incoming.get("checker", "")
        if updates:
            assignments = ", ".join(f"{column}=?" for column in updates)
            self._conn.execute(
                f"UPDATE translation SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*updates.values(), current["id"]),
            )

    def _rows_by_source(self, table: str, entry_id: int) -> dict[int, dict[str, Any]]:
        cursor = self._conn.execute(
            f"SELECT * FROM {table} WHERE entry_id=?", (entry_id,)
        )
        columns = [description[0] for description in cursor.description]
        return {
            int(row[columns.index("source")]): dict(zip(columns, row, strict=True))
            for row in cursor.fetchall()
        }

    def _insert_child_row(self, table: str, row: dict[str, Any], entry_id: int) -> None:
        values = dict(row)
        values["entry_id"] = entry_id
        values.pop("id", None)
        columns = list(values)
        cursor = self._conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({_marks(len(columns))})",
            tuple(values[column] for column in columns),
        )
        if table == "translation" and "revision" in row:
            self._conn.execute(
                "UPDATE translation SET revision=? WHERE id=?",
                (row["revision"], cursor.lastrowid),
            )

    def _copy_history_rows(self, survivor_id: int, duplicate_id: int) -> None:
        cursor = self._conn.execute(
            "SELECT * FROM history WHERE entry_id=?", (duplicate_id,)
        )
        columns = [description[0] for description in cursor.description]
        for row in cursor.fetchall():
            values = dict(zip(columns, row, strict=True))
            values["entry_id"] = survivor_id
            values.pop("id", None)
            names = list(values)
            self._conn.execute(
                f"INSERT INTO history ({','.join(names)}) VALUES ({_marks(len(names))})",
                tuple(values[name] for name in names),
            )

    def _copy_source_changes(self, survivor_id: int, duplicate_id: int) -> None:
        duplicate = self._conn.execute(
            "SELECT kind, mc_version, mod_id, key FROM entry WHERE id=?",
            (duplicate_id,),
        ).fetchone()
        survivor = self._conn.execute(
            "SELECT kind, mc_version, mod_id, key FROM entry WHERE id=?", (survivor_id,)
        ).fetchone()
        if duplicate is None or survivor is None:
            return
        rows = self._conn.execute(
            "SELECT old_en, new_en, detected_at FROM src_change "
            "WHERE kind=? AND mc_version=? AND mod_id=? AND key=?",
            duplicate,
        ).fetchall()
        self._conn.executemany(
            "INSERT OR IGNORE INTO src_change "
            "(kind, mc_version, mod_id, key, old_en, new_en, detected_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [(*survivor, old, new, detected) for old, new, detected in rows],
        )

    def _delete_entry_with_source_changes(self, entry_id: int) -> None:
        identity = self._conn.execute(
            "SELECT kind, mc_version, mod_id, key FROM entry WHERE id=?", (entry_id,)
        ).fetchone()
        if identity is not None:
            self._conn.execute(
                "DELETE FROM src_change WHERE kind=? AND mc_version=? AND mod_id=? AND key=?",
                identity,
            )
        self._conn.execute("DELETE FROM entry WHERE id=?", (entry_id,))

    def _duplicate_identities_reappeared(
        self, identities: list[list[str]], survivor_id: int
    ) -> bool:
        survivor = self._conn.execute(
            "SELECT kind, mc_version, mod_id, key FROM entry WHERE id=?", (survivor_id,)
        ).fetchone()
        return any(
            tuple(identity) != survivor and self._entry_exists(tuple(identity))
            for identity in identities
        )


def _has_translation_conflict(rows: list[tuple[tuple, ...]]) -> bool:
    by_source: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for entry_rows in rows:
        for source, zh_tw, zh_cn in entry_rows:
            if zh_tw or zh_cn:
                by_source[int(source)].add((_normal_text(zh_tw), _normal_text(zh_cn)))
    return any(len(values) > 1 for values in by_source.values())


def _choose_survivor(group: DuplicateGroup) -> DuplicateEntry:
    return max(
        group.entries,
        key=lambda entry: (
            bool(entry.effective_zh_tw.strip()),
            entry.effective_source == 6,
            entry.history_count,
            -entry.entry_id,
        ),
    )
