"""智慧校對歷史的 manifest 與候選分片檔案存取。

``ChunkManifestRepository`` 擁有歷史目錄內的檔案配置：批次 manifest（或舊版單檔
JSON）、``<run_id>.chunks`` 候選分片目錄、原子替換寫入、分片 checkpoint 與 manifest
摘要重建、分片旁的 status sidecar，以及「分片先寫完、manifest 最後發布」的建立流程。方法名稱帶
``_unlocked`` 者要求呼叫端已持有 ``HistoryLock``；這個類別本身不取鎖。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from translation_tool.translation_db.smart_review_history_codec import (
    CHUNKED_STORAGE_FORMAT,
    SCHEMA_VERSION,
    candidate_counts_from_items,
    is_chunked,
    timestamp,
)
from translation_tool.translation_db.smart_review_history_sidecar import (
    StatusRow,
    build_sidecar,
    projection_revision_of,
    rows_if_current,
    sidecar_file_name,
    status_rows_from_candidates,
)
from translation_tool.translation_db.smart_review_status import SmartReviewStatus

logger = logging.getLogger(__name__)
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ChunkManifestRepository:
    """讀寫單一歷史目錄內的批次 manifest 與候選分片。"""

    def __init__(self, directory: Path, storage_chunk_size: int) -> None:
        """綁定歷史目錄與新批次使用的分片大小。"""
        self.directory = directory
        self.storage_chunk_size = storage_chunk_size

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

    def chunk_path_for(self, run_id: str, chunk_index: int) -> Path:
        """取得指定候選分片路徑。"""
        if int(chunk_index) < 0:
            raise ValueError("smart review chunk_index 不可為負數")
        return self._chunk_directory_for(run_id) / f"{int(chunk_index):06d}.json"

    def exists(self, run_id: str) -> bool:
        """判斷批次是否由 JSON 儲存。"""
        return self.path_for(run_id).is_file()

    @staticmethod
    def read_json_path(path: Path) -> dict[str, Any]:
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

    def read_unlocked(self, run_id: str) -> dict[str, Any]:
        """在呼叫端持有 Store 鎖時讀取批次 manifest 或舊版單檔 JSON。"""
        return self.read_json_path(self.path_for(run_id))

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

    def write_unlocked(self, run_id: str, payload: dict[str, Any]) -> None:
        """在呼叫端持有 Store 鎖時，以原子方式寫入批次 manifest。"""
        path = self.path_for(run_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        payload["checkpoint_at"] = timestamp()
        self._write_json_path_unlocked(path, payload)

    def write_chunk_unlocked(
        self, run_id: str, chunk_index: int, payload: dict[str, Any]
    ) -> None:
        """以原子方式寫入單一候選分片。"""
        self._write_chunk_path_unlocked(
            self.chunk_path_for(run_id, chunk_index), payload
        )

    def _write_chunk_path_unlocked(self, path: Path, payload: dict[str, Any]) -> None:
        """以原子方式寫入指定路徑的候選分片並更新其 revision。"""
        payload["checkpoint_at"] = timestamp()
        payload["chunk_revision"] = uuid.uuid4().hex
        self._write_json_path_unlocked(path, payload)

    def chunk_entries(self, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
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
                    entry = {
                        "index": index,
                        "ordinal_start": start,
                        "ordinal_end": end,
                        "count": count,
                    }
                    for key in (
                        "status_counts",
                        "error_counts",
                        "chunk_revision",
                        "signature",
                    ):
                        if key in raw:
                            entry[key] = raw[key]
                    entries.append(entry)
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

    def chunk_index_for_ordinal(
        self, manifest: Mapping[str, Any], ordinal: int
    ) -> int | None:
        """依 manifest 的 ordinal 範圍找到候選所在分片。"""
        value = int(ordinal)
        for entry in self.chunk_entries(manifest):
            if entry["ordinal_start"] <= value <= entry["ordinal_end"]:
                return entry["index"]
        return None

    def read_chunk_unlocked(self, run_id: str, chunk_index: int) -> dict[str, Any]:
        """讀取一個候選分片並驗證其 candidates 結構。"""
        path = self.chunk_path_for(run_id, chunk_index)
        payload = self.read_json_path(path)
        if not isinstance(payload.get("candidates"), list):
            raise TypeError(f"智慧校對候選分片格式無效：{path}")
        return payload

    @staticmethod
    def _validate_strict_chunk_entries(
        manifest: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
    ) -> None:
        """嚴格驗證 manifest 的分片數量與 ordinal 範圍是否完整。"""
        raw_chunk_count = manifest.get("chunk_count")
        if raw_chunk_count is not None:
            try:
                expected_chunk_count = max(0, int(raw_chunk_count))
            except (TypeError, ValueError) as exc:
                raise ValueError("智慧校對 manifest 的 chunk_count 無效") from exc
            if len(entries) != expected_chunk_count:
                raise ValueError("智慧校對 manifest 的分片數量不完整")
        raw_selected_count = manifest.get("selected_count")
        if raw_selected_count is not None:
            try:
                expected_selected_count = max(0, int(raw_selected_count))
            except (TypeError, ValueError) as exc:
                raise ValueError("智慧校對 manifest 的 selected_count 無效") from exc
            if sum(int(entry["count"]) for entry in entries) != expected_selected_count:
                raise ValueError("智慧校對 manifest 的候選數量不完整")
        previous_end: int | None = None
        for position, entry in enumerate(entries):
            start = int(entry["ordinal_start"])
            end = int(entry["ordinal_end"])
            count = int(entry["count"])
            if int(entry["index"]) != position:
                raise ValueError("智慧校對 manifest 的分片索引不連續")
            if (
                (previous_end is not None and start != previous_end + 1)
                or end < start
                or count != end - start + 1
            ):
                raise ValueError("智慧校對 manifest 的 ordinal 範圍不完整")
            previous_end = end

    @staticmethod
    def validated_chunk_candidates(
        run_id: str, entry: Mapping[str, Any], chunk: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """依 manifest 分片描述嚴格驗證一個分片，回傳其候選；不符時丟出 ValueError。"""
        candidates = chunk.get("candidates") or []
        if chunk.get("run_id") not in (None, run_id):
            raise ValueError("智慧校對候選分片的 run_id 不一致")
        if chunk.get("chunk_index") not in (None, entry["index"]):
            raise ValueError("智慧校對候選分片的索引不一致")
        if len(candidates) != int(entry["count"]):
            raise ValueError("智慧校對候選分片的候選數量不完整")
        seen_ordinals: set[int] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("智慧校對候選分片含有無效候選")  # noqa: TRY004 - 檔案內容不合法一律以 ValueError 回報，呼叫端依此隔離損壞分片
            try:
                ordinal = int(candidate["ordinal"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("智慧校對候選分片缺少有效 ordinal") from exc
            if (
                not (
                    int(entry["ordinal_start"]) <= ordinal <= int(entry["ordinal_end"])
                )
                or ordinal in seen_ordinals
            ):
                raise ValueError("智慧校對候選分片的 ordinal 不完整")
            seen_ordinals.add(ordinal)
        return candidates

    def iter_chunk_candidates_unlocked(
        self,
        run_id: str,
        manifest: Mapping[str, Any],
        *,
        strict: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """依 ordinal 順序串流遍歷分片中的候選。"""
        entries = self.chunk_entries(manifest)
        if strict:
            self._validate_strict_chunk_entries(manifest, entries)
        for entry in entries:
            try:
                chunk = self.read_chunk_unlocked(run_id, entry["index"])
            except (FileNotFoundError, OSError, ValueError, TypeError) as exc:
                logger.warning(
                    "智慧校對候選分片無法讀取：run_id=%s，chunk=%s，error=%s",
                    run_id,
                    entry["index"],
                    type(exc).__name__,
                )
                if strict:
                    raise ValueError(
                        f"智慧校對候選分片無法讀取：run_id={run_id}，chunk={entry['index']}"
                    ) from exc
                continue
            if strict:
                yield from self.validated_chunk_candidates(run_id, entry, chunk)
                continue
            candidates = chunk.get("candidates") or []
            for candidate in candidates:
                if isinstance(candidate, dict):
                    yield candidate

    def candidate_items_unlocked(
        self, run_id: str, payload: Mapping[str, Any]
    ) -> Iterator[dict[str, Any]]:
        """以相同介面遍歷舊版單檔或新版分片候選。"""
        if is_chunked(payload):
            yield from self.iter_chunk_candidates_unlocked(run_id, payload)
            return
        for candidate in payload.get("candidates") or []:
            if isinstance(candidate, dict):
                yield candidate

    def read_candidate_chunk_for_ordinal_unlocked(
        self, run_id: str, manifest: Mapping[str, Any], ordinal: int
    ) -> tuple[int, dict[str, Any]] | None:
        """只讀取指定 ordinal 所在分片，回傳分片與內容。"""
        chunk_index = self.chunk_index_for_ordinal(manifest, ordinal)
        if chunk_index is None:
            return None
        try:
            return chunk_index, self.read_chunk_unlocked(run_id, chunk_index)
        except (FileNotFoundError, OSError, ValueError, TypeError) as exc:
            logger.warning(
                "智慧校對指定候選分片無法讀取：run_id=%s，chunk=%s，error=%s",
                run_id,
                chunk_index,
                type(exc).__name__,
            )
            return None

    def status_sidecar_path_for(self, run_id: str, chunk_index: int) -> Path:
        """取得指定分片的 status sidecar 路徑。"""
        return self._chunk_directory_for(run_id) / sidecar_file_name(chunk_index)

    @staticmethod
    def expected_chunk_revision(
        manifest: Mapping[str, Any], entry: Mapping[str, Any]
    ) -> str | None:
        """取得 manifest 登記的分片 revision（sidecar 是否最新的唯一依據）。"""
        revisions = manifest.get("chunk_revisions")
        if isinstance(revisions, dict) and revisions.get(str(entry["index"])):
            return str(revisions[str(entry["index"])])
        revision = entry.get("chunk_revision")
        return str(revision) if revision else None

    def write_status_sidecar_unlocked(
        self,
        run_id: str,
        manifest: Mapping[str, Any],
        chunk_index: int,
        chunk: Mapping[str, Any],
        chunk_revision: str,
    ) -> bool:
        """寫入分片的 status sidecar；它是衍生資料，寫入失敗只記錄警告並回傳 False。"""
        payload = build_sidecar(
            run_id,
            chunk_index,
            chunk_revision,
            [item for item in chunk.get("candidates") or [] if isinstance(item, dict)],
            projection_revision=projection_revision_of(manifest),
        )
        try:
            self._write_json_path_unlocked(
                self.status_sidecar_path_for(run_id, chunk_index), payload
            )
        except OSError:
            logger.warning(
                "無法寫入智慧校對 status sidecar：run_id=%s，chunk=%s",
                run_id,
                chunk_index,
                exc_info=True,
            )
            return False
        return True

    def _chunk_file_matches_checkpoint(
        self, run_id: str, manifest: Mapping[str, Any], chunk_index: int
    ) -> bool:
        """分片檔案的 mtime／大小仍等於 manifest checkpoint；不符代表分片被外部改過或損壞。"""
        signatures = manifest.get("chunk_signatures")
        if not isinstance(signatures, dict):
            return False
        recorded = signatures.get(str(chunk_index))
        actual = self.chunk_signature(self.chunk_path_for(run_id, chunk_index))
        return actual is not None and recorded == actual

    def status_rows_for_chunk_unlocked(
        self, run_id: str, manifest: Mapping[str, Any], entry: Mapping[str, Any]
    ) -> list[StatusRow]:
        """讀取分片的狀態資料列：sidecar 最新就直接用，遺失或過期時從分片重建（不寫檔）。

        重建時以嚴格模式驗證分片，損壞會丟出 ``ValueError``，語意與完整解析相同。
        """
        index = int(entry["index"])
        try:
            sidecar = self.read_json_path(self.status_sidecar_path_for(run_id, index))
        except (OSError, ValueError, TypeError):
            sidecar = None
        if sidecar is not None and self._chunk_file_matches_checkpoint(
            run_id, manifest, index
        ):
            rows = rows_if_current(
                sidecar,
                run_id=run_id,
                chunk_index=index,
                expected_revision=self.expected_chunk_revision(manifest, entry),
                expected_count=int(entry["count"]),
            )
            if rows is not None:
                return rows
        try:
            chunk = self.read_chunk_unlocked(run_id, index)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(
                f"智慧校對候選分片無法讀取：run_id={run_id}，chunk={index}"
            ) from exc
        return status_rows_from_candidates(
            self.validated_chunk_candidates(run_id, entry, chunk)
        )

    def iter_status_rows_unlocked(
        self, run_id: str, manifest: Mapping[str, Any]
    ) -> Iterator[StatusRow]:
        """依 ordinal 順序串流遍歷各分片的狀態資料列；manifest 或分片不完整時丟出 ValueError。"""
        entries = self.chunk_entries(manifest)
        self._validate_strict_chunk_entries(manifest, entries)
        for entry in entries:
            yield from self.status_rows_for_chunk_unlocked(run_id, manifest, entry)

    @staticmethod
    def chunk_signature(path: Path) -> list[int] | None:
        """回傳分片檔案的 mtime 與大小，供 manifest checkpoint 對帳。"""
        try:
            stat = path.stat()
        except OSError:
            return None
        return [int(stat.st_mtime_ns), int(stat.st_size)]

    def update_chunk_checkpoint_unlocked(
        self,
        run_id: str,
        manifest: dict[str, Any],
        chunk_index: int,
        chunk: Mapping[str, Any],
        *,
        write_sidecar: bool = True,
    ) -> None:
        """把已寫入分片的 revision、摘要與檔案版本同步到 manifest，並更新該分片的 sidecar。

        sidecar 在 manifest 發布前寫入；若程序在兩者之間中止，manifest 仍指向舊 revision，
        下次讀取會判定 sidecar 過期並從分片重建。
        """
        summary = candidate_counts_from_items(
            [item for item in chunk.get("candidates") or [] if isinstance(item, dict)]
        )
        signature = self.chunk_signature(self.chunk_path_for(run_id, chunk_index))
        revision = chunk.get("chunk_revision")
        if revision is None and signature is not None:
            # 舊版分片沒有 revision；以檔案版本建立相容 checkpoint，避免每次
            # 只讀摘要都重新掃描同一批舊候選。
            revision = f"legacy:{signature[0]}:{signature[1]}"
        entries = self.chunk_entries(manifest)
        entry = next((item for item in entries if item["index"] == chunk_index), None)
        if entry is not None:
            entry["count"] = len(
                [
                    item
                    for item in chunk.get("candidates") or []
                    if isinstance(item, dict)
                ]
            )
            entry["status_counts"] = dict(summary["status_counts"])
            entry["error_counts"] = dict(summary["error_counts"])
            if revision:
                entry["chunk_revision"] = str(revision)
            for raw in manifest.get("chunks") or []:
                if isinstance(raw, dict) and int(raw.get("index", -1)) == chunk_index:
                    raw.update(entry)
                    break
        revisions = manifest.setdefault("chunk_revisions", {})
        if isinstance(revisions, dict) and revision:
            revisions[str(chunk_index)] = str(revision)
        signatures = manifest.setdefault("chunk_signatures", {})
        if isinstance(signatures, dict) and signature is not None:
            signatures[str(chunk_index)] = signature
        if write_sidecar and revision:
            self.write_status_sidecar_unlocked(
                run_id, manifest, chunk_index, chunk, str(revision)
            )

    def _chunk_checkpoints_current_unlocked(
        self, run_id: str, manifest: Mapping[str, Any]
    ) -> bool:
        """確認 manifest 的分片檔案版本仍與最後一次 checkpoint 相符。"""
        revisions = manifest.get("chunk_revisions")
        signatures = manifest.get("chunk_signatures")
        if not isinstance(revisions, dict) or not isinstance(signatures, dict):
            return False
        for entry in self.chunk_entries(manifest):
            index = str(entry["index"])
            signature = self.chunk_signature(
                self.chunk_path_for(run_id, entry["index"])
            )
            if signature is None or signatures.get(index) != signature:
                return False
            if not revisions.get(index):
                return False
        return True

    def chunk_summary_unlocked(
        self, run_id: str, manifest: Mapping[str, Any]
    ) -> dict[str, Any]:
        """掃描分片重建 manifest 摘要；只在 recovery 或明確統計時使用。"""
        candidates = list(self.iter_chunk_candidates_unlocked(run_id, manifest))
        summary = candidate_counts_from_items(candidates)
        expected = max(0, int(manifest.get("selected_count") or 0))
        missing = max(0, expected - len(candidates))
        if missing:
            status_counts = dict(summary["status_counts"])
            status_counts[SmartReviewStatus.PENDING] = (
                int(status_counts.get(SmartReviewStatus.PENDING, 0)) + missing
            )
            summary["status_counts"] = status_counts
            summary["pending_count"] = int(summary["pending_count"]) + missing
        if not any("ai_status" in item for item in candidates):
            # 舊版候選沒有 ai_status；人工套用後無法從目前 status 還原 AI
            # 結果，因此保留 manifest 已記錄的累計值，避免歷史統計倒退。
            for field in (
                "processed_count",
                "valid_count",
                "invalid_count",
                "request_error_count",
            ):
                summary[field] = max(int(summary[field]), int(manifest.get(field) or 0))
        return summary

    def ensure_chunked_summary_unlocked(
        self,
        run_id: str,
        manifest: dict[str, Any],
        *,
        persist: bool = True,
        force: bool = False,
    ) -> None:
        """在摘要過期或 checkpoint 不一致時重建 manifest 計數。

        ``persist=False`` 只更新目前記憶體中的摘要，供唯讀查詢避免在未取得
        寫入鎖時改寫 manifest；真正的候選變更仍使用預設值保存 checkpoint。
        ``force=True`` 略過「摘要仍是最新」的判斷，一律從分片重建（修復工具使用）。
        """
        if (
            not force
            and isinstance(manifest.get("status_counts"), dict)
            and isinstance(manifest.get("error_counts"), dict)
            and self._chunk_checkpoints_current_unlocked(run_id, manifest)
        ):
            return
        summary = self.chunk_summary_unlocked(run_id, manifest)
        for entry in self.chunk_entries(manifest):
            try:
                chunk = self.read_chunk_unlocked(run_id, entry["index"])
            except (FileNotFoundError, OSError, ValueError, TypeError):
                continue
            self.update_chunk_checkpoint_unlocked(
                run_id, manifest, entry["index"], chunk, write_sidecar=persist
            )
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
        if not persist:
            return
        try:
            self.write_unlocked(run_id, manifest)
        except OSError:
            logger.warning(
                "無法寫回智慧校對 manifest checkpoint：run_id=%s",
                run_id,
                exc_info=True,
            )

    def cleanup_unpublished_chunks_unlocked(self) -> None:
        """清理沒有 manifest 索引的建立中或孤立分片目錄。"""
        for staging in self.directory.glob(".*.chunks.*.tmp"):
            if staging.is_dir():
                shutil.rmtree(staging, ignore_errors=True)
        for chunk_directory in self.directory.glob("*.chunks"):
            run_id = chunk_directory.name[: -len(".chunks")]
            try:
                manifest_path = self.path_for(run_id)
            except ValueError:
                continue
            if not manifest_path.is_file():
                shutil.rmtree(chunk_directory, ignore_errors=True)

    def publish_chunked_run_unlocked(
        self,
        run_id: str,
        manifest: dict[str, Any],
        chunks: list[dict[str, Any]],
        candidates: Sequence[dict[str, Any]],
    ) -> None:
        """先完整寫入暫存分片目錄，再以最後一次 manifest replace 發布批次。

        跨程序鎖同時涵蓋目錄改名與 manifest 發布，避免 recovery 把合法的
        發布中分片誤判為孤立資料而刪除。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        staging_directory = self.directory / (
            f".{run_id}.chunks.{uuid.uuid4().hex}.tmp"
        )
        final_directory = self._chunk_directory_for(run_id)
        published = False
        try:
            staging_directory.mkdir(parents=True, exist_ok=False)
            for chunk in chunks:
                chunk_index = int(chunk["index"])
                start = chunk_index * self.storage_chunk_size
                chunk_payload = {
                    "schema_version": SCHEMA_VERSION,
                    "storage_format": CHUNKED_STORAGE_FORMAT,
                    "run_id": run_id,
                    "chunk_index": chunk_index,
                    "ordinal_start": chunk["ordinal_start"],
                    "ordinal_end": chunk["ordinal_end"],
                    "candidates": candidates[start : start + self.storage_chunk_size],
                }
                self._write_chunk_path_unlocked(
                    staging_directory / f"{chunk_index:06d}.json",
                    chunk_payload,
                )
                chunk["chunk_revision"] = chunk_payload["chunk_revision"]
                self._write_json_path_unlocked(
                    staging_directory / sidecar_file_name(chunk_index),
                    build_sidecar(
                        run_id,
                        chunk_index,
                        chunk_payload["chunk_revision"],
                        chunk_payload["candidates"],
                        projection_revision=projection_revision_of(manifest),
                    ),
                )
            staging_directory.replace(final_directory)
            for chunk in chunks:
                chunk_index = int(chunk["index"])
                signature = self.chunk_signature(
                    self.chunk_path_for(run_id, chunk_index)
                )
                manifest["chunk_revisions"][str(chunk_index)] = str(
                    chunk["chunk_revision"]
                )
                if signature is not None:
                    manifest["chunk_signatures"][str(chunk_index)] = signature
            # manifest 必須最後發布；失敗時不留下可被讀取的批次索引。
            self.write_unlocked(run_id, manifest)
            published = True
        finally:
            if not published:
                shutil.rmtree(staging_directory, ignore_errors=True)
                if not self.path_for(run_id).exists():
                    shutil.rmtree(final_directory, ignore_errors=True)
