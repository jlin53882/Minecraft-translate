"""FTB／KubeJS 預掃描：共用預覽欄位清理，以及計數後檔案被改動（TOCTOU）不會崩潰。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation_tool.core.lm_translator_shared_preview import strip_runtime_fields
from translation_tool.plugins.ftbquests import ftbquests_lmtranslator
from translation_tool.plugins.kubejs import kubejs_tooltip_lmtranslator


def test_strip_runtime_fields_drops_shield_without_mutating_input():
    items = [{"path": "a", "text": "x", "_shielded": object()}, {"path": "b"}]

    cleaned = strip_runtime_fields(items)

    assert cleaned == [{"path": "a", "text": "x"}, {"path": "b"}]
    assert "_shielded" in items[0]  # 原 item 仍保留，翻譯流程還要用


def _write_pending(root: Path) -> list[Path]:
    root.mkdir(parents=True)
    files = []
    for name in ("a", "b"):
        path = root / f"{name}.json"
        path.write_text(
            json.dumps({f"{name}.line": f"Hello {name} brave new world"}),
            encoding="utf-8",
        )
        files.append(path)
    return files


def _read_once_then_vanish(module, monkeypatch):
    """每個檔案只有第一次讀得到；之後模擬被刪除／改動（預掃描與後續處理之間）。"""
    real = module.read_json_dict
    seen: set[Path] = set()

    def reader(path):
        key = Path(path)
        if key in seen:
            raise FileNotFoundError(f"{key} 在計數後被移除")
        seen.add(key)
        return real(path)

    monkeypatch.setattr(module, "read_json_dict", reader)


@pytest.mark.parametrize("dry_run", [True])
def test_kubejs_dry_run_survives_file_changing_after_count(
    tmp_path: Path, monkeypatch, dry_run
):
    pending = tmp_path / "pending"
    _write_pending(pending)
    module = kubejs_tooltip_lmtranslator
    monkeypatch.setattr(module, "validate_api_keys", lambda: None)
    _read_once_then_vanish(module, monkeypatch)

    result = module.translate_kubejs_pending_to_zh_tw(
        pending_dir=pending, output_dir=tmp_path / "out", dry_run=dry_run
    )

    assert result["total_keys"] == 2
    assert {row["file"] for row in result["per_file"]} == {"a.json", "b.json"}


def test_ftb_dry_run_survives_file_changing_after_count(tmp_path: Path, monkeypatch):
    """FTB 對照組：計數時已快取 mapping，後續不重讀檔案。"""
    pending = tmp_path / "lang" / "en_us"
    _write_pending(pending)
    module = ftbquests_lmtranslator
    monkeypatch.setattr(module, "validate_api_keys", lambda: None)
    _read_once_then_vanish(module, monkeypatch)

    result = module.translate_ftb_pending_to_zh_tw(
        input_lang_dir=str(tmp_path / "lang"),
        output_lang_dir=str(tmp_path / "out"),
        dry_run=True,
        write_new_cache=False,
    )

    assert result["total_keys"] == 2
