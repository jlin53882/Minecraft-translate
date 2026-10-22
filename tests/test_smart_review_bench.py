"""智慧校對歷史 1 萬筆規模的效能基準（預設跳過）。

執行方式：``RUN_BENCH=1 python -m pytest tests/test_smart_review_bench.py -s``，
或 ``-m slow`` 一併帶上。門檻刻意寬鬆（慢一個數量級才會失敗），目的是抓出
複雜度退化（例如分頁變成全量讀取），不是比較機器速度；實際耗時用 ``-s`` 印出。
"""

from __future__ import annotations

import os
import random
import sqlite3
import statistics
import time

import pytest

from tests.smart_review_history_support import (
    assert_summary_consistent,
    create_store_run,
    make_result,
    seed_translation_db,
    snapshot_bytes,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)

TOTAL = 10_000
CHUNK = 200
API_BATCH = 500
APPLY_SAMPLE = 300
PAGE_SIZE = 50

pytestmark = pytest.mark.slow


def _require_bench(config) -> None:
    """只有 RUN_BENCH=1 或明確選到 slow 標記時才執行（必須早於昂貴的 fixture）。"""
    markexpr = config.getoption("-m") or ""
    selected_slow = "slow" in markexpr and "not slow" not in markexpr
    if os.environ.get("RUN_BENCH") != "1" and not selected_slow:
        pytest.skip("效能基準預設跳過；設定 RUN_BENCH=1 或使用 -m slow")


class _Timer:
    """收集各操作耗時並在測試結束時印出摘要。"""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}

    def measure(self, name: str, func, *args, **kwargs):
        started = time.perf_counter()
        result = func(*args, **kwargs)
        self.samples.setdefault(name, []).append(time.perf_counter() - started)
        return result

    def p95(self, name: str) -> float:
        values = sorted(self.samples[name])
        return values[min(len(values) - 1, int(len(values) * 0.95))]

    def report(self) -> None:
        print("\n--- smart review history bench (10,000 candidates) ---")
        for name, values in self.samples.items():
            print(
                f"{name:<28} n={len(values):<4} "
                f"mean={statistics.mean(values) * 1000:9.1f} ms  "
                f"p95={self.p95(name) * 1000:9.1f} ms  "
                f"total={sum(values):8.2f} s"
            )


@pytest.fixture(scope="module")
def bench_run(request, tmp_path_factory):
    """建立 1 萬筆資料庫與批次：候選依 ordinal 輪流分成多種狀態。"""
    _require_bench(request.config)
    root = tmp_path_factory.mktemp("sr-bench")
    path = root / "bench.db"
    timer = _Timer()
    snapshots = timer.measure("seed_db(10k entries)", seed_translation_db, path, TOTAL)
    assert len(snapshots) == TOTAL
    store, run_id = timer.measure(
        "create_run(10k)",
        create_store_run,
        path,
        TOTAL,
        chunk_size=CHUNK,
        snapshots=snapshots,
    )
    for start in range(0, TOTAL, API_BATCH):
        results = []
        for item in snapshots[start : start + API_BATCH]:
            if item.ordinal % 100 == 0:
                results.append(make_result(item.ordinal, validation_error="bad_reason"))
            elif item.ordinal % 10 == 0:
                results.append(make_result(item.ordinal, decision="no_change"))
            else:
                results.append(
                    make_result(
                        item.ordinal, suggestion=f"{item.current_zh_tw}（修訂）"
                    )
                )
        timer.measure("record_results(500)", store.record_results, run_id, results)
    return path, store, run_id, snapshots, timer


def test_ten_thousand_candidates_page_filter_apply_reconcile(bench_run):
    """1 萬筆批次的分頁、狀態過濾、套用與全量對帳都維持可接受的成本。"""
    path, store, run_id, snapshots, timer = bench_run
    rng = random.Random(20260101)

    # 摘要：不讀分片。
    assert timer.measure("breakdown", store.breakdown, run_id)["status"] == {
        "ready": 9000,
        "no_change": 900,
        "invalid": 100,
    }
    assert timer.measure("get_run", store.get_run, run_id)["selected_count"] == TOTAL

    # 分頁：隨機頁，檢查內容正確（ordinal 連續）且只讀必要分片。
    for _ in range(100):
        offset = rng.randrange(0, TOTAL - PAGE_SIZE)
        total, rows = timer.measure(
            "page(all, 50)",
            store.page_candidates,
            run_id,
            limit=PAGE_SIZE,
            offset=offset,
        )
        assert total == TOTAL
        assert [r.ordinal for r in rows] == list(
            range(offset + 1, offset + 1 + PAGE_SIZE)
        )

    # 過濾：常見狀態與稀少狀態的深頁。
    for _ in range(50):
        offset = rng.randrange(0, 9000 - PAGE_SIZE)
        total, rows = timer.measure(
            "filter(ready, 50)",
            store.page_candidates,
            run_id,
            status="ready",
            limit=PAGE_SIZE,
            offset=offset,
        )
        assert total == 9000
        assert len(rows) == PAGE_SIZE
        assert {r.status for r in rows} == {"ready"}
    for offset in (0, 50):
        total, rows = timer.measure(
            "filter(invalid, 50)",
            store.page_candidates,
            run_id,
            status="invalid",
            limit=PAGE_SIZE,
            offset=offset,
        )
        assert total == 100
        assert [r.ordinal % 100 for r in rows] == [0] * PAGE_SIZE

    # 套用：抽樣逐筆套用（每次含 SQLite 交易與一個分片的原子替換）。
    db = TranslationDB(path)
    try:
        ready_ordinals = [item.ordinal for item in snapshots if item.ordinal % 10 != 0][
            :APPLY_SAMPLE
        ]
        for ordinal in ready_ordinals:
            assert (
                timer.measure(
                    "apply(1 candidate)",
                    db.apply_smart_review_candidate,
                    run_id,
                    ordinal,
                )
                == "applied"
            )

        # 對帳：以 SQLite 套用紀錄補回其餘 ready 候選的 JSON 狀態（全量 1 萬筆）。
        remaining = [
            item
            for item in snapshots
            if item.ordinal % 10 != 0 and item.ordinal not in set(ready_ordinals)
        ]
        connection = sqlite3.connect(path)
        try:
            connection.executemany(
                "INSERT INTO history (entry_id, batch, actor, action, new_zh_tw, note) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (
                        item.entry_id,
                        f"bench-{item.ordinal}",
                        "bench",
                        "smart_review_apply",
                        f"{item.current_zh_tw}（修訂）",
                        f"run={run_id}; ordinal={item.ordinal}",
                    )
                    for item in remaining
                ],
            )
            connection.commit()
        finally:
            connection.close()

        total, rows = timer.measure(
            "reconcile+page (first)",
            db.load_smart_review_candidate_page,
            run_id,
            limit=PAGE_SIZE,
        )
        assert total == TOTAL
        before = snapshot_bytes(store)
        timer.measure(
            "reconcile+page (idempotent)",
            db.load_smart_review_candidate_page,
            run_id,
            limit=PAGE_SIZE,
        )
        timer.measure(
            "marks(page of entries)",
            db.smart_review_marks,
            [s.entry_id for s in snapshots[:PAGE_SIZE]],
        )
        assert snapshot_bytes(store) == before
        assert db.smart_review_run_breakdown(run_id)["status"] == {
            "applied": 9000,
            "no_change": 900,
            "invalid": 100,
        }
        assert_summary_consistent(store, run_id, TOTAL)

        # 最近批次標籤：冷快取（只剩磁碟）與單筆更新後（只有一個分片變動）。
        SmartReviewHistoryStore.clear_caches()
        _run, cold_marks = timer.measure(
            "latest_run_marks (cold)", store.latest_run_marks
        )
        assert len(cold_marks) == TOTAL
        timer.measure("latest_run_marks (warm)", store.latest_run_marks)
        ordinal = next(item.ordinal for item in snapshots if item.ordinal % 100 == 0)
        store.update_candidate(run_id, ordinal, status="rejected")
        _run, changed_marks = timer.measure(
            "latest_run_marks (1 chunk changed)", store.latest_run_marks
        )
        assert len(changed_marks) == TOTAL
    finally:
        db.close()

    timer.report()
    # 門檻寬鬆：目的在抓出複雜度退化。
    assert timer.p95("page(all, 50)") < 1.0
    assert timer.p95("filter(ready, 50)") < 2.0
    assert timer.p95("filter(invalid, 50)") < 2.0
    assert statistics.mean(timer.samples["apply(1 candidate)"]) < 1.0
    assert timer.samples["reconcile+page (first)"][0] < 120.0
    assert timer.samples["reconcile+page (idempotent)"][0] < 10.0
