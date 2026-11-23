"""Markdown 管線：無語言資料夾的指南（``ae2guide/aae_intro/x.md``）當英文來源（issue #185）。

其他語言放在指南資料夾下以底線開頭的資料夾（``ae2guide/_es_es/``），繁中輸出放在同一層的 ``_zh_tw``。
用真正的 Step 1／Step 2／Step 3，只把 LM 呼叫換成固定回覆。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from translation_tool.core.md_pipeline_pairing import lang_output_key, plan_sources
from translation_tool.core.md_translation_assembly import run_md_pipeline
from translation_tool.plugins.md import md_lmtranslator
from translation_tool.plugins.md.md_extract_qa import (
    detect_lang_segment,
    iter_md_files,
    map_rel_lang_path,
    neutral_guide_index,
)
from translation_tool.plugins.md.md_inject_qa import map_lang_in_rel_path_allow_zh

EN = "# Intro\n\nStore items in cells.\n\nOpen the terminal.\n"


@pytest.fixture
def fake_lm(monkeypatch):
    state = {"sent": []}

    def fake_skeleton(items, *, hooks, **_kwargs):
        for it in items:
            state["sent"].append(it["text"])
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
    return state


def _layout(tmp_path: Path, files: dict[str, str]):
    src = tmp_path / "in"
    for rel, text in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src, tmp_path / "out"


def _run(src: Path, out: Path):
    return run_md_pipeline(str(src), output_dir=str(out), lang_mode="non_cjk_only")


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("ae2guide/aae_intro/x.md", 0),
        ("assets/advancedae/ae2guide/aae_intro/x.md", 2),
        ("guide/index.md", 0),
        ("assets/powah/guides/powah/book/generators/x.md", 4),  # GuideME 預設結構
        ("assets/lbr/guides/lbr/guide/_ru_ru/x.md", None),
        ("assets/powah/guides/powah/x.md", None),  # 還沒走到內容根目錄
        ("ae2guide/_es_es/aae_intro/x.md", None),  # 底線語言資料夾：其他語言
        ("ae2guide/aae_intro/_ja_jp/x.md", None),
        ("ae2guide/de_de/x.md", None),  # 第一層像語言碼：寧可略過
        ("ae2guide/to_do/x.md", None),  # 同上（略過比誤送翻安全）
        ("ae2guide/en_us/x.md", None),  # 已有明確語言資料夾：走原本的規則
        ("notes/x.md", None),  # 沒有指南資料夾
        # 更深層、不同形狀的其他語言都不當英文
        ("ae2guide/intro/de_de/y.md", None),
        ("ae2guide/intro/es-es/z.md", None),
        ("ae2guide/intro/ja/z.md", None),
        ("ae2guide/_fil_ph/w.md", None),
        ("ae2guide/_lzh/w.md", None),
        ("ae2guide/_nds_de/w.md", None),
        ("ae2guide/de_de.md", None),  # 檔名主幹就是語言碼
        ("ae2guide/a/x_de_de.md", None),  # 檔名以語言碼結尾
        ("docs/guide/de_de/x.md", None),
        # 太泛的資料夾名稱不算指南；一般章節名稱不被誤擋
        ("docs/CHANGELOG.md", None),
        ("some/manual/notes.md", None),
        ("ae2guide/items-blocks-machines/sky_dust.md", 0),
        ("ae2guide/items-blocks-machines/io_port.md", 0),
    ],
)
def test_neutral_guide_index(rel, expected):
    assert neutral_guide_index(rel.split("/")) == expected


def test_path_mapping_inserts_zh_tw_below_guide_dir():
    rel = "assets/advancedae/ae2guide/aae_intro/x.md"
    assert (
        map_rel_lang_path(rel, "en_us", "zh_tw")
        == "assets/advancedae/ae2guide/_zh_tw/aae_intro/x.md"
    )
    assert detect_lang_segment(rel.split("/")) == "en_us"
    assert map_lang_in_rel_path_allow_zh(rel) == (
        "assets/advancedae/ae2guide/_zh_tw/aae_intro/x.md",
        "SRC_EN",
    )
    # 其他語言與既有明確語言資料夾不受影響
    assert map_lang_in_rel_path_allow_zh("ae2guide/_es_es/a/x.md")[1] == "OTHER_LANG"
    assert map_lang_in_rel_path_allow_zh("ae2guide/en_us/x.md") == (
        "ae2guide/zh_tw/x.md",
        "SRC_EN",
    )


def test_guideme_default_layout_puts_zh_tw_beside_other_locales():
    rel = "assets/powah/guides/powah/book/generators/x.md"
    assert (
        map_rel_lang_path(rel, "en_us", "zh_tw")
        == "assets/powah/guides/powah/book/_zh_tw/generators/x.md"
    )


def test_iter_md_files_picks_neutral_english_but_not_other_locales(tmp_path):
    src, _ = _layout(
        tmp_path,
        {
            "ae2guide/aae_intro/a.md": EN,
            "ae2guide/_es_es/aae_intro/a.md": EN,
            "ae2guide/_zh_tw/aae_intro/a.md": "# 簡介\n",
            "notes/readme_extra.md": EN,
        },
    )
    found = sorted(p.relative_to(src).as_posix() for p in iter_md_files(src))
    assert found == [
        "ae2guide/_zh_tw/aae_intro/a.md",
        "ae2guide/aae_intro/a.md",
    ]


def test_guide_boundary_is_relative_to_input_root(tmp_path):
    """輸入資料夾本身或上層叫 ae2guide／docs 時，不能因為絕對路徑含這一段就算指南。"""
    inside = tmp_path / "ae2guide"
    (inside / "chapter").mkdir(parents=True)
    (inside / "chapter" / "a.md").write_text(EN, encoding="utf-8")
    assert list(iter_md_files(inside)) == []

    docs_parent = tmp_path / "docs" / "project"
    docs_parent.mkdir(parents=True)
    (docs_parent / "notes.md").write_text(EN, encoding="utf-8")
    assert list(iter_md_files(docs_parent)) == []


def test_pipeline_skips_non_guide_docs_and_nested_locales(tmp_path, fake_lm):
    """docs／manual 的一般文件、巢狀其他語言都不送翻，也不寫輸出。"""
    src, out = _layout(
        tmp_path,
        {
            "docs/pack/CHANGELOG.md": EN,
            "ae2guide/intro/de_de/y.md": "# Titel\n\nHallo Welt.\n",
            "ae2guide/intro/ok.md": EN,
        },
    )

    result = _run(src, out)

    assert result["step1"]["md_files_found"] == 1
    assert all("Hallo" not in sent for sent in fake_lm["sent"])
    finals = sorted(
        p.relative_to(out / "md" / "完成").as_posix()
        for p in (out / "md" / "完成").rglob("*.md")
    )
    assert finals == ["ae2guide/_zh_tw/intro/ok.md"]


def test_neutral_with_zh_cn_sibling_uses_zh_cn_base():
    en, cn = "ae2guide/a/x.md", "ae2guide/_zh_cn/a/x.md"
    plan = plan_sources([en, cn])
    assert plan.pair_cn == {cn: en}


def test_neutral_and_en_us_folder_conflict():
    plan = plan_sources(["ae2guide/a/x.md", "ae2guide/_en_us/a/x.md"])
    assert sorted(plan.skip.values()) == ["output_conflict", "output_conflict"]


def test_neutral_english_and_existing_zh_tw_share_one_output():
    en, zh = "ae2guide/aae_intro/a.md", "ae2guide/_zh_tw/aae_intro/a.md"
    assert lang_output_key(en) == lang_output_key(zh)
    plan = plan_sources([en, zh])
    assert plan.pair_en == {zh: en}
    assert plan.skip == {en: "paired_en"}


def test_end_to_end_neutral_guide_translates_into_zh_tw_dir(tmp_path, fake_lm):
    src, out = _layout(
        tmp_path,
        {
            "ae2guide/aae_intro/a.md": EN,
            "ae2guide/_es_es/aae_intro/a.md": "# Introducción\n\nGuarda objetos.\n",
        },
    )

    result = _run(src, out)

    done = out / "md" / "完成" / "ae2guide" / "_zh_tw" / "aae_intro" / "a.md"
    assert done.is_file()
    text = done.read_text(encoding="utf-8")
    assert "譯:Store items in cells." in text
    # 西班牙文來源不會被送翻，也不會產生輸出
    assert all("Guarda" not in sent for sent in fake_lm["sent"])
    assert result["step1"]["md_files_found"] == 1
    assert not (out / "md" / "完成" / "ae2guide" / "_es_es").exists()


def test_existing_zh_tw_sibling_keeps_translated_blocks(tmp_path, fake_lm):
    zh = "# 簡介\n\n把物品存放在元件中。\n\nOpen the terminal.\n"
    src, out = _layout(
        tmp_path,
        {
            "ae2guide/aae_intro/a.md": EN,
            "ae2guide/_zh_tw/aae_intro/a.md": zh,
        },
    )

    _run(src, out)

    done = out / "md" / "完成" / "ae2guide" / "_zh_tw" / "aae_intro" / "a.md"
    text = done.read_text(encoding="utf-8")
    assert "把物品存放在元件中。" in text
    assert fake_lm["sent"] == ["Open the terminal."]
