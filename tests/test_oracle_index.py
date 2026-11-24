"""Oracle Index 書籍路徑契約：版面判斷、成員身分與譯文路徑。

測試用的路徑都取自實際的 Oritech（新版）與 mffs（舊版）jar；譯文路徑規則另以遊戲的
字串替換（``books/<書>`` → ``books/<書>/translated/<語系>``）逐字重做一次，確認兩者一致。
"""

from __future__ import annotations

import json

import pytest

from translation_tool.core.oracle_index import (
    Layout,
    OracleMember,
    Section,
    layout_from_wiki_meta,
    parse_member,
    split_book_path,
    translated_path,
)

_ORITECH = "assets/oracle_index/books/oritech"
_MFFS = "assets/oracle_index/books/mffs"


def _game_translated_path(path: str, book: str, translated_dir: str) -> str:
    """遊戲（``OracleClient.getTranslatedPath``）的做法：整串字串替換。"""
    return path.replace(f"books/{book}", f"books/{book}{translated_dir}")


# --- 版面判斷 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"id": "oritech", "schema": "1"}', Layout.V1),
        (b'{"id": "oritech", "schema": "1"}', Layout.V1),
        ('{"id": "mffs", "modid": "mffs"}', Layout.LEGACY),  # 沒有 schema
        ('{"schema": "2"}', Layout.LEGACY),
        ('{"schema": 1}', Layout.V1),  # Gson 會把數字讀成 "1"
        ('{"schema": true}', Layout.LEGACY),
        ('{"schema": null}', Layout.LEGACY),
        ("not json", Layout.LEGACY),
        ("[1, 2]", Layout.LEGACY),
        ("", Layout.LEGACY),
        (None, Layout.LEGACY),  # 沒有 sinytra-wiki.json
        (b"\xff\xfe", Layout.LEGACY),
    ],
)
def test_layout_follows_wiki_meta_schema(raw, expected):
    assert layout_from_wiki_meta(raw) == expected


def test_layout_of_real_wiki_files():
    assert (
        layout_from_wiki_meta(
            json.dumps({"id": "oritech", "schema": "1", "owners": ["rearth"]})
        )
        == Layout.V1
    )
    assert (
        layout_from_wiki_meta(
            json.dumps(
                {"id": "mffs", "modid": "mffs", "platforms": {"modrinth": "mffs"}}
            )
        )
        == Layout.LEGACY
    )


# --- 成員身分：新版（Oritech）-----------------------------------------------


@pytest.mark.parametrize(
    ("rel", "section", "is_page", "is_meta", "translatable"),
    [
        (
            "content/components/clay_catalyst_beads.mdx",
            Section.CONTENT,
            True,
            False,
            True,
        ),
        ("content/_meta.json", Section.CONTENT, False, True, True),
        ("content/components/_meta.json", Section.CONTENT, False, True, True),
        ("docs/getting_started/_meta.json", Section.DOCS, False, True, True),
        ("docs/reactor/overview.mdx", Section.DOCS, True, False, True),
        (
            "assets/oritech/item/clay_catalyst_beads.png",
            Section.ASSETS,
            False,
            False,
            False,
        ),
        ("data/oritech/properties/item.json", Section.DATA, False, False, False),
        ("sinytra-wiki.json", Section.MARKER, False, False, False),
        ("README.md", Section.OTHER, False, False, False),
    ],
)
def test_v1_member_roles(rel, section, is_page, is_meta, translatable):
    member = parse_member(f"{_ORITECH}/{rel}", Layout.V1)
    assert member is not None
    assert (member.book, member.layout) == ("oritech", Layout.V1)
    assert member.section == section
    assert (member.is_page, member.is_meta) == (is_page, is_meta)
    assert member.is_translatable_source is translatable
    assert member.locale is None
    assert member.source_rel == rel


def test_v1_translated_member_points_back_to_its_source():
    original = parse_member(f"{_ORITECH}/content/components/x.mdx", Layout.V1)
    zh_cn = parse_member(
        f"{_ORITECH}/translated/zh_cn/content/components/x.mdx", Layout.V1
    )
    fr_fr = parse_member(
        f"{_ORITECH}/translated/fr_fr/content/components/x.mdx", Layout.V1
    )
    assert original is not None
    assert zh_cn is not None
    assert fr_fr is not None
    # 原文與各語系譯文必須得到同一把鍵，掃描階段才配得成對
    assert zh_cn.neutral_key == fr_fr.neutral_key == original.neutral_key
    assert original.neutral_key == "oritech/content/components/x.mdx"
    assert (zh_cn.locale, fr_fr.locale) == ("zh_cn", "fr_fr")
    assert zh_cn.section == fr_fr.section == Section.CONTENT
    assert zh_cn.is_translated and not original.is_translated
    assert not zh_cn.is_translatable_source  # 譯文不是要翻的原文


def test_v1_translated_meta_and_docs():
    meta = parse_member(f"{_ORITECH}/translated/zh_cn/content/_meta.json", Layout.V1)
    docs = parse_member(f"{_ORITECH}/translated/ja_jp/docs/a/b.mdx", Layout.V1)
    assert meta is not None and docs is not None
    assert (meta.section, meta.is_meta, meta.source_rel) == (
        Section.CONTENT,
        True,
        "content/_meta.json",
    )
    assert (docs.section, docs.is_page, docs.source_rel) == (
        Section.DOCS,
        True,
        "docs/a/b.mdx",
    )


@pytest.mark.parametrize(
    "rel",
    [
        "translated/readme.txt",  # translated 底下沒有語系資料夾
        "translated/notalocale/content/x.mdx",  # 資料夾名稱不是語系碼
        "translated/zh_cn",  # 只有語系資料夾本身（沒有檔案）
    ],
)
def test_v1_translated_dir_without_locale_is_not_a_translation(rel):
    member = parse_member(f"{_ORITECH}/{rel}", Layout.V1)
    assert member is not None
    assert member.locale is None
    assert member.section == Section.OTHER
    assert not member.is_translatable_source


# --- 成員身分：舊版（mffs）---------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "section", "translatable"),
    [
        (".content/tools/_meta.json", Section.CONTENT, True),
        (".content/tools/scale_module.mdx", Section.CONTENT, True),
        ("introduction.mdx", Section.DOCS, True),  # 書根目錄的頁面是文件模式
        ("_meta.json", Section.DOCS, True),
        (".data/mffs/recipe/id_card.json", Section.DATA, False),
        (".data/properties.json", Section.DATA, False),
        (".assets/mffs/fortron.png", Section.ASSETS, False),
        ("sinytra-wiki.json", Section.MARKER, False),
    ],
)
def test_legacy_member_roles(rel, section, translatable):
    member = parse_member(f"{_MFFS}/{rel}", Layout.LEGACY)
    assert member is not None
    assert (member.book, member.layout) == ("mffs", Layout.LEGACY)
    assert member.section == section
    assert member.is_translatable_source is translatable
    assert member.source_rel == rel


def test_legacy_translated_members():
    root_page = parse_member(
        f"{_MFFS}/.translated/zh_tw/introduction.mdx", Layout.LEGACY
    )
    content = parse_member(
        f"{_MFFS}/.translated/zh_tw/.content/tools/_meta.json", Layout.LEGACY
    )
    assert root_page is not None and content is not None
    assert (root_page.section, root_page.locale, root_page.source_rel) == (
        Section.DOCS,
        "zh_tw",
        "introduction.mdx",
    )
    assert (content.section, content.is_meta, content.source_rel) == (
        Section.CONTENT,
        True,
        ".content/tools/_meta.json",
    )
    original = parse_member(f"{_MFFS}/introduction.mdx", Layout.LEGACY)
    assert original is not None
    assert root_page.neutral_key == original.neutral_key == "mffs/introduction.mdx"


def test_layout_decides_which_directory_names_count():
    # 同一個路徑，版面不同結論就不同：新版沒有 .content，舊版沒有 content
    assert (
        parse_member(f"{_MFFS}/.content/x.mdx", Layout.V1).section == Section.OTHER  # type: ignore[union-attr]
    )
    assert (
        parse_member(f"{_ORITECH}/content/x.mdx", Layout.LEGACY).section  # type: ignore[union-attr]
        == Section.DOCS
    )


# --- 路徑形狀 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "assets/oritech/patchouli_books/book/en_us/entries/a.json",
        "assets/oracle_index/lang/zh_tw.json",  # 模組自己的介面語系，不是書
        "assets/oracle_index/books/oritech",  # 只有書根目錄
        "assets/oracle_index/books/",
        "data/oracle_index/books/oritech/content/x.mdx",  # 必須在 assets 底下
        "",
    ],
)
def test_non_book_paths_are_ignored(path):
    assert parse_member(path, Layout.V1) is None


def test_member_prefix_and_backslashes_are_tolerated():
    # 翻譯包或解壓資料夾會帶外層資料夾；Windows 路徑用反斜線
    prefixed = parse_member(
        "pack/1.21/assets/oracle_index/books/oritech/content/x.mdx", Layout.V1
    )
    windows = parse_member(
        "assets\\oracle_index\\books\\oritech\\content\\x.mdx", Layout.V1
    )
    assert prefixed is not None and windows is not None
    assert prefixed.source_rel == windows.source_rel == "content/x.mdx"
    assert split_book_path(
        "pack/1.21/assets/oracle_index/books/oritech/content/x.mdx"
    ) == (
        "pack/1.21/assets/oracle_index/books",
        "oritech",
        ("content", "x.mdx"),
    )


def test_book_names_may_contain_hyphens():
    member = parse_member(
        "assets/oracle_index/books/oracle-index/docs/_meta.json", Layout.V1
    )
    assert member is not None
    assert member.book == "oracle-index"
    assert member.section == Section.DOCS


# --- 譯文路徑 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "layout", "book", "translated_dir"),
    [
        (
            f"{_ORITECH}/content/components/clay_catalyst_beads.mdx",
            Layout.V1,
            "oritech",
            "/translated/zh_tw",
        ),
        (f"{_ORITECH}/content/_meta.json", Layout.V1, "oritech", "/translated/zh_tw"),
        (
            f"{_ORITECH}/docs/getting_started/_meta.json",
            Layout.V1,
            "oritech",
            "/translated/zh_tw",
        ),
        (f"{_MFFS}/introduction.mdx", Layout.LEGACY, "mffs", "/.translated/zh_tw"),
        (f"{_MFFS}/_meta.json", Layout.LEGACY, "mffs", "/.translated/zh_tw"),
        (
            f"{_MFFS}/.content/tools/_meta.json",
            Layout.LEGACY,
            "mffs",
            "/.translated/zh_tw",
        ),
        (
            f"{_MFFS}/.content/tools/scale_module.mdx",
            Layout.LEGACY,
            "mffs",
            "/.translated/zh_tw",
        ),
    ],
)
def test_translated_path_matches_the_games_string_replacement(
    path, layout, book, translated_dir
):
    assert translated_path(path, layout) == _game_translated_path(
        path, book, translated_dir
    )


def test_translated_path_round_trips_to_the_same_neutral_key():
    for path, layout in (
        (f"{_ORITECH}/content/components/x.mdx", Layout.V1),
        (f"{_MFFS}/introduction.mdx", Layout.LEGACY),
        (f"{_MFFS}/.content/a/_meta.json", Layout.LEGACY),
    ):
        original = parse_member(path, layout)
        translated = parse_member(translated_path(path, layout), layout)
        assert original is not None and translated is not None
        assert translated.locale == "zh_tw"
        assert translated.neutral_key == original.neutral_key
        assert translated.section == original.section


def test_translated_path_keeps_member_prefix_and_accepts_other_locale():
    result = translated_path(
        "pack/assets/oracle_index/books/oritech/content/x.mdx", Layout.V1, "ZH_CN"
    )
    assert (
        result
        == "pack/assets/oracle_index/books/oritech/translated/zh_cn/content/x.mdx"
    )


@pytest.mark.parametrize(
    ("path", "layout"),
    [
        (f"{_ORITECH}/translated/zh_cn/content/x.mdx", Layout.V1),  # 已是譯文
        (f"{_ORITECH}/assets/oritech/item/a.png", Layout.V1),  # 素材沒有譯文
        (f"{_ORITECH}/data/oritech/properties/item.json", Layout.V1),
        (f"{_ORITECH}/sinytra-wiki.json", Layout.V1),
        (f"{_MFFS}/.data/mffs/recipe/id_card.json", Layout.LEGACY),
        ("assets/oritech/lang/en_us.json", Layout.V1),  # 不是 Oracle 書
    ],
)
def test_translated_path_rejects_non_source_files(path, layout):
    with pytest.raises(ValueError):
        translated_path(path, layout)


def test_translated_path_rejects_bad_locale():
    with pytest.raises(ValueError):
        translated_path(f"{_ORITECH}/content/x.mdx", Layout.V1, "zh-tw!")


def test_member_is_immutable():
    member = parse_member(f"{_ORITECH}/content/x.mdx", Layout.V1)
    assert isinstance(member, OracleMember)
    with pytest.raises(AttributeError):
        member.book = "other"  # type: ignore[misc]


def test_split_translated_pairs_translation_with_source() -> None:
    from translation_tool.core.oracle_index import split_translated

    assert split_translated(
        ["books", "b", "translated", "zh_tw", "content", "a.mdx"]
    ) == (
        "zh_tw",
        "books/b/content/a.mdx",
    )
    assert split_translated(["b", ".translated", "zh_TW", "a.mdx"]) == (
        "zh_tw",
        "b/a.mdx",
    )
    assert split_translated(["books", "b", "content", "a.mdx"]) is None
    assert (
        split_translated(["translated", "zh_tw", "a.mdx"]) is None
    )  # 前面沒有書的資料夾
    assert split_translated(["b", "translated", "notalocale", "a.mdx"]) is None


def test_infer_layout_and_translatable_page() -> None:
    from translation_tool.core.oracle_index import infer_layout, is_translatable_page

    base = "assets/oracle_index/books/b"
    assert infer_layout(f"{base}/content/a.mdx") is Layout.V1
    assert infer_layout(f"{base}/.content/a.mdx") is Layout.LEGACY
    assert infer_layout(f"{base}/a.mdx") is Layout.LEGACY
    assert is_translatable_page(f"{base}/content/a.mdx")
    assert is_translatable_page(f"{base}/translated/zh_tw/docs/a.mdx")
    assert is_translatable_page(f"{base}/a.mdx")
    assert not is_translatable_page(f"{base}/assets/a.mdx")
    assert not is_translatable_page(f"{base}/content/_meta.json")
