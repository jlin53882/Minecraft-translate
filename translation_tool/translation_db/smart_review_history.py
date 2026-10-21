"""智慧校對批次的 JSON 儲存。

校對候選包含原文快照、本地 context 與模型回應，逐批寫進 SQLite 會讓翻譯
資料庫快速膨脹。這個儲存層以一個批次一個 JSON 檔保存完整候選，並以原子
替換更新檔案；TranslationDB 仍提供既有方法，讓 UI 與套用流程不必知道檔案
格式。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewSnapshot,
)

_SCHEMA_VERSION = 1
_HEAD_BYTES = 16_384
_STARTED_AT = re.compile(r'"started_at":\s*"([^"]*)"')
logger = logging.getLogger(__name__)
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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


def _snapshot_payload(run_id: str, item: SmartReviewSnapshot) -> dict[str, Any]:
    """把候選輸入快照轉成 JSON 結構。"""
    return {
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
    """以每批一個 JSON 檔保存智慧校對執行與候選。"""

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

    def __init__(self, db_path: str | Path) -> None:
        """初始化此測試替身或服務物件所需的狀態。"""
        self.directory = history_directory_for_db(db_path)
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

    @staticmethod
    def timestamp() -> str:
        """回傳與批次 JSON 相容的 UTC 顯示時間。"""
        return _timestamp()

    def exists(self, run_id: str) -> bool:
        """判斷批次是否由 JSON 儲存。"""
        return self.path_for(run_id).is_file()

    def _read_unlocked(self, run_id: str) -> dict[str, Any]:
        """在呼叫端持有 Store 鎖時讀取並驗證一個智慧校對批次 JSON。"""
        path = self.path_for(run_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as exc:
            raise ValueError(f"無法讀取智慧校對批次 JSON：{path}") from exc
        if not isinstance(payload, dict):
            raise TypeError(f"智慧校對批次 JSON 格式無效：{path}")
        return payload

    def _write_unlocked(self, run_id: str, payload: dict[str, Any]) -> None:
        """在呼叫端持有 Store 鎖時，以暫存檔原子寫入一個智慧校對批次 JSON。"""
        path = self.path_for(run_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

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
        run_id = uuid.uuid4().hex
        payload = {
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
            "selected_count": len(snapshots),
            "processed_count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "request_error_count": 0,
            "metrics": {},
            "candidates": [_snapshot_payload(run_id, item) for item in snapshots],
        }
        with self._lock:
            self._write_unlocked(run_id, payload)
        return run_id

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

    def record_results(self, run_id: str, results: Sequence[dict[str, Any]]) -> None:
        """把一批模型結果寫回 JSON 候選。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            for result in results:
                ordinal = int(result["id"])
                candidate = self._candidate_unlocked(payload, ordinal)
                if candidate is None:
                    raise ValueError("smart review response id 不在候選快照")
                if result.get("validation_error"):
                    candidate["status"] = "invalid"
                    candidate["validation_error"] = str(result["validation_error"])
                    continue
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
                        "status": _candidate_status(result),
                    }
                )
            self._write_unlocked(run_id, payload)

    def mark_request_error(
        self, run_id: str, ordinals: Sequence[int], *, error_code: str
    ) -> None:
        """把請求失敗寫入 JSON 候選。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            wanted = {int(ordinal) for ordinal in ordinals}
            for candidate in payload.get("candidates") or []:
                if (
                    isinstance(candidate, dict)
                    and int(candidate.get("ordinal") or 0) in wanted
                ):
                    candidate["status"] = "request_failed"
                    candidate["validation_error"] = error_code
            self._write_unlocked(run_id, payload)

    def mark_pending_cancelled(self, run_id: str) -> int:
        """標記尚未送出的候選為取消。"""
        with self._lock:
            payload = self._read_unlocked(run_id)
            updated = 0
            for candidate in payload.get("candidates") or []:
                if isinstance(candidate, dict) and candidate.get("status") == "pending":
                    candidate["status"] = "cancelled"
                    candidate["validation_error"] = "cancelled_before_request"
                    updated += 1
            if updated:
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
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return None
        return {key: value for key, value in payload.items() if key != "candidates"}

    def all_runs(self) -> list[dict[str, Any]]:
        """讀取所有可解析的 JSON 批次，依新到舊排序。"""
        if not self.directory.is_dir():
            return []
        runs: list[dict[str, Any]] = []
        with self._lock:
            for path in self.directory.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(payload, dict) or not payload.get("id"):
                    continue
                runs.append(
                    {
                        key: value
                        for key, value in payload.items()
                        if key != "candidates"
                    }
                )
        return sorted(
            runs,
            key=lambda item: (
                str(item.get("started_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )

    def latest_run_marks(self) -> tuple[str | None, dict[int, tuple[str, str | None]]]:
        """回傳最近一次批次的 ``entry_id -> (狀態, 驗證錯誤)``，供條目列顯示狀態。

        先以檔案開頭的 ``started_at`` 排序（每個檔案只 stat 並讀檔頭，結果以
        (mtime, size) 快取），只完整解析勝出的那一個批次；候選狀態對照每個資料夾
        只保留這一份，歷史批次不會常駐記憶體。損壞的批次會略過並記錄警告。
        """
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
            ranked.append(((cached[1], stamp[0], path.stem), path, stamp))
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

    @staticmethod
    def _parse_marks(
        path: Path,
    ) -> tuple[str, dict[int, tuple[str, str | None]]] | None:
        """完整解析一個批次檔，回傳 (run_id, 條目狀態對照)；結構不符時記錄警告並回傳 None。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("略過無法讀取的智慧校對批次檔：%s", path)
            return None
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list):
            logger.warning(
                "略過格式不符（candidates 不是 list）的智慧校對批次檔：%s", path
            )
            return None
        marks: dict[int, tuple[str, str | None]] = {}
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
        """在單次 JSON 讀取中取得總數與目前頁面的候選。"""
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return 0, []
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
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return 0
            return sum(
                1
                for item in payload.get("candidates") or []
                if isinstance(item, dict)
                and (status is None or item.get("status") == status)
            )

    def breakdown(self, run_id: str, *, error_limit: int = 5) -> dict[str, Any]:
        """統計 JSON 批次狀態與驗證錯誤。"""
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return {"status": {}, "errors": []}
            candidates = [
                item
                for item in payload.get("candidates") or []
                if isinstance(item, dict)
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
            candidate = self._candidate_unlocked(payload, int(ordinal))
            return dict(candidate) if candidate is not None else None

    def update_candidate(self, run_id: str, ordinal: int, **updates: Any) -> bool:
        """原子更新 JSON 候選欄位。"""
        with self._lock:
            try:
                payload = self._read_unlocked(run_id)
            except FileNotFoundError:
                return False
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
