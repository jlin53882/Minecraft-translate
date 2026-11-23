"""Markdown 管線：完成檔的人工修改保護、重複執行一致性、跨檔同文快取、被取代檔的警告。

LM 換成固定回覆（"譯:" + 原文），檔案層級行為與正式流程相同。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.core import md_translation_steps
from translation_tool.core.md_translation_assembly import run_md_pipeline
from translation_tool.plugins.md import md_lmtranslator

GUIDE = "# Title\n\nFirst paragraph.\n\nSecond paragraph.\n"


@pytest.fixture
def fake_lm(monkeypatch):
    """LM 換成固定回覆，並記錄每次實際送出的文字。"""
    sent: list[str] = []

    def fake_skeleton(items, *, hooks, **_kwargs):
        for it in items:
            sent.append(it["text"])
            hooks.on_translated_item({**it, "text": "譯:" + it["text"]})
        return SimpleNamespace(
            status="DONE", elapsed_sec=0.0, completed_calls=1, processed=len(items)
        )

    monkeypatch.setattr(md_lmtranslator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(
        md_lmtranslator,
        "prepare_translator_items",
        lambda items, **_: ([], list(items)),
    )
    monkeypatch.setattr(md_lmtranslator, "run_translator_skeleton", fake_skeleton)
    return sent


@pytest.fixture
def warnings(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        md_translation_steps, "log_warning", lambda msg, *a: seen.append(str(msg))
    )
    return seen


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(src: Path, out: Path, **kwargs):
    return run_md_pipeline(str(src), output_dir=str(out), lang_mode="all", **kwargs)


def _final(out: Path, rel: str = "pack/zh_tw/guide.md") -> Path:
    return out / "md" / "完成" / rel


def _setup(tmp_path: Path):
    src, out = tmp_path / "in", tmp_path / "out"
    guide = _write(src / "pack" / "en_us" / "guide.md", GUIDE)
    return src, out, guide


def test_manually_edited_output_is_not_overwritten(tmp_path, fake_lm, warnings):
    src, out, guide = _setup(tmp_path)
    _run(src, out)
    _final(out).write_text("我手動修好的譯文\n", encoding="utf-8")
    guide.write_text(GUIDE.replace("First", "Changed"), encoding="utf-8")

    result = _run(src, out)

    assert _final(out).read_text(encoding="utf-8") == "我手動修好的譯文\n"
    assert result["step3"]["skipped_manual_edit"] == 1
    assert result["step3"]["written_files"] == 0
    assert any("手動修改" in w for w in warnings)


def test_deleting_the_edited_output_regenerates_it(tmp_path, fake_lm):
    src, out, _guide = _setup(tmp_path)
    _run(src, out)
    _final(out).write_text("手動修改\n", encoding="utf-8")
    _final(out).unlink()

    result = _run(src, out)

    assert result["step3"]["written_files"] == 1
    assert "譯:First paragraph." in _final(out).read_text(encoding="utf-8")


def test_untouched_output_is_still_overwritten_when_source_changes(tmp_path, fake_lm):
    src, out, guide = _setup(tmp_path)
    _run(src, out)
    guide.write_text(GUIDE.replace("First", "Changed"), encoding="utf-8")

    result = _run(src, out)

    assert result["step3"]["written_files"] == 1
    assert "譯:Changed paragraph." in _final(out).read_text(encoding="utf-8")


def test_existing_output_without_a_record_is_overwritten(tmp_path, fake_lm):
    """舊版產生的完成檔沒有紀錄：視為工具產物，照常覆寫並開始記錄。"""
    src, out, _ = _setup(tmp_path)
    _write(_final(out), "舊版留下\n")

    result = _run(src, out)

    assert result["step3"]["written_files"] == 1
    assert "譯:First paragraph." in _final(out).read_text(encoding="utf-8")


def test_running_twice_without_changes_is_stable(tmp_path, fake_lm):
    src, out, _ = _setup(tmp_path)
    _run(src, out)
    snapshot = {
        p.relative_to(out).as_posix(): p.read_bytes()
        for p in sorted((out / "md").rglob("*"))
        if p.is_file()
    }

    second = _run(src, out)

    again = {
        p.relative_to(out).as_posix(): p.read_bytes()
        for p in sorted((out / "md").rglob("*"))
        if p.is_file()
    }
    assert again == snapshot
    assert second["step3"]["skipped_manual_edit"] == 0


def test_same_text_in_two_files_is_translated_once_but_written_to_both(
    tmp_path, fake_lm
):
    src, out = tmp_path / "in", tmp_path / "out"
    _write(src / "a" / "en_us" / "one.md", "Shared sentence here.\n")
    _write(src / "b" / "en_us" / "two.md", "Shared sentence here.\n\nOnly in two.\n")

    result = _run(src, out)

    assert fake_lm.count("Shared sentence here.") == 1
    one = _final(out, "a/zh_tw/one.md").read_text(encoding="utf-8")
    two = _final(out, "b/zh_tw/two.md").read_text(encoding="utf-8")
    assert one == "譯:Shared sentence here.\n"
    assert two == "譯:Shared sentence here.\n\n譯:Only in two.\n"
    assert result["step3"]["written_files"] == 2


def test_replaced_english_translation_does_not_trigger_not_updated_warning(
    tmp_path, fake_lm, warnings
):
    """英文先翻過，之後改由 zh_cn 底稿負責：舊英文譯後檔是預期的略過，不該警告「沒有更新」。"""
    src, out = tmp_path / "in", tmp_path / "out"
    _write(src / "pack" / "en_us" / "guide.md", GUIDE)
    _run(src, out)
    _write(src / "pack" / "zh_cn" / "guide.md", "# 标题\n\n第一段。\n\n第二段。\n")

    result = run_md_pipeline(str(src), output_dir=str(out), lang_mode="non_cjk_only")

    assert result["step3"]["skipped_superseded"] == 1
    assert result["step3"]["skipped_output_conflict"] == 0
    assert not any("沒有更新" in w for w in warnings)
    manifest = json.loads(
        (out / "md" / "完成" / ".md_output_hashes.json").read_text(encoding="utf-8")
    )
    assert "pack/zh_tw/guide.md" in manifest["files"]
