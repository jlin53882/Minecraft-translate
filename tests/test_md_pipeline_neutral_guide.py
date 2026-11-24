"""Markdown 管線：無語言資料夾的指南（``assets/<模組>/ae2guide/aae_intro/x.md``）當英文來源（issue #185）。

位置只看資源包結構（``assets|data/<模組>/<資料夾>/``，``guides`` 往下兩層），不靠資料夾名稱清單；
檔案是不是指南敘述文字靠內容判斷。其他語言放在內容根目錄下以底線開頭的資料夾
（``ae2guide/_es_es/``），繁中輸出放在同一層的 ``_zh_tw``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def _layout(tmp_path: Path, files: dict[str, str]):
    src = tmp_path / "in"
    for rel, text in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src, tmp_path / "out"


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
        # 內容根目錄本身是語言碼、或是原版規格的 lang 資料夾
        (f"{P}/es_es/x.md", None),
        (f"{P}/de_de/book/y.md", None),
        (f"{P}/de/x.md", None),
        (f"{P}/lang/a.md", None),
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


GERMAN = "# Einführung\n\nDieses Modul erlaubt es dir, Gegenstände in Zellen zu speichern und das Terminal zu öffnen.\n"
SPANISH = "# Introducción\n\nEste módulo te permite guardar objetos en celdas y abrir la terminal para verlos todos.\n"
FRENCH = "# Introduction\n\nCe module vous permet de stocker des objets dans des cellules et d'ouvrir le terminal.\n"
RUSSIAN = "# Введение\n\nЭтот мод позволяет хранить предметы в ячейках и открывать терминал для просмотра.\n"
JAPANESE = (
    "---\nnavigation:\n  title: ME能動形成プレーン\n---\n\n# ME能動形成プレーン\n"
)
SHORT_ENGLISH = "---\nnavigation:\n  title: Storage Cells\n---\n\n# Storage Cells\n\nCells hold items.\n"
SHORT_FRENCH = (
    "---\nnavigation:\n  title: Cellules\n---\n\n# Cellules de stockage\n\nÉléments.\n"
)
SHORT_GREEK = "# Resistance\n\nThe value is shown in Ω and Δ units, so check it.\n"
ITEM_LIST = "# Cables\n\n- White Cable\n- Orange Cable\n- Magenta Cable\n- Light Blue Cable\n- Yellow Cable\n- Lime Cable\n- Pink Cable\n- Gray Cable\n"
ENGLISH_WITH_SYMBOL = (
    f"# Profiler\n\n{SENTENCE} A tick takes about 50 μs on average, and the profiler "
    "shows how long each machine in the network needs to finish its work. Values "
    "above 100 μs are shown in red, while values below 20 μs are shown in green so "
    "that you can find slow machines quickly. The values update every second and "
    "they are averaged over the last minute, which makes spikes easier to see. "
    "Hold shift to see the full list of machines sorted from the slowest to the "
    "fastest, and right click a row to jump to that machine in the world.\n"
)


def test_iter_md_files_rejects_non_english_content_without_locale_marker(tmp_path):
    """沒有語言標記的路徑下，西／德／法／俄／日文都不能當英文原文送翻。"""
    src, _ = _layout(
        tmp_path,
        {
            f"{P}/guide/german.md": GERMAN,
            f"{P}/guide/spanish.md": SPANISH,
            f"{P}/guide/french.md": FRENCH,
            f"{P}/guide/russian.md": RUSSIAN,
            f"{P}/guide/japanese.md": JAPANESE,
            f"{P}/guide/english.md": EN,
            f"{P}/guide/symbol.md": ENGLISH_WITH_SYMBOL,  # 零星的 μ 不是其他語言
            f"{P}/guide/short_en.md": SHORT_ENGLISH,
            f"{P}/guide/short_fr.md": SHORT_FRENCH,  # 英文單字太少，靠重音字母判斷
            f"{P}/guide/short_greek.md": SHORT_GREEK,  # 希臘字母單位不算非拉丁文
            f"{P}/guide/item_list.md": ITEM_LIST,  # 已知取捨：沒有停用字的物品清單頁會被略過
        },
    )
    found = sorted(p.name for p in iter_md_files(src))
    assert found == ["english.md", "short_en.md", "short_greek.md", "symbol.md"]


def test_boundary_is_relative_to_input_root(tmp_path):
    """輸入資料夾上層剛好有 assets／ae2guide 時，只看相對於輸入資料夾的路徑。"""
    inside = tmp_path / "assets" / "m" / "ae2guide"
    (inside / "chapter").mkdir(parents=True)
    (inside / "chapter" / "a.md").write_text(EN, encoding="utf-8")
    assert (
        list(iter_md_files(inside)) == []
    )  # 輸入資料夾本身就是內容根目錄：請選它的上層
