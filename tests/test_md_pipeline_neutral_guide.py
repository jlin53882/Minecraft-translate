"""Markdown 管線：無語言資料夾的指南（``assets/<模組>/ae2guide/aae_intro/x.md``）當英文來源（issue #185）。

位置只看資源包結構（``assets|data/<模組>/<資料夾>/``，``guides`` 往下兩層），不靠資料夾名稱清單；
檔案是不是指南敘述文字靠內容判斷。其他語言放在內容根目錄下以底線開頭的資料夾
（``ae2guide/_es_es/``），繁中輸出放在同一層的 ``_zh_tw``。
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

P = "assets/m"  # 資源包路徑前綴：assets/<模組>
SENTENCE = "You can store items in the cells and open the terminal to view them."
EN = (
    f"# Intro\n\n{SENTENCE}\n\nRight click the controller to open the terminal today.\n"
)


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
        (f"{P}/ae2guide/aae_intro/x.md", 2),
        (f"{P}/mi_guidebook/index.md", 2),  # 內容根目錄是模組底下第一層，不看名稱
        (f"{P}/auroral/decorations/log.md", 2),
        ("data/m/guide/x.md", 2),
        (f"{P}/guides/powah/book/generators/x.md", 4),  # GuideME 預設結構
        (f"{P}/guides/lbr/guide/_ru_ru/x.md", None),
        (f"{P}/guides/powah/x.md", None),  # 還沒走到內容根目錄
        ("ae2guide/aae_intro/x.md", None),  # 不在 assets／data 之下
        ("docs/CHANGELOG.md", None),
        (f"{P}/x.md", None),  # 模組底下沒有資料夾
        (f"{P}/ae2guide/_es_es/aae_intro/x.md", None),  # 底線語言資料夾：其他語言
        (f"{P}/ae2guide/aae_intro/_ja_jp/x.md", None),
        (f"{P}/ae2guide/en_us/x.md", None),  # 已有明確語言資料夾：走原本的規則
        # 更深層、不同形狀的其他語言都不當英文
        (f"{P}/ae2guide/de_de/x.md", None),
        (f"{P}/ae2guide/intro/de_de/y.md", None),
        (f"{P}/ae2guide/intro/es-es/z.md", None),
        (f"{P}/ae2guide/intro/ja/z.md", None),
        (f"{P}/ae2guide/_fil_ph/w.md", None),
        (f"{P}/ae2guide/_lzh/w.md", None),
        (f"{P}/ae2guide/_nds_de/w.md", None),
        (f"{P}/ae2guide/de_de.md", None),  # 檔名主幹就是語言碼
        (f"{P}/ae2guide/a/x_de_de.md", None),  # 檔名以語言碼結尾
        # 已知取捨：兩字母章節名稱寧可略過；不帶底線的三字母語言碼目前辨識不出
        (f"{P}/ae2guide/ui/x.md", None),
        (f"{P}/ae2guide/me/x.md", None),
        (f"{P}/ae2guide/lzh/x.md", 2),
        (f"{P}/ae2guide/items-blocks-machines/sky_dust.md", 2),
        (f"{P}/ae2guide/items-blocks-machines/io_port.md", 2),
    ],
)
def test_neutral_guide_index(rel, expected):
    assert neutral_guide_index(rel.split("/")) == expected


def test_path_mapping_inserts_zh_tw_below_content_root():
    rel = f"{P}/ae2guide/aae_intro/x.md"
    assert (
        map_rel_lang_path(rel, "en_us", "zh_tw")
        == f"{P}/ae2guide/_zh_tw/aae_intro/x.md"
    )
    assert detect_lang_segment(rel.split("/")) == "en_us"
    assert map_lang_in_rel_path_allow_zh(rel) == (
        f"{P}/ae2guide/_zh_tw/aae_intro/x.md",
        "SRC_EN",
    )
    # 其他語言與既有明確語言資料夾不受影響
    assert (
        map_lang_in_rel_path_allow_zh(f"{P}/ae2guide/_es_es/a/x.md")[1] == "OTHER_LANG"
    )
    assert map_lang_in_rel_path_allow_zh("ae2guide/en_us/x.md") == (
        "ae2guide/zh_tw/x.md",
        "SRC_EN",
    )


def test_guideme_default_layout_puts_zh_tw_beside_other_locales():
    rel = f"{P}/guides/powah/book/generators/x.md"
    assert (
        map_rel_lang_path(rel, "en_us", "zh_tw")
        == f"{P}/guides/powah/book/_zh_tw/generators/x.md"
    )


def test_iter_md_files_uses_content_not_folder_names(tmp_path):
    """資料夾叫什麼都行，靠內容判斷；授權、更新紀錄、非敘述文字不收。"""
    src, _ = _layout(
        tmp_path,
        {
            f"{P}/ae2guide/aae_intro/a.md": EN,
            f"{P}/whatever_name/page.md": EN,  # 資料夾名稱不在任何清單
            f"{P}/ae2guide/_es_es/aae_intro/a.md": EN,
            f"{P}/ae2guide/_zh_tw/aae_intro/a.md": "# 簡介\n",
            f"{P}/docs/CHANGELOG.md": EN,  # 專案文件名稱
            f"{P}/data_tables/table.md": "| a | b |\n| - | - |\n| 1 | 2 |\n",
            "notes/readme_extra.md": EN,  # 不在 assets／data 之下
        },
    )
    found = sorted(p.relative_to(src).as_posix() for p in iter_md_files(src))
    assert found == [
        f"{P}/ae2guide/_zh_tw/aae_intro/a.md",
        f"{P}/ae2guide/aae_intro/a.md",
        f"{P}/whatever_name/page.md",
    ]


def test_boundary_is_relative_to_input_root(tmp_path):
    """輸入資料夾上層剛好有 assets／ae2guide 時，只看相對於輸入資料夾的路徑。"""
    inside = tmp_path / "assets" / "m" / "ae2guide"
    (inside / "chapter").mkdir(parents=True)
    (inside / "chapter" / "a.md").write_text(EN, encoding="utf-8")
    assert (
        list(iter_md_files(inside)) == []
    )  # 輸入資料夾本身就是內容根目錄：請選它的上層


def test_pipeline_skips_non_guide_docs_and_nested_locales(tmp_path, fake_lm):
    """一般文件、巢狀其他語言都不送翻，也不寫輸出。"""
    src, out = _layout(
        tmp_path,
        {
            f"{P}/docs/CHANGELOG.md": EN,
            f"{P}/ae2guide/intro/de_de/y.md": "# Titel\n\nHallo Welt, das ist ein längerer Satz.\n",
            f"{P}/ae2guide/intro/ok.md": EN,
        },
    )

    result = _run(src, out)

    assert result["step1"]["md_files_found"] == 1
    assert all("Hallo" not in sent for sent in fake_lm["sent"])
    finals = sorted(
        p.relative_to(out / "md" / "完成").as_posix()
        for p in (out / "md" / "完成").rglob("*.md")
    )
    assert finals == [f"{P}/ae2guide/_zh_tw/intro/ok.md"]


def test_neutral_with_zh_cn_sibling_uses_zh_cn_base():
    en, cn = f"{P}/ae2guide/a/x.md", f"{P}/ae2guide/_zh_cn/a/x.md"
    plan = plan_sources([en, cn])
    assert plan.pair_cn == {cn: en}


def test_neutral_and_en_us_folder_conflict():
    plan = plan_sources([f"{P}/ae2guide/a/x.md", f"{P}/ae2guide/_en_us/a/x.md"])
    assert sorted(plan.skip.values()) == ["output_conflict", "output_conflict"]


def test_neutral_english_and_existing_zh_tw_share_one_output():
    en = f"{P}/ae2guide/aae_intro/a.md"
    zh = f"{P}/ae2guide/_zh_tw/aae_intro/a.md"
    assert lang_output_key(en) == lang_output_key(zh)
    plan = plan_sources([en, zh])
    assert plan.pair_en == {zh: en}
    assert plan.skip == {en: "paired_en"}


def test_end_to_end_neutral_guide_translates_into_zh_tw_dir(tmp_path, fake_lm):
    src, out = _layout(
        tmp_path,
        {
            f"{P}/ae2guide/aae_intro/a.md": EN,
            f"{P}/ae2guide/_es_es/aae_intro/a.md": "# Introducción\n\nGuarda objetos.\n",
        },
    )

    result = _run(src, out)

    root = out / "md" / "完成" / "assets" / "m" / "ae2guide"
    done = root / "_zh_tw" / "aae_intro" / "a.md"
    assert done.is_file()
    assert f"譯:{SENTENCE}" in done.read_text(encoding="utf-8")
    # 西班牙文來源不會被送翻，也不會產生輸出
    assert all("Guarda" not in sent for sent in fake_lm["sent"])
    assert result["step1"]["md_files_found"] == 1
    assert not (root / "_es_es").exists()


def test_existing_zh_tw_sibling_keeps_translated_blocks(tmp_path, fake_lm):
    zh = f"# 簡介\n\n把物品存放在元件中。\n\n{SENTENCE.replace('store', 'hold')}\n"
    en = f"# Intro\n\n{SENTENCE}\n\n{SENTENCE.replace('store', 'hold')}\n"
    src, out = _layout(
        tmp_path,
        {
            f"{P}/ae2guide/aae_intro/a.md": en,
            f"{P}/ae2guide/_zh_tw/aae_intro/a.md": zh,
        },
    )

    _run(src, out)

    done = (
        out
        / "md"
        / "完成"
        / "assets"
        / "m"
        / "ae2guide"
        / "_zh_tw"
        / "aae_intro"
        / "a.md"
    )
    assert "把物品存放在元件中。" in done.read_text(encoding="utf-8")
    assert fake_lm["sent"] == [SENTENCE.replace("store", "hold")]
