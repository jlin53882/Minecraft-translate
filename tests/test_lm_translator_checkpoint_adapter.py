import json

import pytest

from translation_tool.core.lm_translator_skeleton import make_checkpoint_adapter


def test_checkpoint_adapter_persists_minimal_recovery_metadata(tmp_path):
    adapter = make_checkpoint_adapter(
        "md",
        [{"file": "a", "path": "p", "source_text": "hello", "text": "hello"}],
        target="out.json",
    )
    adapter.path = tmp_path / "checkpoint.json"

    adapter(
        {
            "cache_type": "md",
            "processed": 3,
            "total": 8,
            "completed_calls": 1,
            "status": "AUTO",
        }
    )

    payload = json.loads(adapter.path.read_text(encoding="utf-8"))
    assert payload["plugin"] == "md"
    assert payload["target"] == "out.json"
    assert payload["processed"] == 3
    assert payload["total"] == 8
    assert payload["fingerprint"]
    adapter.clear()
    assert not adapter.path.exists()


def _state():
    return {
        "cache_type": "md",
        "processed": 3,
        "total": 8,
        "completed_calls": 1,
        "status": "AUTO",
    }


def _adapter(plugin="md"):
    return make_checkpoint_adapter(
        plugin,
        [{"file": "a", "path": "p", "source_text": "hello", "text": "hello"}],
        target="out.json",
    )


def test_default_path_is_under_the_data_root_not_the_working_directory(
    tmp_path, monkeypatch
):
    """#162：預設位置是 ``<資料根目錄>/logs``，不隨執行時的工作目錄改變。"""
    data_root = tmp_path / "data"
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.setenv("MCT_DATA_DIR", str(data_root))
    monkeypatch.chdir(cwd)

    adapter = _adapter("kubejs")
    adapter(_state())

    expected = data_root.resolve() / "logs" / "translator_kubejs_checkpoint.json"
    assert adapter.path == expected
    assert expected.is_file()
    assert not (cwd / "logs").exists(), "不得再寫到工作目錄的 logs/"


def test_each_plugin_gets_its_own_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
    names = {_adapter(p).path.name for p in ("ftbquests", "kubejs", "md")}
    assert names == {
        "translator_ftbquests_checkpoint.json",
        "translator_kubejs_checkpoint.json",
        "translator_md_checkpoint.json",
    }


def test_write_is_fsynced_and_leaves_no_tmp_file(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    adapter = _adapter()

    adapter(_state())

    assert synced, "寫入後必須 fsync，不能依賴關閉時的清理"
    assert not adapter.path.with_suffix(".tmp").exists()
    assert json.loads(adapter.path.read_text(encoding="utf-8"))["processed"] == 3


def test_directory_is_fsynced_after_the_replace(tmp_path, monkeypatch):
    """只 fsync 暫存檔不夠：rename 造成的目錄項目更新也要同步（replace 之後）。"""
    from translation_tool.core import lm_translator_skeleton as skeleton

    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
    events: list[tuple[str, object]] = []
    adapter = _adapter()
    real_replace = type(adapter.path).replace

    def tracking_replace(self, target):
        events.append(("replace", str(target)))
        return real_replace(self, target)

    monkeypatch.setattr(type(adapter.path), "replace", tracking_replace)
    monkeypatch.setattr(
        skeleton, "fsync_directory", lambda path: events.append(("fsync_dir", path))
    )

    adapter(_state())

    assert [name for name, _ in events] == ["replace", "fsync_dir"], (
        "必須先 replace，再同步目錄"
    )
    assert events[1][1] == adapter.path.parent


def test_crash_before_replace_keeps_the_previous_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("MCT_DATA_DIR", str(tmp_path))
    adapter = _adapter()
    adapter(_state())
    before = adapter.path.read_text(encoding="utf-8")

    def crash(self, *_a, **_k):
        raise OSError("simulated power loss before replace")

    monkeypatch.setattr(type(adapter.path), "replace", crash)
    newer = {**_state(), "processed": 6}
    with pytest.raises(OSError):
        adapter(newer)
    monkeypatch.undo()

    assert adapter.path.read_text(encoding="utf-8") == before  # 不會留下半截 JSON


def test_explicit_path_still_wins_and_success_clears_but_failure_keeps(tmp_path):
    adapter = _adapter()
    adapter.path = tmp_path / "custom" / "cp.json"

    adapter(_state())
    assert adapter.path.is_file()  # 失敗／取消只寫入、不清除
    adapter.clear()
    assert not adapter.path.exists()
    adapter.clear()  # 重複清除不得出錯
