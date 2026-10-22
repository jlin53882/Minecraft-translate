"""智慧校對歷史儲存測試的共用工具與跨程序 worker。

只透過公開 API（``SmartReviewHistoryStore``、``history_directory_for_db``、
``TranslationDB``）操作，不依賴歷史儲存內部模組的路徑或私有屬性，
因此儲存層拆檔後這些測試仍然有效。

worker 以 ``python -m tests.smart_review_history_support <command> key=value ...``
啟動成獨立 Python 程序（``PYTHONPATH`` 指向 repo 根目錄）。崩潰注入靠
``os.replace`` 的替換點：歷史 JSON 的每次原子寫入最後都是一次 ``os.replace``，
可在 manifest、候選分片或分片目錄發布的前後以 ``os._exit`` 模擬程序被殺掉。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CRASH_EXIT_CODE = 77
WORKER_TIMEOUT_SECONDS = 90
READY_STATUSES = ("ready", "no_change", "needs_context")


# ---------------------------------------------------------------------------
# 資料建構
# ---------------------------------------------------------------------------


def make_snapshots(count: int, *, entry_id_base: int = 0) -> list[SmartReviewSnapshot]:
    """建立 ordinal 1..count 的候選快照；不需要真實資料庫。"""
    return [
        SmartReviewSnapshot(
            run_id="pending",
            ordinal=index,
            entry_id=entry_id_base + index,
            kind="lang",
            mc_version="1.21.1",
            mod_id="demo",
            key=f"key_{index}",
            en_us=f"Text {index}",
            current_zh_tw=f"文字 {index}",
            effective_source=3,
            review_status="unreviewed",
            effective_revision=1,
        )
        for index in range(1, count + 1)
    ]


def make_result(
    ordinal: int,
    *,
    decision: str = "suggest_revision",
    suggestion: str | None = None,
    validation_error: str | None = None,
) -> dict[str, Any]:
    """建立一筆已通過解析的模型結果。"""
    if validation_error:
        return {"id": str(ordinal), "validation_error": validation_error}
    if suggestion is None:
        suggestion = f"文字 {ordinal}（修訂）" if decision == "suggest_revision" else ""
    return {
        "id": str(ordinal),
        "decision": decision,
        "suggested_zh_tw": suggestion,
        "issue_types": ["style"] if decision == "suggest_revision" else [],
        "evidence_ids": [],
        "confidence": 0.9,
        "reason": f"第 {ordinal} 筆的語氣需要人工確認。",
    }


def create_store_run(
    db_path: str | Path,
    count: int,
    *,
    chunk_size: int | None,
    entry_id_base: int = 0,
    snapshots: Sequence[SmartReviewSnapshot] | None = None,
) -> tuple[SmartReviewHistoryStore, str]:
    """用指定分片大小建立一個 pending 批次，回傳 (store, run_id)。"""
    store = SmartReviewHistoryStore(db_path, storage_chunk_size=chunk_size)
    run_id = store.create_run(
        snapshots
        if snapshots is not None
        else make_snapshots(count, entry_id_base=entry_id_base),
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=count,
        batch_size=max(1, count),
    )
    return store, run_id


def seed_translation_db(path: Path, count: int) -> list[SmartReviewSnapshot]:
    """建立含 count 筆 lang 條目的翻譯資料庫並回傳智慧校對快照後關閉。"""
    from translation_tool.translation_db import (
        KIND_LANG,
        TranslationDB,
        WriteBackItem,
    )

    db = TranslationDB(path)
    try:
        db.write_back(
            "1.21.1",
            [
                WriteBackItem(
                    KIND_LANG, "demo", f"key_{index}", f"Text {index}", f"文字 {index}"
                )
                for index in range(1, count + 1)
            ],
            fill_other_versions=False,
        )
        return db.smart_review_candidates(version="1.21.1", limit=0)
    finally:
        db.close()


def seed_ready_run(
    path: Path, count: int, *, chunk_size: int | None
) -> tuple[SmartReviewHistoryStore, str, list[SmartReviewSnapshot]]:
    """建立資料庫與全部為 ready 的批次；回傳 (store, run_id, snapshots)。"""
    snapshots = seed_translation_db(path, count)
    store, run_id = create_store_run(
        path, count, chunk_size=chunk_size, snapshots=snapshots
    )
    store.record_results(
        run_id,
        [
            make_result(item.ordinal, suggestion=f"{item.current_zh_tw}（修訂）")
            for item in snapshots
        ],
    )
    return store, run_id, snapshots


# ---------------------------------------------------------------------------
# 一致性檢查
# ---------------------------------------------------------------------------


def raw_candidates(
    store: SmartReviewHistoryStore, run_id: str, count: int
) -> list[dict[str, Any]]:
    """逐筆讀出候選原始內容（不經摘要快取），作為一致性的真值。"""
    result = []
    for ordinal in range(1, count + 1):
        candidate = store.get_candidate(run_id, ordinal)
        assert candidate is not None, f"候選 {ordinal} 遺失"
        result.append(candidate)
    return result


def truth_status_counts(
    store: SmartReviewHistoryStore, run_id: str, count: int
) -> dict[str, int]:
    """由候選本身統計出的狀態分佈。"""
    return dict(
        Counter(
            str(item.get("status") or "")
            for item in raw_candidates(store, run_id, count)
        )
    )


def read_json_files(store: SmartReviewHistoryStore) -> dict[str, dict[str, Any]]:
    """讀取歷史目錄下所有已發布的 JSON（含候選分片）；任一檔案損壞都會失敗。"""
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(store.directory.rglob("*.json")):
        if any(
            part.startswith(".") for part in path.relative_to(store.directory).parts
        ):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path
        result[str(path.relative_to(store.directory))] = payload
    return result


def snapshot_bytes(store: SmartReviewHistoryStore) -> dict[str, bytes]:
    """記錄已發布 JSON 的位元組內容，用來證明操作冪等（檔案完全不變）。"""
    return {
        str(path.relative_to(store.directory)): path.read_bytes()
        for path in sorted(store.directory.rglob("*.json"))
        if not any(
            part.startswith(".") for part in path.relative_to(store.directory).parts
        )
    }


def assert_summary_consistent(
    store: SmartReviewHistoryStore,
    run_id: str,
    count: int,
    *,
    check_processed: bool = True,
) -> None:
    """公開摘要 API（breakdown／count／page／get_run）必須與候選真值一致。"""
    truth = truth_status_counts(store, run_id, count)
    assert store.breakdown(run_id)["status"] == truth
    assert store.count_candidates(run_id) == count
    for status, expected in truth.items():
        assert store.count_candidates(run_id, status=status) == expected
        total, rows = store.page_candidates(run_id, status=status, limit=None)
        assert total == expected
        assert len(rows) == expected
        assert {row.status for row in rows} == {status}
    total, rows = store.page_candidates(run_id, limit=None)
    assert total == count
    assert [row.ordinal for row in rows] == list(range(1, count + 1))
    if not check_processed:
        return
    candidates = raw_candidates(store, run_id, count)
    processed = sum(
        1
        for item in candidates
        if (item.get("ai_status") or item.get("status"))
        in {"ready", "no_change", "needs_context", "invalid", "request_failed"}
    )
    run = store.get_run(run_id)
    assert run is not None
    assert run["selected_count"] == count
    assert run["processed_count"] == processed


def assert_disk_manifest_matches_candidates(
    store: SmartReviewHistoryStore, run_id: str, count: int
) -> None:
    """已落盤的 manifest 摘要（不經任何重建）必須與候選一致；僅適用於寫入之後。"""
    manifest = json.loads(store.path_for(run_id).read_text(encoding="utf-8"))
    assert manifest["status_counts"] == truth_status_counts(store, run_id, count)


def no_leftover_directories(store: SmartReviewHistoryStore) -> bool:
    """沒有未發布的暫存分片目錄，也沒有缺 manifest 的孤立分片目錄。"""
    root = store.directory
    if not root.is_dir():
        return True
    staging = [p for p in root.glob(".*.chunks.*.tmp") if p.is_dir()]
    orphans = [
        p for p in root.glob("*.chunks") if not (root / f"{p.name[:-7]}.json").is_file()
    ]
    return not staging and not orphans


# ---------------------------------------------------------------------------
# 子程序管理
# ---------------------------------------------------------------------------


def worker_env() -> dict[str, str]:
    """子程序環境：PYTHONPATH 指向 repo 根目錄，且不寫 bytecode。"""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def spawn(command: str, cwd: Path, **kwargs: object) -> subprocess.Popen[str]:
    """啟動獨立 Python 程序執行 worker 命令。"""
    args = [
        sys.executable,
        "-m",
        "tests.smart_review_history_support",
        command,
        *[f"{key}={value}" for key, value in kwargs.items()],
    ]
    return subprocess.Popen(
        args,
        cwd=cwd,
        env=worker_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def finish(
    proc: subprocess.Popen[str], timeout: float = WORKER_TIMEOUT_SECONDS
) -> tuple[int, str, str]:
    """等待子程序結束，回傳 (returncode, stdout, stderr)；逾時就砍掉並失敗。"""
    if proc.stdin is not None and proc.stdin.closed:
        # 呼叫端已自行關閉 stdin；communicate() 在 Python 3.12 會對已關閉的串流 flush 而失敗。
        proc.stdin = None
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError(f"worker 逾時\nstdout={out}\nstderr={err}") from None
    return proc.returncode, out, err


def run_worker(
    command: str, cwd: Path, *, expect: int | None = 0, **kwargs: object
) -> str:
    """同步執行 worker，回傳 stdout；退出碼與預期不同時帶 stderr 失敗。"""
    proc = spawn(command, cwd, **kwargs)
    code, out, err = finish(proc)
    if expect is not None:
        assert code == expect, f"{command} 退出碼 {code}\nstdout={out}\nstderr={err}"
    return out


def release_together(
    go: Path, ready_files: Sequence[Path], timeout: float = 60
) -> None:
    """等所有 worker 都就緒後同時放行，讓它們盡量同時競爭。"""
    deadline = time.monotonic() + timeout
    while not all(path.exists() for path in ready_files):
        assert time.monotonic() < deadline, "worker 未在時限內就緒"
        time.sleep(0.01)
    go.touch()


@contextlib.contextmanager
def tracked_workers() -> Iterator[list[subprocess.Popen[str]]]:
    """收集測試啟動的子程序；離開時保證沒有殘留。"""
    procs: list[subprocess.Popen[str]] = []
    try:
        yield procs
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            with contextlib.suppress(Exception):
                proc.communicate(timeout=10)


def read_line(proc: subprocess.Popen[str]) -> str:
    """從子程序 stdout 讀一行；子程序提前結束時帶 stderr 失敗。"""
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        code = proc.poll()
        err = proc.stderr.read() if proc.stderr else ""
        raise AssertionError(f"worker 沒有輸出（退出碼 {code}）：{err}")
    return line.strip()


# ---------------------------------------------------------------------------
# worker 端
# ---------------------------------------------------------------------------


def _arm_crash(mode: str) -> None:
    """在 ``os.replace`` 的指定替換點以 ``os._exit`` 模擬程序被殺掉。

    mode 為 ``before_/after_`` 加上 ``manifest``（歷史目錄下的批次 JSON）、
    ``chunk``（``*.chunks`` 內的候選分片）或 ``publish``（建立批次時分片目錄改名發布）。
    """
    real_replace = os.replace

    def kind_of(dst: object) -> str | None:
        path = Path(os.fspath(dst))  # type: ignore[arg-type]
        in_history = path.parent.name.endswith(".smart_review")
        if path.parent.name.endswith(".chunks"):
            return "chunk"
        if in_history and path.suffix == ".json" and not path.name.startswith("."):
            return "manifest"
        if in_history and path.name.endswith(".chunks"):
            return "publish"
        return None

    def hooked(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        kind = kind_of(dst)
        if kind is not None and mode == f"before_{kind}":
            os._exit(CRASH_EXIT_CODE)
        real_replace(src, dst, *args, **kwargs)
        if kind is not None and mode == f"after_{kind}":
            os._exit(CRASH_EXIT_CODE)

    os.replace = hooked  # type: ignore[assignment]


def _arm_fast_clock() -> None:
    """讓 ``time.monotonic`` 每次呼叫快轉 30 秒、``time.sleep`` 不等待。

    用來把 120 秒的檔案鎖等待上限壓縮到瞬間，驗證逾時行為而不必真的等待。
    """
    state = {"now": 0.0}

    def fake_monotonic() -> float:
        state["now"] += 30.0
        return state["now"]

    time.monotonic = fake_monotonic  # type: ignore[assignment]
    time.sleep = lambda _seconds: None  # type: ignore[assignment]


def _wait_for(path: str | None, timeout: float = 60) -> None:
    """等待 go 檔案出現。"""
    if not path:
        return
    deadline = time.monotonic() + timeout
    while not Path(path).exists():
        if time.monotonic() > deadline:
            print("GO_TIMEOUT", flush=True)
            raise SystemExit(3)
        time.sleep(0.005)


def _barrier(opts: dict[str, str]) -> None:
    """ready／go 同步：先宣告就緒，再等放行。"""
    if opts.get("ready"):
        Path(opts["ready"]).touch()
    _wait_for(opts.get("go"))


def _store(opts: dict[str, str]) -> SmartReviewHistoryStore:
    chunk = opts.get("chunk")
    return SmartReviewHistoryStore(
        opts["db"], storage_chunk_size=int(chunk) if chunk else None
    )


def _ordinals(text: str) -> list[int]:
    return [int(part) for part in text.split(",") if part]


def _cmd_writer(opts: dict[str, str]) -> None:
    """依 worker 編號交錯寫入結果，每筆各自呼叫一次 record_results。"""
    store = _store(opts)
    index, workers, total = int(opts["index"]), int(opts["workers"]), int(opts["total"])
    _barrier(opts)
    for ordinal in range(index + 1, total + 1, workers):
        store.record_results(opts["run"], [make_result(ordinal)])
        print(f"OK {ordinal}", flush=True)


def _cmd_creator(opts: dict[str, str]) -> None:
    """建立批次並印出 run_id；可寫入部分結果、保持存活或不 finish 就離開。"""
    store = _store(opts)
    _barrier(opts)
    count = int(opts["count"])
    run_id = store.create_run(
        make_snapshots(count, entry_id_base=int(opts.get("base", "0"))),
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=count,
        batch_size=count,
    )
    done = _ordinals(opts.get("record", ""))
    if done:
        store.record_results(run_id, [make_result(o) for o in done])
    print(run_id, flush=True)
    if opts.get("hold") == "1":
        sys.stdin.read()


def _cmd_orphans(opts: dict[str, str]) -> None:
    """一次建立多個 running 批次後直接離開，留下擁有者已死亡的批次。"""
    store = _store(opts)
    for _ in range(int(opts["runs"])):
        run_id = store.create_run(
            make_snapshots(int(opts["count"])),
            provider="gemini",
            model="test-model",
            version="1.21.1",
            requested_limit=int(opts["count"]),
            batch_size=int(opts["count"]),
        )
        print(run_id, flush=True)


def _cmd_crash_record(opts: dict[str, str]) -> None:
    """在指定替換點崩潰的 record_results。"""
    store = _store(opts)
    _arm_crash(opts["mode"])
    store.record_results(
        opts["run"], [make_result(o) for o in _ordinals(opts["ordinals"])]
    )
    print("SURVIVED", flush=True)


def _cmd_crash_create(opts: dict[str, str]) -> None:
    """在指定替換點崩潰的 create_run。"""
    store = _store(opts)
    _arm_crash(opts["mode"])
    store.create_run(
        make_snapshots(int(opts["count"])),
        provider="gemini",
        model="test-model",
        version="1.21.1",
        requested_limit=int(opts["count"]),
        batch_size=int(opts["count"]),
    )
    print("SURVIVED", flush=True)


def _cmd_holder(opts: dict[str, str]) -> None:
    """持有歷史目錄鎖；``die=1`` 時不釋放就直接結束程序。"""
    store = _store(opts)
    with store.locked():
        print("LOCKED", flush=True)
        if opts.get("die") == "1":
            os._exit(CRASH_EXIT_CODE)
        sys.stdin.read()


def _cmd_try_lock(opts: dict[str, str]) -> None:
    """嘗試取得鎖；``fast=1`` 時快轉時鐘，讓等待上限瞬間到期。"""
    store = _store(opts)
    if opts.get("fast") == "1":
        _arm_fast_clock()
    try:
        with store.locked():
            print("ACQUIRED", flush=True)
    except TimeoutError:
        print("TIMEOUT", flush=True)


def _cmd_recover(opts: dict[str, str]) -> None:
    """同步後執行 recover_abandoned_runs，印出 JSON 結果。"""
    store = _store(opts)
    _barrier(opts)
    started = time.monotonic()
    recovered = store.recover_abandoned_runs(force=opts.get("force") == "1")
    elapsed = time.monotonic() - started
    print(json.dumps({"recovered": recovered, "elapsed": elapsed}), flush=True)


def _cmd_apply_range(opts: dict[str, str]) -> None:
    """依序套用一段 ordinal；每筆印出 ``ordinal:結果``。"""
    from translation_tool.translation_db import TranslationDB

    db = TranslationDB(opts["db"])
    try:
        _barrier(opts)
        for ordinal in _ordinals(opts["ordinals"]):
            print(
                f"{ordinal}:{db.apply_smart_review_candidate(opts['run'], ordinal)}",
                flush=True,
            )
    finally:
        db.close()


def _cmd_reader(opts: dict[str, str]) -> None:
    """反覆讀取候選頁面與條目標籤（兩者都會先對帳 SQLite 套用紀錄）。"""
    from translation_tool.translation_db import TranslationDB

    db = TranslationDB(opts["db"])
    try:
        _barrier(opts)
        stop = Path(opts["stop"])
        entry_ids = _ordinals(opts["entries"])
        deadline = time.monotonic() + 30
        loops = 0
        while not stop.exists() and time.monotonic() < deadline:
            total, rows = db.load_smart_review_candidate_page(opts["run"], limit=50)
            assert len(rows) <= total
            db.smart_review_marks(entry_ids)
            loops += 1
        # 結束前再做一次完整對帳，確保最後狀態已收斂。
        db.load_smart_review_candidate_page(opts["run"], limit=None)
        print(f"LOOPS {loops}", flush=True)
    finally:
        db.close()


def _cmd_crash_apply(opts: dict[str, str]) -> None:
    """SQLite 套用之後、JSON 回寫的指定替換點崩潰。"""
    from translation_tool.translation_db import TranslationDB

    db = TranslationDB(opts["db"])
    _arm_crash(opts["mode"])
    print(
        db.apply_smart_review_candidate(opts["run"], int(opts["ordinal"])), flush=True
    )
    print("SURVIVED", flush=True)


_COMMANDS = {
    "writer": _cmd_writer,
    "creator": _cmd_creator,
    "orphans": _cmd_orphans,
    "crash_record": _cmd_crash_record,
    "crash_create": _cmd_crash_create,
    "holder": _cmd_holder,
    "try_lock": _cmd_try_lock,
    "recover": _cmd_recover,
    "apply_range": _cmd_apply_range,
    "reader": _cmd_reader,
    "crash_apply": _cmd_crash_apply,
}


def main(argv: Sequence[str]) -> int:
    """worker 進入點。"""
    command, *pairs = argv
    opts = dict(pair.split("=", 1) for pair in pairs)
    _COMMANDS[command](opts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
