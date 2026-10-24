"""Smart Review 的真實 70k SQLite／歷史規劃效能閘門。

這個測試只有在 ``SMART_REVIEW_70K_GATE=1`` 時執行，避免一般單元測試每次
建立大型資料集。資料庫與 chunked history 都在測試內建立，setup、核心流程與
清理等待都由外層 gate 計時；因此不能以短小的 list fixture 取代真實資料量。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import time
from pathlib import Path

import pytest

from app.services_impl.moddb_smart_review_service import (
    preview_moddb_smart_review_plan_from_path,
)
from app.views.moddb.smart_review_run_planning import (
    ResumeSelection,
    build_resume_request,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_history_manifest import (
    ChunkManifestRepository,
)

_ROW_COUNT = 70_000
_VERSION = "1.21.1"
_CHUNK_SIZE = 200


def _working_set_mb() -> float | None:
    """讀取目前測試程序的 peak working set，不增加 runtime 依賴。"""
    if os.name != "nt":
        return None

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("PageFaultCount", ctypes.wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    get_info = ctypes.WinDLL("Psapi.dll").GetProcessMemoryInfo
    get_info.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(_Counters),
        ctypes.wintypes.DWORD,
    ]
    get_info.restype = ctypes.wintypes.BOOL
    if not get_info(process, ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / (1024 * 1024), 2)


def _create_70k_database(path: Path) -> TranslationDB:
    """建立真實 70k entry/translation/effective rows，不使用 fake list。"""
    db = TranslationDB(path)
    with db._tx() as conn:
        conn.executemany(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES (?,?,?,?,?)",
            [
                (
                    "lang",
                    _VERSION,
                    "performance-bench",
                    f"key_{index:05d}",
                    f"English {index}",
                )
                for index in range(_ROW_COUNT)
            ],
        )
        conn.executemany(
            "INSERT INTO translation (entry_id, source, zh_tw, review_status) "
            "VALUES (?,?,?,'unreviewed')",
            [(index, SRC_AI, f"翻譯 {index}") for index in range(1, _ROW_COUNT + 1)],
        )
        db._refresh(conn, range(1, _ROW_COUNT + 1))
    return db


def run_70k_gate(root: Path) -> dict[str, object]:
    """執行一次完整 gate；可由 pytest 與外層 timeout runner 共用。"""
    metrics: dict[str, object] = {"rows": _ROW_COUNT}
    started = time.perf_counter()
    db = _create_70k_database(root / "smart-review-70k.db")
    print("70k stage=database_ready", flush=True)
    original_q = TranslationDB._q
    original_one = TranslationDB._one
    original_read = ChunkManifestRepository.read_json_path
    try:
        setup_started = time.perf_counter()
        snapshots = db.smart_review_candidates(
            version=_VERSION,
            effective_source=SRC_AI,
            limit=0,
            include_context=False,
        )
        history = SmartReviewHistoryStore(db.path, storage_chunk_size=_CHUNK_SIZE)
        run_id = history.create_run(
            snapshots,
            provider="chatgpt",
            model="performance-model",
            version=_VERSION,
            requested_limit=0,
            batch_size=100,
            scope={
                "version": _VERSION,
                "mod_id": "performance-bench",
                "kind": "lang",
                "effective_source": SRC_AI,
                "review_status": "unreviewed",
                "query": "",
            },
        )
        metrics["setup_s"] = round(time.perf_counter() - setup_started, 3)
        print("70k stage=history_ready", flush=True)
        chunk_dir = history.directory / f"{run_id}.chunks"
        history_chunk_count = len(list(chunk_dir.glob("*.json")))
        metrics["history_chunks"] = history_chunk_count
        assert history_chunk_count == _ROW_COUNT // _CHUNK_SIZE
        assert len(snapshots) == _ROW_COUNT
        metrics["sqlite_rows"] = len(snapshots)

        count_started = time.perf_counter()
        count = db.count_smart_review_candidates(
            version=_VERSION,
            effective_source=SRC_AI,
        )
        modules, sources = db.smart_review_scope_options(version=_VERSION)
        metrics["count_scope_s"] = round(time.perf_counter() - count_started, 3)
        print("70k stage=scope_ready", flush=True)
        assert count == _ROW_COUNT
        assert modules == ("performance-bench",)
        assert sources[None] == (SRC_AI,)

        sql_counter = {"queries": 0}

        def traced_q(self, sql, params=()):
            if Path(self.path).resolve() == db.path.resolve():
                sql_counter["queries"] += 1
            return original_q(self, sql, params)

        def traced_one(self, sql, params=()):
            if Path(self.path).resolve() == db.path.resolve():
                sql_counter["queries"] += 1
            return original_one(self, sql, params)

        TranslationDB._q = traced_q
        TranslationDB._one = traced_one
        json_counter = {"reads": 0}

        def traced_read(path):
            json_counter["reads"] += 1
            return original_read(path)

        ChunkManifestRepository.read_json_path = staticmethod(traced_read)

        def preview_for(query: str) -> dict[str, object]:
            """執行一次與 UI 相同的唯讀 fast estimate。"""
            return preview_moddb_smart_review_plan_from_path(
                db.path,
                tuple(db.priority),
                version=_VERSION,
                mod_id="performance-bench",
                kind="lang",
                effective_source=SRC_AI,
                query=query,
                limit=0,
                batch_size=100,
                use_recent_history=True,
                skip_valid_history=True,
                model="performance-model",
            )

        preview_started = time.perf_counter()
        preview = preview_for("")
        metrics["preview_s"] = round(time.perf_counter() - preview_started, 3)
        print("70k stage=preview_ready", flush=True)
        assert preview["selected_count"] == _ROW_COUNT
        assert preview["estimated_to_process_count"] == _ROW_COUNT
        assert preview["history_runs_consulted"] == 1

        switch_sql_start = sql_counter["queries"]
        switch_json_start = json_counter["reads"]
        switch_started = time.perf_counter()
        switched_preview = preview_for("key_69999")
        metrics["scope_switch_s"] = round(time.perf_counter() - switch_started, 3)
        metrics["scope_switch_sql_queries"] = sql_counter["queries"] - switch_sql_start
        metrics["scope_switch_json_reads"] = json_counter["reads"] - switch_json_start
        assert switched_preview["selected_count"] == 1
        assert switched_preview["history_runs_consulted"] == 1
        metrics["sql_queries"] = sql_counter["queries"]
        metrics["json_reads"] = json_counter["reads"]

        selection = ResumeSelection(
            run_id=run_id,
            version=_VERSION,
            mod_id="performance-bench",
            kind="lang",
            effective_source=SRC_AI,
            query="",
            requested_limit=0,
            model="performance-model",
            batch_size=100,
            selected_count=_ROW_COUNT,
            processed_count=0,
            pending_count=_ROW_COUNT,
        )
        builder_started = time.perf_counter()
        last_request = None
        for _ in range(_ROW_COUNT):
            last_request = build_resume_request(
                selection,
                path=db.path,
                priority=tuple(db.priority),
                max_batch_size=100,
            )
        metrics["resume_builder_s"] = round(time.perf_counter() - builder_started, 3)
        assert last_request is not None
        assert last_request.resume_run_id == run_id
        assert last_request.effective_source == SRC_AI
        metrics["builder_requests"] = _ROW_COUNT
    finally:
        db.close()
        TranslationDB._q = original_q
        TranslationDB._one = original_one
        ChunkManifestRepository.read_json_path = original_read

    metrics["peak_working_set_mb"] = _working_set_mb()
    metrics["total_s"] = round(time.perf_counter() - started, 3)
    print(
        "SMART_REVIEW_70K_METRICS=" + json.dumps(metrics, ensure_ascii=False),
        flush=True,
    )
    assert float(metrics["total_s"]) <= 60.0
    return metrics


def test_smart_review_70k_performance_gate(tmp_path):
    """驗證 70k scope count、chunked history preview 與純續跑 builder。"""
    if os.environ.get("SMART_REVIEW_70K_GATE") != "1":
        pytest.skip("set SMART_REVIEW_70K_GATE=1 to run the 70k performance gate")
    run_70k_gate(tmp_path)
