"""舊版 Markdown 殘留續跑標記的讀取、指紋驗證、偵測與清除。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from translation_tool.core import lm_resume, lm_translator, plugin_resume


@pytest.fixture(autouse=True)
def _data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        lm_translator,
        "CHECKPOINT_FILE",
        str(tmp_path / "data/logs/translation_checkpoint.json"),
    )
    yield


def _put(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(text)
    return path


def _md_pack(tmp_path: Path) -> Path:
    root = tmp_path / "pack" / "docs"
    _put(root / "en_us/a.md", "# Hello")
    _put(root / "en_us/z.md", "# World")
    return root


# ------------------------------------------------------------------ 指紋


class TestSourceFingerprint:
    def test_depends_on_file_contents_not_mtime(self, tmp_path):
        root = _md_pack(tmp_path)
        before = plugin_resume.compute_source_fingerprint("md", str(root), None)

        os.utime(root / "en_us/a.md", ns=(1_000_000_000, 1_000_000_000))
        assert plugin_resume.compute_source_fingerprint("md", str(root), None) == before

        (root / "en_us/a.md").write_text("scene.text('x', 'Changed')", encoding="utf-8")
        assert plugin_resume.compute_source_fingerprint("md", str(root), None) != before

    def test_same_size_different_content_is_detected(self, tmp_path):
        root = _md_pack(tmp_path)
        f = root / "en_us/a.md"
        f.write_text("AAAA", encoding="utf-8")
        a = plugin_resume.compute_source_fingerprint("md", str(root), None)
        f.write_text("BBBB", encoding="utf-8")

        assert plugin_resume.compute_source_fingerprint("md", str(root), None) != a

    def test_a_renamed_or_added_file_changes_it(self, tmp_path):
        root = _md_pack(tmp_path)
        base = plugin_resume.compute_source_fingerprint("md", str(root), None)
        (root / "en_us/a.md").rename(root / "en_us/b.md")
        renamed = plugin_resume.compute_source_fingerprint("md", str(root), None)
        _put(root / "en_us/c.md", "x")
        added = plugin_resume.compute_source_fingerprint("md", str(root), None)

        assert len({base, renamed, added}) == 3

    def test_files_the_flow_never_reads_are_ignored(self, tmp_path):
        root = _md_pack(tmp_path)
        base = plugin_resume.compute_source_fingerprint("md", str(root), None)
        _put(root / "assets/mod/textures/big.png", b"\x89PNG" * 1000)

        assert plugin_resume.compute_source_fingerprint("md", str(root), None) == base

    def test_the_output_folder_is_excluded(self, tmp_path):
        """輸出預設在輸入底下的 Output/：流程自己寫的檔案不得讓指紋改變。"""
        root = _md_pack(tmp_path)
        base = plugin_resume.compute_source_fingerprint("md", str(root), None)
        _put(root / "Output/en_us/完成/a.md", "generated")
        _put(root / "Output/en_us/raw/x.md", "{}")

        assert plugin_resume.compute_source_fingerprint("md", str(root), None) == base
        custom = root / "my_out"
        _put(custom / "gen.md", "generated")
        assert (
            plugin_resume.compute_source_fingerprint("md", str(root), str(custom))
            != base
        ), "自訂輸出資料夾排除的是它自己，Output/ 此時屬於來源"

    def test_no_sources_or_missing_folder_means_no_resume(self, tmp_path):
        assert (
            plugin_resume.compute_source_fingerprint("md", str(tmp_path / "nope"), None)
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
        root = _md_pack(tmp_path)
        monkeypatch.setattr(plugin_resume, "MAX_FINGERPRINT_FILES", 1)
        assert plugin_resume.compute_source_fingerprint("md", str(root), None) is None
        monkeypatch.setattr(plugin_resume, "MAX_FINGERPRINT_FILES", 50_000)
        monkeypatch.setattr(plugin_resume, "MAX_FINGERPRINT_BYTES", 5)
        assert plugin_resume.compute_source_fingerprint("md", str(root), None) is None

    def test_unknown_kind_does_not_crash(self, tmp_path):
        assert (
            plugin_resume.compute_source_fingerprint("nope", str(tmp_path), None)
            is None
        )


# ------------------------------------------------------------------ 讀取標記


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
        _write_plugin_marker("md")

        tasks = lm_resume.peek_interrupted_tasks()

        assert [t.kind for t in tasks] == ["lm_directory", "md"]
        assert [t.label for t in tasks] == ["機器翻譯", "Markdown 翻譯"]

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
        _put(plugin_resume.marker_path("md"), json.dumps({"plugin": "md"}))
        (task,) = lm_resume.peek_interrupted_tasks()

        lm_resume.discard_interrupted_task(task)

        assert not plugin_resume.marker_path("md").exists()
        assert lm_resume.peek_interrupted_tasks() == []

    @pytest.mark.parametrize(
        "overrides",
        [{"kind": "other"}, {"input_dir": None}, {"version": 1}],
        ids=["wrong-kind", "missing-input", "old-version"],
    )
    def test_malformed_current_markers_are_also_reported_not_ignored(self, overrides):
        _write_plugin_marker("md", **overrides)

        (task,) = lm_resume.peek_interrupted_tasks()

        assert task.kind == "md" and not task.has_current_format
        assert lm_resume.check_resume_feasibility(task).ok is False

    def test_detection_never_calls_the_api(self, monkeypatch):
        _write_plugin_marker("md")

        def forbidden(*_a, **_k):
            raise AssertionError("偵測不得呼叫翻譯 API")

        monkeypatch.setattr(lm_translator, "translate_batch_smart", forbidden)
        monkeypatch.setattr(lm_translator, "validate_api_keys", forbidden)
        assert len(lm_resume.peek_interrupted_tasks()) == 1


class TestCheckPluginTask:
    def _task(self, tmp_path, **overrides):
        root = _md_pack(tmp_path)
        fp = plugin_resume.compute_source_fingerprint("md", str(root), None)
        _write_plugin_marker(
            "md",
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
        (root / "en_us/a.md").write_text("changed", encoding="utf-8")

        check = lm_resume.check_resume_feasibility(task)

        assert check.ok is False and "不同" in check.reason
        assert plugin_resume.marker_path("md").exists()

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
        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="x"
        )
        md = next(t for t in lm_resume.peek_interrupted_tasks() if t.kind == "md")

        lm_resume.discard_interrupted_task(md)

        assert not plugin_resume.marker_path("md").exists()
        assert Path(lm_translator.CHECKPOINT_FILE).exists()

    def test_discard_without_a_task_still_clears_the_machine_translation_marker(self):
        lm_translator.save_checkpoint(
            1, 1, 2, [], "out", input_dir="in", fingerprint="x"
        )
        lm_resume.discard_interrupted_task()
        assert not Path(lm_translator.CHECKPOINT_FILE).exists()


class TestDiscardFailure:
    def test_a_failed_discard_raises_so_the_task_stays_listed(
        self, tmp_path, monkeypatch
    ):
        """放棄失敗（例如檔案被占用）時不可靜默成功，否則任務會消失卻仍在下次啟動出現。"""
        monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
        _write_plugin_marker("md")
        task = next(t for t in lm_resume.peek_interrupted_tasks() if t.kind == "md")

        def locked(self, *args, **kwargs):
            raise PermissionError("locked")

        monkeypatch.setattr(Path, "unlink", locked)
        with pytest.raises(PermissionError):
            lm_resume.discard_interrupted_task(task)
        assert plugin_resume.marker_path("md").exists()


class TestPurgeLegacyMarkers:
    def test_legacy_markers_are_purged_and_current_ones_are_kept(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
        for kind in ("md", "kubejs", "ftbquests"):
            legacy = plugin_resume.marker_path(kind)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(
                json.dumps({"plugin": kind, "processed": 2, "total": 7}),
                encoding="utf-8",
            )

        assert lm_resume.purge_legacy_plugin_markers() == ["md", "kubejs", "ftbquests"]
        assert all(
            not plugin_resume.marker_path(kind).exists()
            for kind in ("md", "kubejs", "ftbquests")
        )

        _write_plugin_marker("md")
        _write_plugin_marker("kubejs")
        _write_plugin_marker("ftbquests")
        assert lm_resume.purge_legacy_plugin_markers() == []
        assert all(
            plugin_resume.marker_path(kind).exists()
            for kind in ("md", "kubejs", "ftbquests")
        )
