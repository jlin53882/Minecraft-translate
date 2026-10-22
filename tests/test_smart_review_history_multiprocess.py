"""智慧校對歷史儲存的跨程序測試（P2-07）。

每個情境都啟動獨立的 Python 程序（見 ``smart_review_history_support``），
透過公開 API 競爭同一個歷史目錄；崩潰以 ``os._exit`` 在 ``os.replace`` 的
前後模擬程序被殺掉，鎖由作業系統在程序結束時釋放。
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

import pytest

from tests.smart_review_history_support import (
    CRASH_EXIT_CODE,
    assert_disk_manifest_matches_candidates,
    assert_summary_consistent,
    create_store_run,
    finish,
    make_result,
    no_leftover_directories,
    raw_candidates,
    read_json_files,
    read_line,
    release_together,
    run_worker,
    seed_ready_run,
    spawn,
    tracked_workers,
)
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="跨程序崩潰注入以 POSIX 檔案鎖語意為準"
)


@pytest.fixture
def db_path(tmp_path):
    """歷史目錄會建在此路徑旁；資料庫本身在僅 store 的測試中不需要存在。"""
    return tmp_path / "review.db"


# ---------------------------------------------------------------------------
# 多程序同時寫入
# ---------------------------------------------------------------------------


def test_concurrent_processes_record_results_without_loss(db_path, tmp_path):
    """4 個程序交錯寫入同一批次的同一批分片：不遺失、不損壞、摘要一致。"""
    total, workers, chunk = 48, 4, 8
    _store, run_id = create_store_run(db_path, total, chunk_size=chunk)
    go = tmp_path / "go"
    ready = [tmp_path / f"ready-{i}" for i in range(workers)]

    with tracked_workers() as procs:
        for index in range(workers):
            procs.append(
                spawn(
                    "writer",
                    tmp_path,
                    db=db_path,
                    run=run_id,
                    chunk=chunk,
                    index=index,
                    workers=workers,
                    total=total,
                    ready=ready[index],
                    go=go,
                )
            )
        release_together(go, ready)
        outputs = [finish(proc) for proc in procs]

    for code, out, err in outputs:
        assert code == 0, err
    written = sorted(
        int(line.split()[1])
        for _code, out, _err in outputs
        for line in out.splitlines()
        if line.startswith("OK")
    )
    assert written == list(range(1, total + 1))

    fresh = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    assert {c["status"] for c in raw_candidates(fresh, run_id, total)} == {"ready"}
    assert_summary_consistent(fresh, run_id, total)
    assert_disk_manifest_matches_candidates(fresh, run_id, total)
    assert fresh.get_run(run_id)["processed_count"] == total
    assert len(read_json_files(fresh)) == 1 + total // chunk
    assert not list(fresh.directory.rglob("*.tmp"))


def test_concurrent_processes_create_runs_without_orphans(db_path, tmp_path):
    """多個程序同時建立大批次：每個批次完整可讀，沒有孤立分片目錄。"""
    workers, count, chunk = 4, 13, 5
    go = tmp_path / "go"
    ready = [tmp_path / f"ready-{i}" for i in range(workers)]
    with tracked_workers() as procs:
        for index in range(workers):
            procs.append(
                spawn(
                    "creator",
                    tmp_path,
                    db=db_path,
                    chunk=chunk,
                    count=count,
                    ready=ready[index],
                    go=go,
                )
            )
        release_together(go, ready)
        results = [finish(proc) for proc in procs]

    run_ids = []
    for code, out, err in results:
        assert code == 0, err
        run_ids.append(out.strip().splitlines()[-1])
    assert len(set(run_ids)) == workers

    store = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    listed = {run["id"] for run in store.all_runs()}
    assert listed == set(run_ids)
    for run_id in run_ids:
        total, rows = store.page_candidates(run_id, limit=None)
        assert total == count
        assert [row.ordinal for row in rows] == list(range(1, count + 1))
    assert no_leftover_directories(store)


# ---------------------------------------------------------------------------
# 崩潰後由另一個程序復原
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", ["before_chunk", "after_chunk", "before_manifest", "after_manifest"]
)
def test_record_results_crash_points_are_recoverable(db_path, tmp_path, mode):
    """record_results 在分片／manifest 替換前後被殺：讀取一致，重送結果後收斂。"""
    total, chunk = 12, 4
    _store, run_id = create_store_run(db_path, total, chunk_size=chunk)
    # 第 3、6、10 筆落在 3 個不同分片，崩潰時分片可能只更新到一部分。
    ordinals = "3,6,10"
    run_worker(
        "crash_record",
        tmp_path,
        expect=CRASH_EXIT_CODE,
        db=db_path,
        run=run_id,
        chunk=chunk,
        mode=mode,
        ordinals=ordinals,
    )

    # 崩潰的程序不可留下損壞的已發布 JSON；暫存檔不會被讀成批次。
    reader = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    read_json_files(reader)
    # 任何一個崩潰點，公開摘要都必須反映分片實際內容（不信任過期的 manifest）。
    assert_summary_consistent(reader, run_id, total)
    assert reader.get_run(run_id)["status"] == "running"

    # 另一個程序重送同一批結果（冪等），最後全部到位。
    resumed = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    resumed.record_results(run_id, [make_result(o) for o in (3, 6, 10)])
    statuses = {
        c["ordinal"]: c["status"] for c in raw_candidates(resumed, run_id, total)
    }
    assert [statuses[o] for o in (3, 6, 10)] == ["ready"] * 3
    assert sum(1 for s in statuses.values() if s == "pending") == total - 3
    assert_summary_consistent(resumed, run_id, total)
    assert_disk_manifest_matches_candidates(resumed, run_id, total)


def test_crash_after_chunk_replace_is_repaired_by_a_separate_process(db_path, tmp_path):
    """第一個分片已替換、manifest 未更新時被殺；獨立的第三個程序讀到正確計數並可續寫。"""
    total, chunk = 12, 4
    _store, run_id = create_store_run(db_path, total, chunk_size=chunk)
    run_worker(
        "crash_record",
        tmp_path,
        expect=CRASH_EXIT_CODE,
        db=db_path,
        run=run_id,
        chunk=chunk,
        mode="after_chunk",
        ordinals="1,2,5,9",
    )
    # 第一個分片（ordinal 1、2）已落盤，其餘分片沒有。
    reader = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    assert reader.breakdown(run_id)["status"] == {"ready": 2, "pending": total - 2}

    # 由獨立程序補寫其餘結果。
    out = run_worker(
        "writer",
        tmp_path,
        db=db_path,
        run=run_id,
        chunk=chunk,
        index=0,
        workers=1,
        total=total,
    )
    assert out.count("OK") == total
    final = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    assert final.breakdown(run_id)["status"] == {"ready": total}
    assert_disk_manifest_matches_candidates(final, run_id, total)


@pytest.mark.parametrize("mode", ["before_publish", "before_manifest"])
def test_create_run_crash_leaves_no_visible_run_and_orphans_are_cleaned(
    db_path, tmp_path, mode
):
    """建立大批次時在發布前被殺：不產生可見批次，孤立分片由下一個程序清掉。"""
    run_worker(
        "crash_create",
        tmp_path,
        expect=CRASH_EXIT_CODE,
        db=db_path,
        chunk=5,
        count=17,
        mode=mode,
    )
    directory = SmartReviewHistoryStore(db_path, storage_chunk_size=5).directory
    assert directory.is_dir()
    assert not list(directory.glob("*.json")), "manifest 必須最後才發布"

    other = SmartReviewHistoryStore(db_path, storage_chunk_size=5)
    assert other.all_runs() == []
    assert no_leftover_directories(other)

    # 清理後仍可正常建立批次。
    _store, run_id = create_store_run(db_path, 17, chunk_size=5)
    assert [run["id"] for run in other.all_runs()] == [run_id]
    assert other.count_candidates(run_id) == 17


def test_sigkill_during_writes_never_corrupts_history(db_path, tmp_path):
    """對持續寫入中的程序送 SIGKILL（崩潰點隨機）：已發布 JSON 皆完整，重送後收斂。"""
    total, chunk = 60, 6
    _store, run_id = create_store_run(db_path, total, chunk_size=chunk)
    with tracked_workers() as procs:
        proc = spawn(
            "writer",
            tmp_path,
            db=db_path,
            run=run_id,
            chunk=chunk,
            index=0,
            workers=1,
            total=total,
        )
        procs.append(proc)
        first = read_line(proc)
        assert first.startswith("OK")
        os.kill(proc.pid, signal.SIGKILL)
        code, _out, _err = finish(proc)
    assert code == -signal.SIGKILL or code != 0

    reader = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk)
    read_json_files(reader)
    assert_summary_consistent(reader, run_id, total)

    reader.record_results(run_id, [make_result(o) for o in range(1, total + 1)])
    assert reader.breakdown(run_id)["status"] == {"ready": total}
    assert_disk_manifest_matches_candidates(reader, run_id, total)


# ---------------------------------------------------------------------------
# 批次擁有者死亡與復原
# ---------------------------------------------------------------------------


def test_dead_owner_run_is_recovered_by_another_process_and_keeps_results(
    db_path, tmp_path
):
    """擁有者程序死亡（未 finish）：另一個程序標記為 interrupted，已完成結果保留。"""
    out = run_worker("creator", tmp_path, db=db_path, chunk=4, count=10, record="1,2,3")
    run_id = out.strip().splitlines()[-1]

    store = SmartReviewHistoryStore(db_path, storage_chunk_size=4)
    assert store.recover_abandoned_runs(force=True) == [run_id]
    run = store.get_run(run_id)
    assert run["status"] == "interrupted"
    assert run["processed_count"] == 3
    assert run["metrics"]["recovered_after_unexpected_exit"] is True
    assert run["metrics"]["interrupted_pending_count"] == 7
    assert store.breakdown(run_id)["status"] == {"ready": 3, "pending": 7}
    # 冪等：再次復原不會重複標記。
    assert store.recover_abandoned_runs(force=True) == []


def test_live_owner_run_is_not_recovered_by_another_process(db_path, tmp_path):
    """擁有者程序仍存活時，其他程序不可把批次標成 interrupted。"""
    with tracked_workers() as procs:
        owner = spawn("creator", tmp_path, db=db_path, chunk=4, count=8, hold=1)
        procs.append(owner)
        run_id = read_line(owner)

        other = SmartReviewHistoryStore(db_path, storage_chunk_size=4)
        assert other.recover_abandoned_runs(force=True) == []
        assert other.get_run(run_id)["status"] == "running"

        assert owner.stdin is not None
        owner.stdin.close()
        code, _out, err = finish(owner)
        assert code == 0, err

    assert other.recover_abandoned_runs(force=True) == [run_id]
    assert other.get_run(run_id)["status"] == "interrupted"


def test_many_processes_recover_the_same_runs_exactly_once(db_path, tmp_path):
    """多個程序同時復原同一批失聯批次：每個批次只被復原一次，內容不損壞。"""
    out = run_worker("orphans", tmp_path, db=db_path, chunk=4, runs=3, count=9)
    orphan_ids = out.split()
    assert len(orphan_ids) == 3

    workers = 4
    go = tmp_path / "go"
    ready = [tmp_path / f"ready-{i}" for i in range(workers)]
    with tracked_workers() as procs:
        for index in range(workers):
            procs.append(
                spawn(
                    "recover",
                    tmp_path,
                    db=db_path,
                    chunk=4,
                    force=1,
                    ready=ready[index],
                    go=go,
                )
            )
        release_together(go, ready)
        results = [finish(proc) for proc in procs]

    recovered: list[str] = []
    for code, out, err in results:
        assert code == 0, err
        recovered.extend(json.loads(out.strip().splitlines()[-1])["recovered"])
    assert sorted(recovered) == sorted(orphan_ids)

    store = SmartReviewHistoryStore(db_path, storage_chunk_size=4)
    for run_id in orphan_ids:
        run = store.get_run(run_id)
        assert run["status"] == "interrupted"
        assert run["metrics"]["interrupted_pending_count"] == 9
        assert_summary_consistent(store, run_id, 9)
    read_json_files(store)


# ---------------------------------------------------------------------------
# apply 與 reconcile 同時進行
# ---------------------------------------------------------------------------


def test_apply_and_reconcile_run_concurrently_in_separate_processes(tmp_path):
    """一個程序逐筆 apply，另外兩個程序不斷讀取（對帳）：沒有重複套用，最終全部 applied。"""
    import sqlite3

    total, chunk = 24, 6
    path = tmp_path / "apply.db"
    _store, run_id, snapshots = seed_ready_run(path, total, chunk_size=chunk)
    entries = ",".join(str(item.entry_id) for item in snapshots)
    ordinals = ",".join(str(item.ordinal) for item in snapshots)
    stop = tmp_path / "stop"
    go = tmp_path / "go"
    ready = [tmp_path / f"ready-{i}" for i in range(3)]

    with tracked_workers() as procs:
        applier = spawn(
            "apply_range",
            tmp_path,
            db=path,
            run=run_id,
            ordinals=ordinals,
            ready=ready[0],
            go=go,
        )
        readers = [
            spawn(
                "reader",
                tmp_path,
                db=path,
                run=run_id,
                entries=entries,
                stop=stop,
                ready=ready[i],
                go=go,
            )
            for i in (1, 2)
        ]
        procs.extend([applier, *readers])
        release_together(go, ready)
        applied_code, applied_out, applied_err = finish(applier)
        stop.touch()
        reader_results = [finish(reader) for reader in readers]

    assert applied_code == 0, applied_err
    assert [line.split(":")[1] for line in applied_out.split()] == ["applied"] * total
    for code, out, err in reader_results:
        assert code == 0, err
        assert out.startswith("LOOPS")

    connection = sqlite3.connect(path)
    try:
        apply_rows = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT note) FROM history "
            "WHERE action='smart_review_apply'"
        ).fetchone()
    finally:
        connection.close()
    assert apply_rows == (total, total)

    fresh = SmartReviewHistoryStore(path, storage_chunk_size=chunk)
    assert fresh.breakdown(run_id)["status"] == {"applied": total}
    assert_summary_consistent(fresh, run_id, total)
    assert_disk_manifest_matches_candidates(fresh, run_id, total)


def test_two_processes_applying_the_same_candidate_only_one_wins(tmp_path):
    """兩個程序同時 apply 同一筆候選：一個 applied、一個 not_ready，只寫一筆歷史。"""
    import sqlite3

    path = tmp_path / "race.db"
    _store, run_id, _snapshots = seed_ready_run(path, 4, chunk_size=2)
    go = tmp_path / "go"
    ready = [tmp_path / f"ready-{i}" for i in range(2)]
    with tracked_workers() as procs:
        for index in range(2):
            procs.append(
                spawn(
                    "apply_range",
                    tmp_path,
                    db=path,
                    run=run_id,
                    ordinals="2",
                    ready=ready[index],
                    go=go,
                )
            )
        release_together(go, ready)
        results = [finish(proc) for proc in procs]
    outcomes = []
    for code, out, err in results:
        assert code == 0, err
        outcomes.append(out.strip().split(":")[1])
    assert sorted(outcomes) == ["applied", "not_ready"]

    connection = sqlite3.connect(path)
    try:
        (count,) = connection.execute(
            "SELECT COUNT(*) FROM history WHERE action='smart_review_apply'"
        ).fetchone()
    finally:
        connection.close()
    assert count == 1
    fresh = SmartReviewHistoryStore(path, storage_chunk_size=2)
    assert fresh.breakdown(run_id)["status"] == {"applied": 1, "ready": 3}


# ---------------------------------------------------------------------------
# 檔案鎖：逾時與殘留鎖
# ---------------------------------------------------------------------------


def test_lock_wait_times_out_while_another_process_holds_the_lock(db_path, tmp_path):
    """鎖被其他程序持有時，取得鎖會以 TimeoutError 結束（時鐘快轉，不實際等 120 秒）。"""
    _store, run_id = create_store_run(db_path, 3, chunk_size=None)
    with tracked_workers() as procs:
        holder = spawn("holder", tmp_path, db=db_path)
        procs.append(holder)
        assert read_line(holder) == "LOCKED"

        out = run_worker("try_lock", tmp_path, db=db_path, fast=1)
        assert out.strip() == "TIMEOUT"

        # 持有者放開後同一個鎖立即可取得，且寫入不受先前逾時影響。
        assert holder.stdin is not None
        holder.stdin.close()
        code, _out, err = finish(holder)
        assert code == 0, err
    assert run_worker("try_lock", tmp_path, db=db_path).strip() == "ACQUIRED"
    SmartReviewHistoryStore(db_path).record_results(run_id, [make_result(1)])


def test_non_forced_recovery_skips_instead_of_blocking_on_a_held_lock(
    db_path, tmp_path
):
    """一般讀取觸發的復原遇到鎖被占用時立即略過，鎖釋放後才真正復原。"""
    out = run_worker("creator", tmp_path, db=db_path, count=5)
    run_id = out.strip().splitlines()[-1]

    holder_store = SmartReviewHistoryStore(db_path)
    with holder_store.locked():
        result = json.loads(
            run_worker("recover", tmp_path, db=db_path, force=0).strip()
        )
        assert result["recovered"] == []
        assert result["elapsed"] < 5, "非強制復原不可阻塞等待鎖"

    after = json.loads(run_worker("recover", tmp_path, db=db_path, force=0).strip())
    assert after["recovered"] == [run_id]


def test_stale_lock_left_by_killed_process_does_not_block_others(db_path, tmp_path):
    """持鎖程序被殺後，鎖檔雖然留在磁碟上但已失效：其他程序立刻可取得並寫入。"""
    _store, run_id = create_store_run(db_path, 4, chunk_size=2)
    run_worker("holder", tmp_path, expect=CRASH_EXIT_CODE, db=db_path, die=1)

    started = time.monotonic()
    store = SmartReviewHistoryStore(db_path, storage_chunk_size=2)
    with store.locked():
        pass
    store.record_results(run_id, [make_result(1)])
    assert time.monotonic() - started < 10
    assert store.breakdown(run_id)["status"] == {"ready": 1, "pending": 3}
    # 其他程序同樣能取得。
    assert run_worker("try_lock", tmp_path, db=db_path).strip() == "ACQUIRED"
