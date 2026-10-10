"""Durable, bounded-by-query storage for rejected Mod DB repair drafts."""

from __future__ import annotations

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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_review_status "
            "ON repair_review(run_id,status,item_index)"
        )
    return path


def append_item(path: str | Path, run_id: str, item: dict[str, Any]) -> int:
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(path, timeout=10) as conn:
        index = conn.execute(
            "SELECT COALESCE(MAX(item_index),0)+1 FROM repair_review WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO repair_review(run_id,item_index,payload) VALUES (?,?,?)",
            (run_id, index, payload),
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


def indices(
    path: str | Path,
    run_id: str,
    status: str | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[int]:
    sql = "SELECT item_index FROM repair_review WHERE run_id=?"
    params: tuple[Any, ...] = (run_id,)
    if status and status != "all":
        sql += " AND status=?"
        params += (status,)
    sql += " ORDER BY item_index"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += (max(int(limit), 0), max(int(offset), 0))
    with sqlite3.connect(path, timeout=10) as conn:
        return [int(row[0]) for row in conn.execute(sql, params)]


def index_count(path: str | Path, run_id: str, status: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM repair_review WHERE run_id=?"
    params: tuple[Any, ...] = (run_id,)
    if status and status != "all":
        sql += " AND status=?"
        params += (status,)
    with sqlite3.connect(path, timeout=10) as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def status_counts(path: str | Path, run_id: str) -> dict[str, int]:
    with sqlite3.connect(path, timeout=10) as conn:
        rows = conn.execute(
            "SELECT status,COUNT(*) FROM repair_review WHERE run_id=? GROUP BY status",
            (run_id,),
        )
    return {str(status): int(count) for status, count in rows}


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
