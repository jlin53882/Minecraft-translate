"""checkpoint／續跑標記的原子寫入：Windows 鎖檔重試、失敗保留舊檔、不留暫存檔。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from translation_tool.core import lm_translator, plugin_resume
from translation_tool.core.lm_translator_skeleton import JsonCheckpointAdapter
from translation_tool.utils import file_replace


class _FlakyReplace:
    """前 ``failures`` 次 ``os.replace`` 丟 PermissionError（模擬 Windows 防毒／同步程式鎖檔）。"""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self._real = os.replace

    def __call__(self, source, target):
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError(5, "Access is denied")
        return self._real(source, target)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(file_replace.time, "sleep", lambda _s: None)


def test_atomic_write_text_retries_locked_replace(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    flaky = _FlakyReplace(failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    file_replace.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert flaky.calls == 3
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_text_keeps_old_file_and_cleans_temp_on_permanent_lock(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _FlakyReplace(failures=10**6))

    with pytest.raises(PermissionError):
        file_replace.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_text_crash_before_replace_keeps_old_file(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def boom(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(file_replace.os, "fsync", boom)

    with pytest.raises(OSError, match="disk full"):
        file_replace.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_text_does_not_touch_same_named_stale_temp(
    tmp_path: Path,
) -> None:
    """舊版固定的 ``.tmp`` 名稱殘檔（可能屬於別的寫入者）不會被覆蓋或刪除。"""
    target = tmp_path / "state.json"
    stale = tmp_path / "state.tmp"
    stale.write_text("other writer", encoding="utf-8")

    file_replace.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert stale.read_text(encoding="utf-8") == "other writer"


def test_atomic_write_text_creates_missing_parent(tmp_path: Path) -> None:
    target = tmp_path / "logs" / "deep" / "state.json"
    file_replace.atomic_write_text(target, "x")
    assert target.read_text(encoding="utf-8") == "x"


# --- 各呼叫端：鎖檔暫時拒絕時 checkpoint／標記仍要寫成功 -----------------------


def test_plugin_resume_marker_survives_locked_replace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        plugin_resume, "marker_path", lambda kind: tmp_path / f"{kind}.json"
    )
    flaky = _FlakyReplace(failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    plugin_resume.write_marker("ftbquests", {"status": "STARTED"})

    assert (tmp_path / "ftbquests.json").read_text(encoding="utf-8") == (
        '{"status": "STARTED"}'
    )
    assert flaky.calls == 3


def test_json_checkpoint_adapter_survives_locked_replace(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = JsonCheckpointAdapter("md", "fp", path=tmp_path / "md.json")
    flaky = _FlakyReplace(failures=1)
    monkeypatch.setattr(os, "replace", flaky)

    adapter({"processed": 3, "total": 5, "status": "RUNNING"})

    assert '"processed": 3' in (tmp_path / "md.json").read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_lm_save_checkpoint_survives_locked_replace(
    tmp_path: Path, monkeypatch
) -> None:
    checkpoint = tmp_path / "translation_checkpoint.json"
    monkeypatch.setattr(lm_translator, "CHECKPOINT_FILE", str(checkpoint))
    flaky = _FlakyReplace(failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    lm_translator.save_checkpoint(1, 2, 10, [], str(tmp_path / "out"))

    assert '"completed_count": 2' in checkpoint.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp*"))


# --- 其他同類寫入點（原本各自 os.replace／自製重試）也共用鎖檔重試 -------------


def test_kubejs_atomic_write_bytes_uses_shared_retry(
    tmp_path: Path, monkeypatch
) -> None:
    from translation_tool.core import kubejs_translator_state

    target = tmp_path / "current.json"
    flaky = _FlakyReplace(failures=5)  # 超過舊版自製的 4 次，共用重試要能撐過
    monkeypatch.setattr(os, "replace", flaky)

    kubejs_translator_state.atomic_write_bytes(target, b"{}")

    assert target.read_bytes() == b"{}"
    assert not list(tmp_path.glob("*.tmp"))


def test_bundle_state_sidecar_survives_locked_replace(
    tmp_path: Path, monkeypatch
) -> None:
    from translation_tool.core import output_bundler

    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(b"zip")
    flaky = _FlakyReplace(failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    output_bundler._commit_state(str(zip_path), "fp")

    assert Path(output_bundler._state_path(str(zip_path))).exists()


def test_quality_report_replace_survives_locked_replace(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services_impl import moddb_translate_report

    flaky = _FlakyReplace(failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    path = moddb_translate_report.write_replace_rule_conflicts(
        tmp_path, [{"source": "a", "destination": "ab"}]
    )

    assert path is not None and path.exists()
    assert not list(tmp_path.glob("*.tmp"))
