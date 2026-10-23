"""智慧校對批次的 JSON 儲存。

校對候選包含原文快照、本地 context 與模型回應，逐批寫進 SQLite 會讓翻譯
資料庫快速膨脹。這個儲存層以 JSON 保存候選；小批次維持單檔，大批次使用
manifest 加多個 JSON 分片，更新時只原子替換受影響的分片。TranslationDB
仍提供既有方法，讓 UI 與套用流程不必知道檔案格式。

``SmartReviewHistoryStore`` 是對外門面，負責批次生命週期與候選寫入交易；實際
職責分在同目錄的模組：

- ``smart_review_history_lock``：程序內鎖與跨程序檔案鎖（``HistoryLock``）。
- ``smart_review_history_codec``：候選／批次 JSON 結構、摘要計算（純函式）。
- ``smart_review_history_manifest``：manifest、分片與 status sidecar 檔案存取（``ChunkManifestRepository``）。
- ``smart_review_history_sidecar``：status sidecar 格式（純函式）。
- ``smart_review_history_recovery``：失聯批次復原（``HistoryRecovery``）。
- ``smart_review_history_query``：分頁、計數與最近標籤快取（``HistoryQuery``）。
- ``smart_review_history_migrations``：schema migration registry、一致性檢查與修復。
"""

from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self

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
    mark_projection,
    refresh_status_counts,
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
from translation_tool.translation_db.smart_review_status import (
    PROCESSED_STATUSES,
    ProjectionState,
    SmartReviewStatus,
)
from translation_tool.utils.config_manager import get_smart_review_settings


def history_directory_for_db(db_path: str | Path) -> Path:
    """回傳指定翻譯資料庫專用的智慧校對 JSON 目錄。"""
    path = Path(db_path).expanduser().resolve()
    return path.parent / f"{path.stem}.smart_review"


_RESUME_SNAPSHOT_KEYS = (
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
    "context",
)


def _resume_fingerprint(
    value: Mapping[str, Any], *, use_original: bool
) -> tuple[Any, ...]:
    """建立續跑用的完整快照指紋。"""
    original = value.get("original_snapshot") if use_original else None
    source = original if isinstance(original, Mapping) else value
    values: list[Any] = []
    for key in _RESUME_SNAPSHOT_KEYS:
        value = source.get("entry_key") if key == "entry_key" else source.get(key)
        if key == "context":
            value = json.dumps(
                value or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        values.append(value)
    return tuple(values)


def _reset_resume_candidate(
    candidate: dict[str, Any], current: Mapping[str, Any]
) -> bool:
    """把候選重設為 pending，並回傳是否由快照變更觸發。"""
    old = {
        key: candidate.get(key)
        for key in (
            "status",
            "ai_status",
            "decision",
            "suggested_zh_tw",
            "issue_types",
            "evidence_ids",
            "confidence",
            "reason",
            "validation_error",
            "original_snapshot",
        )
    }
    attempts = candidate.setdefault("attempt_history", [])
    if isinstance(attempts, list):
        attempts.append(old)
    for key in _RESUME_SNAPSHOT_KEYS:
        candidate[key] = current.get(key)
    candidate["original_snapshot"] = {
        key: candidate.get(key) for key in _RESUME_SNAPSHOT_KEYS
    }
    changed = _resume_fingerprint(old, use_original=True) != _resume_fingerprint(
        current, use_original=False
    )
    candidate.update(
        {
            "decision": None,
            "suggested_zh_tw": None,
            "issue_types": [],
            "evidence_ids": [],
            "confidence": None,
            "reason": None,
            "response": None,
            "draft_zh_tw": None,
            "validation_error": "manual_edit_retry" if changed else None,
            "status": SmartReviewStatus.PENDING,
            "ai_status": None,
            "applied_batch": None,
            "applied_at": None,
        }
    )
    return changed


def _prepare_resume_candidate(
    candidate: dict[str, Any], current: Mapping[str, Any] | None
) -> str:
    """回傳候選的續跑動作：``excluded``、``retry`` 或 ``keep``。"""
    status = str(candidate.get("status") or SmartReviewStatus.PENDING)
    ai_status = candidate.get("ai_status") or status
    if current is None:
        if status in {SmartReviewStatus.APPLIED, SmartReviewStatus.REJECTED}:
            return "keep"
        candidate["status"] = SmartReviewStatus.CANCELLED
        candidate["validation_error"] = "excluded_from_scope"
        return "excluded"
    retryable = status in {
        SmartReviewStatus.PENDING,
        SmartReviewStatus.CANCELLED,
        SmartReviewStatus.REQUEST_FAILED,
    }
    changed = _resume_fingerprint(candidate, use_original=True) != _resume_fingerprint(
        current, use_original=False
    )
    invalidated = (
        ai_status in PROCESSED_STATUSES
        and status not in {SmartReviewStatus.APPLIED, SmartReviewStatus.REJECTED}
        and changed
    )
    if retryable or invalidated:
        _reset_resume_candidate(candidate, current)
        return "invalidated" if invalidated else "retry"
    return "keep"


class SmartReviewHistoryBatch:
    """在同一個歷史鎖內批次更新一個 run 的候選。

    單筆 ``update_candidate`` 為了保證程序崩潰時的可恢復性，每次都會讀取
    manifest、替換分片、寫 status sidecar，再替換 manifest。審查批量操作若
    每筆都走這條路徑，實際上會反覆處理同一個分片。本類別把候選變更先留在
    記憶體，離開 context 時每個受影響分片只發布一次；SQLite 的 CAS 仍可在
    context 內逐筆執行，兩個儲存邊界的安全語意不變。
    """

    def __init__(self, store: SmartReviewHistoryStore, run_id: str) -> None:
        self.store = store
        self.run_id = str(run_id)
        self.payload: dict[str, Any] | None = None
        self._lock_context: AbstractContextManager[None] | None = None
        self._chunks: dict[int, dict[str, Any]] = {}
        self._dirty_chunks: set[int] = set()
        self._dirty = False
        self._committed = False

    def __enter__(self) -> Self:
        self._lock_context = self.store._write_lock()
        try:
            self._lock_context.__enter__()
            self.payload = self.store.repository.read_unlocked(self.run_id)
            if is_chunked(self.payload):
                self.store.repository.ensure_chunked_summary_unlocked(
                    self.run_id, self.payload
                )
            return self
        except BaseException:
            self._release_lock(None, None, None)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None and not self._committed:
                self.commit()
        finally:
            self._release_lock(exc_type, exc, traceback)

    def _release_lock(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lock_context = self._lock_context
        self._lock_context = None
        if lock_context is not None:
            lock_context.__exit__(exc_type, exc, traceback)

    def _require_payload(self) -> dict[str, Any]:
        if self.payload is None:
            raise RuntimeError("智慧校對歷史批次尚未進入 context")
        return self.payload

    def _load_chunk(self, chunk_index: int) -> dict[str, Any]:
        chunk = self._chunks.get(int(chunk_index))
        if chunk is None:
            chunk = self.store.repository.read_chunk_unlocked(
                self.run_id, int(chunk_index)
            )
            self._chunks[int(chunk_index)] = chunk
        return chunk

    def _locate_candidate(
        self, ordinal: int
    ) -> tuple[dict[str, Any] | None, int | None]:
        payload = self._require_payload()
        value = int(ordinal)
        if is_chunked(payload):
            chunk_index = self.store.repository.chunk_index_for_ordinal(payload, value)
            if chunk_index is None:
                return None, None
            chunk = self._load_chunk(chunk_index)
            candidate = next(
                (
                    item
                    for item in chunk.get("candidates") or []
                    if isinstance(item, dict) and int(item.get("ordinal") or 0) == value
                ),
                None,
            )
            return candidate, int(chunk_index)
        return find_candidate(payload, value), None

    def get_candidate(self, ordinal: int) -> dict[str, Any] | None:
        """回傳批次內可變的候選字典，不再重新讀取 JSON。"""
        candidate, _chunk_index = self._locate_candidate(ordinal)
        return candidate

    def _mark_candidate_dirty(
        self,
        candidate: dict[str, Any],
        *,
        chunk_index: int | None,
        old_status: object,
        old_error: object,
    ) -> None:
        payload = self._require_payload()
        candidate_metadata_delta(
            payload,
            old_status,
            candidate.get("status"),
            old_error,
            candidate.get("validation_error"),
        )
        self._dirty = True
        if chunk_index is not None:
            self._dirty_chunks.add(int(chunk_index))

    def update_candidate(self, ordinal: int, **updates: Any) -> bool:
        """在記憶體中更新一筆候選，稍後由 ``commit`` 按分片發布。"""
        candidate, chunk_index = self._locate_candidate(ordinal)
        if candidate is None:
            return False
        old_status = candidate.get("status")
        old_error = candidate.get("validation_error")
        candidate.update(updates)
        self._mark_candidate_dirty(
            candidate,
            chunk_index=chunk_index,
            old_status=old_status,
            old_error=old_error,
        )
        return True

    def _reconcile_chunked_applied(
        self, records: Mapping[int, Mapping[str, Any]]
    ) -> int:
        """在分片候選內補回 SQLite 套用紀錄。"""
        payload = self._require_payload()
        grouped: dict[int, dict[int, Mapping[str, Any]]] = defaultdict(dict)
        for ordinal, record in records.items():
            chunk_index = self.store.repository.chunk_index_for_ordinal(
                payload, int(ordinal)
            )
            if chunk_index is not None:
                grouped[int(chunk_index)][int(ordinal)] = record
        updated = 0
        for chunk_index, chunk_records in grouped.items():
            chunk = self._load_chunk(chunk_index)
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
                self._mark_candidate_dirty(
                    candidate,
                    chunk_index=chunk_index,
                    old_status=old_status,
                    old_error=old_error,
                )
                updated += 1
        return updated

    def _reconcile_legacy_applied(
        self, records: Mapping[int, Mapping[str, Any]]
    ) -> int:
        """在單檔候選內補回 SQLite 套用紀錄。"""
        payload = self._require_payload()
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
            self._mark_candidate_dirty(
                candidate,
                chunk_index=None,
                old_status=old_status,
                old_error=old_error,
            )
            updated += 1
        return updated

    def reconcile_applied(self, records: Mapping[int, Mapping[str, Any]]) -> int:
        """在目前批次快照內一次補回 SQLite 已提交的套用紀錄。"""
        if not records:
            return 0
        return (
            self._reconcile_chunked_applied(records)
            if is_chunked(self._require_payload())
            else self._reconcile_legacy_applied(records)
        )

    def commit(self) -> None:
        """把目前記憶體中的候選變更以最少檔案替換發布。"""
        if self._committed:
            return
        payload = self._require_payload()
        if not self._dirty:
            self._committed = True
            return
        if is_chunked(payload):
            for chunk_index in sorted(self._dirty_chunks):
                chunk = self._chunks[chunk_index]
                self.store.repository.write_chunk_unlocked(
                    self.run_id, chunk_index, chunk
                )
                self.store.repository.update_chunk_checkpoint_unlocked(
                    self.run_id, payload, chunk_index, chunk
                )
            mark_projection(payload)
        else:
            refresh_status_counts(payload)
            mark_projection(payload)
        self.store.repository.write_unlocked(self.run_id, payload)
        self._committed = True


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

    def batch(self, run_id: str) -> SmartReviewHistoryBatch:
        """建立一個可在同一歷史鎖內合併寫入的候選批次。"""
        return SmartReviewHistoryBatch(self, run_id)

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

    def recent_candidate_payloads(
        self, *, max_runs: int = 2
    ) -> tuple[int, list[dict[str, Any]]]:
        """讀取最近最多 ``max_runs`` 份可解析 manifest 的候選。

        排序使用與歷史頁相同的 ``started_at``／mtime／run_id。損壞檔案只會
        被略過，不會佔用「最近兩份」的額度；SQLite 舊批次不在此查詢範圍。
        """
        bounded = max(0, int(max_runs))
        if bounded == 0:
            return 0, []
        self.recover_abandoned_runs()
        consulted = 0
        candidates: list[dict[str, Any]] = []
        with self.locked():
            for _key, path, _stamp in self._query.ranked_run_files():
                try:
                    payload = self.repository.read_json_path(path)
                    if not isinstance(payload, dict) or not payload.get("id"):
                        continue
                    items = self.repository.candidate_items_unlocked(path.stem, payload)
                    parsed_items = [
                        dict(item) for item in items if isinstance(item, dict)
                    ]
                except (OSError, ValueError, TypeError, KeyError):
                    continue
                consulted += 1
                for item in parsed_items:
                    item["run_id"] = str(payload.get("id") or path.stem)
                    item["_history_provider"] = payload.get("provider")
                    item["_history_model"] = payload.get("model")
                    candidates.append(item)
                if consulted >= bounded:
                    break
        return consulted, candidates

    def prepare_resume(
        self,
        run_id: str,
        current_by_ordinal: Mapping[int, Mapping[str, Any] | None],
    ) -> dict[str, int]:
        """以同一 run_id 準備復原，保留已完成結果與人工變更軌跡。

        ``pending``／``cancelled``／``request_failed`` 會回到 pending；若已完成
        的候選 CAS 快照已改變，舊結果先放入 ``attempt_history`` 再重試。已套用或
        已拒絕的候選不會被悄悄改回可套用狀態。
        """
        changed = 0
        retried = 0
        excluded = 0
        invalidated = 0
        with self._write_lock():
            payload = self.repository.read_unlocked(run_id)
            if str(payload.get("status") or "") == "running":
                raise RuntimeError("智慧校對批次仍在執行中，不能重複啟動")
            items = list(self.repository.candidate_items_unlocked(run_id, payload))
            for candidate in items:
                ordinal = int(candidate.get("ordinal") or 0)
                action = _prepare_resume_candidate(
                    candidate, current_by_ordinal.get(ordinal)
                )
                if action == "excluded":
                    excluded += 1
                elif action in {"retry", "invalidated"}:
                    retried += 1
                    changed += 1
                    if action == "invalidated":
                        invalidated += 1

            payload.update(candidate_counts(payload))
            metrics = payload.get("metrics")
            if not isinstance(metrics, dict):
                metrics = {}
            metrics["excluded_count"] = (
                int(metrics.get("excluded_count") or 0) + excluded
            )
            metrics["invalidated_by_manual_edit_count"] = (
                int(metrics.get("invalidated_by_manual_edit_count") or 0) + invalidated
            )
            payload["metrics"] = metrics
            payload.update(
                {
                    "status": "running",
                    "finished_at": None,
                    "owner_pid": os.getpid(),
                    "checkpoint_at": timestamp(),
                    "resume_count": int(payload.get("resume_count") or 0) + 1,
                    "last_resume_at": timestamp(),
                }
            )
            if is_chunked(payload):
                updates = {int(item.get("ordinal") or 0): item for item in items}
                for entry in self.repository.chunk_entries(payload):
                    chunk = self.repository.read_chunk_unlocked(run_id, entry["index"])
                    for item in chunk.get("candidates") or []:
                        replacement = updates.get(int(item.get("ordinal") or 0))
                        if replacement is not None:
                            item.clear()
                            item.update(replacement)
                    self.repository.write_chunk_unlocked(run_id, entry["index"], chunk)
                    self.repository.update_chunk_checkpoint_unlocked(
                        run_id, payload, entry["index"], chunk
                    )
                payload.update(self.repository.chunk_summary_unlocked(run_id, payload))
            self.repository.write_unlocked(run_id, payload)
        return {
            "changed": changed,
            "retried": retried,
            "excluded": excluded,
            "invalidated": invalidated,
        }

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
                            SmartReviewStatus.PENDING,
                            SmartReviewStatus.CANCELLED,
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
                    mark_projection(payload)
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
                mark_projection(payload)
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
                mark_projection(payload)
                self.repository.write_unlocked(run_id, payload)
                return True
            candidate = find_candidate(payload, int(ordinal))
            if candidate is None:
                return False
            candidate.update(updates)
            refresh_status_counts(payload)
            mark_projection(payload)
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
                            SmartReviewStatus.APPLIED,
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
                    mark_projection(payload, ProjectionState.RECONCILED)
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
                refresh_status_counts(payload)
                mark_projection(payload, ProjectionState.RECONCILED)
                self.repository.write_unlocked(run_id, payload)
            return updated


__all__ = [
    "SMART_REVIEW_CAS_SNAPSHOT_FIELDS",
    "SmartReviewHistoryBatch",
    "SmartReviewHistoryStore",
    "history_directory_for_db",
]
