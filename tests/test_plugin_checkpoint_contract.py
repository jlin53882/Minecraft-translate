"""三外掛的 checkpoint 清除契約：只有迴圈 DONE 才清；取消、部分完成、金鑰耗盡、失敗都保留。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.plugins.ftbquests import ftbquests_lmtranslator
from translation_tool.plugins.kubejs import kubejs_tooltip_lmtranslator
from translation_tool.plugins.md import md_lmtranslator

STATUSES = ["DONE", "CANCELLED", "PARTIAL", "ALL_KEYS_EXHAUSTED", "FAILED"]


class _RecordingCheckpoint:
    def __init__(self) -> None:
        self.cleared = 0

    def __call__(self, state) -> None:  # 供 hooks.on_batch_checkpoint
        pass

    def clear(self) -> None:
        self.cleared += 1


def _install(module, monkeypatch, status: str) -> _RecordingCheckpoint:
    checkpoint = _RecordingCheckpoint()
    monkeypatch.setattr(module, "validate_api_keys", lambda: None)
    monkeypatch.setattr(module, "make_checkpoint_adapter", lambda *a, **k: checkpoint)
    monkeypatch.setattr(
        module,
        "run_translator_skeleton",
        lambda *a, **k: SimpleNamespace(
            status=status, processed=0, elapsed_sec=0.0, completed_calls=0
        ),
    )
    return checkpoint


def _run_ftb(tmp_path: Path) -> dict:
    source = tmp_path / "lang" / "en_us"
    source.mkdir(parents=True)
    (source / "a.json").write_text(
        json.dumps({"a.line": "Hello brave new world"}), encoding="utf-8"
    )
    return ftbquests_lmtranslator.translate_ftb_pending_to_zh_tw(
        input_lang_dir=str(tmp_path / "lang"),
        output_lang_dir=str(tmp_path / "out"),
        dry_run=False,
        write_new_cache=False,
    )


def _run_kubejs(tmp_path: Path) -> dict:
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "a.json").write_text(
        json.dumps({"a.line": "Hello brave new world"}), encoding="utf-8"
    )
    return kubejs_tooltip_lmtranslator.translate_kubejs_pending_to_zh_tw(
        pending_dir=pending, output_dir=tmp_path / "out", write_new_cache=False
    )


def _run_md(tmp_path: Path) -> dict:
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "intro.md.json").write_text(
        json.dumps(
            {
                "schema": "md_pending_blocks_v1",
                "items": [
                    {
                        "id": "1",
                        "text": "Hello brave new world",
                        "content_hash": "h1",
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return md_lmtranslator.translate_md_pending(
        pending_dir=str(pending), out_dir=str(tmp_path / "out"), write_new_cache=False
    )


PLUGINS = {
    "ftbquests": (ftbquests_lmtranslator, _run_ftb),
    "kubejs": (kubejs_tooltip_lmtranslator, _run_kubejs),
    "md": (md_lmtranslator, _run_md),
}


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("plugin", sorted(PLUGINS))
def test_checkpoint_is_cleared_only_when_loop_is_done(
    plugin, status, tmp_path: Path, monkeypatch
) -> None:
    module, run = PLUGINS[plugin]
    checkpoint = _install(module, monkeypatch, status)

    run(tmp_path)

    assert checkpoint.cleared == (1 if status == "DONE" else 0), (plugin, status)


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_cancelled"),
    [("done", "DONE", False), ("cancelled", "CANCELLED", True)],
)
@pytest.mark.parametrize("plugin", sorted(PLUGINS))
def test_plugin_normalizes_loop_status_before_returning(
    plugin, status, expected_status, expected_cancelled, tmp_path: Path, monkeypatch
) -> None:
    """三個外掛回傳的狀態與取消旗標必須使用一致的大寫契約。"""
    module, run = PLUGINS[plugin]
    _install(module, monkeypatch, status)

    result = run(tmp_path)

    assert result["status"] == expected_status
    assert result["cancelled"] is expected_cancelled
