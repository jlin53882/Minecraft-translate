from __future__ import annotations

import json
from pathlib import Path

from translation_tool.core import md_translation_assembly


class _Session:
    def __init__(self):
        self.values = []

    def set_progress(self, value):
        self.values.append(value)


def test_step2_translate_uses_progress_proxy_and_forwards_flags(tmp_path: Path, monkeypatch) -> None:
    calls = {}
    session = _Session()

    def fake_translate_md_pending(**kwargs):
        calls.update(kwargs)
        kwargs["session"].set_progress(0.5)
        return {"dry_run": kwargs["dry_run"], "files": 1}

    monkeypatch.setattr(md_translation_assembly, "translate_md_pending", fake_translate_md_pending)

    result = md_translation_assembly.step2_translate(
        pending_dir=str(tmp_path / "pending"),
        translated_dir=str(tmp_path / "translated"),
        session=session,
        progress_base=0.33,
        progress_span=0.33,
        dry_run=True,
        write_new_cache=False,
    )

    assert result == {"dry_run": True, "files": 1}
    assert calls["dry_run"] is True
    assert calls["write_new_cache"] is False
    assert session.values[-2:] == [0.495, 0.66]


def test_step1_extract_writes_manifest_when_no_md_files(tmp_path: Path) -> None:
    session = _Session()
    pending_dir = tmp_path / "pending"

    result = md_translation_assembly.step1_extract(
        input_dir=str(tmp_path),
        pending_dir=str(pending_dir),
        lang_mode="non_cjk_only",
        session=session,
    )

    manifest = json.loads((pending_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert result["md_files_found"] == 0
    assert manifest["schema"] == "md_pending_manifest_blocks_v1"
    assert session.values[-1] == 0.33
