"""智慧校對歷史的唯讀查詢。

``HistoryQuery`` 負責 UI 與合併流程讀歷史時的路徑：批次清單排序、候選分頁與
計數、單筆候選讀取，以及「最近一次批次的條目標籤」快取。查詢只做讀取；遇到
分片 manifest 摘要過期時只更新記憶體中的摘要（``persist=False``），不改寫檔案。
快取以歷史目錄為鍵，在同一程序的所有 Store 之間共用。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

from translation_tool.translation_db.models import SmartReviewCandidate
from translation_tool.translation_db.smart_review_history_codec import (
    candidate_from_payload,
    find_candidate,
    is_chunked,
)
from translation_tool.translation_db.smart_review_history_lock import HistoryLock
from translation_tool.translation_db.smart_review_history_manifest import (
    ChunkManifestRepository,
)
from translation_tool.translation_db.smart_review_history_recovery import (
    HistoryRecovery,
)

_HEAD_BYTES = 16_384
_STARTED_AT = re.compile(r'"started_at":\s*"([^"]*)"')
logger = logging.getLogger(__name__)
_FileStamp = tuple[int, int]
_MarksStamp = tuple[
    _FileStamp,
    tuple[tuple[int, _FileStamp | None], ...],
]


def _run_sort_key(
    started_at: object, mtime_ns: int, run_id: object
) -> tuple[str, int, str]:
    """建立歷史清單與最近批次共用的穩定排序鍵。"""
    return str(started_at or ""), int(mtime_ns), str(run_id or "")


class HistoryQuery:
    """歷史批次與候選的唯讀查詢，並維護最近標籤快取。"""

    # 檔案 -> ((mtime, size), started_at)：只存字串，供排序用
    started_cache: ClassVar[dict[Path, tuple[tuple[int, int], str]]] = {}
    # 資料夾 -> (檔案, manifest 與分片版本, run_id, 條目狀態對照)
    latest_marks: ClassVar[
        dict[Path, tuple[Path, _MarksStamp, str, dict[int, tuple[str, str | None]]]]
    ] = {}
    # 已確認損壞的批次檔 -> manifest 與分片版本，避免每次換頁重複解析
    corrupt_runs: ClassVar[dict[Path, _MarksStamp]] = {}

    def __init__(
        self,
        directory: Path,
        lock: HistoryLock,
        repository: ChunkManifestRepository,
        recovery: HistoryRecovery,
    ) -> None:
        """綁定歷史目錄與查詢所需的鎖、manifest 存取及復原流程。"""
        self.directory = directory
        self.lock = lock
        self.repository = repository
        self.recovery = recovery

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """讀取單一批次摘要，不把候選全部放進歷史卡片。"""
        self.recovery.recover_abandoned_runs()
        with self.lock.process_lock:
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return None
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(
                    run_id, payload, persist=False
                )
        return {key: value for key, value in payload.items() if key != "candidates"}

    def all_runs(self, *, include_sort_metadata: bool = False) -> list[dict[str, Any]]:
        """讀取所有可解析的 JSON 批次，依新到舊排序。

        ``include_sort_metadata`` 僅供 SQLite 與 JSON 歷史合併排序使用；一般
        呼叫維持原本的摘要格式，不把檔案 mtime 暴露給 UI 或其他呼叫端。
        """
        self.recovery.recover_abandoned_runs()
        if not self.directory.is_dir():
            return []
        runs: list[tuple[dict[str, Any], tuple[str, int, str]]] = []
        with self.lock.process_lock:
            for _rank, path, stamp in self.ranked_run_files():
                parsed = self._summary_for_path(
                    path, stamp, include_sort_metadata=include_sort_metadata
                )
                if parsed is not None:
                    runs.append(parsed)
        return [
            item
            for item, _key in sorted(runs, key=lambda value: value[1], reverse=True)
        ]

    def _summary_for_path(
        self,
        path: Path,
        stamp: _FileStamp,
        *,
        include_sort_metadata: bool,
    ) -> tuple[dict[str, Any], tuple[str, int, str]] | None:
        """讀取單一 manifest 摘要，不把候選內容帶入歷史清單。"""
        try:
            payload = self.repository.read_json_path(path)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict) or not payload.get("id"):
            return None
        if is_chunked(payload):
            self.repository.ensure_chunked_summary_unlocked(
                path.stem, payload, persist=False
            )
        summary = {key: value for key, value in payload.items() if key != "candidates"}
        if include_sort_metadata:
            summary["_history_mtime_ns"] = stamp[0]
        return summary, _run_sort_key(
            payload.get("started_at"), stamp[0], payload.get("id")
        )

    def marks_cache_stamp(self, path: Path, manifest_stamp: _FileStamp) -> _MarksStamp:
        """建立最近標籤快取版本，包含分片檔案的存在與 stat 版本。"""
        try:
            manifest = self.repository.read_json_path(path)
        except (OSError, ValueError, TypeError):
            return (manifest_stamp, ())
        if not is_chunked(manifest):
            return (manifest_stamp, ())
        chunk_stamps: list[tuple[int, _FileStamp | None]] = []
        for entry in self.repository.chunk_entries(manifest):
            try:
                chunk_path = self.repository.chunk_path_for(path.stem, entry["index"])
            except ValueError:
                return (manifest_stamp, ())
            signature = self.repository.chunk_signature(chunk_path)
            chunk_stamps.append(
                (
                    int(entry["index"]),
                    None
                    if signature is None
                    else (int(signature[0]), int(signature[1])),
                )
            )
        return (manifest_stamp, tuple(chunk_stamps))

    def latest_run_marks(self) -> tuple[str | None, dict[int, tuple[str, str | None]]]:
        """回傳最近一次批次的 ``entry_id -> (狀態, 驗證錯誤)``，供條目列顯示狀態。

        先以檔案開頭的 ``started_at`` 排序（每個檔案只 stat 並讀檔頭），只完整
        解析勝出的那一個批次；分片批次的快取版本也包含各分片的 stat，避免分片
        損壞或修復後沿用舊標籤。候選狀態對照每個資料夾只保留這一份，歷史批次
        不會常駐記憶體。損壞的批次會略過並記錄警告。
        """
        self.recovery.recover_abandoned_runs()
        if not self.directory.is_dir():
            return None, {}
        with self.lock.process_lock:
            for _key, path, stamp in self.ranked_run_files():
                marks_stamp = self.marks_cache_stamp(path, stamp)
                latest = self.latest_marks.get(self.directory)
                if latest is not None and latest[:2] == (path, marks_stamp):
                    return latest[2], latest[3]
                if self.corrupt_runs.get(path) == marks_stamp:
                    continue
                parsed = self.parse_marks(path)
                if parsed is None:
                    self.corrupt_runs[path] = marks_stamp
                    continue
                self.latest_marks[self.directory] = (path, marks_stamp, *parsed)
                return parsed
        return None, {}

    def ranked_run_files(
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
            cached = self.started_cache.get(path)
            if cached is None or cached[0] != stamp:
                cached = (stamp, self.read_started_at(path))
                self.started_cache[path] = cached
            ranked.append((_run_sort_key(cached[1], stamp[0], path.stem), path, stamp))
        for table in (self.started_cache, self.corrupt_runs):
            for gone in [
                p for p in table if p.parent == self.directory and p not in live
            ]:
                del table[gone]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked

    @staticmethod
    def read_started_at(path: Path) -> str:
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

    def parse_marks(
        self,
        path: Path,
    ) -> tuple[str, dict[int, tuple[str, str | None]]] | None:
        """完整解析一個批次檔，回傳 (run_id, 條目狀態對照)；結構不符時記錄警告並回傳 None。"""
        try:
            payload = self.repository.read_json_path(path)
        except (OSError, ValueError, TypeError):
            logger.warning("略過無法讀取的智慧校對批次檔：%s", path)
            return None
        marks: dict[int, tuple[str, str | None]] = {}
        try:
            if is_chunked(payload):
                # 分片批次讀各分片旁的 status sidecar；過期或遺失時才從分片重建。
                for (
                    _ordinal,
                    entry_id,
                    status,
                    error,
                ) in self.repository.iter_status_rows_unlocked(path.stem, payload):
                    marks[entry_id] = (status, error)
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
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning("略過含有損壞候選分片的智慧校對批次檔：%s", path)
            return None
        return str(payload.get("id") or path.stem), marks

    def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_sort_metadata: bool = False,
    ) -> list[dict[str, Any]]:
        """分頁讀取 JSON 批次摘要，只完整解析足以填滿目前頁的 manifest。"""
        self.recovery.recover_abandoned_runs()
        if not self.directory.is_dir():
            return []
        start = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        end = start + bounded_limit
        runs: list[tuple[dict[str, Any], tuple[str, int, str]]] = []
        started = time.perf_counter()
        scanned = 0
        with self.lock.process_lock:
            for _rank, path, stamp in self.ranked_run_files():
                scanned += 1
                parsed = self._summary_for_path(
                    path, stamp, include_sort_metadata=include_sort_metadata
                )
                if parsed is None:
                    continue
                runs.append(parsed)
                if len(runs) >= end:
                    break
        ordered = sorted(runs, key=lambda value: value[1], reverse=True)
        result = [item for item, _key in ordered[start:end]]
        logger.debug(
            "Smart Review 歷史清單載入：limit=%s offset=%s scanned=%s parsed=%s returned=%s elapsed_ms=%.1f",
            bounded_limit,
            start,
            scanned,
            len(runs),
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result

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
        self.recovery.recover_abandoned_runs()
        with self.lock.process_lock:
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return 0, []
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(
                    run_id, payload, persist=False
                )
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
                for entry in self.repository.chunk_entries(payload):
                    if wanted is not None and len(selected) >= wanted:
                        break
                    entry_status_counts = entry.get("status_counts")
                    if status is not None and isinstance(entry_status_counts, dict):
                        entry_total = int(entry_status_counts.get(status, 0) or 0)
                        if start >= entry_total:
                            start -= entry_total
                            continue
                    elif status is None:
                        entry_total = int(entry.get("count") or 0)
                        if start >= entry_total:
                            start -= entry_total
                            continue
                    try:
                        chunk = self.repository.read_chunk_unlocked(
                            run_id, entry["index"]
                        )
                    except (FileNotFoundError, OSError, ValueError, TypeError):
                        logger.warning(
                            "智慧校對分頁遇到無法讀取的候選分片：run_id=%s，chunk=%s",
                            run_id,
                            entry["index"],
                        )
                        return 0, []
                    local_skip = start
                    start = 0
                    for item in chunk.get("candidates") or []:
                        if not isinstance(item, dict):
                            continue
                        if status is not None and item.get("status") != status:
                            continue
                        if local_skip > 0:
                            local_skip -= 1
                            matched += 1
                            continue
                        selected.append(item)
                        matched += 1
                        if wanted is not None and len(selected) >= wanted:
                            break
                if status is not None and not isinstance(status_counts, dict):
                    total = matched
                return total, [
                    candidate_from_payload(run_id, item) for item in selected
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
        return total, [candidate_from_payload(run_id, item) for item in selected]

    def count_candidates(self, run_id: str, *, status: str | None = None) -> int:
        """計算 JSON 批次候選數量。"""
        self.recovery.recover_abandoned_runs()
        with self.lock.process_lock:
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return 0
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(
                    run_id, payload, persist=False
                )
                status_counts = payload.get("status_counts")
                if isinstance(status_counts, dict):
                    if status is None:
                        return int(payload.get("selected_count") or 0)
                    return int(status_counts.get(status, 0))
                return sum(
                    1
                    for item in self.repository.candidate_items_unlocked(
                        run_id, payload
                    )
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
        self.recovery.recover_abandoned_runs()
        with self.lock.process_lock:
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return {"status": {}, "errors": []}
            if is_chunked(payload):
                self.repository.ensure_chunked_summary_unlocked(
                    run_id, payload, persist=False
                )
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
                item
                for item in self.repository.candidate_items_unlocked(run_id, payload)
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
        with self.lock.process_lock:
            try:
                payload = self.repository.read_unlocked(run_id)
            except FileNotFoundError:
                return None
            if is_chunked(payload):
                located = self.repository.read_candidate_chunk_for_ordinal_unlocked(
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
            candidate = find_candidate(payload, int(ordinal))
            return dict(candidate) if candidate is not None else None
