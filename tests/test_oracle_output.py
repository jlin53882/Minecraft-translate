"""Oracle Index 書籍的輸出：頁面與 _meta.json 找得到、能組回原檔，譯文放在 translated/<語系>/。"""

from __future__ import annotations

import json
from pathlib import Path

from translation_tool.core.guide_blocks import (
    apply_meta_translations,
    extract_meta_entries,
)
from translation_tool.core.guide_translation import (
    find_guide_files,
    guide_locale_rel,
    guide_output_rel,
    load_guide,
    render_guide,
)

BOOK = "assets/oracle_index/books/b"
META = {
    "arcane": "Arcane",
    "empty": "",
    "nested": {"name": "Getting Started", "icon": None},
    "scripts.sh": "Generate Meta",
}


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_translated_path_for_v1_and_legacy(tmp_path: Path) -> None:
    assert guide_output_rel(Path(f"{BOOK}/content/a/x.mdx"), tmp_path) == Path(
        f"{BOOK}/translated/zh_tw/content/a/x.mdx"
    )
    assert guide_output_rel(Path(f"{BOOK}/.content/x.mdx"), tmp_path) == Path(
        f"{BOOK}/.translated/zh_tw/.content/x.mdx"
    )
    assert guide_output_rel(Path(f"{BOOK}/docs/_meta.json"), tmp_path) == Path(
        f"{BOOK}/translated/zh_tw/docs/_meta.json"
    )


def test_source_locale_path_is_the_original(tmp_path: Path) -> None:
    rel = Path(f"{BOOK}/content/x.mdx")
    assert guide_locale_rel(rel, tmp_path, "_en_us") == rel


def test_extracted_wrapper_is_kept(tmp_path: Path) -> None:
    rel = Path(f"mod_extracted/{BOOK}/content/x.mdx")
    assert guide_output_rel(rel, tmp_path) == Path(
        f"mod_extracted/{BOOK}/translated/zh_tw/content/x.mdx"
    )


def test_assets_and_data_are_not_oracle_translations(tmp_path: Path) -> None:
    rel = Path(f"{BOOK}/data/x/info.mdx")
    assert guide_output_rel(rel, tmp_path) != Path(
        f"{BOOK}/translated/zh_tw/data/x/info.mdx"
    )


def test_find_guide_files_takes_pages_and_meta_but_not_translations(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        f"{BOOK}/content/x.mdx",
        "# Hi\n\nA long enough paragraph for the player.\n",
    )
    _write(tmp_path, f"{BOOK}/content/_meta.json", json.dumps(META))
    _write(tmp_path, f"{BOOK}/translated/zh_cn/content/_meta.json", json.dumps(META))
    _write(tmp_path, f"{BOOK}/data/_meta.json", json.dumps(META))
    _write(tmp_path, "assets/other/lang_stuff/_meta.json", json.dumps(META))
    found = {p.relative_to(tmp_path).as_posix() for p in find_guide_files(tmp_path)}
    assert found == {f"{BOOK}/content/x.mdx", f"{BOOK}/content/_meta.json"}


def test_meta_roundtrip_keeps_every_key_and_untouched_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path, f"{BOOK}/content/_meta.json", json.dumps(META, indent=2) + "\n"
    )
    guide = next(p for p in find_guide_files(tmp_path) if p.name == "_meta.json")
    doc, items = load_guide(guide)
    assert [i["source_text"] for i in items] == [
        "Arcane",
        "Getting Started",
        "Generate Meta",
    ]
    assert items[0]["db_ident"] == (
        "guide",
        "oracle_index",
        "books/b/content/_meta.json#0",
    )
    doc["blocks"] = ["奧術", "開始使用", "產生中繼資料"]
    out = json.loads(render_guide(doc))
    assert list(out) == list(META)  # 全部的鍵與順序
    assert out["empty"] == ""  # 空值維持空值
    assert out["nested"] == {"name": "開始使用", "icon": None}
    assert out["arcane"] == "奧術"
    assert render_guide(doc).endswith("\n")
    assert path.read_text(encoding="utf-8").startswith("{")  # 來源沒被改


def test_unchanged_meta_is_rendered_byte_for_byte(tmp_path: Path) -> None:
    text = '{"a":   "A"}'
    _write(tmp_path, f"{BOOK}/content/_meta.json", text)
    guide = find_guide_files(tmp_path)[0]
    doc, _ = load_guide(guide)
    assert render_guide(doc) == text


def test_apply_meta_translations_refuses_a_wrong_count() -> None:
    text = json.dumps({"a": "A", "b": "B"})
    assert apply_meta_translations(text, ["only one"]) == text
    assert extract_meta_entries(text) == [("a", "A"), ("b", "B")]


def test_page_title_and_body_are_rendered_back(tmp_path: Path) -> None:
    _write(
        tmp_path,
        f"{BOOK}/content/x.mdx",
        "---\ntitle: Overview\nid: x\n---\n\nThe machine stores energy for later use.\n",
    )
    guide = find_guide_files(tmp_path)[0]
    doc, items = load_guide(guide)
    assert [i["source_text"] for i in items] == [
        "The machine stores energy for later use.",
        "Overview",
    ]
    doc["blocks"] = ["這台機器會儲存能量供之後使用。", "概覽"]
    rendered = render_guide(doc)
    assert "title: 概覽" in rendered and "id: x" in rendered
