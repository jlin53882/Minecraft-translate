"""智慧校對歷史分片的 status sidecar 格式（純函式）。

每個候選分片 ``<run_id>.chunks/NNNNNN.json`` 旁邊有一個輕量
``NNNNNN.status``（內容為 JSON，刻意不用 ``.json`` 副檔名，避免被當成候選分片或批次 manifest），只保存查詢狀態所需的欄位（ordinal、entry_id、最新
status、validation_error 摘要），不含原文與 context。它是可由分片重建的衍生資料：
``chunk_revision`` 必須等於 manifest 記錄的該分片 revision 才算最新，否則呼叫端要
忽略它並從分片重建。檔案讀寫與鎖由 ``ChunkManifestRepository`` 負責。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from translation_tool.translation_db.smart_review_history_codec import (
    SCHEMA_VERSION,
    timestamp,
)

SIDECAR_VERSION = 1
SIDECAR_SUFFIX = ".status"
ERROR_SUMMARY_LIMIT = 500

# (ordinal, entry_id, status, validation_error 摘要)
StatusRow = tuple[int, int, str, str | None]


def sidecar_file_name(chunk_index: int) -> str:
    """回傳指定分片的 sidecar 檔名。"""
    return f"{int(chunk_index):06d}{SIDECAR_SUFFIX}"


def is_sidecar_file_name(name: str) -> bool:
    """判斷分片目錄內的檔名是否為 status sidecar。"""
    return name.endswith(SIDECAR_SUFFIX)


def _error_summary(value: object) -> str | None:
    """把 validation_error 縮成固定長度以內的摘要；沒有錯誤時為 None。"""
    if not value:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:ERROR_SUMMARY_LIMIT]


def status_rows_from_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[StatusRow]:
    """由分片候選建立 sidecar 資料列；缺少有效 ordinal 或 entry_id 的候選略過。"""
    rows: list[StatusRow] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        try:
            ordinal = int(candidate["ordinal"])
            entry_id = int(candidate["entry_id"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            (
                ordinal,
                entry_id,
                str(candidate.get("status") or ""),
                _error_summary(candidate.get("validation_error")),
            )
        )
    return rows


def build_sidecar(
    run_id: str,
    chunk_index: int,
    chunk_revision: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    projection_revision: int = 0,
) -> dict[str, Any]:
    """建立一個分片的 sidecar payload。"""
    rows = status_rows_from_candidates(candidates)
    return {
        "schema_version": SCHEMA_VERSION,
        "sidecar_version": SIDECAR_VERSION,
        "run_id": run_id,
        "chunk_index": int(chunk_index),
        "chunk_revision": str(chunk_revision),
        "projection_revision": int(projection_revision),
        "updated_at": timestamp(),
        "candidate_count": len(candidates),
        "rows": [list(row) for row in rows],
    }


def projection_revision_of(manifest: Mapping[str, Any]) -> int:
    """取得 manifest 目前的 ``projection.revision``；舊批次沒有時為 0。"""
    info = manifest.get("projection")
    if isinstance(info, Mapping):
        try:
            return int(info.get("revision") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def rows_if_current(
    payload: Mapping[str, Any],
    *,
    run_id: str,
    chunk_index: int,
    expected_revision: object,
    expected_count: int,
) -> list[StatusRow] | None:
    """sidecar 與 manifest 登記的分片 revision 相符且結構完整時回傳資料列，否則回傳 None。"""
    if payload.get("sidecar_version") != SIDECAR_VERSION:
        return None
    if payload.get("run_id") != run_id or payload.get("chunk_index") != chunk_index:
        return None
    if not expected_revision or payload.get("chunk_revision") != str(expected_revision):
        return None
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return None
    rows: list[StatusRow] = []
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) != 4:
            return None
        ordinal, entry_id, status, error = raw
        if not (isinstance(ordinal, int) and isinstance(entry_id, int)):
            return None
        if not isinstance(status, str) or not (error is None or isinstance(error, str)):
            return None
        rows.append((ordinal, entry_id, status, error))
    if payload.get("candidate_count") != int(expected_count) or len(rows) > int(
        expected_count
    ):
        return None
    return rows
