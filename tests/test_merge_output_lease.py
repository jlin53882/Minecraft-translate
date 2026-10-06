"""合併輸出資料夾的獨占租約：同一個輸出不能同時被兩個任務使用，取消清理不會刪到別人的成果。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.services_impl.pipelines import merge_service
from app.services_impl.pipelines.output_lease import (
    OutputInUseError,
    acquire_output_lease,
)
from app.tasks.task_session import TaskSession
from translation_tool.utils.cancellation import TaskCancelled

# ------------------------------------------------------------------ 租約本身


def test_second_lease_on_the_same_output_is_refused(tmp_path):
    first = acquire_output_lease(str(tmp_path / "out"))
    try:
        with pytest.raises(OutputInUseError):
            acquire_output_lease(str(tmp_path / "out"))
    finally:
        first.release()


def test_path_spellings_resolve_to_the_same_output(tmp_path):
    first = acquire_output_lease(str(tmp_path / "out"))
    try:
        with pytest.raises(OutputInUseError):
            acquire_output_lease(str(tmp_path / "x" / ".." / "out"))
        with pytest.raises(OutputInUseError):
            acquire_output_lease(str(tmp_path / "out") + "/")
    finally:
        first.release()


def test_nested_outputs_conflict_in_both_directions(tmp_path):
    """取消時 rmtree 會連子資料夾一起刪，所以包含關係也算衝突。"""
    outer = acquire_output_lease(str(tmp_path / "out"))
    try:
        with pytest.raises(OutputInUseError):
            acquire_output_lease(str(tmp_path / "out" / "sub"))
    finally:
        outer.release()
    inner = acquire_output_lease(str(tmp_path / "out" / "sub"))
    try:
        with pytest.raises(OutputInUseError):
            acquire_output_lease(str(tmp_path / "out"))
    finally:
        inner.release()


def test_sibling_outputs_do_not_conflict(tmp_path):
    a = acquire_output_lease(str(tmp_path / "out"))
    b = acquire_output_lease(str(tmp_path / "out2"))  # 名字前綴相同，但不是子資料夾
    a.release()
    b.release()


def test_release_is_idempotent_and_frees_the_output(tmp_path):
    lease = acquire_output_lease(str(tmp_path / "out"))
    lease.release()
    lease.release()
    acquire_output_lease(str(tmp_path / "out")).release()


# ------------------------------------------------- 服務層：兩個同時進行的合併


def _blocking_core(started: threading.Event, proceed: threading.Event, name: str):
    """核心：先在輸出寫下自己的檔案，通知「已開始」，等到被放行才結束。"""

    def core(input_dir, output_dir, *args, **kwargs):
        out = Path(output_dir)
        (out / "lang_output").mkdir(parents=True, exist_ok=True)
        (out / "lang_output" / f"{name}.json").write_text(name, encoding="utf-8")
        started.set()
        proceed.wait(timeout=10)
        yield {"progress": 0.5, "log": f"{name} 處理中"}

    return core


def _run_folder(session, in_dir, out_dir, **kw):
    return merge_service.run_merge_folder_batch_service(
        str(in_dir), str(out_dir), session, True, **kw
    )


def test_second_merge_on_the_same_output_fails_instead_of_sharing_it(
    tmp_path, monkeypatch
):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "shared_out"
    started, proceed = threading.Event(), threading.Event()
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _blocking_core(started, proceed, "A"),
    )
    a, b = TaskSession(name="A"), TaskSession(name="B")
    a.start()
    b.start()
    outcome = {}

    def run_a():
        try:
            list(_run_folder(a, in_dir, out))
        except TaskCancelled:
            outcome["a"] = "cancelled"

    thread = threading.Thread(target=run_a)
    thread.start()
    assert started.wait(timeout=10)

    updates_b = list(_run_folder(b, in_dir, out))  # A 還在跑：B 不能用同一個輸出

    assert b.status == "ERROR"
    assert any("正在被另一個任務使用" in e.text for e in b.snapshot()["logs"]) or any(
        "正在被另一個任務使用" in str(u) for u in updates_b
    )
    assert (out / "lang_output" / "A.json").exists()  # B 沒有碰 A 的成果

    a.request_cancel()  # 取消 A
    proceed.set()
    thread.join(timeout=10)

    assert outcome.get("a") == "cancelled"
    assert not out.exists()  # A 新建的輸出被清掉（B 從沒寫過任何東西）


def test_rejected_second_merge_never_cleans_up_the_first_ones_output(
    tmp_path, monkeypatch
):
    """B 被拒絕又被取消：它的 finally 不能把 A 的輸出刪掉。"""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "shared_out"
    started, proceed = threading.Event(), threading.Event()
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _blocking_core(started, proceed, "A"),
    )
    a, b = TaskSession(name="A"), TaskSession(name="B")
    a.start()
    b.start()
    thread = threading.Thread(target=lambda: list(_run_folder(a, in_dir, out)))
    thread.start()
    assert started.wait(timeout=10)

    b.request_cancel()  # B 在被拒絕的同時也被要求取消
    list(_run_folder(b, in_dir, out))

    assert (out / "lang_output" / "A.json").exists()  # A 的成果還在
    proceed.set()
    thread.join(timeout=10)
    assert (out / "lang_output" / "A.json").exists()  # A 正常結束，成果保留


def test_output_is_free_again_after_the_first_merge_finishes(tmp_path, monkeypatch):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "out"
    done = threading.Event()
    done.set()
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _blocking_core(threading.Event(), done, "X"),
    )
    first, second = TaskSession(name="1"), TaskSession(name="2")
    first.start()
    list(_run_folder(first, in_dir, out))
    second.start()
    list(_run_folder(second, in_dir, out))

    assert first.status == "DONE" and second.status == "DONE"


def test_one_click_style_sequential_calls_share_the_output(tmp_path, monkeypatch):
    """一鍵流程對同一個輸出依序合併兩個來源（finish_session=False）：不能被租約擋住。"""
    out = tmp_path / "out"
    src_a, src_b = tmp_path / "a", tmp_path / "b"
    src_a.mkdir()
    src_b.mkdir()
    done = threading.Event()
    done.set()
    monkeypatch.setattr(
        merge_service,
        "merge_zhcn_to_zhtw_from_folder",
        _blocking_core(threading.Event(), done, "S"),
    )
    session = TaskSession(name="一鍵")
    session.start()

    for src in (src_a, src_b):
        list(_run_folder(session, src, out, finish_session=False))

    assert session.status != "ERROR"


def test_lease_is_released_when_the_merge_is_cancelled(tmp_path, monkeypatch):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "out"
    session = TaskSession(name="取消")
    session.start()

    def cancelling_core(input_dir, output_dir, *a, **k):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        session.request_cancel()
        yield {"progress": 0.1, "log": "x"}
        yield {"progress": 0.2, "log": "y"}

    monkeypatch.setattr(
        merge_service, "merge_zhcn_to_zhtw_from_folder", cancelling_core
    )
    with pytest.raises(TaskCancelled):
        list(_run_folder(session, in_dir, out))

    acquire_output_lease(str(out)).release()  # 租約已釋放，不會拋出


def test_zip_merge_also_refuses_a_busy_output(tmp_path, monkeypatch):
    out = tmp_path / "zip_out"
    held = acquire_output_lease(str(out))
    try:
        session = TaskSession(name="ZIP")
        session.start()
        list(
            merge_service.run_merge_zip_batch_service(
                [str(tmp_path / "a.zip")], str(out), session, True
            )
        )
        assert session.status == "ERROR"
        assert not out.exists()  # 被拒絕的任務什麼都不建立、不清理
    finally:
        held.release()
