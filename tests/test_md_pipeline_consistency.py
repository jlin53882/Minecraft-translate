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


# ---- en_us／zh_tw 配對：有對應 zh_tw 時以 zh_tw 為底稿，只翻仍是英文原文的區塊 ----


def _pair_layout(tmp_path: Path, en: str, zh: str):
    src = tmp_path / "in"
    out = tmp_path / "out"
    for lang, text in (("en_us", en), ("zh_tw", zh)):
        path = src / "pack" / lang / "guide.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src, out


def _run_non_cjk(src: Path, out: Path, **kwargs):
    return run_md_pipeline(
        str(src), output_dir=str(out), lang_mode="non_cjk_only", **kwargs
    )


def _pair_final(out: Path) -> str:
    return (out / "md" / "完成" / "pack" / "zh_tw" / "guide.md").read_text(
        encoding="utf-8"
    )


def _pending_texts(out: Path, lang: str = "zh_tw") -> list[str]:
    path = out / "md" / "待翻譯" / "pack" / lang / "guide.md.json"
    if not path.exists():
        return []
    return [it["text"] for it in json.loads(path.read_text(encoding="utf-8"))["items"]]


def test_pair_keeps_translated_blocks_and_translates_only_english_ones(
    tmp_path, fake_lm
):
    src, out = _pair_layout(
        tmp_path,
        "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n",
        "第一段已翻譯。\n\n第二段已翻譯。\n\nThird paragraph.\n",
    )

    result = _run_non_cjk(src, out)

    assert _pending_texts(out) == ["Third paragraph."]
    assert _pending_texts(out, "en_us") == []  # 英文檔不再產生 pending
    assert result["step1"]["paired_docs"] == 1
    assert (
        _pair_final(out) == "第一段已翻譯。\n\n第二段已翻譯。\n\n譯:Third paragraph.\n"
    )
    assert result["step3"]["written_files"] == 1


def test_pair_classification_does_not_depend_on_cjk_characters(tmp_path, fake_lm):
    src, out = _pair_layout(
        tmp_path,
        "# Title\n\nUse the tool now.\n\nBrand Name\n\nOld text.\n",
        "# 標題\n\nUse the 工具 now.\n\nBrand Name\n\nOutdated english text.\n",
    )

    result = _run_non_cjk(src, out)

    # 與英文原文完全相同才送翻；混合中英文、已譯標題、不同的舊英文都保留
    assert _pending_texts(out) == ["Brand Name"]
    assert result["step1"]["ambiguous_kept"] == 1  # 舊英文只回報、不送翻
    text = _pair_final(out)
    assert "# 標題" in text and "Use the 工具 now." in text
    assert "Outdated english text." in text and "譯:Brand Name" in text


def test_pair_block_count_mismatch_is_reported_and_nothing_is_misaligned(
    tmp_path, fake_lm
):
    src, out = _pair_layout(
        tmp_path,
        "Alpha.\n\nBeta.\n\nGamma.\n",
        "甲已翻譯。\n\nGamma.\n",
    )

    result = _run_non_cjk(src, out)

    assert result["step1"]["structure_mismatch"] == ["pack/zh_tw/guide.md"]
    assert _pending_texts(out) == ["Gamma."]
    assert _pair_final(out) == "甲已翻譯。\n\n譯:Gamma.\n"


def test_pair_fully_translated_zh_leaves_no_pending(tmp_path, fake_lm):
    src, out = _pair_layout(tmp_path, "Alpha.\n\nBeta.\n", "甲。\n\n乙。\n")
    _run_non_cjk(src, out, step_translate=False, step_inject=False)
    stale = out / "md" / "待翻譯" / "pack" / "en_us" / "guide.md.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        json.dumps({"schema": "md_pending_blocks_v1", "items": []}), encoding="utf-8"
    )

    result = _run_non_cjk(src, out)

    assert not stale.exists()  # 舊的英文 pending 由過期清理移除
    assert result["step1"]["json_written"] == 0


def test_pair_run_twice_is_idempotent(tmp_path, fake_lm):
    src, out = _pair_layout(tmp_path, "One.\n\nTwo.\n", "一。\n\nTwo.\n")

    _run_non_cjk(src, out)
    first = (_pair_final(out), _pending_texts(out))
    _run_non_cjk(src, out)

    assert (_pair_final(out), _pending_texts(out)) == first


def test_pair_non_cjk_modes_other_than_non_cjk_only_still_prefer_zh_base(
    tmp_path, fake_lm
):
    src, out = _pair_layout(tmp_path, "One.\n\nTwo.\n", "一。\n\nTwo.\n")

    result = _run(src, out)  # lang_mode="all"

    assert _pending_texts(out, "en_us") == []
    assert result["step1"]["paired_docs"] == 1


def test_step3_does_not_write_the_same_output_twice_in_one_run(tmp_path, fake_lm):
    src, out = _pair_layout(tmp_path, "One.\n\nTwo.\n", "一。\n\nTwo.\n")
    _run_non_cjk(src, out, step_inject=False)
    translated = out / "md" / "LM翻譯後"
    copy = translated / "dup" / "pack" / "zh_tw" / "guide.md.json"
    copy.parent.mkdir(parents=True)
    copy.write_text(
        (translated / "pack" / "zh_tw" / "guide.md.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = _run_non_cjk(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["written_files"] == 1
    assert result["step3"]["skipped_output_conflict"] == 1
    # 勝者固定是正規位置那份，與檔案列舉順序無關
    assert _pair_final(out) == "一。\n\n譯:Two.\n"


def test_plan_sources_pairs_by_output_identity_regardless_of_order():
    from translation_tool.core.md_pipeline_pairing import plan_sources

    rels = [
        "a/_en_us/x.md",
        "a/_zh_tw/x.md",  # 底線一致：配對
        "b/EN_US/y.md",
        "b/zh_TW/y.md",  # 大小寫不同：配對
        "c/en_us/z.md",
        "c/_zh_tw/z.md",  # 底線不一致：輸出路徑本來就不同，不配對也不衝突
        "d/zh_tw/w.md",
        "d/ZH_TW/w.md",  # 兩個 zh 候選：衝突
        "e/zh_cn/v.md",  # zh_cn 不參與
    ]

    plan = plan_sources(rels)
    reversed_plan = plan_sources(list(reversed(rels)))

    assert plan.pair_en == {
        "a/_zh_tw/x.md": "a/_en_us/x.md",
        "b/zh_TW/y.md": "b/EN_US/y.md",
    }
    assert plan.skip["a/_en_us/x.md"] == "paired_en"
    assert "c/en_us/z.md" not in plan.skip and "c/_zh_tw/z.md" not in plan.skip
    assert plan.conflicts == [["d/ZH_TW/w.md", "d/zh_tw/w.md"]] or plan.conflicts == [
        ["d/zh_tw/w.md", "d/ZH_TW/w.md"]
    ]
    assert "e/zh_cn/v.md" not in plan.skip
    assert (plan.pair_en, plan.skip) == (reversed_plan.pair_en, reversed_plan.skip)


def test_resume_step3_with_legacy_en_and_zh_pending_keeps_zh_translations(
    tmp_path, fake_lm
):
    """舊版留下 en_us 與 zh_tw 兩份 pending／譯後檔時，只續跑 Step 3 也不能讓英文那份蓋掉繁中。"""
    src, out = _pair_layout(tmp_path, "One.\n\nTwo.\n", "一。\n\nTwo.\n")
    _run_non_cjk(src, out)
    pending_root = out / "md" / "待翻譯"
    translated = out / "md" / "LM翻譯後"
    # 重建舊版狀態：en_us 也有自己的 pending 與譯後檔（整份英文都被翻成「譯:」）
    for root in (pending_root, translated):
        en_doc = {
            "schema": "md_pending_blocks_v1",
            "source_md": "pack/en_us/guide.md",
            "items": [],
        }
        (root / "pack" / "en_us").mkdir(parents=True, exist_ok=True)
        (root / "pack" / "en_us" / "guide.md.json").write_text(
            json.dumps(en_doc), encoding="utf-8"
        )

    result = _run_non_cjk(src, out, step_extract=False, step_translate=False)

    assert _pair_final(out) == "一。\n\n譯:Two.\n"
    assert result["step3"]["skipped_superseded"] == 1


def test_pair_classification_ignores_a_bom_at_the_start_of_one_file(tmp_path, fake_lm):
    src, out = _pair_layout(tmp_path, "# Title\n\nBody.\n", "﻿# Title\n\n正文。\n")

    result = _run_non_cjk(src, out)

    assert _pending_texts(out) == ["﻿# Title"]  # 未譯標題仍會被送翻
    assert result["step1"]["ambiguous_kept"] == 0


def test_moving_zh_tw_out_of_input_lets_english_be_retranslated_from_scratch(
    tmp_path, fake_lm
):
    """文件建議的整份重翻做法：zh_tw 移出輸入資料夾後，譯後資料夾殘留的舊 zh 譯後檔不能擋住英文。"""
    src, out = _pair_layout(tmp_path, "One.\n\nTwo.\n", "一。\n\nTwo.\n")
    _run_non_cjk(src, out)
    zh_dir = src / "pack" / "zh_tw"
    (zh_dir / "guide.md").unlink()
    zh_dir.rmdir()

    result = _run_non_cjk(src, out)

    assert result["step3"]["written_files"] == 1
    assert _pair_final(out) == "譯:One.\n\n譯:Two.\n"


# ---- zh_cn 檔不產生 pending（Step 3 不會寫入 zh_cn，送翻只是白花 API）----


def _zh_cn_layout(tmp_path: Path):
    src = tmp_path / "in"
    out = tmp_path / "out"
    for rel, text in (
        ("pack/en_us/guide.md", "First.\n\nSecond.\n"),
        (
            "pack/zh_cn/guide.md",
            "第一段。\n\n第二段。\n\n多出來的段落。\n",
        ),  # 結構對不上
        ("only_cn/zh_cn/a.md", "English left.\n"),
    ):
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src, out


def test_zh_cn_files_never_produce_pending_or_translation_requests(
    tmp_path, fake_lm, monkeypatch
):
    src, out = _zh_cn_layout(tmp_path)
    sent: list[str] = []
    real = md_lmtranslator.run_translator_skeleton

    def spy(items, **kwargs):
        sent.extend(it["text"] for it in items)
        return real(items, **kwargs)

    monkeypatch.setattr(md_lmtranslator, "run_translator_skeleton", spy)

    result = _run_non_cjk(src, out)

    pending_root = out / "md" / "待翻譯"
    assert not list(pending_root.rglob("zh_cn/**/*.json"))
    assert not (pending_root / "only_cn").exists()
    assert result["step1"]["zh_cn_skipped"] == 2
    assert "English left." not in sent  # 只有 zh_cn 的檔不會送 API
    assert sorted(sent) == ["First.", "Second."]
    assert result["step3"]["written_files"] == 1  # 只有 en_us 寫出


def test_zh_cn_files_are_skipped_in_every_lang_mode(tmp_path, fake_lm):
    src, out = _zh_cn_layout(tmp_path)

    result = _run(src, out)  # lang_mode="all"

    assert result["step1"]["zh_cn_skipped"] == 2
    assert not list((out / "md" / "待翻譯").rglob("zh_cn/**/*.json"))


def test_stale_zh_cn_pending_from_older_runs_is_cleaned_up(tmp_path, fake_lm):
    src, out = _zh_cn_layout(tmp_path)
    stale = out / "md" / "待翻譯" / "pack" / "zh_cn" / "guide.md.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        json.dumps(
            {
                "schema": "md_pending_blocks_v1",
                "source_md": "pack/zh_cn/guide.md",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    result = _run_non_cjk(src, out)

    assert not stale.exists()  # 舊版留下的 zh_cn pending 由過期清理移除
    assert result["step1"]["stale_removed"] >= 1
