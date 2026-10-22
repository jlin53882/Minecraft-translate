"""智慧校對批次的 JSON 儲存。

校對候選包含原文快照、本地 context 與模型回應，逐批寫進 SQLite 會讓翻譯
資料庫快速膨脹。這個儲存層以 JSON 保存候選；小批次維持單檔，大批次使用
manifest 加多個 JSON 分片，更新時只原子替換受影響的分片。TranslationDB
仍提供既有方法，讓 UI 與套用流程不必知道檔案格式。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewSnapshot,
)
from translation_tool.utils.config_manager import get_smart_review_settings

_SCHEMA_VERSION = 1
_CHUNKED_STORAGE_FORMAT = "chunked_json_v1"
_HEAD_BYTES = 16_384
_STARTED_AT = re.compile(r'"started_at":\s*"([^"]*)"')
logger = logging.getLogger(__name__)
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PROCESSED_CANDIDATE_STATUSES = frozenset(
    {"ready", "no_change", "needs_context", "invalid", "request_failed"}
)
SMART_REVIEW_CAS_SNAPSHOT_FIELDS = (
    "kind",
    "mc_version",
    "mod_id",
    "entry_key",
    "en_us",
    "current_zh_tw",
    "effective_source",
    "review_status",
    "effective_revision",
    "manual_zh_tw",
    "manual_checker",
    "manual_review_status",
    "manual_revision",
)


def _run_sort_key(
    started_at: object, mtime_ns: int, run_id: object
) -> tuple[str, int, str]:
    """建立歷史清單與最近批次共用的穩定排序鍵。"""
    return str(started_at or ""), int(mtime_ns), str(run_id or "")


def history_directory_for_db(db_path: str | Path) -> Path:
    """回傳指定翻譯資料庫專用的智慧校對 JSON 目錄。"""
    path = Path(db_path).expanduser().resolve()
    return path.parent / f"{path.stem}.smart_review"


def _timestamp() -> str:
    """建立與 SQLite CURRENT_TIMESTAMP 相容的 UTC 顯示時間。"""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _copy_context(value: object) -> list[dict[str, Any]]:
    """把 context 正規化成可安全寫入 JSON 的字典清單。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _process_is_alive(pid: object) -> bool:
    """判斷批次擁有者程序是否仍存在；無法判斷時寧可保留執行中狀態。"""
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        # Windows 對仍存在但無權限查詢的程序可能回報 ERROR_ACCESS_DENIED。
        return getattr(exc, "winerror", None) == 5 or getattr(exc, "errno", None) == 1
    return True


def _candidate_counts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """從已落盤候選狀態重建可恢復的批次摘要計數。"""
    candidates = [
        item for item in payload.get("candidates") or [] if isinstance(item, dict)
    ]
    return _candidate_counts_from_items(candidates)


def _candidate_counts_from_items(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """從候選字典清單建立狀態、錯誤與摘要計數。"""
    statuses = [str(item.get("status") or "pending") for item in candidates]
    status_counts = Counter(statuses)
    error_counts = Counter(
        str(item.get("validation_error"))
        for item in candidates
        if item.get("validation_error")
    )
    return {
        "processed_count": sum(
            status in _PROCESSED_CANDIDATE_STATUSES for status in statuses
        ),
        "valid_count": sum(
            status in {"ready", "no_change", "needs_context"} for status in statuses
        ),
        "invalid_count": statuses.count("invalid"),
        "request_error_count": statuses.count("request_failed"),
        "pending_count": statuses.count("pending"),
        "status_counts": dict(status_counts),
        "error_counts": dict(error_counts),
    }


def _snapshot_payload(run_id: str, item: SmartReviewSnapshot) -> dict[str, Any]:
    """把候選輸入快照轉成 JSON 結構。"""
    candidate = {
        "run_id": run_id,
        "ordinal": int(item.ordinal),
        "entry_id": int(item.entry_id),
        "kind": item.kind,
        "mc_version": item.mc_version,
        "mod_id": item.mod_id,
        "entry_key": item.key,
        "en_us": item.en_us,
        "current_zh_tw": item.current_zh_tw,
        "effective_source": item.effective_source,
        "review_status": item.review_status,
        "effective_revision": item.effective_revision,
        "manual_zh_tw": item.manual_zh_tw,
        "manual_checker": item.manual_checker,
        "manual_review_status": item.manual_review_status,
        "manual_revision": item.manual_revision,
        "twin_entry_ids": [int(value) for value in item.twin_entry_ids],
        "context": _copy_context(item.context),
        "decision": None,
        "suggested_zh_tw": None,
        "issue_types": [],
        "evidence_ids": [],
        "confidence": None,
        "reason": None,
        "response": None,
        "draft_zh_tw": None,
        "validation_error": None,
        "status": "pending",
        "applied_batch": None,
        "applied_at": None,
    }
    candidate["original_snapshot"] = {
        key: candidate[key] for key in SMART_REVIEW_CAS_SNAPSHOT_FIELDS
    }
    return candidate


def _candidate_from_payload(
    run_id: str, payload: dict[str, Any]
) -> SmartReviewCandidate:
    """把 JSON 候選轉回既有的資料模型。"""
    response = payload.get("response")
    reason = payload.get("reason")
    if reason is None and isinstance(response, dict):
        candidate_reason = response.get("reason")
        reason = candidate_reason if isinstance(candidate_reason, str) else None
    issue_types = payload.get("issue_types") or []
    evidence_ids = payload.get("evidence_ids") or []
    return SmartReviewCandidate(
        run_id=run_id,
        ordinal=int(payload.get("ordinal") or 0),
        entry_id=int(payload.get("entry_id") or 0),
        kind=str(payload.get("kind") or ""),
        mc_version=str(payload.get("mc_version") or ""),
        mod_id=str(payload.get("mod_id") or ""),
        key=str(payload.get("entry_key") or ""),
        en_us=str(payload.get("en_us") or ""),
        current_zh_tw=str(payload.get("current_zh_tw") or ""),
        effective_source=(
            None
            if payload.get("effective_source") is None
            else int(payload["effective_source"])
        ),
        review_status=payload.get("review_status"),
        effective_revision=(
            None
            if payload.get("effective_revision") is None
            else int(payload["effective_revision"])
        ),
        context=tuple(_copy_context(payload.get("context"))),
        manual_zh_tw=payload.get("manual_zh_tw"),
        manual_checker=payload.get("manual_checker"),
        manual_review_status=payload.get("manual_review_status"),
        manual_revision=(
            None
            if payload.get("manual_revision") is None
            else int(payload["manual_revision"])
        ),
        decision=payload.get("decision"),
        suggested_zh_tw=payload.get("suggested_zh_tw"),
        issue_types=tuple(str(item) for item in issue_types),
        evidence_ids=tuple(str(item) for item in evidence_ids),
        confidence=(
            None if payload.get("confidence") is None else float(payload["confidence"])
        ),
        reason=reason,
        draft_zh_tw=payload.get("draft_zh_tw"),
        validation_error=payload.get("validation_error"),
        status=str(payload.get("status") or "pending"),
        applied_batch=payload.get("applied_batch"),
        applied_at=payload.get("applied_at"),
    )


class SmartReviewHistoryStore:
    """以 JSON 保存智慧校對執行與候選，並支援大型批次的分片讀寫。

    舊版單檔 JSON 仍可直接讀取；新建立且超過分片大小的批次使用一個
    manifest 加多個候選分片，checkpoint 只替換受影響的分片檔案。
    """

    _locks_guard = threading.Lock()
    _locks: ClassVar[dict[Path, threading.RLock]] = {}
    # 檔案 -> ((mtime, size), started_at)：只存字串，供排序用
    _started_cache: ClassVar[dict[Path, tuple[tuple[int, int], str]]] = {}
    # 資料夾 -> (檔案, (mtime, size), run_id, 條目狀態對照)：每個資料夾只留最近一次批次
    _latest_marks: ClassVar[
        dict[
            Path,
            tuple[Path, tuple[int, int], str, dict[int, tuple[str, str | None]]],
        ]
    ] = {}
    # 已確認損壞的批次檔 -> (mtime, size)，避免每次換頁重複解析
    _corrupt_runs: ClassVar[dict[Path, tuple[int, int]]] = {}
    # 本程序每個歷史目錄只需掃描一次失聯批次；避免每批 JSON 讀寫都重掃整個目錄。
    _recovery_checked: ClassVar[set[Path]] = set()

    def __init__(
        self, db_path: str | Path, *, storage_chunk_size: int | None = None
    ) -> None:
        """初始化此測試替身或服務物件所需的狀態。"""
        self.directory = history_directory_for_db(db_path)
        configured_chunk_size = get_smart_review_settings()["storage_chunk_size"]
        self.storage_chunk_size = max(
            1,
            int(
                configured_chunk_size
                if storage_chunk_size is None
                else storage_chunk_size
            ),
        )
        with self._locks_guard:
            self._lock = self._locks.setdefault(self.directory, threading.RLock())

    @contextmanager
    def locked(self) -> Iterator[None]:
        """在跨越資料庫 CAS 與 JSON 更新時保護同一批次檔案。"""
        with self._lock:
            yield

    def path_for(self, run_id: str) -> Path:
        """取得批次 JSON 路徑並拒絕路徑穿越。"""
        value = str(run_id or "")
        if not _RUN_ID.fullmatch(value):
            raise ValueError("smart review run_id 格式無效")
        return self.directory / f"{value}.json"

    def _chunk_directory_for(self, run_id: str) -> Path:
        """取得指定批次的候選分片目錄。"""
        self.path_for(run_id)
        return self.directory / f"{run_id}.chunks"

    def _chunk_path_for(self, run_id: str, chunk_index: int) -> Path:
        """取得指定候選分片路徑。"""
        if int(chunk_index) < 0:
            raise ValueError("smart review chunk_index 不可為負數")
        return self._chunk_directory_for(run_id) / f"{int(chunk_index):06d}.json"

    @staticmethod
    def timestamp() -> str:
        """回傳與批次 JSON 相容的 UTC 顯示時間。"""
        return _timestamp()

    def exists(self, run_id: str) -> bool:
        """判斷批次是否由 JSON 儲存。"""
        return self.path_for(run_id).is_file()

    @staticmethod
    def _read_json_path(path: Path) -> dict[str, Any]:
        """讀取並驗證一個 JSON object；錯誤訊息包含實際檔案路徑。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as exc:
            raise ValueError(f"無法讀取智慧校對批次 JSON：{path}") from exc
        if not isinstance(payload, dict):
            raise TypeError(f"智慧校對批次 JSON 格式無效：{path}")
        return payload

    def _read_unlocked(self, run_id: str) -> dict[str, Any]:
        """在呼叫端持有 Store 鎖時讀取批次 manifest 或舊版單檔 JSON。"""
        return self._read_json_path(self.path_for(run_id))

    @staticmethod
    def _is_chunked(payload: Mapping[str, Any]) -> bool:
        """判斷 manifest 是否使用候選 JSON 分片格式。"""
        return payload.get("storage_format") == _CHUNKED_STORAGE_FORMAT

    def _write_json_path_unlocked(self, path: Path, payload: dict[str, Any]) -> None:
        """以暫存檔、flush、fsync 與 replace 原子寫入任一 JSON 檔。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_unlocked(self, run_id: str, payload: dict[str, Any]) -> None:
        """在呼叫端持有 Store 鎖時，以原子方式寫入批次 manifest。"""
        path = self.path_for(run_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        payload["checkpoint_at"] = _timestamp()
        self._write_json_path_unlocked(path, payload)

    def _write_chunk_unlocked(
        self, run_id: str, chunk_index: int, payload: dict[str, Any]
    ) -> None:
        """以原子方式寫入單一候選分片。"""
        payload["checkpoint_at"] = _timestamp()
        self._write_json_path_unlocked(
            self._chunk_path_for(run_id, chunk_index), payload
        )

    def _chunk_entries(self, manifest: Mapping[str, Any]) -> list[dict[str, int]]:
        """回傳 manifest 中可定位候選 ordinal 的分片描述。"""
        raw_chunks = manifest.get("chunks")
        if isinstance(raw_chunks, list):
            entries = []
            for raw in raw_chunks:
                if not isinstance(raw, dict):
                    continue
                try:
                    index = int(raw["index"])
                    start = int(raw["ordinal_start"])
                    end = int(raw["ordinal_end"])
                    count = int(raw.get("count") or max(0, end - start + 1))
                except (KeyError, TypeError, ValueError):
                    continue
                if index >= 0 and start > 0 and end >= start:
                    entries.append(
                        {
                            "index": index,
                            "ordinal_start": start,
                            "ordinal_end": end,
                            "count": count,
                        }
                    )
            if entries:
                return sorted(entries, key=lambda item: item["index"])
        chunk_size = max(
            1, int(manifest.get("storage_chunk_size") or self.storage_chunk_size)
        )
        chunk_count = max(0, int(manifest.get("chunk_count") or 0))
        selected_count = max(0, int(manifest.get("selected_count") or 0))
        return [
            {
                "index": index,
                "ordinal_start": index * chunk_size + 1,
                "ordinal_end": min(selected_count, (index + 1) * chunk_size),
                "count": max(
                    0,
                    min(selected_count, (index + 1) * chunk_size)
                    - (index * chunk_size),
                ),
            }
            for index in range(chunk_count)
        ]

    def _chunk_index_for_ordinal(
        self, manifest: Mapping[str, Any], ordinal: int
    ) -> int | None:
        """依 manifest 的 ordinal 範圍找到候選所在分片。"""
        value = int(ordinal)
        for entry in self._chunk_entries(manifest):
            if entry["ordinal_start"] <= value <= entry["ordinal_end"]:
                return entry["index"]
        return None

    def _read_chunk_unlocked(self, run_id: str, chunk_index: int) -> dict[str, Any]:
        """讀取一個候選分片並驗證其 candidates 結構。"""
        path = self._chunk_path_for(run_id, chunk_index)
        payload = self._read_json_path(path)
        if not isinstance(payload.get("candidates"), list):
            raise TypeError(f"智慧校對候選分片格式無效：{path}")
        return payload

    def _iter_chunk_candidates_unlocked(
        self, run_id: str, manifest: Mapping[str, Any]
    ) -> Iterator[dict[str, Any]]:
        """依 ordinal 順序串流遍歷分片中的候選。"""
        for entry in self._chunk_entries(manifest):
            try:
                chunk = self._read_chunk_unlocked(run_id, entry["index"])
            except FileNotFoundError:
                logger.warning(
                    "略過缺少的智慧校對候選分片：run_id=%s，chunk=%s",
                    run_id,
                    entry["index"],
                )
                continue
            for candidate in chunk.get("candidates") or []:
                if isinstance(candidate, dict):
                    yield candidate

    def _candidate_items_unlocked(
        self, run_id: str, payload: Mapping[str, Any]
    ) -> Iterator[dict[str, Any]]:
        """以相同介面遍歷舊版單檔或新版分片候選。"""
        if self._is_chunked(payload):
            yield from self._iter_chunk_candidates_unlocked(run_id, payload)
            return
        for candidate in payload.get("candidates") or []:
            if isinstance(candidate, dict):
                yield candidate

    def _read_candidate_chunk_for_ordinal_unlocked(
        self, run_id: str, manifest: Mapping[str, Any], ordinal: int
    ) -> tuple[int, dict[str, Any]] | None:
        """只讀取指定 ordinal 所在分片，回傳分片與內容。"""
        chunk_index = self._chunk_index_for_ordinal(manifest, ordinal)
        if chunk_index is None:
            return None
        try:
            return chunk_index, self._read_chunk_unlocked(run_id, chunk_index)
        except FileNotFoundError:
            return None

    @staticmethod
    def _candidate_metadata_delta(
        manifest: dict[str, Any],
        old_status: object,
        new_status: object,
        old_error: object,
        new_error: object,
    ) -> None:
        """更新分片 checkpoint 後 manifest 的狀態與錯誤摘要。"""
        old = str(old_status or "pending")
        new = str(new_status or "pending")
        statuses = manifest.setdefault("status_counts", {})
        if not isinstance(statuses, dict):
            statuses = {}
            manifest["status_counts"] = statuses
        if old != new:
            statuses[old] = max(0, int(statuses.get(old, 0)) - 1)
            statuses[new] = int(statuses.get(new, 0)) + 1
        errors = manifest.setdefault("error_counts", {})
        if not isinstance(errors, dict):
            errors = {}
            manifest["error_counts"] = errors
        old_key = str(old_error) if old_error else None
        new_key = str(new_error) if new_error else None
        if old_key != new_key:
            if old_key is not None:
                errors[old_key] = max(0, int(errors.get(old_key, 0)) - 1)
            if new_key is not None:
                errors[new_key] = int(errors.get(new_key, 0)) + 1
        for key in list(statuses):
            if int(statuses[key]) <= 0:
                del statuses[key]
        for key in list(errors):
            if int(errors[key]) <= 0:
                del errors[key]
        manifest.update(
            {
                "processed_count": sum(
                    int(count)
                    for status, count in statuses.items()
                    if status in _PROCESSED_CANDIDATE_STATUSES
                ),
                "valid_count": sum(
                    int(count)
                    for status, count in statuses.items()
                    if status in {"ready", "no_change", "needs_context"}
                ),
                "invalid_count": int(statuses.get("invalid", 0)),
                "request_error_count": int(statuses.get("request_failed", 0)),
            }
        )

    def _chunk_summary_unlocked(
        self, run_id: str, manifest: Mapping[str, Any]
    ) -> dict[str, Any]:
        """掃描分片重建 manifest 摘要；只在 recovery 或明確統計時使用。"""
        candidates = list(self._iter_chunk_candidates_unlocked(run_id, manifest))
        summary = _candidate_counts_from_items(candidates)
        expected = max(0, int(manifest.get("selected_count") or 0))
        missing = max(0, expected - len(candidates))
        if missing:
            status_counts = dict(summary["status_counts"])
            status_counts["pending"] = int(status_counts.get("pending", 0)) + missing
            summary["status_counts"] = status_counts
            summary["pending_count"] = int(summary["pending_count"]) + missing
        return summary

    def _ensure_chunked_summary_unlocked(
        self, run_id: str, manifest: dict[str, Any]
    ) -> None:
        """在舊的分片 manifest 缺少摘要時，先掃描一次分片補齊計數。"""
        if isinstance(manifest.get("status_counts"), dict) and isinstance(
            manifest.get("error_counts"), dict
        ):
            return
        summary = self._chunk_summary_unlocked(run_id, manifest)
        manifest.update(
            {
                "processed_count": summary["processed_count"],
                "valid_count": summary["valid_count"],
                "invalid_count": summary["invalid_count"],
                "request_error_count": summary["request_error_count"],
                "status_counts": summary["status_counts"],
                "error_counts": summary["error_counts"],
            }
        )

    def create_run(
        self,
        snapshots: Sequence[SmartReviewSnapshot],
        *,
        provider: str,
        model: str,
        version: str | None,
        requested_limit: int,
        batch_size: int,
        scope: dict[str, Any] | None = None,
    ) -> str:
        """建立一個新的批次 JSON，不寫入 SQLite。"""
        self.recover_abandoned_runs()
        run_id = uuid.uuid4().hex
        candidates = [_snapshot_payload(run_id, item) for item in snapshots]
        common_payload = {
            "schema_version": _SCHEMA_VERSION,
            "id": run_id,
            "provider": provider,
            "model": model,
            "scope_version": version,
            "requested_limit": int(requested_limit),
            "batch_size": int(batch_size),
            "scope": scope or {},
            "started_at": _timestamp(),
            "finished_at": None,
            "status": "running",
            "owner_pid": os.getpid(),
            "checkpoint_at": _timestamp(),
            "selected_count": len(snapshots),
            "processed_count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "request_error_count": 0,
            "metrics": {},
        }
        if len(candidates) <= self.storage_chunk_size:
            payload = {
                **common_payload,
                "status_counts": {"pending": len(candidates)} if candidates else {},
                "error_counts": {},
                "candidates": candidates,
            }
            with self._lock:
                self._write_unlocked(run_id, payload)
            return run_id

        chunks: list[dict[str, int]] = []
        for index in range(0, len(candidates), self.storage_chunk_size):
            chunk_index = index // self.storage_chunk_size
            chunk_candidates = candidates[index : index + self.storage_chunk_size]
            chunks.append(
                {
                    "index": chunk_index,
                    "ordinal_start": int(chunk_candidates[0]["ordinal"]),
                    "ordinal_end": int(chunk_candidates[-1]["ordinal"]),
                    "count": len(chunk_candidates),
                }
            )
        manifest = {
            **common_payload,
            "storage_format": _CHUNKED_STORAGE_FORMAT,
            "storage_chunk_size": self.storage_chunk_size,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "status_counts": {"pending": len(candidates)} if candidates else {},
            "error_counts": {},
        }
        with self._lock:
            # 先建立 manifest，再逐個原子建立分片；意外退出時 recovery
            # 可以辨識已建立的批次，尚未建立的候選仍會保留為 pending。
            self._write_unlocked(run_id, manifest)
            for chunk in chunks:
                chunk_index = chunk["index"]
                start = chunk_index * self.storage_chunk_size
                chunk_payload = {
                    "schema_version": _SCHEMA_VERSION,
                    "storage_format": _CHUNKED_STORAGE_FORMAT,
                    "run_id": run_id,
                    "chunk_index": chunk_index,
                    "ordinal_start": chunk["ordinal_start"],
                    "ordinal_end": chunk["ordinal_end"],
                    "candidates": candidates[start : start + self.storage_chunk_size],
                }
                self._write_chunk_unlocked(run_id, chunk_index, chunk_payload)
        return run_id

    def recover_abandoned_runs(self, *, force: bool = False) -> list[str]:
        """把上一個已死亡程序留下的 ``running`` 批次收斂為 ``interrupted``。

        批次候選本身採原子 checkpoint，因此已完成的批次結果會保留；尚未
        寫入的候選仍維持 ``pending``，可在歷史批次中辨識為未處理。沒有
        ``owner_pid`` 的舊版 JSON 不自動改寫，避免誤判仍在執行的舊批次。
        """
        if not self.directory.is_dir():
            return []
        with self._lock:
            if not force and self.directory in self._recovery_checked:
                return []
            self._recovery_checked.add(self.directory)
            recovered: list[str] = []
            for path in self.directory.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(payload, dict) or payload.get("status") != "running":
                    continue
                owner_pid = payload.get("owner_pid")
                if owner_pid is None or _process_is_alive(owner_pid):
                    continue
                run_id = path.stem
                try:
                    self.path_for(run_id)
                except ValueError:
                    logger.warning("略過無效檔名的智慧校對批次檔：%s", path)
                    continue
                payload_run_id = payload.get("id")
                if payload_run_id not in (None, run_id):
                    logger.warning("略過檔名與內容 id 不一致的智慧校對批次檔：%s", path)
                    continue
                counts = (
                    self._chunk_summary_unlocked(run_id, payload)
                    if self._is_chunked(payload)
                    else _candidate_counts(payload)
                )
                recovered_at = _timestamp()
                raw_metrics = payload.get("metrics")
                metrics = dict(raw_metrics) if isinstance(raw_metrics, dict) else {}
                metrics.update(
                    {
                        "recovered_after_unexpected_exit": True,
                        "recovery_reason": "owner_process_not_alive",
                        "recovered_at": recovered_at,
                        "interrupted_pending_count": counts["pending_count"],
                    }
                )
                payload.update(
                    {
                        "finished_at": recovered_at,
                        "status": "interrupted",
                        "processed_count": counts["processed_count"],
                        "valid_count": counts["valid_count"],
                        "invalid_count": counts["invalid_count"],
                        "request_error_count": counts["request_error_count"],
                        "status_counts": counts["status_counts"],
                        "error_counts": counts["error_counts"],
                        "metrics": metrics,
                        "recovery_reason": "owner_process_not_alive",
                        "recovered_at": recovered_at,
                    }
                )
                self._write_unlocked(run_id, payload)
                recovered.append(run_id)
                logger.warning(
                    "已將非預期退出的智慧校對批次標記為 interrupted：run_id=%s，"
                    "已處理 %s 筆，未處理 %s 筆",
                    run_id,
                    counts["processed_count"],
                    counts["pending_count"],
                )
            return recovered

    def _candidate_unlocked(
        self, payload: dict[str, Any], ordinal: int
    ) -> dict[str, Any] | None:
        """在已讀取的批次 payload 中依 ordinal 找到對應候選。"""
        for candidate in payload.get("candidates") or []:
            if (
                isinstance(candidate, dict)
                and int(candidate.get("ordinal") or 0) == ordinal
            ):
                return candidate
        return None

    @staticmethod
    def _apply_result_to_candidate(
        candidate: dict[str, Any], result: Mapping[str, Any]
    ) -> tuple[str, str, object, object]:
        """把一筆已解析結果套用到候選，回傳狀態與錯誤的前後值。"""
        old_status = str(candidate.get("status") or "pending")
        old_error = candidate.get("validation_error")
        if result.get("validation_error"):
            candidate["status"] = "invalid"
            candidate["validation_error"] = str(result["validation_error"])
        else:
            candidate.update(
                {
                    "decision": result["decision"],
                    "suggested_zh_tw": result["suggested_zh_tw"],
                    "issue_types": list(result["issue_types"]),
                    "evidence_ids": list(result["evidence_ids"]),
                    "confidence": float(result["confidence"]),
                    "reason": result.get("reason"),
                    "response": dict(result),
                    "validation_error": None,
                    "status": _candidate_status(dict(result)),
                }
            )
        return (
            old_status,
            str(candidate.get("status") or "pending"),
            old_error,
            candidate.get("validation_error"),
        )

    def _group_results_by_chunk(
        self,
        run_id: str,
        manifest: Mapping[str, Any],
        results: Sequence[Mapping[str, Any]],
    ) -> dict[int, list[Mapping[str, Any]]]:
        """依 ordinal 將結果分組，避免一批結果重複讀取同一分片。"""
        grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for result in results:
            ordinal = int(result["id"])
            chunk_index = self._chunk_index_for_ordinal(manifest, ordinal)
            if chunk_index is None:
                raise ValueError("smart review response id 不在候選快照")
            grouped[chunk_index].append(result)
        return grouped

    def _apply_status_delta(
        self,
        manifest: dict[str, Any],
        old_status: object,
        new_status: object,
        old_error: object,
        new_error: object,
    ) -> None:
        """把一筆候選狀態異動套用到分片 manifest 摘要。"""
        self._candidate_metadata_delta(
            manifest, old_status, new_status, old_error, new_error
        )

    def record_results(self, run_id: str, results: Sequence[dict[str, Any]]) -> None:
        """把一批模型結果寫回 JSON 候選。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            if self._is_chunked(payload):
                self._ensure_chunked_summary_unlocked(run_id, payload)
                grouped = self._group_results_by_chunk(run_id, payload, results)
                loaded: dict[int, dict[str, Any]] = {}
                candidates_by_chunk: dict[int, dict[int, dict[str, Any]]] = {}
                for chunk_index in grouped:
                    chunk = self._read_chunk_unlocked(run_id, chunk_index)
                    loaded[chunk_index] = chunk
                    candidates_by_chunk[chunk_index] = {
                        int(item.get("ordinal") or 0): item
                        for item in chunk.get("candidates") or []
                        if isinstance(item, dict)
                    }
                for chunk_index, chunk_results in grouped.items():
                    candidates = candidates_by_chunk[chunk_index]
                    for result in chunk_results:
                        ordinal = int(result["id"])
                        candidate = candidates.get(ordinal)
                        if candidate is None:
                            raise ValueError("smart review response id 不在候選快照")
                        old_status, new_status, old_error, new_error = (
                            self._apply_result_to_candidate(candidate, result)
                        )
                        self._apply_status_delta(
                            payload,
                            old_status,
                            new_status,
                            old_error,
                            new_error,
                        )
                    self._write_chunk_unlocked(run_id, chunk_index, loaded[chunk_index])
                self._write_unlocked(run_id, payload)
                return
            for result in results:
                ordinal = int(result["id"])
                candidate = self._candidate_unlocked(payload, ordinal)
                if candidate is None:
                    raise ValueError("smart review response id 不在候選快照")
                self._apply_result_to_candidate(candidate, result)
            payload.update(_candidate_counts(payload))
            self._write_unlocked(run_id, payload)

    def mark_request_error(
        self, run_id: str, ordinals: Sequence[int], *, error_code: str
    ) -> None:
        """把請求失敗寫入 JSON 候選。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            wanted = {int(ordinal) for ordinal in ordinals}
            if self._is_chunked(payload):
                self._ensure_chunked_summary_unlocked(run_id, payload)
                grouped: dict[int, set[int]] = defaultdict(set)
                for ordinal in wanted:
                    chunk_index = self._chunk_index_for_ordinal(payload, ordinal)
                    if chunk_index is not None:
                        grouped[chunk_index].add(ordinal)
                for chunk_index, chunk_ordinals in grouped.items():
                    chunk = self._read_chunk_unlocked(run_id, chunk_index)
                    for candidate in chunk.get("candidates") or []:
                        if not isinstance(candidate, dict):
                            continue
                        if int(candidate.get("ordinal") or 0) not in chunk_ordinals:
                            continue
                        old_status = candidate.get("status")
                        old_error = candidate.get("validation_error")
                        candidate["status"] = "request_failed"
                        candidate["validation_error"] = error_code
                        self._apply_status_delta(
                            payload,
                            old_status,
                            "request_failed",
                            old_error,
                            error_code,
                        )
                    self._write_chunk_unlocked(run_id, chunk_index, chunk)
                self._write_unlocked(run_id, payload)
                return
            for candidate in payload.get("candidates") or []:
                if (
                    isinstance(candidate, dict)
                    and int(candidate.get("ordinal") or 0) in wanted
                ):
                    candidate["status"] = "request_failed"
                    candidate["validation_error"] = error_code
            payload.update(_candidate_counts(payload))
            self._write_unlocked(run_id, payload)

    def mark_pending_cancelled(self, run_id: str) -> int:
        """標記尚未送出的候選為取消。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            if self._is_chunked(payload):
                self._ensure_chunked_summary_unlocked(run_id, payload)
                updated = 0
                for entry in self._chunk_entries(payload):
                    chunk = self._read_chunk_unlocked(run_id, entry["index"])
                    chunk_updated = False
                    for candidate in chunk.get("candidates") or []:
                        if not isinstance(candidate, dict):
                            continue
                        if candidate.get("status") != "pending":
                            continue
                        old_error = candidate.get("validation_error")
                        candidate["status"] = "cancelled"
                        candidate["validation_error"] = "cancelled_before_request"
                        self._apply_status_delta(
                            payload,
                            "pending",
                            "cancelled",
                            old_error,
                            "cancelled_before_request",
                        )
                        updated += 1
                        chunk_updated = True
                    if chunk_updated:
                        self._write_chunk_unlocked(run_id, entry["index"], chunk)
                if updated:
                    self._write_unlocked(run_id, payload)
                return updated
            updated = 0
            for candidate in payload.get("candidates") or []:
                if isinstance(candidate, dict) and candidate.get("status") == "pending":
                    candidate["status"] = "cancelled"
                    candidate["validation_error"] = "cancelled_before_request"
                    updated += 1
            if updated:
                payload.update(_candidate_counts(payload))
                self._write_unlocked(run_id, payload)
            return updated

    def finish_run(
        self,
        run_id: str,
        *,
        processed_count: int,
        valid_count: int,
        invalid_count: int,
        request_error_count: int,
        metrics: dict[str, Any] | None,
        status: str,
    ) -> None:
        """寫入批次結束狀態與統計。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            payload.update(
                {
                    "finished_at": _timestamp(),
                    "status": status,
                    "processed_count": int(processed_count),
                    "valid_count": int(valid_count),
                    "invalid_count": int(invalid_count),
                    "request_error_count": int(request_error_count),
                    "metrics": metrics or {},
                }
            )
            self._write_unlocked(run_id, payload)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """讀取單一批次摘要，不把候選全部放進歷史卡片。"""
        self.recover_abandoned_runs()
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return None
        return {key: value for key, value in payload.items() if key != "candidates"}

    def all_runs(self, *, include_sort_metadata: bool = False) -> list[dict[str, Any]]:
        """讀取所有可解析的 JSON 批次，依新到舊排序。

        ``include_sort_metadata`` 僅供 SQLite 與 JSON 歷史合併排序使用；一般
        呼叫維持原本的摘要格式，不把檔案 mtime 暴露給 UI 或其他呼叫端。
        """
        self.recover_abandoned_runs()
        if not self.directory.is_dir():
            return []
        runs: list[tuple[dict[str, Any], tuple[str, int, str]]] = []
        with self._lock:
            for path in self.directory.glob("*.json"):
                try:
                    mtime_ns = path.stat().st_mtime_ns
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(payload, dict) or not payload.get("id"):
                    continue
                summary = {
                    key: value for key, value in payload.items() if key != "candidates"
                }
                if include_sort_metadata:
                    summary["_history_mtime_ns"] = mtime_ns
                runs.append(
                    (
                        summary,
                        _run_sort_key(
                            payload.get("started_at"),
                            mtime_ns,
                            payload.get("id"),
                        ),
                    )
                )
        return [
            item
            for item, _key in sorted(runs, key=lambda value: value[1], reverse=True)
        ]

    def latest_run_marks(self) -> tuple[str | None, dict[int, tuple[str, str | None]]]:
        """回傳最近一次批次的 ``entry_id -> (狀態, 驗證錯誤)``，供條目列顯示狀態。

        先以檔案開頭的 ``started_at`` 排序（每個檔案只 stat 並讀檔頭，結果以
        (mtime, size) 快取），只完整解析勝出的那一個批次；候選狀態對照每個資料夾
        只保留這一份，歷史批次不會常駐記憶體。損壞的批次會略過並記錄警告。
        """
        self.recover_abandoned_runs()
        if not self.directory.is_dir():
            return None, {}
        with self._lock:
            for _key, path, stamp in self._ranked_run_files():
                latest = self._latest_marks.get(self.directory)
                if latest is not None and latest[:2] == (path, stamp):
                    return latest[2], latest[3]
                if self._corrupt_runs.get(path) == stamp:
                    continue
                parsed = self._parse_marks(path)
                if parsed is None:
                    self._corrupt_runs[path] = stamp
                    continue
                self._latest_marks[self.directory] = (path, stamp, *parsed)
                return parsed
        return None, {}

    def _ranked_run_files(
        self,
    ) -> list[tuple[tuple[str, int, str], Path, tuple[int, int]]]:
        """依 (started_at, mtime, run_id) 由新到舊列出批次檔；不解析候選內容。"""
        ranked = []
        live: set[Path] = set()
        for path in self.directory.glob("*.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            live.add(path)
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self._started_cache.get(path)
            if cached is None or cached[0] != stamp:
                cached = (stamp, self._read_started_at(path))
                self._started_cache[path] = cached
            ranked.append((_run_sort_key(cached[1], stamp[0], path.stem), path, stamp))
        for table in (self._started_cache, self._corrupt_runs):
            for gone in [
                p for p in table if p.parent == self.directory and p not in live
            ]:
                del table[gone]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked

    @staticmethod
    def _read_started_at(path: Path) -> str:
        """只讀檔頭取得 ``started_at``；檔頭沒有時才完整解析，失敗回傳空字串（排在最後）。"""
        try:
            with path.open("rb") as handle:
                head = handle.read(_HEAD_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            return ""
        found = _STARTED_AT.search(head)
        if found:
            return found.group(1)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return str(payload.get("started_at") or "") if isinstance(payload, dict) else ""

    def _parse_marks(
        self,
        path: Path,
    ) -> tuple[str, dict[int, tuple[str, str | None]]] | None:
        """完整解析一個批次檔，回傳 (run_id, 條目狀態對照)；結構不符時記錄警告並回傳 None。"""
        try:
            payload = self._read_json_path(path)
        except (OSError, ValueError):
            logger.warning("略過無法讀取的智慧校對批次檔：%s", path)
            return None
        marks: dict[int, tuple[str, str | None]] = {}
        if self._is_chunked(payload):
            candidates = self._candidate_items_unlocked(path.stem, payload)
        else:
            candidates = payload.get("candidates")
            if not isinstance(candidates, list):
                logger.warning(
                    "略過格式不符（candidates 不是 list）的智慧校對批次檔：%s",
                    path,
                )
                return None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            try:
                entry_id = int(candidate["entry_id"])
            except (KeyError, TypeError, ValueError):
                continue
            marks[entry_id] = (
                str(candidate.get("status") or ""),
                candidate.get("validation_error"),
            )
        return str(payload.get("id") or path.stem), marks

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """分頁讀取 JSON 批次摘要。"""
        bounded_limit = max(1, min(int(limit), 100))
        return self.all_runs()[
            max(0, int(offset)) : max(0, int(offset)) + bounded_limit
        ]

    def list_candidates(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SmartReviewCandidate]:
        """分頁讀取 JSON 批次候選。"""
        _total, selected = self.page_candidates(
            run_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return selected

    def page_candidates(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        known_total: int | None = None,
        clamp_offset: bool = False,
    ) -> tuple[int, list[SmartReviewCandidate]]:
        """取得總數與目前頁面的候選；分片批次只讀必要的 JSON 分片。"""
        self.recover_abandoned_runs()
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return 0, []
            if self._is_chunked(payload):
                status_counts = payload.get("status_counts")
                if status is None:
                    total = (
                        int(known_total)
                        if known_total is not None
                        else int(payload.get("selected_count") or 0)
                    )
                elif known_total is not None:
                    total = max(0, int(known_total))
                elif isinstance(status_counts, dict):
                    total = max(0, int(status_counts.get(status, 0)))
                else:
                    total = 0
                start = max(0, int(offset))
                if clamp_offset and total:
                    start = min(start, total - 1)
                wanted = None if limit is None else max(0, int(limit))
                selected: list[dict[str, Any]] = []
                matched = 0
                for entry in self._chunk_entries(payload):
                    if wanted is not None and len(selected) >= wanted:
                        break
                    chunk = self._read_chunk_unlocked(run_id, entry["index"])
                    for item in chunk.get("candidates") or []:
                        if not isinstance(item, dict):
                            continue
                        if status is not None and item.get("status") != status:
                            continue
                        if matched < start:
                            matched += 1
                            continue
                        selected.append(item)
                        matched += 1
                        if wanted is not None and len(selected) >= wanted:
                            break
                if status is not None and not isinstance(status_counts, dict):
                    total = matched
                return total, [
                    _candidate_from_payload(run_id, item) for item in selected
                ]
            candidates = [
                item
                for item in payload.get("candidates") or []
                if isinstance(item, dict)
                and (status is None or item.get("status") == status)
            ]
        candidates.sort(key=lambda item: int(item.get("ordinal") or 0))
        total = len(candidates) if known_total is None else max(0, int(known_total))
        start = max(0, int(offset))
        if clamp_offset and total:
            start = min(start, total - 1)
        selected = (
            candidates[start:]
            if limit is None
            else candidates[start : start + max(0, int(limit))]
        )
        return total, [_candidate_from_payload(run_id, item) for item in selected]

    def count_candidates(self, run_id: str, *, status: str | None = None) -> int:
        """計算 JSON 批次候選數量。"""
        self.recover_abandoned_runs()
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return 0
            if self._is_chunked(payload):
                status_counts = payload.get("status_counts")
                if isinstance(status_counts, dict):
                    if status is None:
                        return int(payload.get("selected_count") or 0)
                    return int(status_counts.get(status, 0))
                return sum(
                    1
                    for item in self._candidate_items_unlocked(run_id, payload)
                    if status is None or item.get("status") == status
                )
            return sum(
                1
                for item in payload.get("candidates") or []
                if isinstance(item, dict)
                and (status is None or item.get("status") == status)
            )

    def breakdown(self, run_id: str, *, error_limit: int = 5) -> dict[str, Any]:
        """統計 JSON 批次狀態與驗證錯誤。"""
        self.recover_abandoned_runs()
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return {"status": {}, "errors": []}
            if self._is_chunked(payload):
                status_counts = payload.get("status_counts")
                error_counts = payload.get("error_counts")
                if isinstance(status_counts, dict) and isinstance(error_counts, dict):
                    return {
                        "status": {
                            str(status): int(count)
                            for status, count in status_counts.items()
                        },
                        "errors": sorted(
                            (
                                (str(message), int(count))
                                for message, count in error_counts.items()
                            ),
                            key=lambda item: (-item[1], item[0]),
                        )[: max(0, int(error_limit))],
                    }
            candidates = [
                item for item in self._candidate_items_unlocked(run_id, payload)
            ]
        status_counts = Counter(str(item.get("status") or "") for item in candidates)
        errors = Counter(
            str(item.get("validation_error"))
            for item in candidates
            if item.get("validation_error")
        )
        return {
            "status": dict(status_counts),
            "errors": sorted(errors.items(), key=lambda item: (-item[1], item[0]))[
                : max(0, int(error_limit))
            ],
        }

    def get_candidate(self, run_id: str, ordinal: int) -> dict[str, Any] | None:
        """讀取單一 JSON 候選的可變快照。"""
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return None
            if self._is_chunked(payload):
                located = self._read_candidate_chunk_for_ordinal_unlocked(
                    run_id, payload, int(ordinal)
                )
                if located is None:
                    return None
                _chunk_index, chunk = located
                candidate = next(
                    (
                        item
                        for item in chunk.get("candidates") or []
                        if isinstance(item, dict)
                        and int(item.get("ordinal") or 0) == int(ordinal)
                    ),
                    None,
                )
                return dict(candidate) if candidate is not None else None
            candidate = self._candidate_unlocked(payload, int(ordinal))
            return dict(candidate) if candidate is not None else None

    def update_candidate(self, run_id: str, ordinal: int, **updates: Any) -> bool:
        """原子更新 JSON 候選欄位。"""
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return False
            if self._is_chunked(payload):
                self._ensure_chunked_summary_unlocked(run_id, payload)
                located = self._read_candidate_chunk_for_ordinal_unlocked(
                    run_id, payload, int(ordinal)
                )
                if located is None:
                    return False
                chunk_index, chunk = located
                candidate = next(
                    (
                        item
                        for item in chunk.get("candidates") or []
                        if isinstance(item, dict)
                        and int(item.get("ordinal") or 0) == int(ordinal)
                    ),
                    None,
                )
                if candidate is None:
                    return False
                old_status = candidate.get("status")
                old_error = candidate.get("validation_error")
                candidate.update(updates)
                self._apply_status_delta(
                    payload,
                    old_status,
                    candidate.get("status"),
                    old_error,
                    candidate.get("validation_error"),
                )
                self._write_chunk_unlocked(run_id, chunk_index, chunk)
                self._write_unlocked(run_id, payload)
                return True
            candidate = self._candidate_unlocked(payload, int(ordinal))
            if candidate is None:
                return False
            candidate.update(updates)
            self._write_unlocked(run_id, payload)
            return True

    def reconcile_applied(
        self, run_id: str, records: Mapping[int, Mapping[str, Any]]
    ) -> int:
        """依 SQLite 套用歷史，補回尚未標記為 applied（或指向舊套用）的 JSON 候選。

        SQLite 翻譯與 JSON 歷史分屬兩個儲存邊界；若前者已提交、後者在
        原子替換時遇到 I/O 錯誤，下一次讀取可以用同一個 run／ordinal 的
        SQLite audit marker 對帳，而不會再次把候選當成 ready 套用。
        """
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return 0
            if self._is_chunked(payload):
                self._ensure_chunked_summary_unlocked(run_id, payload)
                updated = 0
                for entry in self._chunk_entries(payload):
                    chunk = self._read_chunk_unlocked(run_id, entry["index"])
                    chunk_updated = False
                    for candidate in chunk.get("candidates") or []:
                        if not isinstance(candidate, dict):
                            continue
                        ordinal = int(candidate.get("ordinal") or 0)
                        record = records.get(ordinal)
                        if record is None or int(record.get("entry_id") or 0) != int(
                            candidate.get("entry_id") or 0
                        ):
                            continue
                        batch = str(record.get("batch") or "") or None
                        if candidate.get("status") == "applied" and (
                            candidate.get("applied_batch") == batch
                        ):
                            continue
                        old_status = candidate.get("status")
                        old_error = candidate.get("validation_error")
                        candidate.update(
                            {
                                "status": "applied",
                                "applied_batch": batch,
                                "applied_at": record.get("applied_at"),
                            }
                        )
                        self._apply_status_delta(
                            payload,
                            old_status,
                            "applied",
                            old_error,
                            candidate.get("validation_error"),
                        )
                        updated += 1
                        chunk_updated = True
                    if chunk_updated:
                        self._write_chunk_unlocked(run_id, entry["index"], chunk)
                if updated:
                    self._write_unlocked(run_id, payload)
                return updated
            updated = 0
            for candidate in payload.get("candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                ordinal = int(candidate.get("ordinal") or 0)
                record = records.get(ordinal)
                if record is None or int(record.get("entry_id") or 0) != int(
                    candidate.get("entry_id") or 0
                ):
                    continue
                batch = str(record.get("batch") or "") or None
                # 已標成 applied 且指向同一次套用的不用改；其餘（待審查，或人工編輯後
                # 狀態回寫失敗的各種狀態）以 SQLite 的最新套用紀錄為準補回。
                if candidate.get("status") == "applied" and (
                    candidate.get("applied_batch") == batch
                ):
                    continue
                candidate.update(
                    {
                        "status": "applied",
                        "applied_batch": batch,
                        "applied_at": record.get("applied_at"),
                    }
                )
                updated += 1
            if updated:
                self._write_unlocked(run_id, payload)
            return updated


def _candidate_status(result: dict[str, Any]) -> str:
    """從已驗證模型結果推導候選狀態。"""
    if result.get("validation_error"):
        return "invalid"
    return {
        "no_change": "no_change",
        "suggest_revision": "ready",
        "needs_context": "needs_context",
    }[result["decision"]]


__all__ = ["SmartReviewHistoryStore", "history_directory_for_db"]
