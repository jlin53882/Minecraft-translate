"""#170：核心合併在單一 update 內也能取消（檔名掃描、任務提交、佇列中的任務）。"""

from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import pytest

from app.services_impl.pipelines import merge_service
from app.tasks.task_session import TaskSession
from translation_tool.core import lang_merger
from translation_tool.core.lang_merger import (
    merge_zhcn_to_zhtw_from_folder,
    merge_zhcn_to_zhtw_from_zip,
)
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

MODS = 60


@pytest.fixture
def calls(monkeypatch):
    """把真正的處理函式換成計數的假函式；單一工作執行緒讓佇列行為可預測。"""
    seen: list[str] = []
    lock = threading.Lock()

    def fake_mod(*args, **kwargs):
        with lock:
            seen.append("mod")
        return {"success": True, "log": "ok"}

    monkeypatch.setattr(lang_merger, "_process_single_mod", fake_mod)
    monkeypatch.setattr(lang_merger.os, "cpu_count", lambda: 2)  # → 1 個工作執行緒
    return seen


def _make_folder(tmp_path):
    src = tmp_path / "in"
    for i in range(MODS):
        lang = src / f"mod{i}" / "assets" / f"m{i}" / "lang"
        lang.mkdir(parents=True)
        (lang / "en_us.json").write_text('{"a": "b"}', encoding="utf-8")
    return src


def _make_zip(tmp_path):
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(MODS):
            zf.writestr(f"mod{i}/assets/m{i}/lang/en_us.json", '{"a": "b"}')
    return path


def test_folder_cancel_before_start_processes_nothing(tmp_path, calls):
    src = _make_folder(tmp_path)
    with cancel_scope(lambda: True), pytest.raises(TaskCancelled):
        list(merge_zhcn_to_zhtw_from_folder(str(src), str(tmp_path / "out")))
    assert calls == []


def test_folder_cancel_in_the_middle_stops_inside_one_update(tmp_path, calls):
    """第一個任務完成後取消：佇列中還沒開始的任務被丟棄，不會把 60 個都跑完。"""
    src = _make_folder(tmp_path)
    with cancel_scope(lambda: len(calls) >= 1), pytest.raises(TaskCancelled):
        list(merge_zhcn_to_zhtw_from_folder(str(src), str(tmp_path / "out")))
    assert 1 <= len(calls) < MODS // 2


def test_zip_cancel_before_start_processes_nothing(tmp_path, calls):
    zip_path = _make_zip(tmp_path)
    with cancel_scope(lambda: True), pytest.raises(TaskCancelled):
        list(merge_zhcn_to_zhtw_from_zip(str(zip_path), str(tmp_path / "out")))
    assert calls == []


def test_zip_cancel_in_the_middle_stops_inside_one_update(tmp_path, calls):
    zip_path = _make_zip(tmp_path)
    with cancel_scope(lambda: len(calls) >= 1), pytest.raises(TaskCancelled):
        list(merge_zhcn_to_zhtw_from_zip(str(zip_path), str(tmp_path / "out")))
    assert 1 <= len(calls) < MODS // 2


def test_closing_the_generator_discards_queued_work(tmp_path, calls):
    """consumer 關閉 generator（#155 的取消路徑）時，佇列中的任務同樣被丟棄。"""
    src = _make_folder(tmp_path)
    gen = merge_zhcn_to_zhtw_from_folder(str(src), str(tmp_path / "out"))
    for update in gen:
        if update.get("pending_count") is not None and calls:
            break
    gen.close()
    assert len(calls) < MODS // 2


def test_without_cancel_everything_is_processed(tmp_path, calls):
    src = _make_folder(tmp_path)
    list(merge_zhcn_to_zhtw_from_folder(str(src), str(tmp_path / "out")))
    assert len(calls) == MODS


def test_folder_service_removes_new_output_when_cancelled(tmp_path, monkeypatch):
    """服務層取消時清掉本次新建的資料夾，避免留下半成品。"""
    output = tmp_path / "out"

    def cancelled(*args, **kwargs):
        (Path(kwargs.get("output_dir", args[1]))).mkdir(parents=True)
        args[2].request_cancel()
        raise TaskCancelled()

    monkeypatch.setattr(merge_service, "_run_folder_stages", cancelled)
    session = TaskSession()
    session.start()

    with pytest.raises(TaskCancelled):
        list(
            merge_service.run_merge_folder_batch_service(
                str(tmp_path / "input"), str(output), session, True
            )
        )

    assert not output.exists()


def test_zip_service_removes_new_output_when_cancelled(tmp_path, monkeypatch):
    """ZIP 服務層取消時同樣不保留新建半成品。"""
    output = tmp_path / "out"

    def cancelled(*args, **kwargs):
        (Path(kwargs.get("output_dir", args[1]))).mkdir(parents=True)
        args[2].request_cancel()
        raise TaskCancelled()

    monkeypatch.setattr(merge_service, "_merge_one_zip", cancelled)
    session = TaskSession()
    session.start()

    with pytest.raises(TaskCancelled):
        list(
            merge_service.run_merge_zip_batch_service(
                [str(tmp_path / "input.zip")], str(output), session, True
            )
        )

    assert not output.exists()


def test_cleanup_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    """清理失敗（檔案被鎖、權限）時不能記「已清理」。"""
    from app.services_impl.pipelines import merge_service

    output = tmp_path / "out"
    output.mkdir()
    session = TaskSession()
    session.start()
    session.request_cancel()
    logged = []
    monkeypatch.setattr(
        merge_service,
        "_session_log",
        lambda s, text, level="info": logged.append((level, text)),
    )

    def locked(path):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(merge_service.shutil, "rmtree", locked)

    merge_service._cleanup_cancelled_output(str(output), False, False, session)

    assert logged and logged[0][0] == "warning"
    assert "失敗" in logged[0][1]
    assert not any("已清理" in text for _, text in logged)
    assert output.exists()


def test_cleanup_success_is_reported(tmp_path, monkeypatch):
    from app.services_impl.pipelines import merge_service

    output = tmp_path / "out"
    output.mkdir()
    session = TaskSession()
    session.start()
    session.request_cancel()
    logged = []
    monkeypatch.setattr(
        merge_service,
        "_session_log",
        lambda s, text, level="info": logged.append((level, text)),
    )

    merge_service._cleanup_cancelled_output(str(output), False, False, session)

    assert not output.exists()
    assert any("已清理半成品輸出" in text for _, text in logged)


# ------------------------------------------- 資料夾來源的初始掃描本身也要可取消（os.walk）


def _many_dirs(tmp_path, count=50):
    src = tmp_path / "scan"
    for i in range(count):
        d = src / f"d{i}"
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x", encoding="utf-8")
    return src


def test_folder_reader_scan_stops_when_cancelled(tmp_path):
    """list_all 以前要整個 os.walk 完才回傳，取消只能等掃完；現在每進一個資料夾就檢查。"""
    from translation_tool.core.lang_merge_io import FolderReader

    src = _many_dirs(tmp_path)
    checks = []

    def cancel_after_a_few():
        checks.append(1)
        return len(checks) >= 4

    with cancel_scope(cancel_after_a_few), pytest.raises(TaskCancelled):
        FolderReader(str(src)).list_all()

    assert len(checks) < 50  # 沒有把 50 個資料夾都走完


def test_folder_reader_without_cancellation_lists_everything(tmp_path):
    from translation_tool.core.lang_merge_io import FolderReader

    src = _many_dirs(tmp_path)

    names = FolderReader(str(src)).list_all()

    assert len(names) == 50 and "d0/f.txt" in names


def test_folder_merge_cancelled_during_the_scan_never_starts_processing(
    tmp_path, calls
):
    """取消發生在初始掃描：連第一個處理任務都不該開始。"""
    src = _make_folder(tmp_path)
    seen = []

    def cancel_on_second_check():
        seen.append(1)
        return len(seen) >= 2

    with cancel_scope(cancel_on_second_check), pytest.raises(TaskCancelled):
        list(merge_zhcn_to_zhtw_from_folder(str(src), str(tmp_path / "out")))

    assert calls == []


def _spy_checkpoints(monkeypatch):
    seen = []
    original = lang_merger._checkpoint

    def spy(index):
        seen.append(index)
        return original(index)

    monkeypatch.setattr(lang_merger, "_checkpoint", spy)
    return seen


def test_zip_first_pass_prefix_scan_has_its_own_checkpoints(
    tmp_path, calls, monkeypatch
):
    """ZIP 的第一輪（包裝前綴掃描）與第二輪（分類）都要有檢查點：索引會從 0 開始兩次。"""
    zip_path = _make_zip(tmp_path)
    seen = _spy_checkpoints(monkeypatch)

    list(merge_zhcn_to_zhtw_from_zip(str(zip_path), str(tmp_path / "out")))

    assert seen.count(0) >= 2
    assert max(seen) >= MODS - 1


def test_folder_first_pass_prefix_scan_has_its_own_checkpoints(
    tmp_path, calls, monkeypatch
):
    src = _make_folder(tmp_path)
    seen = _spy_checkpoints(monkeypatch)

    list(merge_zhcn_to_zhtw_from_folder(str(src), str(tmp_path / "out")))

    assert seen.count(0) >= 2


def test_zip_cancelled_in_the_first_scan_never_starts_processing(tmp_path, calls):
    zip_path = _make_zip(tmp_path)
    with cancel_scope(lambda: True), pytest.raises(TaskCancelled):
        list(merge_zhcn_to_zhtw_from_zip(str(zip_path), str(tmp_path / "out")))
    assert calls == []


def test_folder_reader_checks_inside_a_single_huge_directory(tmp_path):
    """單一資料夾有大量檔案時，也要每 256 個檢查一次取消（不只是每個資料夾）。"""
    from translation_tool.core.lang_merge_io import FolderReader

    flat = tmp_path / "flat"
    flat.mkdir()
    for i in range(600):
        (flat / f"f{i}.txt").write_text("x", encoding="utf-8")
    checks = []

    def cancel_on_third_check():
        checks.append(1)
        return len(checks) >= 3  # 進資料夾 1 次 + 檔案 256、512 各 1 次

    with cancel_scope(cancel_on_third_check), pytest.raises(TaskCancelled):
        FolderReader(str(flat)).list_all()
