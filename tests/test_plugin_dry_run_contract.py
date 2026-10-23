"""FTB／MD／KubeJS dry-run 的認證與預覽契約。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.plugins.ftbquests import ftbquests_lmtranslator
from translation_tool.plugins.kubejs import kubejs_tooltip_lmtranslator
from translation_tool.plugins.md import md_lmtranslator


def _write_ftb_input(root: Path) -> Path:
    pending = root / "ftb_pending" / "en_us"
    pending.mkdir(parents=True)
    (pending / "a.json").write_text(
        json.dumps({"a.line": "Hello brave new world"}), encoding="utf-8"
    )
    return pending.parent


def _write_kubejs_input(root: Path) -> Path:
    pending = root / "kubejs_pending"
    pending.mkdir(parents=True)
    (pending / "a.json").write_text(
        json.dumps({"a.line": "Hello brave new world"}), encoding="utf-8"
    )
    return pending


def _write_md_input(root: Path) -> Path:
    pending = root / "md_pending"
    pending.mkdir(parents=True)
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
    return pending


@pytest.mark.parametrize("plugin", ["ftbquests", "kubejs", "md"])
def test_dry_run_does_not_require_api_credentials(
    plugin: str, tmp_path: Path, monkeypatch
) -> None:
    """dry-run 只建立預覽，不應因未設定或不可用的認證而中止。"""
    if plugin == "ftbquests":
        input_dir = _write_ftb_input(tmp_path)
        module = ftbquests_lmtranslator
        result = lambda: module.translate_ftb_pending_to_zh_tw(
            input_lang_dir=input_dir,
            output_lang_dir=tmp_path / "ftb_out",
            dry_run=True,
            write_new_cache=False,
        )
    elif plugin == "kubejs":
        input_dir = _write_kubejs_input(tmp_path)
        module = kubejs_tooltip_lmtranslator
        result = lambda: module.translate_kubejs_pending_to_zh_tw(
            pending_dir=input_dir,
            output_dir=tmp_path / "kubejs_out",
            dry_run=True,
            write_new_cache=False,
        )
    else:
        input_dir = _write_md_input(tmp_path)
        module = md_lmtranslator
        result = lambda: module.translate_md_pending(
            pending_dir=input_dir,
            out_dir=tmp_path / "md_out",
            dry_run=True,
            write_new_cache=False,
        )

    monkeypatch.setattr(
        module,
        "validate_api_keys",
        lambda: pytest.fail("dry-run 不應驗證 API 認證"),
    )

    assert result()["dry_run"] is True
