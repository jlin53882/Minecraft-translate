"""Durable, bounded-by-query storage for rejected Mod DB repair drafts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def store_path(database_path: str | Path) -> Path:
    return Path(f"{Path(database_path)}.repair-review.sqlite3")


def create_run(database_path: str | Path, run_id: str) -> Path:
    path = store_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS repair_review ("
            "run_id TEXT NOT NULL, item_index INTEGER NOT NULL, payload TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending', draft TEXT, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (run_id, item_index))"
        )
        _ensure_group_column(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_review_status "
            "ON repair_review(run_id,status,item_index)"
        )
    return path


_GROUP_FIELDS = (
    "database_identity",
    "database_file_id",
    "version",
    "mod_id",
    "kind",
    "key",
    "en_us",
    "old_translation",
    "ai_translation",
)


def _ensure_group_column(conn: sqlite3.Connection) -> None:
    """Older stores lack ``group_key``; add it (their rows stay ungrouped)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(repair_review)")}
    if "group_key" not in columns:
        conn.execute("ALTER TABLE repair_review ADD COLUMN group_key TEXT")


def group_key(item: dict[str, Any]) -> str | None:
    """Same DB, version, mod, key, source text, old text and AI draft = one review.

    Rows only differ by the source (#2 簡中轉繁, #4 i18n…) that carried the text.
    Returns ``None`` when an identity field is missing, so such rows stay single.
    """
    values = [item.get(field) for field in _GROUP_FIELDS]
    if any(value in (None, "") for value in values[:7]):
        return None
    raw = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def append_item(path: str | Path, run_id: str, item: dict[str, Any]) -> int:
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(path, timeout=10) as conn:
        index = conn.execute(
            "SELECT COALESCE(MAX(item_index),0)+1 FROM repair_review WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        _ensure_group_column(conn)
        conn.execute(
            "INSERT INTO repair_review(run_id,item_index,payload,group_key) "
            "VALUES (?,?,?,?)",
            (run_id, index, payload, group_key(item)),
        )
    return int(index)


def run_count(path: str | Path, run_id: str) -> int:
    with sqlite3.connect(path, timeout=10) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM repair_review WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
        )


def load_item(path: str | Path, run_id: str, index: int) -> dict[str, Any] | None:
    with sqlite3.connect(path, timeout=10) as conn:
        row = conn.execute(
            "SELECT payload,status,draft FROM repair_review "
            "WHERE run_id=? AND item_index=?",
            (run_id, index),
        ).fetchone()
    if row is None:
        return None
    item = json.loads(row[0])
    item["review_status"] = row[1]
    item["draft"] = row[2] if row[2] is not None else item.get("ai_translation", "")
    item["item_index"] = index
    return item


def matching_items(
    path: str | Path,
    run_id: str,
    item: dict[str, Any],
    *,
    statuses: tuple[str, ...] = ("pending", "invalid"),
) -> list[dict[str, Any]]:
    """Return unresolved review items that are the same review group as ``item``.

    Same group = same database, version, mod, key, source text, old translation and
    AI draft (see ``group_key``); a confirmation never reaches rows whose old text or
    draft differs, nor rows without a reliable identity.
    """
    if not statuses:
        return []
    wanted = group_key(item)
    if wanted is None:
        return []

    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for status in statuses:
        for index in indices(path, run_id, status):
            if index in seen:
                continue
            candidate = load_item(path, run_id, index)
            if candidate is None:
                continue
            if group_key(candidate) == wanted:
                matches.append(candidate)
                seen.add(index)
    return matches


def indices(
    path: str | Path,
    run_id: str,
    status: str | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
    grouped: bool = False,
) -> list[int]:
    """Item indices for a status; ``grouped`` keeps one (the first) per identical group."""
    where = "run_id=?"
    params: tuple[Any, ...] = (run_id,)
    if status and status != "all":
        where += " AND status=?"
        params += (status,)
    with sqlite3.connect(path, timeout=10) as conn:
        if grouped:
            _ensure_group_column(conn)
            sql = (
                "SELECT MIN(item_index) AS first FROM repair_review "
                f"WHERE {where} GROUP BY COALESCE(group_key, 'i' || item_index) "
                "ORDER BY first"
            )
        else:
            sql = f"SELECT item_index FROM repair_review WHERE {where} ORDER BY item_index"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += (max(int(limit), 0), max(int(offset), 0))
        return [int(row[0]) for row in conn.execute(sql, params)]


def index_count(
    path: str | Path,
    run_id: str,
    status: str | None = None,
    *,
    grouped: bool = False,
) -> int:
    where = "run_id=?"
    params: tuple[Any, ...] = (run_id,)
    if status and status != "all":
        where += " AND status=?"
        params += (status,)
    with sqlite3.connect(path, timeout=10) as conn:
        if grouped:
            _ensure_group_column(conn)
            sql = (
                "SELECT COUNT(DISTINCT COALESCE(group_key, 'i' || item_index)) "
                f"FROM repair_review WHERE {where}"
            )
        else:
            sql = f"SELECT COUNT(*) FROM repair_review WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])


def status_counts(
    path: str | Path, run_id: str, *, grouped: bool = False
) -> dict[str, int]:
    with sqlite3.connect(path, timeout=10) as conn:
        if grouped:
            _ensure_group_column(conn)
            count = "COUNT(DISTINCT COALESCE(group_key, 'i' || item_index))"
        else:
            count = "COUNT(*)"
        rows = conn.execute(
            f"SELECT status,{count} FROM repair_review WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
    return {str(status): int(n) for status, n in rows}


def group_indices(path: str | Path, run_id: str, index: int) -> list[int]:
    """Every item index that is reviewed together with ``index`` (always includes it)."""
    with sqlite3.connect(path, timeout=10) as conn:
        _ensure_group_column(conn)
        row = conn.execute(
            "SELECT group_key FROM repair_review WHERE run_id=? AND item_index=?",
            (run_id, index),
        ).fetchone()
        if row is None or row[0] is None:
            return [int(index)]
        return [
            int(r[0])
            for r in conn.execute(
                "SELECT item_index FROM repair_review "
                "WHERE run_id=? AND group_key=? ORDER BY item_index",
                (run_id, row[0]),
            )
        ]


def save_review_state(
    path: str | Path, run_id: str, index: int, status: str, draft: str
) -> bool:
    if status not in {"pending", "kept_old", "applied", "stale", "invalid"}:
        raise ValueError("unknown repair review status")
    with sqlite3.connect(path, timeout=10) as conn:
        result = conn.execute(
            "UPDATE repair_review SET status=?,draft=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE run_id=? AND item_index=?",
            (status, draft, run_id, index),
        )
    return result.rowcount == 1
