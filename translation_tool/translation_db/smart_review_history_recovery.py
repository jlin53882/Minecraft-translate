"""智慧校對歷史的失聯批次復原。

``HistoryRecovery`` 負責「上一個程序死亡後留下什麼」：每個歷史目錄在本程序只掃描
一次，清理未發布的分片目錄，並把擁有者程序已不存在的 ``running`` 批次收斂為
``interrupted``。判斷批次是否失聯需要檔案鎖與 manifest 存取，所以由
``HistoryLock`` 與 ``ChunkManifestRepository`` 組合而成。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import ClassVar

from translation_tool.translation_db.smart_review_history_codec import (
    candidate_counts,
    is_chunked,
    timestamp,
)
from translation_tool.translation_db.smart_review_history_lock import HistoryLock
from translation_tool.translation_db.smart_review_history_manifest import (
    ChunkManifestRepository,
)

logger = logging.getLogger(__name__)


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


class HistoryRecovery:
    """掃描歷史目錄並復原擁有者程序已不存在的批次。"""

    # 本程序每個歷史目錄只需掃描一次失聯批次；避免每批 JSON 讀寫都重掃整個目錄。
    checked: ClassVar[set[Path]] = set()

    def __init__(
        self,
        directory: Path,
        lock: HistoryLock,
        repository: ChunkManifestRepository,
    ) -> None:
        """綁定歷史目錄與復原所需的鎖及 manifest 存取。"""
        self.directory = directory
        self.lock = lock
        self.repository = repository

    def recover_abandoned_runs(self, *, force: bool = False) -> list[str]:
        """把上一個已死亡程序留下的 ``running`` 批次收斂為 ``interrupted``。

        批次候選本身採原子 checkpoint，因此已完成的批次結果會保留；尚未
        寫入的候選仍維持 ``pending``，可在歷史批次中辨識為未處理。沒有
        ``owner_pid`` 的舊版 JSON 不自動改寫，避免誤判仍在執行的舊批次。
        """
        if not self.directory.is_dir():
            return []
        if not force and self.directory in self.checked:
            return []
        with (
            self.lock.process_lock,
            self.lock.recovery_interprocess(force=force) as acquired,
        ):
            if not acquired:
                logger.debug(
                    "略過目前無法取得的智慧校對 recovery 鎖：directory=%s",
                    self.directory,
                )
                return []
            if not force and self.directory in self.checked:
                return []
            self.checked.add(self.directory)
            self.repository.cleanup_unpublished_chunks_unlocked()
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
                    self.repository.path_for(run_id)
                except ValueError:
                    logger.warning("略過無效檔名的智慧校對批次檔：%s", path)
                    continue
                payload_run_id = payload.get("id")
                if payload_run_id not in (None, run_id):
                    logger.warning("略過檔名與內容 id 不一致的智慧校對批次檔：%s", path)
                    continue
                counts = (
                    self.repository.chunk_summary_unlocked(run_id, payload)
                    if is_chunked(payload)
                    else candidate_counts(payload)
                )
                recovered_at = timestamp()
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
                self.repository.write_unlocked(run_id, payload)
                recovered.append(run_id)
                logger.warning(
                    "已將非預期退出的智慧校對批次標記為 interrupted：run_id=%s，"
                    "已處理 %s 筆，未處理 %s 筆",
                    run_id,
                    counts["processed_count"],
                    counts["pending_count"],
                )
            return recovered
