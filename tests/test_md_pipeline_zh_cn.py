"""Markdown 管線：沒有 zh_tw 時以 zh_cn 為底稿補譯（issue #185 Phase B）。

用真正的 Step 1／Step 2／Step 3，只把 LM 呼叫換成固定回覆（"譯:" + 原文），並記錄實際送出的文字。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.core.md_pipeline_pairing import plan_sources
from translation_tool.core.md_pipeline_zh_cn import (
    check_zh_cn_alignment,
    convert_markdown_lines,
)
from translation_tool.core.md_translation_assembly import run_md_pipeline
from translation_tool.plugins.md import md_lmtranslator

EN = "# Title\n\nFirst.\n\nSecond.\n\n```\ncode 内存\n```\n\n![img](a.png)\n"
CN = "# 标题\n\n内存和软件。\n\nSecond.\n\n```\ncode 内存\n```\n\n![图](a.png)\n"
CN_FULL = "# 标题\n\n内存和软件。\n\n第二段。\n\n```\ncode 内存\n```\n\n![图](a.png)\n"


@pytest.fixture
def fake_lm(monkeypatch):
    """LM 換成固定回覆；sent 記錄實際送出翻譯的文字。"""
    state = {"status": "DONE", "sent": []}

    def fake_skeleton(items, *, hooks, **_kwargs):
        for it in items:
            state["sent"].append(it["text"])
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


def _layout(tmp_path: Path, files: dict[str, str]):
    src = tmp_path / "in"
    out = tmp_path / "out"
    for rel, text in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src, out


def _run(src: Path, out: Path, **kwargs):
    return run_md_pipeline(
        str(src), output_dir=str(out), lang_mode="non_cjk_only", **kwargs
    )


def _final(out: Path) -> str:
    return (out / "md" / "完成" / "pack" / "zh_tw" / "guide.md").read_text(
        encoding="utf-8"
    )


def test_aligned_zh_cn_is_converted_and_only_english_leftovers_are_sent(
    tmp_path, fake_lm
):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})

    result = _run(src, out)

    assert fake_lm["sent"] == ["Second."]  # 只有與英文相同的殘留區塊送 LM
    text = _final(out)
    assert "# 標題" in text and "記憶體和軟體" in text  # 簡轉繁（含替換規則）
    assert "譯:Second." in text
    assert "code 内存" in text  # 程式碼區塊原樣保留
    assert "![圖](a.png)" in text
    assert "标题" not in text
    assert result["step1"]["zh_cn_based_docs"] == ["pack/zh_cn/guide.md"]
    assert result["step3"]["written_from_zh_cn"] == 1
    assert result["step3"]["written_files"] == 1
    # 轉繁的內容不進 Step 2 的譯後 JSON，也就不會污染去重與快取
    translated = (
        out / "md" / "LM翻譯後" / "pack" / "zh_cn" / "guide.md.json"
    ).read_text(encoding="utf-8")
    assert "記憶體" not in translated


def test_fully_translated_zh_cn_still_produces_output_without_calling_lm(
    tmp_path, fake_lm
):
    src, out = _layout(
        tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN_FULL}
    )

    result = _run(src, out)

    assert fake_lm["sent"] == []
    assert "第二段。" in _final(out) and "標題" in _final(out)
    assert result["step3"]["written_from_zh_cn"] == 1


@pytest.mark.parametrize(
    "bad_cn",
    [
        CN + "\n多出來的段落。\n",  # 多一段
        CN.replace("# 标题", "## 标题"),  # 標題層級不同
        CN.replace("code 内存", "code other"),  # 程式碼內容不同
        CN.replace("a.png", "b.png"),  # 圖片目標不同
        CN.replace("内存和软件。", "[内存](http://x.y)和软件。"),  # 多一個連結
    ],
)
def test_misaligned_zh_cn_falls_back_to_english_and_is_reported(
    tmp_path, fake_lm, bad_cn
):
    src, out = _layout(
        tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": bad_cn}
    )

    result = _run(src, out)

    assert result["step1"]["zh_cn_unaligned"][0]["zh_cn"] == "pack/zh_cn/guide.md"
    assert result["step1"]["zh_cn_based_docs"] == []
    assert "First." in fake_lm["sent"]  # 退回英文整份送翻
    text = _final(out)
    assert "譯:First." in text and "标题" not in text and "內存" not in text
    assert result["step3"]["written_from_zh_cn"] == 0


def test_zh_tw_takes_priority_over_zh_cn(tmp_path, fake_lm):
    src, out = _layout(
        tmp_path,
        {
            "pack/en_us/guide.md": "First.\n\nSecond.\n",
            "pack/zh_tw/guide.md": "第一段已翻。\n\nSecond.\n",
            "pack/zh_cn/guide.md": "简体第一段。\n\n简体第二段。\n",
        },
    )

    result = _run(src, out)

    assert fake_lm["sent"] == ["Second."]
    assert _final(out) == "第一段已翻。\n\n譯:Second.\n"
    assert result["step1"]["zh_cn_based_docs"] == []
    assert result["step3"]["written_from_zh_cn"] == 0


def test_zh_cn_without_english_is_never_sent(tmp_path, fake_lm):
    src, out = _layout(tmp_path, {"only/zh_cn/a.md": "English left.\n"})

    result = _run(src, out)

    assert fake_lm["sent"] == []
    assert result["step1"]["zh_cn_skipped"] == 1


def test_non_cjk_only_is_required_for_zh_cn_base(tmp_path, fake_lm):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})

    result = run_md_pipeline(str(src), output_dir=str(out), lang_mode="all")

    assert result["step1"]["zh_cn_based_docs"] == []
    assert result["step3"]["written_from_zh_cn"] == 0


def test_resume_step3_only_still_works_for_zh_cn_base(tmp_path, fake_lm):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})
    _run(src, out, step_inject=False)

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["written_from_zh_cn"] == 1
    assert "譯:Second." in _final(out)


def test_zh_cn_changed_after_extract_is_rejected_by_step3(tmp_path, fake_lm):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})
    _run(src, out, step_inject=False)
    (src / "pack" / "zh_cn" / "guide.md").write_text(CN + "\n", encoding="utf-8")

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["skipped_stale_source"] == 1
    assert result["step3"]["written_files"] == 0


def test_english_changed_after_extract_is_rejected_by_step3(tmp_path, fake_lm):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})
    _run(src, out, step_inject=False)
    (src / "pack" / "en_us" / "guide.md").write_text(EN + "\n", encoding="utf-8")

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["skipped_stale_source"] == 1


def test_switching_from_zh_cn_base_back_to_english_does_not_reuse_old_translation(
    tmp_path, fake_lm
):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})
    _run(src, out)
    (src / "pack" / "zh_cn" / "guide.md").unlink()

    result = _run(src, out)

    assert result["step3"]["written_from_zh_cn"] == 0
    assert "譯:First." in _final(out)  # 改由英文整份翻譯，不沿用舊的 zh_cn 底稿結果


def test_run_twice_is_idempotent(tmp_path, fake_lm):
    src, out = _layout(tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": CN})

    _run(src, out)
    first = _final(out)
    _run(src, out)

    assert _final(out) == first


# ---- 純函式 ----


def test_plan_sources_pair_cn_follows_output_identity():
    plan = plan_sources(
        [
            "a/_EN_US/x.md",
            "a/_zh_cn/x.md",  # 底線一致：候選底稿
            "b/en_us/y.md",
            "b/_zh_cn/y.md",  # 底線不一致：不同輸出，不配對
            "c/en_us/z.md",
            "c/zh_tw/z.md",
            "c/zh_cn/z.md",  # 有 zh_tw：zh_tw 優先，zh_cn 不是候選
            "d/en_us/w.md",
            "d/zh_cn/w.md",
            "d/ZH_CN/w.md",  # 兩份候選：衝突，不採用
        ]
    )

    assert plan.pair_cn == {"a/_zh_cn/x.md": "a/_EN_US/x.md"}
    assert plan.pair_en == {"c/zh_tw/z.md": "c/en_us/z.md"}
    assert any(
        set(group) == {"d/zh_cn/w.md", "d/ZH_CN/w.md"} for group in plan.conflicts
    )


def test_alignment_accepts_a_faithful_translation_and_rejects_structure_changes():
    en = '---\ntitle: A\ncategories:\n  - x\n---\n\n# Head\n\nSee [docs](http://a.b) and `code`.\n\n- one\n- two\n\n<Tag id="1"></Tag>\n'
    cn = '---\ntitle: 甲\ncategories:\n  - 乙\n---\n\n# 标题\n\n见 [文档](http://a.b) 和 `code`。\n\n- 一\n- 二\n\n<Tag id="1"></Tag>\n'

    assert check_zh_cn_alignment(en, cn) is None
    assert check_zh_cn_alignment(en, cn.replace("- 二\n", "")) is not None  # 清單少一項
    assert (
        check_zh_cn_alignment(en, cn.replace("`code`", "`码`")) is not None
    )  # 行內程式碼被改
    assert (
        check_zh_cn_alignment(en, cn.replace("<Tag", "<Other")) is not None
    )  # 元件名稱不同
    assert (
        check_zh_cn_alignment(en, cn.replace("title:", "name:")) is not None
    )  # frontmatter key 不同


def test_convert_markdown_lines_keeps_line_count_and_code_fences():
    lines = ["内存", "```py", "内存 = 1", "```", "~~~", "软件", "~~~", "软件"]

    converted = convert_markdown_lines(
        lines, lambda s: s.replace("内存", "記憶體").replace("软件", "軟體")
    )

    assert len(converted) == len(lines)
    assert converted == [
        "記憶體",
        "```py",
        "内存 = 1",
        "```",
        "~~~",
        "软件",
        "~~~",
        "軟體",
    ]


def test_alignment_ignores_bom_on_one_side_only():
    en = "---\ntitle: A\n---\n\n# Head\n\nText.\n"
    cn = "\ufeff---\ntitle: 甲\n---\n\n# 标题\n\n文字。\n"

    assert check_zh_cn_alignment(en, cn) is None
    assert check_zh_cn_alignment(en, cn.replace("---", "", 0).lstrip("\ufeff")) is None


def test_legacy_zh_cn_pending_without_base_does_not_block_english_output(
    tmp_path, fake_lm
):
    """舊版替 zh_cn 檔產生的 pending（沒有 base）不是底稿：英文的譯後檔照常寫出。"""
    bad_cn = CN + "\n多出來的段落。\n"  # 不對齊：Step 1 退回英文
    src, out = _layout(
        tmp_path, {"pack/en_us/guide.md": EN, "pack/zh_cn/guide.md": bad_cn}
    )
    _run(src, out, step_inject=False)
    pending = out / "md" / "待翻譯"
    translated = out / "md" / "LM翻譯後"
    legacy_pending = pending / "pack" / "zh_cn" / "guide.md.json"
    legacy_pending.parent.mkdir(parents=True, exist_ok=True)
    legacy_pending.write_text(
        (pending / "pack" / "en_us" / "guide.md.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    legacy_done = translated / "pack" / "zh_cn" / "guide.md.json"
    legacy_done.parent.mkdir(parents=True, exist_ok=True)
    legacy_done.write_text(
        (translated / "pack" / "en_us" / "guide.md.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = _run(src, out, step_extract=False, step_translate=False)

    assert result["step3"]["written_files"] >= 1
    assert "譯:First." in _final(out)
