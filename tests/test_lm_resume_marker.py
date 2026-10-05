"""機器翻譯續跑標記（checkpoint）與「上次中斷的任務」判斷（#151）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from translation_tool.core import lm_resume, lm_translator


@pytest.fixture
def checkpoint(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "logs" / "checkpoint.json"
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(path))
    return path


def _lang_file(root: Path, entries: dict[str, str]) -> Path:
    f = root / "assets" / "demo" / "lang" / "en_us.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return f


def _fingerprint_of(root: Path) -> str:
    _p, _l, files = lm_translator.scan_translatable_files(root.resolve())
    _fc, items, _ev = lm_translator._extract_directory_items(
        files, export_lang=False, work_thread=1
    )
    return lm_translator.compute_checkpoint_fingerprint(str(root), items)


def _save(root: Path, **overrides) -> None:
    kwargs = {
        "input_dir": str(root),
        "fingerprint": _fingerprint_of(root),
        "export_lang": False,
        "write_new_cache": True,
    }
    kwargs.update(overrides)
    lm_translator.save_checkpoint(2, 4, 10, [], str(root / "out"), **kwargs)


class TestSaveCheckpoint:
    def test_writes_versioned_payload_with_run_options(self, checkpoint, tmp_path):
        lm_translator.save_checkpoint(
            3,
            5,
            12,
            [{"path": f"k{i}"} for i in range(9)],
            str(tmp_path / "out"),
            input_dir=str(tmp_path / "in"),
            fingerprint="abc",
            export_lang=True,
            write_new_cache=False,
        )
        data = json.loads(checkpoint.read_text("utf-8"))

        assert data["version"] == lm_translator.CHECKPOINT_VERSION
        assert (data["batch_index"], data["completed_count"], data["total"]) == (
            3,
            5,
            12,
        )
        assert data["export_lang"] is True and data["write_new_cache"] is False
        assert data["input_dir"] == str(tmp_path / "in")
        assert data["fingerprint"] == "abc"
        assert data["updated_at"]
        assert len(data["remaining_sample"]) == 3  # 不存完整清單

    def test_write_is_atomic_and_fsynced(self, checkpoint, monkeypatch):
        synced: list[int] = []
        real_fsync = os.fsync
        monkeypatch.setattr(
            lm_translator.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))
        )
        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="x"
        )

        assert synced, "寫入後必須 fsync，不能依賴關閉時的清理"
        assert not Path(f"{checkpoint}.tmp").exists()

    def test_directory_is_fsynced_after_the_replace(self, checkpoint, monkeypatch):
        """rename 本身也要持久化：replace 之後同步父目錄。"""
        events: list[str] = []
        real_replace = os.replace
        monkeypatch.setattr(
            lm_translator.os,
            "replace",
            lambda a, b: (events.append("replace"), real_replace(a, b))[1],
        )
        monkeypatch.setattr(
            lm_translator,
            "fsync_directory",
            lambda path: events.append(f"fsync_dir:{Path(path).name}"),
        )

        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="x"
        )

        assert events == ["replace", f"fsync_dir:{checkpoint.parent.name}"]

    def test_crash_while_writing_keeps_the_previous_checkpoint(
        self, checkpoint, monkeypatch
    ):
        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="old"
        )
        before = checkpoint.read_text("utf-8")

        def crash(*_a, **_k):
            raise OSError("simulated power loss before replace")

        monkeypatch.setattr(lm_translator.os, "replace", crash)
        with pytest.raises(OSError):
            lm_translator.save_checkpoint(
                2, 2, 2, [], "out", input_dir="in", fingerprint="new"
            )

        assert checkpoint.read_text("utf-8") == before  # 不會留下半截 JSON
        assert lm_translator.load_checkpoint()["fingerprint"] == "old"


class TestPeekInterruptedTask:
    def test_no_checkpoint_means_nothing_to_resume(self, checkpoint):
        assert lm_resume.peek_interrupted_task() is None

    def test_reads_the_marker_without_touching_it(self, checkpoint, tmp_path):
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello"})
        _save(root, export_lang=True, write_new_cache=False)
        before = checkpoint.read_bytes()

        task = lm_resume.peek_interrupted_task()

        assert task.input_dir == str(root)
        assert task.export_lang is True and task.write_new_cache is False
        assert (task.completed, task.total) == (4, 10)
        assert task.has_current_format
        assert checkpoint.read_bytes() == before  # 唯讀

    def test_legacy_checkpoint_is_reported_not_ignored(self, checkpoint):
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text(
            json.dumps({"batch_index": 2, "completed_count": 200, "total": 314}),
            encoding="utf-8",
        )

        task = lm_resume.peek_interrupted_task()

        assert task is not None and not task.has_current_format
        check = lm_resume.check_resume_feasibility(task)
        assert check.ok is False and "舊版" in check.reason

    def test_corrupt_checkpoint_is_treated_as_missing(self, checkpoint):
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text("{not json", encoding="utf-8")
        assert lm_resume.peek_interrupted_task() is None


class TestCheckResumeFeasibility:
    def test_same_input_is_resumable(self, checkpoint, tmp_path):
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello", "b": "World"})
        _save(root)

        check = lm_resume.check_resume_feasibility(lm_resume.peek_interrupted_task())

        assert check.ok is True

    def test_changed_input_cannot_resume_and_says_why(self, checkpoint, tmp_path):
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello"})
        _save(root)
        _lang_file(root, {"a": "Hello (edited)"})

        check = lm_resume.check_resume_feasibility(lm_resume.peek_interrupted_task())

        assert check.ok is False
        assert "不同" in check.reason
        assert checkpoint.exists(), "檢查本身不得清除標記，由使用者決定放棄"

    def test_resume_check_is_unaffected_by_translation_cache_progress(
        self, checkpoint, tmp_path, monkeypatch
    ):
        """指紋涵蓋全部抽取項目：快取裡多了已完成的譯文也不會讓標記失效。"""
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello", "b": "World"})
        _save(root)
        monkeypatch.setattr(
            lm_translator,
            "get_cache_dict_ref",
            lambda _t: {"a": {"src": "Hello", "dst": "哈囉"}},
        )

        assert lm_resume.check_resume_feasibility(lm_resume.peek_interrupted_task()).ok

    def test_missing_input_folder(self, checkpoint, tmp_path):
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello"})
        _save(root)
        for p in sorted(root.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        root.rmdir()

        check = lm_resume.check_resume_feasibility(lm_resume.peek_interrupted_task())

        assert check.ok is False and "不存在" in check.reason

    def test_scan_failure_is_reported_as_reason(
        self, checkpoint, tmp_path, monkeypatch
    ):
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello"})
        _save(root)

        def boom(*_a, **_k):
            raise OSError("disk error")

        monkeypatch.setattr(lm_translator, "_extract_directory_items", boom)

        check = lm_resume.check_resume_feasibility(lm_resume.peek_interrupted_task())

        assert check.ok is False and "disk error" in check.reason

    def test_never_calls_the_translation_api(self, checkpoint, tmp_path, monkeypatch):
        root = tmp_path / "in"
        _lang_file(root, {"a": "Hello"})
        _save(root)

        def forbidden(*_a, **_k):
            raise AssertionError("偵測與檢查不得呼叫翻譯 API")

        monkeypatch.setattr(lm_translator, "translate_batch_smart", forbidden)
        monkeypatch.setattr(lm_translator, "validate_api_keys", forbidden)

        task = lm_resume.peek_interrupted_task()
        assert lm_resume.check_resume_feasibility(task).ok


def test_discard_clears_the_marker(checkpoint, tmp_path):
    root = tmp_path / "in"
    _lang_file(root, {"a": "Hello"})
    _save(root)

    lm_resume.discard_interrupted_task()

    assert not checkpoint.exists()
    assert lm_resume.peek_interrupted_task() is None
