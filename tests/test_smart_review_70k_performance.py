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
from translation_tool.translation_db import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_LANG,
    KIND_KUBEJS_SCRIPT,
    TranslationDB,
)
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


# 模組包範圍（KubeJS／FTB）的候選 context：詞庫範圍是 entry_origin.pack_scope，
# 已審核列超過 bulk glossary 的 5,000 列上限時，每筆候選都要走詞組查詢。
_PACK_SCOPE = "bench-pack"
_PACK_REVIEWED_ROWS = 5_200
_PACK_CANDIDATES_PER_KIND = 300
_PACK_TOTAL_BUDGET_S = 60.0
# (kind, 模組, 未審核候選數；0 表示只用預設候選數)：語系 kind 另有超過 bulk 上限的已審核列，
# 腳本與任務沒有自己的詞庫，取用同包語系的詞庫
_PACK_SPECS = (
    (KIND_KUBEJS_LANG, tuple(f"mod{n:02d}@{_PACK_SCOPE}" for n in range(20)), 800),
    (KIND_KUBEJS_SCRIPT, (f"kubejs@{_PACK_SCOPE}",), 0),
    (KIND_FTB_QUEST_LANG, (f"ftbquests@{_PACK_SCOPE}",), 800),
    (KIND_FTB_QUESTS, (f"ftbquests@{_PACK_SCOPE}",), 0),
)


def _create_pack_scope_database(path: Path) -> TranslationDB:
    """建立 KubeJS／FTB 模組包：語系 kind 各有超過 bulk 上限的已審核列。"""
    db = TranslationDB(path)
    entries: list[tuple[str, str, str, str, str]] = []
    for kind, mods, candidates in _PACK_SPECS:
        reviewed = _PACK_REVIEWED_ROWS if kind in _LANG_KINDS else 0
        for index in range(reviewed + (candidates or _PACK_CANDIDATES_PER_KIND)):
            mod = mods[index % len(mods)]
            # 詞組在模組之間重複，已審核列與候選才會互相命中
            phrase = f"Machine Part {index % 400}"
            key = f"{kind}.{index:05d}"
            entries.append(
                (
                    kind,
                    mod,
                    key,
                    phrase,
                    "reviewed" if index < reviewed else "unreviewed",
                )
            )
    with db._tx() as conn:
        conn.executemany(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) VALUES (?,?,?,?,?)",
            [
                (kind, _VERSION, mod, key, phrase)
                for kind, mod, key, phrase, _ in entries
            ],
        )
        ids = {
            (kind, mod, key): entry_id
            for entry_id, kind, mod, key in conn.execute(
                "SELECT id, kind, mod_id, key FROM entry"
            )
        }
        rows = [
            (ids[(kind, mod, key)], phrase, status)
            for kind, mod, key, phrase, status in entries
        ]
        conn.executemany(
            "INSERT INTO translation (entry_id, source, zh_tw, review_status) VALUES (?,?,?,?)",
            [
                (entry_id, SRC_AI, f"譯 {phrase}", status)
                for entry_id, phrase, status in rows
            ],
        )
        conn.executemany(
            "INSERT INTO entry_origin (entry_id, pack_scope, source_kind, source_path, "
            "selector, source_locale) VALUES (?,?,?,?,?,?)",
            [
                (
                    ids[(kind, mod, key)],
                    _PACK_SCOPE,
                    kind,
                    f"pack/{key}",
                    f"lang:en_us:{key}",
                    "en_us",
                )
                for kind, mod, key, _phrase, _status in entries
            ],
        )
        db._refresh(conn, [entry_id for entry_id, _, _ in rows])
    return db


_LANG_KINDS = frozenset({KIND_KUBEJS_LANG, KIND_FTB_QUEST_LANG})


def run_pack_scope_context_gate(root: Path) -> dict[str, object]:
    """KubeJS／FTB 模組包範圍的候選 context（``include_context=True``）。

    以 SQL 敘述驗證成本而不只看時間：每筆候選的詞組查詢必須走 ``idx_entry_en``，
    不得出現會掃整個版本的 ``ORDER BY e.id LIMIT 96`` 形式。
    """
    started = time.perf_counter()
    db = _create_pack_scope_database(root / "smart-review-pack-scope.db")
    metrics: dict[str, object] = {"scope": _PACK_SCOPE}
    print("pack-scope stage=database_ready", flush=True)
    try:
        for kind, _mods, _candidates in _PACK_SPECS:
            statements: list[str] = []
            db._conn.set_trace_callback(statements.append)
            kind_started = time.perf_counter()
            try:
                snapshots = db.smart_review_candidates(
                    version=_VERSION,
                    kind=kind,
                    review_status="unreviewed",
                    limit=_PACK_CANDIDATES_PER_KIND,
                    include_context=True,
                )
            finally:
                db._conn.set_trace_callback(None)
            elapsed = time.perf_counter() - kind_started
            phrase_queries = [sql for sql in statements if "en_us IN (" in sql]
            metrics[f"{kind}_s"] = round(elapsed, 3)
            metrics[f"{kind}_phrase_queries"] = len(phrase_queries)
            print(f"pack-scope stage={kind}", flush=True)

            assert len(snapshots) == _PACK_CANDIDATES_PER_KIND
            assert any(snapshot.context for snapshot in snapshots)
            # 已審核列超過 bulk 上限，候選必須逐筆走詞組查詢，且全部走詞組索引
            assert phrase_queries, f"{kind}: 沒有走到被截斷的詞組查詢路徑"
            assert all("INDEXED BY idx_entry_en" in sql for sql in phrase_queries)
            assert not any("ORDER BY e.id LIMIT 96" in sql for sql in phrase_queries)
            plan = " ".join(
                str(row[3]) for row in db._q("EXPLAIN QUERY PLAN " + phrase_queries[0])
            )
            assert "idx_entry_en" in plan and "idx_entry_version" not in plan
    finally:
        db.close()
    metrics["total_s"] = round(time.perf_counter() - started, 3)
    print(
        "SMART_REVIEW_PACK_SCOPE_METRICS=" + json.dumps(metrics, ensure_ascii=False),
        flush=True,
    )
    assert float(metrics["total_s"]) <= _PACK_TOTAL_BUDGET_S
    return metrics


def test_smart_review_pack_scope_context_gate(tmp_path):
    """驗證 KubeJS／FTB 模組包範圍的候選 context 不會逐筆掃整個版本。"""
    if os.environ.get("SMART_REVIEW_70K_GATE") != "1":
        pytest.skip("set SMART_REVIEW_70K_GATE=1 to run the pack-scope context gate")
    run_pack_scope_context_gate(tmp_path)


def test_smart_review_70k_performance_gate(tmp_path):
    """驗證 70k scope count、chunked history preview 與純續跑 builder。"""
    if os.environ.get("SMART_REVIEW_70K_GATE") != "1":
        pytest.skip("set SMART_REVIEW_70K_GATE=1 to run the 70k performance gate")
    run_70k_gate(tmp_path)
