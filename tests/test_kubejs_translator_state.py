from __future__ import annotations

from pathlib import Path

from translation_tool.core import kubejs_translator_state


def test_commit_staging_run_copies_when_windows_blocks_directory_rename(
    tmp_path: Path, monkeypatch
) -> None:
    staging = tmp_path / ".pipeline" / "staging" / "abc"
    committed = tmp_path / ".pipeline" / "runs" / "abc"
    staging.mkdir(parents=True)
    (staging / "data.json").write_text('{"key": "value"}', encoding="utf-8")

    def blocked_rename(*_args, **_kwargs):
        raise PermissionError("simulated Windows directory lock")

    monkeypatch.setattr(kubejs_translator_state.os, "replace", blocked_rename)

    used_copy = kubejs_translator_state.commit_staging_run(staging, committed)

    assert used_copy is True
    assert (committed / "data.json").read_text(encoding="utf-8") == '{"key": "value"}'
    assert not staging.exists()
