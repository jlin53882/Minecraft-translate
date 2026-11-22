"""Markdown 管線：本輪一致性與安全寫回（issue #185 Phase A）。

用真正的 Step 1／Step 2／Step 3，只把 LM 呼叫換成固定回覆（"譯:" + 原文），
所以檔案層級（pending／LM翻譯後／完成）的行為與正式流程相同。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.core.md_translation_assembly import run_md_pipeline
from translation_tool.plugins.md import md_lmtranslator

GUIDE = "# Title\n\nFirst paragraph.\n\nSecond paragraph.\n"


@pytest.fixture
def fake_lm(monkeypatch):
    """LM 換成固定回覆；status 可由測試調整（預設 DONE）。"""
    state = {"status": "DONE"}

    def fake_skeleton(items, *, hooks, **_kwargs):
        for it in items:
            hooks.on_translated_item({**it, "text": "譯:" + it["text"]})
        return SimpleNamespace(
            status=state["status"],
            elapsed_sec=0.0,
            completed_calls=1,
            processed=len(items),
        )

    monkeypatch.setattr(md_lmtranslator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(
        md_lmtranslator,
        "prepare_translator_items",
        lambda items, **_: ([], list(items)),
    )
    monkeypatch.setattr(md_lmtranslator, "run_translator_skeleton", fake_skeleton)
    return state


def _layout(tmp_path: Path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    guide = src / "pack" / "en_us" / "guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_text(GUIDE, encoding="utf-8")
    return src, out, guide


def _run(src: Path, out: Path, **kwargs):
    return run_md_pipeline(str(src), output_dir=str(out), lang_mode="all", **kwargs)


def _final(out: Path) -> Path:
    return out / "md" / "完成" / "pack" / "zh_tw" / "guide.md"


def _pending(out: Path) -> Path:
    return out / "md" / "待翻譯" / "pack" / "en_us" / "guide.md.json"


def test_full_run_writes_translated_markdown(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)

    result = _run(src, out)

    text = _final(out).read_text(encoding="utf-8")
    assert "譯:First paragraph." in text and "譯:Second paragraph." in text
    assert result["step3"]["written_files"] == 1


def test_resume_step3_only_still_injects_when_source_unchanged(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)
    assert not _final(out).exists()

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["written_files"] == 1
    assert "譯:First paragraph." in _final(out).read_text(encoding="utf-8")


def test_no_translated_json_does_not_write_english_as_done(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)

    result = _run(src, out, step_translate=False)

    assert result["step3"] == {"skipped": True, "reason": "no_translated_json"}
    assert not _final(out).exists()


def test_stale_translation_is_not_injected_after_source_changes(tmp_path, fake_lm):
    src, out, guide = _layout(tmp_path)
    _run(src, out)
    first_output = _final(out).read_text(encoding="utf-8")
    guide.write_text(
        GUIDE.replace("First paragraph.", "Brand new paragraph."), encoding="utf-8"
    )

    # Step 1 重新抽取、Step 2 停用：LM翻譯後 仍是舊來源的譯文
    result = _run(src, out, step_translate=False)

    assert result["step3"]["written_files"] == 0
    assert result["step3"]["skipped_pending_mismatch"] == 1
    assert _final(out).read_text(encoding="utf-8") == first_output


def test_source_edited_after_extract_is_detected_by_block_hash(tmp_path, fake_lm):
    src, out, guide = _layout(tmp_path)
    _run(src, out, step_inject=False)
    # 抽取後才改原始檔（行數不變）：只跑 Step 3
    guide.write_text(
        GUIDE.replace("First paragraph.", "Edited paragraph."), encoding="utf-8"
    )

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["skipped_stale_source"] == 1
    assert not _final(out).exists()


def test_incomplete_step2_output_is_not_injected_on_resume(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    fake_lm["status"] = "FAILED"
    first = _run(src, out)
    assert first["step3"]["reason"] == "translation_failed"

    # 之後只跑 Step 3：LM翻譯後 內是失敗時留下的半成品
    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["skipped_incomplete"] == 1
    assert not _final(out).exists()


def test_deleted_source_removes_its_pending_but_keeps_foreign_json(tmp_path, fake_lm):
    src, out, guide = _layout(tmp_path)
    other = src / "pack" / "en_us" / "other.md"
    other.write_text("Other doc.\n", encoding="utf-8")
    _run(src, out, step_translate=False, step_inject=False)
    pending_root = out / "md" / "待翻譯"
    foreign = pending_root / "notes.json"
    foreign.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    assert _pending(out).exists()

    guide.unlink()
    result = _run(src, out, step_translate=False, step_inject=False)

    assert not _pending(out).exists()
    assert (pending_root / "pack" / "en_us" / "other.md.json").exists()
    assert foreign.exists()
    assert result["step1"]["stale_removed"] == 1


def test_file_with_no_translatable_blocks_drops_its_old_pending(tmp_path, fake_lm):
    src, out, guide = _layout(tmp_path)
    _run(src, out, step_translate=False, step_inject=False)
    assert _pending(out).exists()

    guide.write_text("![img](a.png)\n", encoding="utf-8")
    _run(src, out, step_translate=False, step_inject=False)

    assert not _pending(out).exists()


def test_pending_write_leaves_no_temp_json(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_translate=False, step_inject=False)

    leftovers = [
        p.name for p in (out / "md" / "待翻譯").rglob("*") if p.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_dry_run_preview_files_are_not_counted_as_inject_errors(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out)
    translated = out / "md" / "LM翻譯後"
    (translated / "_md_dry_run_preview.json").write_text(
        json.dumps({"meta": {}, "items": []}), encoding="utf-8"
    )

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["error_files"] == 0
    assert result["step3"]["written_files"] == 1


@pytest.mark.parametrize("bad", ["../outside.md", "C:/outside.md", "/abs/outside.md"])
def test_unsafe_source_md_is_rejected(tmp_path, fake_lm, bad):
    src, out, _ = _layout(tmp_path)
    (tmp_path / "outside.md").write_text("Outside.\n", encoding="utf-8")
    _run(src, out)
    translated = next((out / "md" / "LM翻譯後").rglob("guide.md.json"))
    doc = json.loads(translated.read_text(encoding="utf-8"))
    doc["source_md"] = bad
    translated.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    before = _final(out).read_text(encoding="utf-8")

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["skipped_unsafe_path"] == 1
    assert _final(out).read_text(encoding="utf-8") == before
    assert not (tmp_path / "zh_tw").exists()


def test_switching_input_folder_warns_and_clears_old_pending(tmp_path, fake_lm, caplog):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_translate=False, step_inject=False)

    other_src = tmp_path / "in_b"
    doc = other_src / "pack" / "en_us" / "b.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Doc B.\n", encoding="utf-8")
    result = _run(other_src, out, step_translate=False, step_inject=False)

    assert not _pending(out).exists()
    assert result["step1"]["stale_removed"] == 1


def test_longer_translation_is_inserted_not_truncated_end_to_end(
    tmp_path, monkeypatch, fake_lm
):
    src, out, guide = _layout(tmp_path)
    guide.write_text("# T\n\nOnly one line.\n\nLast block.\n", encoding="utf-8")

    def two_lines(items, *, hooks, **_kwargs):
        for it in items:
            hooks.on_translated_item({**it, "text": "譯一\n譯二"})
        return SimpleNamespace(
            status="DONE", elapsed_sec=0.0, completed_calls=1, processed=len(items)
        )

    monkeypatch.setattr(md_lmtranslator, "run_translator_skeleton", two_lines)

    _run(src, out)

    lines = _final(out).read_text(encoding="utf-8").splitlines()
    # 三個區塊各被譯成兩行；後面的區塊不會因前面插入而錯位
    assert lines == ["譯一", "譯二", "", "譯一", "譯二", "", "譯一", "譯二"]


def test_translated_json_is_written_directly_under_translated_dir(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)

    translated = out / "md" / "LM翻譯後"
    assert (translated / "pack" / "en_us" / "guide.md.json").exists()
    # 不再多一層同名資料夾
    assert not (translated / "LM翻譯後").exists()
    assert (translated / "translation_map_md.json").exists()


def test_legacy_double_nested_translated_files_are_migrated(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)
    translated = out / "md" / "LM翻譯後"
    legacy = translated / "LM翻譯後" / "pack" / "en_us"
    legacy.mkdir(parents=True)
    old = json.loads(
        (translated / "pack" / "en_us" / "guide.md.json").read_text(encoding="utf-8")
    )
    for it in old["items"]:
        it["text"] = "舊版譯文"
    (legacy / "guide.md.json").write_text(
        json.dumps(old, ensure_ascii=False), encoding="utf-8"
    )
    (translated / "LM翻譯後" / "translation_map_md.json").write_text(
        "[]", encoding="utf-8"
    )
    foreign = translated / "LM翻譯後" / "note.txt"
    foreign.write_text("keep", encoding="utf-8")

    result = _run(src, out)

    assert not (legacy / "guide.md.json").exists()
    assert not (translated / "LM翻譯後" / "translation_map_md.json").exists()
    assert foreign.exists()  # 不是工具產生的檔案不動
    assert "舊版譯文" not in _final(out).read_text(encoding="utf-8")
    assert result["step3"]["written_files"] == 1


def test_failed_step2_keeps_legacy_nested_translated_files(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)
    legacy = out / "md" / "LM翻譯後" / "LM翻譯後" / "pack" / "en_us"
    legacy.mkdir(parents=True)
    old = legacy / "guide.md.json"
    old.write_text(
        json.dumps({"schema": "md_pending_blocks_v1", "items": []}), encoding="utf-8"
    )

    fake_lm["status"] = "FAILED"
    _run(src, out, step_inject=False)

    assert old.exists()  # 新譯文沒完整寫好前，舊檔是唯一可用的譯文


def test_dry_run_does_not_remove_stale_pending_files(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)
    other = tmp_path / "other"
    (other / "pack" / "en_us").mkdir(parents=True)
    (other / "pack" / "en_us" / "x.md").write_text("Hello world.\n", encoding="utf-8")

    _run(other, out, dry_run=True)

    assert _pending(out).exists()  # 預覽不能清掉原專案的待翻譯檔


def test_dry_run_does_not_delete_legacy_nested_translated_files(tmp_path, fake_lm):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)
    legacy = out / "md" / "LM翻譯後" / "LM翻譯後" / "pack" / "en_us"
    legacy.mkdir(parents=True)
    old = legacy / "guide.md.json"
    old.write_text(
        json.dumps({"schema": "md_pending_blocks_v1", "items": []}), encoding="utf-8"
    )

    _run(src, out, dry_run=True)

    assert old.exists()  # dry-run 只預覽，不動現有檔案


def test_resume_step3_works_with_legacy_nested_layout_before_step2_reruns(
    tmp_path, fake_lm
):
    src, out, _ = _layout(tmp_path)
    _run(src, out, step_inject=False)
    translated = out / "md" / "LM翻譯後"
    nested = translated / "LM翻譯後"
    nested.mkdir()
    for p in list(translated.rglob("*.json")):
        if (
            p.name.startswith("translation_map")
            or "LM翻譯後" in p.relative_to(translated).parts
        ):
            continue
        target = nested / p.relative_to(translated)
        target.parent.mkdir(parents=True, exist_ok=True)
        p.replace(target)

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["written_files"] == 1
