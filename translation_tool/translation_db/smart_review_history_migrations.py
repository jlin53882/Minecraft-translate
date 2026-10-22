"""智慧校對歷史的 schema 版本升級、一致性檢查與修復。

三件事共用「歷史目錄長什麼樣子」這個知識，但互相獨立：

- **migration registry**：``MIGRATIONS`` 以來源版本對應升級函式；升級函式不改變輸入、
  具冪等性。目前寫入端的 ``schema_version`` 是 1，v1 到 v2 的升級只是已註冊的範例，
  沒有任何寫入流程會自動升版。
- **檢查器** ``check_history_directory``：唯讀掃描，回傳結構化的 ``HistoryIssue`` 清單。
- **修復** ``repair_history_directory``：重建 manifest 摘要與 status sidecar，把孤立
  檔案搬進 ``quarantine/`` 而不是刪除；``dry_run=True`` 只回報會做什麼。

檢查與修復都在歷史目錄的寫入鎖內執行，與應用程式的寫入互斥。
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from translation_tool.translation_db.smart_review_history_codec import (
    SCHEMA_VERSION,
    candidate_counts_from_items,
    is_chunked,
)
from translation_tool.translation_db.smart_review_history_lock import HistoryLock
from translation_tool.translation_db.smart_review_history_manifest import (
    ChunkManifestRepository,
)
from translation_tool.translation_db.smart_review_history_sidecar import (
    SIDECAR_SUFFIX,
    is_sidecar_file_name,
    rows_if_current,
    status_rows_from_candidates,
)
from translation_tool.translation_db.smart_review_status import ProjectionState
from translation_tool.utils.config_manager import get_smart_review_settings

logger = logging.getLogger(__name__)

QUARANTINE_DIRECTORY_NAME = "quarantine"
_CHUNK_FILE = re.compile(r"^(\d{6})\.json$")
_RUN_FILE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


# ---------------------------------------------------------------------------
# schema migration registry
# ---------------------------------------------------------------------------


class MigrationError(ValueError):
    """批次 payload 無法依 registry 升級到目標版本。"""


Migration = Callable[[dict[str, Any]], dict[str, Any]]


def payload_schema_version(payload: Mapping[str, Any]) -> int:
    """取得批次 payload 的 schema 版本；沒有欄位的舊檔視為 v1。"""
    raw = payload.get("schema_version")
    if raw is None:
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"schema_version 無效：{raw!r}") from exc


def upgrade_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """v1 到 v2 的範例升級：補上 ``projection`` 預設值並把版本記為 2。

    只補缺少的欄位、不覆寫既有值，所以對已是 v2 的 payload 重跑結果相同。
    """
    if not isinstance(payload.get("projection"), dict):
        payload["projection"] = {"revision": 0, "state": str(ProjectionState.PROJECTED)}
    payload["schema_version"] = 2
    return payload


# 來源版本 -> 升到下一版的函式。登錄 N 代表「可以從 N 升到 N + 1」。
MIGRATIONS: dict[int, Migration] = {1: upgrade_v1_to_v2}


def latest_known_schema_version() -> int:
    """registry 能升級到的最高 schema 版本。"""
    return max(MIGRATIONS, default=SCHEMA_VERSION - 1) + 1


def migrate_payload(
    payload: Mapping[str, Any], *, target: int = SCHEMA_VERSION
) -> dict[str, Any]:
    """回傳升級到 ``target`` 版的新 payload；已達或超過目標版本時只回傳複本。

    預設目標是目前寫入端的版本，因此現行資料不會被改動。
    """
    result = copy.deepcopy(dict(payload))
    version = payload_schema_version(result)
    if target > latest_known_schema_version():
        raise MigrationError(f"沒有升級到 schema v{target} 的路徑")
    while version < target:
        step = MIGRATIONS.get(version)
        if step is None:
            raise MigrationError(f"沒有從 schema v{version} 升級的函式")
        result = step(result)
        upgraded = payload_schema_version(result)
        if upgraded != version + 1:
            raise MigrationError(
                f"schema 升級函式沒有把版本從 {version} 推進到 {version + 1}"
            )
        version = upgraded
    return result


# ---------------------------------------------------------------------------
# 檢查器
# ---------------------------------------------------------------------------


class IssueCode(StrEnum):
    """一致性檢查回報的問題種類。"""

    MANIFEST_CORRUPT = "manifest_corrupt"
    MANIFEST_INVALID = "manifest_invalid"
    RUN_ID_MISMATCH = "run_id_mismatch"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    SCHEMA_OUTDATED = "schema_outdated"
    CHUNK_MISSING = "chunk_missing"
    CHUNK_CORRUPT = "chunk_corrupt"
    CHUNK_COUNT_MISMATCH = "chunk_count_mismatch"
    ORPHAN_CHUNK = "orphan_chunk"
    ORPHAN_CHUNK_DIRECTORY = "orphan_chunk_directory"
    STALE_TEMP = "stale_temp"
    SUMMARY_MISMATCH = "summary_mismatch"
    CHECKPOINT_STALE = "checkpoint_stale"
    SIDECAR_MISSING = "sidecar_missing"
    SIDECAR_CORRUPT = "sidecar_corrupt"
    SIDECAR_STALE = "sidecar_stale"
    SIDECAR_MISMATCH = "sidecar_mismatch"


# 修復工具能處理的問題；其餘（缺分片、損壞 JSON 等）需要從備份還原。
_REPAIRABLE_CODES = frozenset(
    {
        IssueCode.ORPHAN_CHUNK,
        IssueCode.ORPHAN_CHUNK_DIRECTORY,
        IssueCode.STALE_TEMP,
        IssueCode.SUMMARY_MISMATCH,
        IssueCode.CHECKPOINT_STALE,
        IssueCode.SIDECAR_MISSING,
        IssueCode.SIDECAR_CORRUPT,
        IssueCode.SIDECAR_STALE,
        IssueCode.SIDECAR_MISMATCH,
    }
)
_SIDECAR_CODES = frozenset(
    {
        IssueCode.SIDECAR_MISSING,
        IssueCode.SIDECAR_CORRUPT,
        IssueCode.SIDECAR_STALE,
        IssueCode.SIDECAR_MISMATCH,
    }
)
_WARNING_CODES = frozenset(
    {
        IssueCode.SCHEMA_OUTDATED,
        IssueCode.ORPHAN_CHUNK,
        IssueCode.ORPHAN_CHUNK_DIRECTORY,
        IssueCode.STALE_TEMP,
        IssueCode.CHECKPOINT_STALE,
        *_SIDECAR_CODES,
    }
)


@dataclass(frozen=True)
class HistoryIssue:
    """一個檢查發現的問題。``path`` 相對於歷史目錄，方便跨平台比對與顯示。"""

    code: IssueCode
    run_id: str | None
    path: str
    detail: str
    chunk_index: int | None = None

    @property
    def severity(self) -> str:
        """``warning`` 代表可由衍生資料重建；``error`` 代表資料本身不完整。"""
        return "warning" if self.code in _WARNING_CODES else "error"

    @property
    def repairable(self) -> bool:
        """修復工具是否能自動處理。"""
        return self.code in _REPAIRABLE_CODES

    def to_dict(self) -> dict[str, Any]:
        """轉成可輸出為 JSON 的字典。"""
        return {
            "code": str(self.code),
            "severity": self.severity,
            "repairable": self.repairable,
            "run_id": self.run_id,
            "path": self.path,
            "chunk_index": self.chunk_index,
            "detail": self.detail,
        }


def _relative(directory: Path, path: Path) -> str:
    """回傳相對歷史目錄的 POSIX 路徑。"""
    return path.relative_to(directory).as_posix()


def _read_json(path: Path) -> Any:
    """讀取 JSON 檔；失敗時丟出 ``ValueError``（含檔案不存在以外的 I/O 錯誤）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


class _RunChecker:
    """檢查單一批次（單檔或 manifest 加分片）並累積問題。"""

    def __init__(
        self,
        directory: Path,
        repository: ChunkManifestRepository,
        run_id: str,
        manifest: dict[str, Any],
    ) -> None:
        """綁定目錄、批次識別碼與已解析的 manifest。"""
        self.directory = directory
        self.repository = repository
        self.run_id = run_id
        self.manifest = manifest
        self.issues: list[HistoryIssue] = []
        self.chunk_errors = 0

    def add(
        self,
        code: IssueCode,
        path: Path,
        detail: str,
        chunk_index: int | None = None,
    ) -> None:
        """記錄一個問題。"""
        self.issues.append(
            HistoryIssue(
                code, self.run_id, _relative(self.directory, path), detail, chunk_index
            )
        )

    def check(self) -> list[HistoryIssue]:
        """執行此批次的全部檢查。"""
        manifest_path = self.repository.path_for(self.run_id)
        if self.manifest.get("id") not in (None, self.run_id):
            self.add(IssueCode.RUN_ID_MISMATCH, manifest_path, "檔名與內容 id 不一致")
        self._check_schema(manifest_path)
        if is_chunked(self.manifest):
            self._check_chunked(manifest_path)
        else:
            self._check_single_file(manifest_path)
        return self.issues

    def _check_schema(self, manifest_path: Path) -> None:
        """schema 版本比已知版本新代表需要較新的程式；比寫入端舊代表可升級。"""
        try:
            version = payload_schema_version(self.manifest)
        except MigrationError as exc:
            self.add(IssueCode.SCHEMA_UNSUPPORTED, manifest_path, str(exc))
            return
        if version > latest_known_schema_version():
            self.add(
                IssueCode.SCHEMA_UNSUPPORTED,
                manifest_path,
                f"schema v{version} 比此程式已知的 v{latest_known_schema_version()} 新",
            )
        elif version < SCHEMA_VERSION:
            self.add(
                IssueCode.SCHEMA_OUTDATED,
                manifest_path,
                f"schema v{version} 低於目前寫入版本 v{SCHEMA_VERSION}",
            )

    def _check_single_file(self, manifest_path: Path) -> None:
        """單檔批次：候選清單必須存在，且記錄的計數要與候選內容一致。"""
        candidates = self.manifest.get("candidates")
        if not isinstance(candidates, list):
            self.add(IssueCode.MANIFEST_INVALID, manifest_path, "candidates 不是 list")
            return
        items = [item for item in candidates if isinstance(item, dict)]
        actual = candidate_counts_from_items(items)
        self._compare_summary(manifest_path, actual, selected=len(candidates))

    def _compare_summary(
        self, path: Path, actual: Mapping[str, Any], *, selected: int
    ) -> None:
        """比較 manifest 記錄的 selected／status／error 計數與實際內容。"""
        problems = []
        recorded_selected = self.manifest.get("selected_count")
        if recorded_selected is not None and recorded_selected != selected:
            problems.append(
                f"selected_count 記錄 {recorded_selected!r}，實際 {selected}"
            )
        for key in ("status_counts", "error_counts"):
            recorded = self.manifest.get(key)
            if recorded is None:
                continue
            if not isinstance(recorded, dict) or _positive(recorded) != _positive(
                actual[key]
            ):
                problems.append(f"{key} 與候選實際內容不符")
        if problems:
            self.add(IssueCode.SUMMARY_MISMATCH, path, "；".join(problems))

    def _check_chunked(self, manifest_path: Path) -> None:
        """分片批次：manifest 結構、每個分片、孤立檔案、摘要、checkpoint 與 sidecar。"""
        entries = self.repository.chunk_entries(self.manifest)
        try:
            self.repository._validate_strict_chunk_entries(self.manifest, entries)
        except ValueError as exc:
            self.add(IssueCode.MANIFEST_INVALID, manifest_path, str(exc))
        chunks = self._check_chunk_files(entries)
        self._check_orphans(entries)
        if self.chunk_errors == 0 and len(chunks) == len(entries):
            self._check_chunk_summary(manifest_path, entries, chunks)
            if not self.repository._chunk_checkpoints_current_unlocked(
                self.run_id, self.manifest
            ):
                self.add(
                    IssueCode.CHECKPOINT_STALE,
                    manifest_path,
                    "chunk_revisions／chunk_signatures 與分片檔案不符",
                )
        self._check_sidecars(entries, chunks)

    def _check_chunk_files(
        self, entries: list[dict[str, Any]]
    ) -> dict[int, list[dict[str, Any]]]:
        """逐一讀取分片，回報缺少、損壞與數量不符；回傳可讀分片的候選。"""
        chunks: dict[int, list[dict[str, Any]]] = {}
        for entry in entries:
            index = int(entry["index"])
            path = self.repository.chunk_path_for(self.run_id, index)
            try:
                payload = _read_json(path)
            except FileNotFoundError:
                self.add(IssueCode.CHUNK_MISSING, path, "分片檔案不存在", index)
                self.chunk_errors += 1
                continue
            except ValueError as exc:
                self.add(IssueCode.CHUNK_CORRUPT, path, f"無法解析 JSON：{exc}", index)
                self.chunk_errors += 1
                continue
            candidates = (
                payload.get("candidates") if isinstance(payload, dict) else None
            )
            if not isinstance(candidates, list):
                self.add(IssueCode.CHUNK_CORRUPT, path, "candidates 不是 list", index)
                self.chunk_errors += 1
                continue
            try:
                self.repository.validated_chunk_candidates(self.run_id, entry, payload)
            except ValueError as exc:
                code = (
                    IssueCode.CHUNK_COUNT_MISMATCH
                    if len(candidates) != int(entry["count"])
                    else IssueCode.CHUNK_CORRUPT
                )
                self.add(code, path, str(exc), index)
                self.chunk_errors += 1
                continue
            chunks[index] = candidates
        return chunks

    def _check_orphans(self, entries: list[dict[str, Any]]) -> None:
        """分片目錄內不屬於 manifest 的檔案，以及發布中斷留下的暫存檔。"""
        chunk_directory = self.directory / f"{self.run_id}.chunks"
        if not chunk_directory.is_dir():
            return
        known = {int(entry["index"]) for entry in entries}
        for path in sorted(chunk_directory.iterdir()):
            name = path.name
            if name.startswith("."):
                self.add(IssueCode.STALE_TEMP, path, "寫入中斷留下的暫存檔")
                continue
            matched = _CHUNK_FILE.match(name)
            if matched and int(matched.group(1)) in known:
                continue
            stem = name[: -len(SIDECAR_SUFFIX)]
            if is_sidecar_file_name(name) and stem.isdigit() and int(stem) in known:
                continue
            self.add(IssueCode.ORPHAN_CHUNK, path, "manifest 沒有登錄此檔案")

    def _check_chunk_summary(
        self,
        manifest_path: Path,
        entries: list[dict[str, Any]],
        chunks: Mapping[int, list[dict[str, Any]]],
    ) -> None:
        """manifest 與各分片摘要必須等於分片實際內容。"""
        everything = [item for items in chunks.values() for item in items]
        self._compare_summary(
            manifest_path,
            candidate_counts_from_items(everything),
            selected=len(everything),
        )
        for entry in entries:
            recorded = entry.get("status_counts")
            if recorded is None:
                continue
            actual = candidate_counts_from_items(chunks[int(entry["index"])])
            if not isinstance(recorded, dict) or _positive(recorded) != _positive(
                actual["status_counts"]
            ):
                self.add(
                    IssueCode.SUMMARY_MISMATCH,
                    manifest_path,
                    f"分片 {entry['index']} 的 status_counts 與分片內容不符",
                    int(entry["index"]),
                )

    def _check_sidecars(
        self,
        entries: list[dict[str, Any]],
        chunks: Mapping[int, list[dict[str, Any]]],
    ) -> None:
        """每個可讀分片的 sidecar 必須存在、最新，且內容等於分片。"""
        for entry in entries:
            index = int(entry["index"])
            if index not in chunks:
                continue
            path = self.repository.status_sidecar_path_for(self.run_id, index)
            try:
                payload = _read_json(path)
            except FileNotFoundError:
                self.add(IssueCode.SIDECAR_MISSING, path, "sidecar 不存在", index)
                continue
            except ValueError as exc:
                self.add(
                    IssueCode.SIDECAR_CORRUPT, path, f"無法解析 JSON：{exc}", index
                )
                continue
            rows = (
                rows_if_current(
                    payload,
                    run_id=self.run_id,
                    chunk_index=index,
                    expected_revision=self.repository.expected_chunk_revision(
                        self.manifest, entry
                    ),
                    expected_count=int(entry["count"]),
                )
                if isinstance(payload, dict)
                else None
            )
            if rows is None:
                self.add(
                    IssueCode.SIDECAR_STALE,
                    path,
                    "sidecar 的 chunk_revision 與 manifest 不符或結構無效",
                    index,
                )
            elif rows != status_rows_from_candidates(chunks[index]):
                self.add(
                    IssueCode.SIDECAR_MISMATCH,
                    path,
                    "sidecar 狀態與分片候選不一致",
                    index,
                )


def _positive(counts: Mapping[str, Any]) -> dict[str, int]:
    """只保留大於 0 的計數，讓「沒有此鍵」與「計數為 0」視為相同。"""
    result: dict[str, int] = {}
    for key, value in counts.items():
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = -1
        if number != 0:
            result[str(key)] = number
    return result


def _default_repository(
    directory: Path, storage_chunk_size: int | None
) -> ChunkManifestRepository:
    """建立檢查／修復使用的 manifest 存取；既有 manifest 以自己記錄的分片大小為準。"""
    if storage_chunk_size is None:
        storage_chunk_size = int(get_smart_review_settings()["storage_chunk_size"])
    return ChunkManifestRepository(directory, max(1, int(storage_chunk_size)))


def _check_unlocked(
    directory: Path, repository: ChunkManifestRepository
) -> list[HistoryIssue]:
    """在已持有寫入鎖時掃描整個歷史目錄。"""
    issues: list[HistoryIssue] = []
    run_ids: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        run_id = path.stem
        if not _RUN_FILE.fullmatch(run_id):
            continue
        run_ids.add(run_id)
        try:
            manifest = _read_json(path)
        except ValueError as exc:
            issues.append(
                HistoryIssue(
                    IssueCode.MANIFEST_CORRUPT,
                    run_id,
                    _relative(directory, path),
                    f"無法解析 JSON：{exc}",
                )
            )
            continue
        if not isinstance(manifest, dict):
            issues.append(
                HistoryIssue(
                    IssueCode.MANIFEST_CORRUPT,
                    run_id,
                    _relative(directory, path),
                    "批次 JSON 不是 object",
                )
            )
            continue
        issues.extend(_RunChecker(directory, repository, run_id, manifest).check())
    issues.extend(_check_unattached_directories(directory, run_ids))
    return issues


def _check_unattached_directories(
    directory: Path, run_ids: set[str]
) -> list[HistoryIssue]:
    """沒有 manifest 的分片目錄，以及批次建立中斷留下的暫存目錄。"""
    issues = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir():
            continue
        if path.name.startswith(".") and ".chunks." in path.name:
            issues.append(
                HistoryIssue(
                    IssueCode.STALE_TEMP,
                    None,
                    _relative(directory, path),
                    "批次建立中斷留下的暫存分片目錄",
                )
            )
        elif (
            path.name.endswith(".chunks")
            and path.name[: -len(".chunks")] not in run_ids
        ):
            issues.append(
                HistoryIssue(
                    IssueCode.ORPHAN_CHUNK_DIRECTORY,
                    path.name[: -len(".chunks")],
                    _relative(directory, path),
                    "沒有對應的批次 manifest",
                )
            )
    return issues


def check_history_directory(
    directory: str | Path, *, storage_chunk_size: int | None = None
) -> list[HistoryIssue]:
    """唯讀檢查歷史目錄，回傳依路徑排序的問題清單（沒有問題時為空）。

    涵蓋：損壞的 manifest／分片 JSON、缺少的分片、孤立檔案、manifest 計數與分片實際
    內容不符、sidecar 遺失或過期、schema 版本。目錄不存在視為沒有歷史。
    """
    root = Path(directory)
    if not root.is_dir():
        return []
    repository = _default_repository(root, storage_chunk_size)
    with HistoryLock(root).write():
        issues = _check_unlocked(root, repository)
    return sorted(issues, key=lambda item: (item.path, str(item.code)))


# ---------------------------------------------------------------------------
# 修復
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepairAction:
    """修復工具做過（或在 dry-run 時預計要做）的一個動作。"""

    kind: str
    run_id: str | None
    path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """轉成可輸出為 JSON 的字典。"""
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "path": self.path,
            "detail": self.detail,
        }


@dataclass
class RepairReport:
    """修復結果：動作清單與修復後仍存在的問題。"""

    dry_run: bool
    actions: list[RepairAction] = field(default_factory=list)
    unresolved: list[HistoryIssue] = field(default_factory=list)
    quarantine_directory: str | None = None


def _quarantine_destination(directory: Path, stamp: str, source: Path) -> Path:
    """決定隔離位置：``quarantine/<stamp>/<原相對路徑>``，已存在時加序號。"""
    base = directory / QUARANTINE_DIRECTORY_NAME / stamp / _relative(directory, source)
    candidate, counter = base, 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}.{counter}")
        counter += 1
    return candidate


def _plan_repairs(
    directory: Path, issues: list[HistoryIssue]
) -> tuple[list[RepairAction], list[HistoryIssue]]:
    """把可修復的問題整理成動作；回傳 (動作, 仍無法處理的問題)。"""
    actions: list[RepairAction] = []
    full_rebuild: set[str] = set()
    blocked_runs = {
        issue.run_id
        for issue in issues
        if issue.run_id
        and not issue.repairable
        and issue.code != IssueCode.SCHEMA_OUTDATED
    }
    unresolved = [issue for issue in issues if not issue.repairable]
    for issue in issues:
        if not issue.repairable:
            continue
        if issue.code in {
            IssueCode.ORPHAN_CHUNK,
            IssueCode.ORPHAN_CHUNK_DIRECTORY,
            IssueCode.STALE_TEMP,
        }:
            actions.append(
                RepairAction("quarantine", issue.run_id, issue.path, issue.detail)
            )
        elif issue.run_id in blocked_runs and issue.code not in _SIDECAR_CODES:
            unresolved.append(issue)
        elif issue.code in {IssueCode.SUMMARY_MISMATCH, IssueCode.CHECKPOINT_STALE}:
            full_rebuild.add(str(issue.run_id))
        else:
            actions.append(
                RepairAction("rebuild_sidecar", issue.run_id, issue.path, issue.detail)
            )
    # 完整重建會連同全部 sidecar 一起重寫，單獨的 sidecar 動作不必重複列出。
    actions = [
        action
        for action in actions
        if not (action.kind == "rebuild_sidecar" and action.run_id in full_rebuild)
    ]
    for run_id in sorted(full_rebuild):
        actions.append(
            RepairAction(
                "rebuild_manifest_summary",
                run_id,
                f"{run_id}.json",
                "從分片重建 manifest 摘要、checkpoint 與全部 sidecar",
            )
        )
    return actions, unresolved


def _rebuild_single_file_summary(
    repository: ChunkManifestRepository, run_id: str, manifest: dict[str, Any]
) -> None:
    """單檔批次：以候選內容重算 selected／status／error 計數。"""
    candidates = [c for c in manifest.get("candidates") or [] if isinstance(c, dict)]
    counts = candidate_counts_from_items(candidates)
    manifest["selected_count"] = len(manifest.get("candidates") or [])
    manifest["status_counts"] = counts["status_counts"]
    manifest["error_counts"] = counts["error_counts"]
    repository.write_unlocked(run_id, manifest)


def _rebuild_manifest_summary(repository: ChunkManifestRepository, run_id: str) -> None:
    """重建一個批次的摘要：分片批次連同 checkpoint 與 sidecar，單檔批次只重算計數。"""
    manifest = repository.read_unlocked(run_id)
    if is_chunked(manifest):
        repository.ensure_chunked_summary_unlocked(run_id, manifest, force=True)
    else:
        _rebuild_single_file_summary(repository, run_id, manifest)


def _rebuild_sidecar(repository: ChunkManifestRepository, action: RepairAction) -> None:
    """依 manifest 登記的 revision 重寫一個分片的 sidecar。"""
    run_id = str(action.run_id)
    index = int(Path(action.path).name.split(".")[0])
    manifest = repository.read_unlocked(run_id)
    entry = next(e for e in repository.chunk_entries(manifest) if e["index"] == index)
    revision = repository.expected_chunk_revision(manifest, entry)
    chunk = repository.read_chunk_unlocked(run_id, index)
    if revision is None:
        return  # manifest 沒有登記 revision：需先重建 checkpoint，留給事後檢查回報
    if not repository.write_status_sidecar_unlocked(
        run_id, manifest, index, chunk, revision
    ):
        raise OSError(f"無法重建 sidecar：{action.path}")


def _apply_actions(
    directory: Path,
    repository: ChunkManifestRepository,
    actions: list[RepairAction],
) -> str:
    """依序執行修復動作；回傳本次使用的隔離目錄名稱。"""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for action in actions:
        if action.kind == "quarantine":
            source = directory / action.path
            target = _quarantine_destination(directory, stamp, source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        elif action.kind == "rebuild_manifest_summary":
            _rebuild_manifest_summary(repository, str(action.run_id))
        else:
            _rebuild_sidecar(repository, action)
    return stamp


def repair_history_directory(
    directory: str | Path,
    *,
    dry_run: bool = False,
    storage_chunk_size: int | None = None,
) -> RepairReport:
    """修復歷史目錄中可重建的問題；``dry_run=True`` 不改任何檔案。

    - 重建 manifest 摘要、checkpoint 與 status sidecar（來源是分片實際內容）。
    - 孤立分片、孤立分片目錄與寫入中斷的暫存檔搬進 ``quarantine/<時間戳>/``，不刪除。
    - 缺少或損壞的分片、損壞的 manifest 無法由本工具重建，列在 ``unresolved``，
      請從備份還原。
    """
    root = Path(directory)
    report = RepairReport(dry_run=dry_run)
    if not root.is_dir():
        return report
    repository = _default_repository(root, storage_chunk_size)
    with HistoryLock(root).write():
        issues = _check_unlocked(root, repository)
        actions, unresolved = _plan_repairs(root, issues)
        report.actions = actions
        if dry_run:
            report.unresolved = unresolved
            return report
        if any(action.kind == "quarantine" for action in actions):
            stamp = _apply_actions(root, repository, actions)
            report.quarantine_directory = f"{QUARANTINE_DIRECTORY_NAME}/{stamp}"
        else:
            _apply_actions(root, repository, actions)
        report.unresolved = _check_unlocked(root, repository)
    logger.info(
        "智慧校對歷史修復完成：directory=%s，動作 %s 個，未解決 %s 個",
        root,
        len(report.actions),
        len(report.unresolved),
    )
    return report


def summarize_issues(issues: list[HistoryIssue]) -> dict[str, int]:
    """依問題種類統計數量，供 CLI 摘要使用。"""
    return dict(Counter(str(issue.code) for issue in issues))


__all__ = [
    "MIGRATIONS",
    "QUARANTINE_DIRECTORY_NAME",
    "HistoryIssue",
    "IssueCode",
    "MigrationError",
    "RepairAction",
    "RepairReport",
    "check_history_directory",
    "latest_known_schema_version",
    "migrate_payload",
    "payload_schema_version",
    "repair_history_directory",
    "summarize_issues",
    "upgrade_v1_to_v2",
]
