"""智慧校對批次的 JSON 儲存。

校對候選包含原文快照、本地 context 與模型回應，逐批寫進 SQLite 會讓翻譯
資料庫快速膨脹。這個儲存層以 JSON 保存候選；小批次維持單檔，大批次使用
manifest 加多個 JSON 分片，更新時只原子替換受影響的分片。TranslationDB
仍提供既有方法，讓 UI 與套用流程不必知道檔案格式。

``SmartReviewHistoryStore`` 是對外門面，負責批次生命週期與候選寫入交易；實際
職責分在同目錄的模組：

- ``smart_review_history_lock``：程序內鎖與跨程序檔案鎖（``HistoryLock``）。
- ``smart_review_history_codec``：候選／批次 JSON 結構、摘要計算（純函式）。
- ``smart_review_history_manifest``：manifest 與分片檔案存取（``ChunkManifestRepository``）。
- ``smart_review_history_recovery``：失聯批次復原（``HistoryRecovery``）。
- ``smart_review_history_query``：分頁、計數與最近標籤快取（``HistoryQuery``）。
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, ClassVar

from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.smart_review_history_codec import (
    CHUNKED_STORAGE_FORMAT,
    SCHEMA_VERSION,
    SMART_REVIEW_CAS_SNAPSHOT_FIELDS,
    apply_result_delta,
    apply_result_to_candidate,
    candidate_ai_status,
    candidate_counts,
    candidate_metadata_delta,
    find_candidate,
    is_chunked,
    snapshot_payload,
    timestamp,
)
from translation_tool.translation_db.smart_review_history_lock import HistoryLock
from translation_tool.translation_db.smart_review_history_manifest import (
    ChunkManifestRepository,
)
from translation_tool.translation_db.smart_review_history_query import HistoryQuery
from translation_tool.translation_db.smart_review_history_recovery import (
    HistoryRecovery,
)
from translation_tool.translation_db.smart_review_status import SmartReviewStatus
from translation_tool.utils.config_manager import get_smart_review_settings


def history_directory_for_db(db_path: str | Path) -> Path:
    """回傳指定翻譯資料庫專用的智慧校對 JSON 目錄。"""
    path = Path(db_path).expanduser().resolve()
    return path.parent / f"{path.stem}.smart_review"


class SmartReviewHistoryStore:
    """以 JSON 保存智慧校對執行與候選，並支援大型批次的分片讀寫。

    舊版單檔 JSON 仍可直接讀取；新建立且超過分片大小的批次使用一個
    manifest 加多個候選分片，checkpoint 只替換受影響的分片檔案。
    """

    # 以下類別層級狀態由各職責模組持有，這裡指向同一個物件，讓測試與
    # clear 流程可以從 Store 直接存取。
    _locks_guard = HistoryLock.guard
    _locks: ClassVar[dict[Path, Any]] = HistoryLock.process_locks
    _interprocess_state: ClassVar[Any] = HistoryLock.interprocess_state
    _started_cache = HistoryQuery.started_cache
    _latest_marks = HistoryQuery.latest_marks
    _corrupt_runs = HistoryQuery.corrupt_runs
    _recovery_checked = HistoryRecovery.checked

    @classmethod
    def clear_caches(cls, directory: str | Path | None = None) -> None:
        """清除行程內快取；指定 ``directory`` 時只清該歷史目錄，不影響磁碟資料。

        切換大量資料庫、測試隔離或外部程序改寫歷史檔後呼叫，避免舊 stamp 殘留。
        """
        target = Path(directory) if directory is not None else None

        def _under(path: Path) -> bool:
            return target is None or path == target or target in path.parents

        with cls._locks_guard:
            for cache in (cls._started_cache, cls._latest_marks, cls._corrupt_runs):
                for key in [key for key in cache if _under(key)]:
                    del cache[key]
            cls._recovery_checked.difference_update(
                [path for path in cls._recovery_checked if _under(path)]
            )

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
        self._history_lock = HistoryLock(self.directory)
        self._lock = self._history_lock.process_lock
        self.repository = ChunkManifestRepository(
            self.directory, self.storage_chunk_size
        )
        self._recovery = HistoryRecovery(
            self.directory, self._history_lock, self.repository
        )
        self._query = HistoryQuery(
            self.directory, self._history_lock, self.repository, self._recovery
        )

    @contextmanager
    def locked(self) -> Iterator[None]:
        """在跨程序保護下執行候選狀態檢查、CAS 與 JSON 更新。"""
        with self._write_lock():
            yield

    def _write_lock(self) -> AbstractContextManager[None]:
        """同時鎖定程序內與跨程序寫入，保護完整 read-modify-write 交易。"""
        return self._history_lock.write()

    def path_for(self, run_id: str) -> Path:
        """取得批次 JSON 路徑並拒絕路徑穿越。"""
        return self.repository.path_for(run_id)

    @staticmethod
    def timestamp() -> str:
        """回傳與批次 JSON 相容的 UTC 顯示時間。"""
        return timestamp()

    def exists(self, run_id: str) -> bool:
        """判斷批次是否由 JSON 儲存。"""
        return self.repository.exists(run_id)

    def recover_abandoned_runs(self, *, force: bool = False) -> list[str]:
        """把上一個已死亡程序留下的 ``running`` 批次收斂為 ``interrupted``。"""
        return self._recovery.recover_abandoned_runs(force=force)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """讀取單一批次摘要，不把候選全部放進歷史卡片。"""
        return self._query.get_run(run_id)

    def all_runs(self, *, include_sort_metadata: bool = False) -> list[dict[str, Any]]:
        """讀取所有可解析的 JSON 批次，依新到舊排序。

        ``include_sort_metadata`` 僅供 SQLite 與 JSON 歷史合併排序使用；一般
        呼叫維持原本的摘要格式，不把檔案 mtime 暴露給 UI 或其他呼叫端。
        """
        return self._query.all_runs(include_sort_metadata=include_sort_metadata)

    def latest_run_marks(self) -> tuple[str | None, dict[int, tuple[str, str | None]]]:
        """回傳最近一次批次的 ``entry_id -> (狀態, 驗證錯誤)``，供條目列顯示狀態。"""
        return self._query.latest_run_marks()

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """分頁讀取 JSON 批次摘要。"""
        return self._query.list_runs(limit=limit, offset=offset)

    def list_candidates(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SmartReviewCandidate]:
        """分頁讀取 JSON 批次候選。"""
        return self._query.list_candidates(
            run_id, status=status, limit=limit, offset=offset
        )

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
        return self._query.page_candidates(
            run_id,
            status=status,
            limit=limit,
            offset=offset,
            known_total=known_total,
            clamp_offset=clamp_offset,
        )

    def count_candidates(self, run_id: str, *, status: str | None = None) -> int:
        """計算 JSON 批次候選數量。"""
        return self._query.count_candidates(run_id, status=status)

    def breakdown(self, run_id: str, *, error_limit: int = 5) -> dict[str, Any]:
        """統計 JSON 批次狀態與驗證錯誤。"""
        return self._query.breakdown(run_id, error_limit=error_limit)

    def get_candidate(self, run_id: str, ordinal: int) -> dict[str, Any] | None:
        """讀取單一 JSON 候選的可變快照。"""
        return self._query.get_candidate(run_id, ordinal)

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
        candidates = [snapshot_payload(run_id, item) for item in snapshots]
        common_payload = {
            "schema_version": SCHEMA_VERSION,
            "id": run_id,
            "provider": provider,
            "model": model,
            "scope_version": version,
            "requested_limit": int(requested_limit),
            "batch_size": int(batch_size),
            "scope": scope or {},
            "started_at": timestamp(),
            "finished_at": None,
            "status": "running",
            "owner_pid": os.getpid(),
            "checkpoint_at": timestamp(),
            "selected_count": len(snapshots),
            "processed_count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "request_error_count": 0,
            "ai_metrics_version": 1,
            "metrics": {},
        }
        if len(candidates) <= self.storage_chunk_size:
            payload = {
                **common_payload,
                "status_counts": {SmartReviewStatus.PENDING: len(candidates)}
                if candidates
                else {},
                "error_counts": {},
                "candidates": candidates,
            }
            with self._write_lock():
                self.repository.write_unlocked(run_id, payload)
            return run_id

        chunks: list[dict[str, Any]] = []
        for index in range(0, len(candidates), self.storage_chunk_size):
            chunk_index = index // self.storage_chunk_size
            chunk_candidates = candidates[index : index + self.storage_chunk_size]
            chunks.append(
                {
                    "index": chunk_index,
                    "ordinal_start": int(chunk_candidates[0]["ordinal"]),
                    "ordinal_end": int(chunk_candidates[-1]["ordinal"]),
                    "count": len(chunk_candidates),
                    "status_counts": {SmartReviewStatus.PENDING: len(chunk_candidates)},
                    "error_counts": {},
                }
            )
        manifest = {
            **common_payload,
            "storage_format": CHUNKED_STORAGE_FORMAT,
            "storage_chunk_size": self.storage_chunk_size,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "status_counts": {SmartReviewStatus.PENDING: len(candidates)}
            if candidates
            else {},
            "error_counts": {},
            "chunk_revisions": {},
            "chunk_signatures": {},
        }
        with self._write_lock():
            self.repository.publish_chunked_run_unlocked(
                run_id, manifest, chunks, candidates
            )
        return run_id

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
            chunk_index = self.repository.chunk_index_for_ordinal(manifest, ordinal)
            if chunk_index is None:
                raise ValueError("smart review response id 不在候選快照")
            grouped[chunk_index].append(result)
        return grouped

    def record_results(self, run_id: str, results: Sequence[dict[str, Any]]) -> None:
        """把一批模型結果寫回 JSON 候選。"""
        with self._write_lock():
            payload = self.repository.read_unlocked(run_id)
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(run_id, payload)
                grouped = self._group_results_by_chunk(run_id, payload, results)
                loaded: dict[int, dict[str, Any]] = {}
                candidates_by_chunk: dict[int, dict[int, dict[str, Any]]] = {}
                for chunk_index in grouped:
                    chunk = self.repository.read_chunk_unlocked(run_id, chunk_index)
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
                        (
                            old_status,
                            new_status,
                            old_error,
                            new_error,
                            old_ai_status,
                            new_ai_status,
                        ) = apply_result_to_candidate(candidate, result)
                        apply_result_delta(
                            payload,
                            old_status,
                            new_status,
                            old_error,
                            new_error,
                            old_ai_status,
                            new_ai_status,
                        )
                    self.repository.write_chunk_unlocked(
                        run_id, chunk_index, loaded[chunk_index]
                    )
                    self.repository.update_chunk_checkpoint_unlocked(
                        run_id, payload, chunk_index, loaded[chunk_index]
                    )
                self.repository.write_unlocked(run_id, payload)
                return
            for result in results:
                ordinal = int(result["id"])
                candidate = find_candidate(payload, ordinal)
                if candidate is None:
                    raise ValueError("smart review response id 不在候選快照")
                apply_result_to_candidate(candidate, result)
            payload.update(candidate_counts(payload))
            self.repository.write_unlocked(run_id, payload)

    def mark_request_error(
        self, run_id: str, ordinals: Sequence[int], *, error_code: str
    ) -> None:
        """把請求失敗寫入 JSON 候選。"""
        with self._write_lock():
            payload = self.repository.read_unlocked(run_id)
            wanted = {int(ordinal) for ordinal in ordinals}
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(run_id, payload)
                grouped: dict[int, set[int]] = defaultdict(set)
                for ordinal in wanted:
                    chunk_index = self.repository.chunk_index_for_ordinal(
                        payload, ordinal
                    )
                    if chunk_index is not None:
                        grouped[chunk_index].add(ordinal)
                for chunk_index, chunk_ordinals in grouped.items():
                    chunk = self.repository.read_chunk_unlocked(run_id, chunk_index)
                    for candidate in chunk.get("candidates") or []:
                        if not isinstance(candidate, dict):
                            continue
                        if int(candidate.get("ordinal") or 0) not in chunk_ordinals:
                            continue
                        old_status = candidate.get("status")
                        old_error = candidate.get("validation_error")
                        old_ai_status = candidate_ai_status(candidate)
                        candidate["status"] = SmartReviewStatus.REQUEST_FAILED
                        candidate["validation_error"] = error_code
                        candidate["ai_status"] = SmartReviewStatus.REQUEST_FAILED
                        apply_result_delta(
                            payload,
                            old_status,
                            "request_failed",
                            old_error,
                            error_code,
                            old_ai_status,
                            "request_failed",
                        )
                    self.repository.write_chunk_unlocked(run_id, chunk_index, chunk)
                    self.repository.update_chunk_checkpoint_unlocked(
                        run_id, payload, chunk_index, chunk
                    )
                self.repository.write_unlocked(run_id, payload)
                return
            for candidate in payload.get("candidates") or []:
                if (
                    isinstance(candidate, dict)
                    and int(candidate.get("ordinal") or 0) in wanted
                ):
                    candidate["status"] = SmartReviewStatus.REQUEST_FAILED
                    candidate["validation_error"] = error_code
                    candidate["ai_status"] = SmartReviewStatus.REQUEST_FAILED
            payload.update(candidate_counts(payload))
            self.repository.write_unlocked(run_id, payload)

    def mark_pending_cancelled(self, run_id: str) -> int:
        """標記尚未送出的候選為取消。"""
        with self._write_lock():
            payload = self.repository.read_unlocked(run_id)
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(run_id, payload)
                updated = 0
                for entry in self.repository.chunk_entries(payload):
                    chunk_index = entry["index"]
                    chunk = self.repository.read_chunk_unlocked(run_id, chunk_index)
                    chunk_updated = False
                    for candidate in chunk.get("candidates") or []:
                        if not isinstance(candidate, dict):
                            continue
                        if candidate.get("status") != SmartReviewStatus.PENDING:
                            continue
                        old_error = candidate.get("validation_error")
                        candidate["status"] = SmartReviewStatus.CANCELLED
                        candidate["validation_error"] = "cancelled_before_request"
                        candidate_metadata_delta(
                            payload,
                            "pending",
                            "cancelled",
                            old_error,
                            "cancelled_before_request",
                        )
                        updated += 1
                        chunk_updated = True
                    if chunk_updated:
                        self.repository.write_chunk_unlocked(run_id, chunk_index, chunk)
                        self.repository.update_chunk_checkpoint_unlocked(
                            run_id, payload, chunk_index, chunk
                        )
                if updated:
                    self.repository.write_unlocked(run_id, payload)
                return updated
            updated = 0
            for candidate in payload.get("candidates") or []:
                if (
                    isinstance(candidate, dict)
                    and candidate.get("status") == SmartReviewStatus.PENDING
                ):
                    candidate["status"] = SmartReviewStatus.CANCELLED
                    candidate["validation_error"] = "cancelled_before_request"
                    updated += 1
            if updated:
                payload.update(candidate_counts(payload))
                self.repository.write_unlocked(run_id, payload)
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
        with self._write_lock():
            payload = self.repository.read_unlocked(run_id)
            payload.update(
                {
                    "finished_at": timestamp(),
                    "status": status,
                    "processed_count": int(processed_count),
                    "valid_count": int(valid_count),
                    "invalid_count": int(invalid_count),
                    "request_error_count": int(request_error_count),
                    "metrics": metrics or {},
                }
            )
            self.repository.write_unlocked(run_id, payload)

    def update_candidate(self, run_id: str, ordinal: int, **updates: Any) -> bool:
        """原子更新 JSON 候選欄位。"""
        with self._write_lock():
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return False
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(run_id, payload)
                located = self.repository.read_candidate_chunk_for_ordinal_unlocked(
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
                candidate_metadata_delta(
                    payload,
                    old_status,
                    candidate.get("status"),
                    old_error,
                    candidate.get("validation_error"),
                )
                self.repository.write_chunk_unlocked(run_id, chunk_index, chunk)
                self.repository.update_chunk_checkpoint_unlocked(
                    run_id, payload, chunk_index, chunk
                )
                self.repository.write_unlocked(run_id, payload)
                return True
            candidate = find_candidate(payload, int(ordinal))
            if candidate is None:
                return False
            candidate.update(updates)
            self.repository.write_unlocked(run_id, payload)
            return True

    def reconcile_applied(
        self, run_id: str, records: Mapping[int, Mapping[str, Any]]
    ) -> int:
        """依 SQLite 套用歷史，補回尚未標記為 applied（或指向舊套用）的 JSON 候選。

        SQLite 翻譯與 JSON 歷史分屬兩個儲存邊界；若前者已提交、後者在
        原子替換時遇到 I/O 錯誤，下一次讀取可以用同一個 run／ordinal 的
        SQLite audit marker 對帳，而不會再次把候選當成 ready 套用。
        """
        with self._write_lock():
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return 0
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(run_id, payload)
                updated = 0
                records_by_chunk: dict[int, dict[int, Mapping[str, Any]]] = defaultdict(
                    dict
                )
                for ordinal, record in records.items():
                    chunk_index = self.repository.chunk_index_for_ordinal(
                        payload, int(ordinal)
                    )
                    if chunk_index is not None:
                        records_by_chunk[chunk_index][int(ordinal)] = record
                for chunk_index, chunk_records in records_by_chunk.items():
                    chunk = self.repository.read_chunk_unlocked(run_id, chunk_index)
                    chunk_updated = False
                    for candidate in chunk.get("candidates") or []:
                        if not isinstance(candidate, dict):
                            continue
                        ordinal = int(candidate.get("ordinal") or 0)
                        record = chunk_records.get(ordinal)
                        if record is None or int(record.get("entry_id") or 0) != int(
                            candidate.get("entry_id") or 0
                        ):
                            continue
                        batch = str(record.get("batch") or "") or None
                        if candidate.get("status") == SmartReviewStatus.APPLIED and (
                            candidate.get("applied_batch") == batch
                        ):
                            continue
                        old_status = candidate.get("status")
                        old_error = candidate.get("validation_error")
                        candidate.update(
                            {
                                "status": SmartReviewStatus.APPLIED,
                                "applied_batch": batch,
                                "applied_at": record.get("applied_at"),
                            }
                        )
                        candidate_metadata_delta(
                            payload,
                            old_status,
                            "applied",
                            old_error,
                            candidate.get("validation_error"),
                        )
                        updated += 1
                        chunk_updated = True
                    if chunk_updated:
                        self.repository.write_chunk_unlocked(run_id, chunk_index, chunk)
                        self.repository.update_chunk_checkpoint_unlocked(
                            run_id, payload, chunk_index, chunk
                        )
                if updated:
                    self.repository.write_unlocked(run_id, payload)
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
                if candidate.get("status") == SmartReviewStatus.APPLIED and (
                    candidate.get("applied_batch") == batch
                ):
                    continue
                candidate.update(
                    {
                        "status": SmartReviewStatus.APPLIED,
                        "applied_batch": batch,
                        "applied_at": record.get("applied_at"),
                    }
                )
                updated += 1
            if updated:
                self.repository.write_unlocked(run_id, payload)
            return updated


__all__ = [
    "SMART_REVIEW_CAS_SNAPSHOT_FIELDS",
    "SmartReviewHistoryStore",
    "history_directory_for_db",
]
