"""FTB／KubeJS／MD 的續跑標記、指紋、偵測與任務翻譯頁的續跑入口（#164）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.core import lm_resume, lm_translator, plugin_resume
from translation_tool.core.lm_translator_skeleton import (
    JsonCheckpointAdapter,
    make_checkpoint_adapter,
)


@pytest.fixture(autouse=True)
def _data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        lm_translator,
        "CHECKPOINT_FILE",
        str(tmp_path / "data/logs/translation_checkpoint.json"),
    )
    with plugin_resume._ACTIVE_LOCK:
        plugin_resume._ACTIVE.clear()
    yield
    with plugin_resume._ACTIVE_LOCK:
        plugin_resume._ACTIVE.clear()


def _put(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)
    return path


def _kubejs(tmp_path: Path) -> Path:
    root = tmp_path / "pack" / "kubejs"
    _put(root / "client_scripts/a.js", "scene.text('x', 'Hello')")
    _put(root / "assets/mod/lang/en_us.json", '{"k": "v"}')
    return root


# ------------------------------------------------------------------ 指紋


class TestSourceFingerprint:
    def test_depends_on_file_contents_not_mtime(self, tmp_path):
        root = _kubejs(tmp_path)
        before = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)

        os.utime(root / "client_scripts/a.js", ns=(1_000_000_000, 1_000_000_000))
        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
            == before
        )

        (root / "client_scripts/a.js").write_text(
            "scene.text('x', 'Changed')", encoding="utf-8"
        )
        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
            != before
        )

    def test_same_size_different_content_is_detected(self, tmp_path):
        root = _kubejs(tmp_path)
        f = root / "client_scripts/a.js"
        f.write_text("AAAA", encoding="utf-8")
        a = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        f.write_text("BBBB", encoding="utf-8")

        assert plugin_resume.compute_source_fingerprint("kubejs", str(root), None) != a

    def test_a_renamed_or_added_file_changes_it(self, tmp_path):
        root = _kubejs(tmp_path)
        base = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        (root / "client_scripts/a.js").rename(root / "client_scripts/b.js")
        renamed = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        _put(root / "server_scripts/c.js", "x")
        added = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)

        assert len({base, renamed, added}) == 3

    def test_files_the_flow_never_reads_are_ignored(self, tmp_path):
        root = _kubejs(tmp_path)
        base = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        _put(root / "assets/mod/textures/big.png", b"\x89PNG" * 1000)

        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), None) == base
        )

    def test_the_output_folder_is_excluded(self, tmp_path):
        """輸出預設在輸入底下的 Output/：流程自己寫的檔案不得讓指紋改變。"""
        root = _kubejs(tmp_path)
        base = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        _put(root / "Output/kubejs/完成/a.js", "generated")
        _put(root / "Output/kubejs/raw/x.json", "{}")

        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), None) == base
        )
        custom = root / "my_out"
        _put(custom / "gen.js", "generated")
        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), str(custom))
            != base
        ), "自訂輸出資料夾排除的是它自己，Output/ 此時屬於來源"

    def test_ftb_uses_the_quests_folder_and_md_only_the_language_folders(
        self, tmp_path
    ):
        pack = tmp_path / "ftb"
        _put(pack / "config/ftbquests/quests/a.snbt", "{}")
        _put(pack / "mods/huge.jar", b"x" * 10)
        ftb = plugin_resume.compute_source_fingerprint("ftbquests", str(pack), None)
        _put(pack / "mods/other.jar", b"y" * 10)
        assert (
            plugin_resume.compute_source_fingerprint("ftbquests", str(pack), None)
            == ftb
        )

        docs = tmp_path / "docs"
        _put(docs / "en_us/a.md", "hello")
        _put(docs / "other/b.md", "ignored")
        md = plugin_resume.compute_source_fingerprint("md", str(docs), None)
        _put(docs / "other/c.md", "still ignored")
        assert plugin_resume.compute_source_fingerprint("md", str(docs), None) == md
        _put(docs / "en_us/d.md", "counts")
        assert plugin_resume.compute_source_fingerprint("md", str(docs), None) != md

    def test_kinds_do_not_collide(self, tmp_path):
        root = tmp_path / "x"
        _put(root / "en_us/a.md", "same")
        _put(root / "config/ftbquests/quests/a.snbt", "same")
        assert plugin_resume.compute_source_fingerprint(
            "md", str(root), None
        ) != plugin_resume.compute_source_fingerprint("ftbquests", str(root), None)

    def test_no_sources_or_missing_folder_means_no_resume(self, tmp_path):
        assert (
            plugin_resume.compute_source_fingerprint(
                "kubejs", str(tmp_path / "nope"), None
            )
            is None
        )
        (tmp_path / "empty").mkdir()
        assert (
            plugin_resume.compute_source_fingerprint(
                "md", str(tmp_path / "empty"), None
            )
            is None
        )

    def test_too_many_files_or_bytes_means_no_resume(self, tmp_path, monkeypatch):
        root = _kubejs(tmp_path)
        monkeypatch.setattr(plugin_resume, "MAX_FINGERPRINT_FILES", 1)
        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), None) is None
        )
        monkeypatch.setattr(plugin_resume, "MAX_FINGERPRINT_FILES", 50_000)
        monkeypatch.setattr(plugin_resume, "MAX_FINGERPRINT_BYTES", 5)
        assert (
            plugin_resume.compute_source_fingerprint("kubejs", str(root), None) is None
        )

    def test_unknown_kind_does_not_crash(self, tmp_path):
        assert (
            plugin_resume.compute_source_fingerprint("nope", str(tmp_path), None)
            is None
        )


# ------------------------------------------------------------------ resume_task


def _session(**flags):
    return SimpleNamespace(error=False, cancel_requested=False, **flags)


def _task(**overrides):
    kwargs = {
        "kind": "kubejs",
        "input_dir": "in",
        "output_dir": None,
        "options": {"step_translate": True},
        "fingerprint_fn": lambda *_a: "fp",
    }
    kwargs.update(overrides)
    return plugin_resume.resume_task(
        **{k: v for k, v in kwargs.items() if k != "kind"}, kind=kwargs["kind"]
    )


class TestResumeTask:
    def _marker(self, kind="kubejs") -> Path:
        path = plugin_resume.marker_path(kind)
        _put(path, "{}")
        return path

    def test_registers_the_context_while_running_and_removes_it_after(self):
        with _task() as ctx:
            assert plugin_resume.active_context("kubejs") is ctx
            assert ctx.fingerprint == "fp"
        assert plugin_resume.active_context("kubejs") is None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"dry_run": True},
            {"translate_enabled": False},
            {"fingerprint_fn": lambda *_a: None},
        ],
    )
    def test_no_context_and_no_marker_changes_when_resume_is_not_applicable(
        self, kwargs
    ):
        marker = self._marker()
        with _task(**kwargs) as ctx:
            assert ctx is None
            assert plugin_resume.active_context("kubejs") is None
        assert marker.exists(), "不適用時不得碰既有標記"

    def test_a_fully_completed_task_clears_the_marker(self):
        marker = self._marker()
        with _task(session=_session()) as ctx:
            ctx.loop_started()
            ctx.loop_completed(3)
        assert not marker.exists()

    def test_a_task_with_no_translation_loops_also_clears_it(self):
        """所有項目都由快取命中：沒有迴圈需要跑，任務就是完成了。"""
        marker = self._marker()
        with _task(session=_session()):
            pass
        assert not marker.exists()

    def test_an_unfinished_loop_keeps_the_marker(self):
        marker = self._marker()
        with _task(session=_session()) as ctx:
            ctx.loop_started()
            ctx.loop_started()
            ctx.loop_completed()
        assert marker.exists()

    @pytest.mark.parametrize("flag", ["error", "cancel_requested"])
    def test_error_or_cancel_keeps_the_marker(self, flag):
        marker = self._marker()
        session = _session()
        setattr(session, flag, True)
        with _task(session=session) as ctx:
            ctx.loop_started()
            ctx.loop_completed()
        assert marker.exists()

    def test_a_crash_inside_the_task_keeps_the_marker(self):
        marker = self._marker()
        # 例如強制結束；finally 不得清除標記
        with pytest.raises(KeyboardInterrupt), _task(session=_session()) as ctx:
            ctx.loop_started()
            raise KeyboardInterrupt
        assert marker.exists()

    def test_the_initial_marker_is_durable_before_any_adapter_exists(self):
        """review：第一個批次 durable 之前被強制結束，重開後也要偵測得到。"""
        with _task(
            kind="md",
            input_dir="C:/in",
            output_dir="C:/out",
            options={"step_translate": True, "lang_mode": "all"},
        ):
            marker = plugin_resume.marker_path("md")
            assert marker.exists(), "還沒有任何翻譯批次，標記就必須已經在磁碟上"
            data = json.loads(marker.read_text("utf-8"))

        assert data["version"] == 2 and data["kind"] == "md"
        assert (data["input_dir"], data["output_dir"]) == ("C:/in", "C:/out")
        assert data["options"] == {"step_translate": True, "lang_mode": "all"}
        assert data["fingerprint"] == "fp"
        assert (data["completed_count"], data["total"]) == (0, 0)
        assert data["status"] == "STARTED" and data["updated_at"]

    def test_a_crash_before_the_first_adapter_keeps_the_initial_marker(self):
        with pytest.raises(KeyboardInterrupt), _task(session=_session()):
            raise KeyboardInterrupt  # 例如抽取途中被強制結束

        (task,) = lm_resume.peek_interrupted_tasks()
        assert task.kind == "kubejs" and task.has_current_format
        assert (task.completed, task.total) == (0, 0)

    def test_the_initial_marker_is_detected_and_resumable(self, tmp_path):
        root = _kubejs(tmp_path)
        fp = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        with (
            pytest.raises(KeyboardInterrupt),
            _task(input_dir=str(root), fingerprint_fn=lambda *_a: fp),
        ):
            raise KeyboardInterrupt

        (task,) = lm_resume.peek_interrupted_tasks()

        assert lm_resume.check_resume_feasibility(task).ok

    def test_a_new_task_replaces_the_previous_marker_of_the_same_flow(self):
        self._marker()  # 舊任務遺留的標記
        with _task(options={"step_translate": True, "fresh": True}):
            data = json.loads(plugin_resume.marker_path("kubejs").read_text("utf-8"))
        assert data["options"].get("fresh") is True and data["completed_count"] == 0

    def test_a_failed_initial_write_never_breaks_the_task(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(plugin_resume, "log_warning", warnings.append)

        def deny(*_a, **_k):
            raise PermissionError("read-only")

        monkeypatch.setattr(plugin_resume, "write_marker", deny)
        with _task(session=_session()) as ctx:
            assert ctx is not None  # 任務照常執行，adapter 之後仍可寫標記
        assert any("初始續跑標記" in w for w in warnings)

    def test_the_initial_marker_write_is_atomic_and_fsynced(self, monkeypatch):
        synced: list[str] = []
        real_fsync = os.fsync
        monkeypatch.setattr(
            os, "fsync", lambda fd: (synced.append("file"), real_fsync(fd))
        )
        monkeypatch.setattr(
            plugin_resume, "fsync_directory", lambda path: synced.append("dir")
        )

        with _task():
            pass

        assert synced == ["file", "dir"]
        assert not plugin_resume.marker_path("kubejs").with_suffix(".tmp").exists()

    def test_progress_accumulates_across_loops(self):
        with _task() as ctx:
            ctx.loop_started()
            ctx.loop_completed(4)
            ctx.loop_started()
            ctx.loop_completed(2)
            assert ctx.completed_base == 6 and ctx.all_loops_done


# ------------------------------------------------------------------ 標記讀取


class TestReadMarker:
    def test_missing_marker_is_none(self):
        assert plugin_resume.read_marker("md") is None

    def test_corrupt_marker_is_quarantined_with_a_warning(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(plugin_resume, "log_warning", warnings.append)
        path = _put(plugin_resume.marker_path("md"), "{not json")

        assert plugin_resume.read_marker("md") is None

        assert not path.exists() and Path(f"{path}.corrupt").exists()
        assert warnings and ".corrupt" in warnings[0]
        assert plugin_resume.read_marker("md") is None  # 下一次是安靜的

    def test_non_object_json_is_quarantined(self):
        path = _put(plugin_resume.marker_path("md"), "[1]")
        assert plugin_resume.read_marker("md") is None
        assert Path(f"{path}.corrupt").exists()


# ------------------------------------------------------------------ adapter


def _state(processed=1, total=8):
    return {
        "cache_type": "kubejs",
        "processed": processed,
        "total": total,
        "completed_calls": 1,
        "status": "AUTO",
    }


def _ctx(kind="kubejs", **kwargs):
    ctx = plugin_resume.ResumeContext(kind, "in", "out", {"opt": 1}, "stable-fp")
    for key, value in kwargs.items():
        setattr(ctx, key, value)
    with plugin_resume._ACTIVE_LOCK:
        plugin_resume._ACTIVE[kind] = ctx
    return ctx


class TestAdapterUnderAResumeTask:
    def test_writes_a_version_2_marker_with_the_task_level_fingerprint(self):
        ctx = _ctx()
        adapter = make_checkpoint_adapter(
            "kubejs",
            [{"file": "f", "path": "p", "source_text": "s", "text": "s"}],
            target="t",
        )

        adapter(_state(processed=3, total=9))

        data = json.loads(plugin_resume.marker_path("kubejs").read_text("utf-8"))
        assert data["version"] == 2 and data["kind"] == "kubejs"
        assert data["fingerprint"] == "stable-fp"
        assert data["items_fingerprint"] == adapter.fingerprint
        assert (data["input_dir"], data["output_dir"], data["options"]) == (
            "in",
            "out",
            {"opt": 1},
        )
        assert (data["completed_count"], data["total"]) == (3, 9)
        assert data["updated_at"]
        assert ctx.loops_started == 1

    def test_progress_includes_the_loops_that_already_finished(self):
        ctx = _ctx()
        first = make_checkpoint_adapter("kubejs", [], target="a")
        first(_state(processed=4, total=10))
        first.clear()
        second = make_checkpoint_adapter("kubejs", [], target="b")

        second(_state(processed=2, total=10))

        assert ctx.completed_base == 4
        data = json.loads(plugin_resume.marker_path("kubejs").read_text("utf-8"))
        assert data["completed_count"] == 6

    def test_clear_only_reports_the_loop_and_keeps_the_task_marker(self):
        ctx = _ctx()
        adapter = make_checkpoint_adapter("kubejs", [], target="t")
        adapter(_state())

        adapter.clear()

        assert plugin_resume.marker_path("kubejs").exists(), "整個任務完成時才清除"
        assert ctx.loops_completed == 1

    def test_without_a_task_the_legacy_behaviour_is_unchanged(self):
        adapter = make_checkpoint_adapter("kubejs", [], target="t")
        adapter(_state())
        data = json.loads(plugin_resume.marker_path("kubejs").read_text("utf-8"))

        assert "version" not in data and "kind" not in data
        adapter.clear()
        assert not plugin_resume.marker_path("kubejs").exists()

    def test_an_explicit_path_never_joins_a_task(self, tmp_path):
        ctx = _ctx()
        adapter = JsonCheckpointAdapter("kubejs", "fp", path=tmp_path / "x.json")
        adapter(_state())

        assert ctx.loops_started == 0
        assert "version" not in json.loads((tmp_path / "x.json").read_text("utf-8"))

    def test_another_flows_task_is_not_joined(self):
        ctx = _ctx("md")
        make_checkpoint_adapter("kubejs", [], target="t")
        assert ctx.loops_started == 0


# ------------------------------------------------------------------ lm_resume


def _write_plugin_marker(file_kind, **overrides):
    data = {
        "version": 2,
        "kind": file_kind,
        "input_dir": "C:/in",
        "output_dir": "C:/out",
        "options": {"step_translate": True},
        "fingerprint": "abc",
        "completed_count": 3,
        "total": 9,
        "updated_at": "2026-10-05T12:00:00+0000",
    }
    data.update(overrides)
    _put(plugin_resume.marker_path(file_kind), json.dumps(data))


class TestPeekInterruptedTasks:
    def test_nothing_to_resume(self):
        assert lm_resume.peek_interrupted_tasks() == []

    def test_lists_every_kind_in_a_stable_order(self):
        lm_translator.save_checkpoint(
            1,
            1,
            2,
            [],
            "out",
            input_dir="in",
            fingerprint="x",
            export_lang=False,
            write_new_cache=True,
        )
        for kind in ("md", "ftbquests", "kubejs"):
            _write_plugin_marker(kind)

        tasks = lm_resume.peek_interrupted_tasks()

        assert [t.kind for t in tasks] == ["lm_directory", "ftbquests", "kubejs", "md"]
        assert [t.label for t in tasks] == [
            "機器翻譯",
            "FTB 任務翻譯",
            "KubeJS 翻譯",
            "Markdown 翻譯",
        ]

    def test_task_carries_the_options_and_progress(self):
        _write_plugin_marker("md", options={"lang_mode": "all", "step_inject": False})

        (task,) = lm_resume.peek_interrupted_tasks()

        assert task.options == {"lang_mode": "all", "step_inject": False}
        assert (task.input_dir, task.output_dir, task.completed, task.total) == (
            "C:/in",
            "C:/out",
            3,
            9,
        )
        assert task.has_current_format

    def test_legacy_write_only_markers_are_reported_as_not_resumable(self):
        """舊版 adapter 的檔案沒有版本、沒有輸入資料夾：不能續跑，但**不得靜默忽略**——
        使用者要能看到原因並放棄（#164 驗收條件）。"""
        _put(
            plugin_resume.marker_path("md"),
            json.dumps(
                {"plugin": "md", "target": "C:/old_out", "processed": 3, "total": 9}
            ),
        )

        (task,) = lm_resume.peek_interrupted_tasks()

        assert task.kind == "md" and task.label == "Markdown 翻譯"
        assert not task.has_current_format and task.fingerprint is None
        assert (task.output_dir, task.completed, task.total) == ("C:/old_out", 3, 9)
        check = lm_resume.check_resume_feasibility(task)
        assert check.ok is False
        assert "舊版" in check.reason and "放棄" in check.reason

    def test_a_legacy_marker_can_be_discarded_and_then_stops_being_reported(self):
        _put(plugin_resume.marker_path("kubejs"), json.dumps({"plugin": "kubejs"}))
        (task,) = lm_resume.peek_interrupted_tasks()

        lm_resume.discard_interrupted_task(task)

        assert not plugin_resume.marker_path("kubejs").exists()
        assert lm_resume.peek_interrupted_tasks() == []

    @pytest.mark.parametrize(
        "overrides",
        [{"kind": "kubejs"}, {"input_dir": None}, {"version": 1}],
        ids=["wrong-kind", "missing-input", "old-version"],
    )
    def test_malformed_current_markers_are_also_reported_not_ignored(self, overrides):
        _write_plugin_marker("md", **overrides)

        (task,) = lm_resume.peek_interrupted_tasks()

        assert task.kind == "md" and not task.has_current_format
        assert lm_resume.check_resume_feasibility(task).ok is False

    def test_detection_never_calls_the_api(self, monkeypatch):
        _write_plugin_marker("kubejs")

        def forbidden(*_a, **_k):
            raise AssertionError("偵測不得呼叫翻譯 API")

        monkeypatch.setattr(lm_translator, "translate_batch_smart", forbidden)
        monkeypatch.setattr(lm_translator, "validate_api_keys", forbidden)
        assert len(lm_resume.peek_interrupted_tasks()) == 1


class TestCheckPluginTask:
    def _task(self, tmp_path, **overrides):
        root = _kubejs(tmp_path)
        fp = plugin_resume.compute_source_fingerprint("kubejs", str(root), None)
        _write_plugin_marker(
            "kubejs",
            **{
                "input_dir": str(root),
                "output_dir": "",
                "fingerprint": fp,
                **overrides,
            },
        )
        (task,) = lm_resume.peek_interrupted_tasks()
        return root, task

    def test_same_input_is_resumable(self, tmp_path):
        _, task = self._task(tmp_path)
        assert lm_resume.check_resume_feasibility(task).ok

    def test_changed_input_cannot_resume_and_the_marker_is_kept(self, tmp_path):
        root, task = self._task(tmp_path)
        (root / "client_scripts/a.js").write_text("changed", encoding="utf-8")

        check = lm_resume.check_resume_feasibility(task)

        assert check.ok is False and "不同" in check.reason
        assert plugin_resume.marker_path("kubejs").exists()

    def test_missing_input_folder(self, tmp_path):
        root, task = self._task(tmp_path)
        import shutil

        shutil.rmtree(root.parent)

        check = lm_resume.check_resume_feasibility(task)

        assert check.ok is False and "不存在" in check.reason

    def test_marker_without_a_fingerprint(self, tmp_path):
        _, task = self._task(tmp_path, fingerprint="")
        assert lm_resume.check_resume_feasibility(task).ok is False

    def test_unreadable_sources(self, tmp_path, monkeypatch):
        _, task = self._task(tmp_path)
        monkeypatch.setattr(
            plugin_resume, "compute_source_fingerprint", lambda *_a: None
        )
        assert "無法讀取" in lm_resume.check_resume_feasibility(task).reason


class TestDiscard:
    def test_discarding_a_plugin_task_removes_only_its_marker(self, tmp_path):
        _write_plugin_marker("md")
        _write_plugin_marker("kubejs")
        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="x"
        )
        md = next(t for t in lm_resume.peek_interrupted_tasks() if t.kind == "md")

        lm_resume.discard_interrupted_task(md)

        assert not plugin_resume.marker_path("md").exists()
        assert plugin_resume.marker_path("kubejs").exists()
        assert Path(lm_translator.CHECKPOINT_FILE).exists()

    def test_discard_without_a_task_still_clears_the_machine_translation_marker(self):
        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="x"
        )
        lm_resume.discard_interrupted_task()
        assert not Path(lm_translator.CHECKPOINT_FILE).exists()
