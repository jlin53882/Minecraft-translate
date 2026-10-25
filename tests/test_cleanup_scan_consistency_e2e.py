"""掃描與清理共用同一套規則的端到端驗證（合成 jar + 暫存資料庫）。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.scanner import ScanOptions, scan_jar
from translation_tool.translation_db.schema import SRC_MANUAL

VERSION = "1.21.1"
DIRS = ("patchouli_books", "guide")
LICENSE = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy."
)

LANG = {
    "item.m.sword": "Iron Sword",
    "block.m.todo": "TODO",
    "gui.m.arrows": "← →",
    "gui.m.angle": "180°",
    "gui.m.color": "#FFFFFF",
    "language.name": "English",
    "language.code": "en_us",
    "language.region": "United States",
    "gui.m.nested": {"text": "Nested hello world"},
    "gui.m.list": ["First line of text", "Second line of text"],
}
BOOK_ENTRY = {
    "name": "Getting Started",
    "pages": [
        {"type": "patchouli:text", "text": "Welcome to the mod, adventurer."},
        {"type": "patchouli:spotlight", "anchor": "QuickStart", "name": "QuickStart"},
    ],
}
GUIDE_MD = f"# Title Here\n\nOpen the terminal with a right click.\n\n{LICENSE}\n"


def _jar(path: Path, lang_en=None, zh=None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("assets/m/lang/en_us.json", json.dumps(lang_en or LANG))
        if zh is not None:
            zf.writestr("assets/m/lang/zh_tw.json", json.dumps(zh, ensure_ascii=False))
        zf.writestr(
            "assets/m/patchouli_books/b/en_us/entries/start.json",
            json.dumps(BOOK_ENTRY),
        )
        zf.writestr("assets/m/guide/a.md", GUIDE_MD)
    return path


def _scan(jar: Path, **kw) -> list[ScanItem]:
    result = scan_jar(jar, ScanOptions(version=VERSION, **kw), dir_names=DIRS)
    assert not result.error
    return result.items


def _keys(items) -> set[str]:
    return {i.key for i in items}


def _report_keys(db: TranslationDB, tmp: Path, name: str, protected=False) -> set:
    path = tmp / name
    (db.export_protected_report if protected else db.export_cleanup_report)(
        VERSION, path
    )
    return {i["key"] for i in json.loads(path.read_text(encoding="utf-8"))["items"]}


def _clean(db: TranslationDB, tmp: Path, name: str = "c.json") -> None:
    path = tmp / name
    db.export_cleanup_report(VERSION, path)
    db.apply_cleanup(path, db.preview_cleanup(path).token)


ZH = {
    "block.m.todo": "待辦",
    "gui.m.arrows": "← →",
    "gui.m.angle": "180°",
    "gui.m.color": "#FFFFFF",
    "language.code": "zh_tw",
    "item.m.sword": "鐵劍",
}
JUNK_LANG = {
    "block.m.todo",
    "gui.m.arrows",
    "gui.m.angle",
    "gui.m.color",
    "language.code",
}
LICENSE_KEY = "guide/a.md#9"
ANCHOR_KEY = "patchouli_books/b/entries/start.json#pages[1].anchor"
OLD_JUNK = JUNK_LANG | {LICENSE_KEY, ANCHOR_KEY}


def _legacy_items(jar: Path) -> list[ScanItem]:
    """舊版掃描不過濾的結果：lang 全部頂層字串逐字匯入，再加上舊版會收的授權段落與 anchor。"""
    return [
        ScanItem("lang", "m", key, value)
        for key, value in LANG.items()
        if isinstance(value, str)
    ] + [
        ScanItem("guide", "m", LICENSE_KEY, LICENSE),
        ScanItem("patchouli", "m", ANCHOR_KEY, "QuickStart"),
    ]


def _all_keys(db: TranslationDB) -> set[str]:
    return {r[0] for r in db._q("SELECT key FROM entry")}


def _delete_all(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["items"]:
        item["delete"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_first_scan_with_clean_english_ingests_no_flagged_rows(tmp_path):
    jar = _jar(tmp_path / "m.jar")
    items = _scan(jar)
    keys = _keys(items)
    assert not keys & OLD_JUNK
    assert {i.en_us for i in items if i.key.startswith("guide/")} == {
        "# Title Here",
        "Open the terminal with a right click.",
    }  # 授權段落沒有進來
    # 巢狀 text 與清單值有收
    assert {"gui.m.nested.text", "gui.m.list[0]", "gui.m.list[1]"} <= keys
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(VERSION, items)
    assert _report_keys(db, tmp_path, "r.json") == set()
    assert _report_keys(db, tmp_path, "p.json", protected=True) == set()


def test_old_unfiltered_data_is_cleaned_and_rescan_does_not_readd_it(tmp_path):
    jar = _jar(tmp_path / "m.jar")
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(VERSION, _legacy_items(jar))
    assert OLD_JUNK <= _all_keys(db)

    flagged = _report_keys(db, tmp_path, "r.json")
    assert flagged == OLD_JUNK
    path = tmp_path / "r.json"
    plan = db.preview_cleanup(path)
    db.apply_cleanup(path, plan.token)
    assert not OLD_JUNK & _all_keys(db)
    kept = _all_keys(db)

    # 重掃同一個 jar（預設清理）：被清掉的不會回來，原本留下的也不會重複或遺失
    db.ingest(VERSION, _scan(jar))
    assert not OLD_JUNK & _all_keys(db)
    added = _all_keys(db) - kept
    assert {"gui.m.nested.text", "gui.m.list[0]", "gui.m.list[1]"} <= added
    assert _report_keys(db, tmp_path, "r2.json") == set()


def test_clean_english_off_still_skips_what_cleanup_removes(tmp_path):
    """關閉「清理」只放寬機器翻譯的篩選；清理規則判定不是文字的仍不收，清理後重掃不會回來。"""
    jar = _jar(tmp_path / "m.jar")
    db = TranslationDB(tmp_path / "t.db")
    items = _scan(jar, clean_english=False)
    assert not _keys(items) & JUNK_LANG
    db.ingest(VERSION, _legacy_items(jar))
    _clean(db, tmp_path)
    db.ingest(VERSION, items)
    assert not JUNK_LANG & _all_keys(db)
    assert _report_keys(db, tmp_path, "r2.json") == set()


def test_clean_english_off_keeps_nested_and_list_lang_values(tmp_path):
    """關閉「清理」只會多收：鍵集合包含預設模式的鍵集合（巢狀 text、清單值也收）。"""
    jar = _jar(tmp_path / "m.jar")
    off = _keys(_scan(jar, clean_english=False))
    on = _keys(_scan(jar))
    assert {"gui.m.nested.text", "gui.m.list[0]", "gui.m.list[1]"} <= off
    assert on <= off


def test_translated_zip_matches_nested_and_list_translations(tmp_path):
    zh = {"gui.m.nested": {"text": "巢狀你好"}, "gui.m.list": ["第一行", "第二行"]}
    jar = _jar(tmp_path / "m.jar", zh=zh)
    for kw in ({"translated": True}, {"translated": True, "clean_english": False}):
        rows = {i.key: i for i in _scan(jar, **kw)}
        assert rows["gui.m.nested.text"].zh_tw == "巢狀你好"
        assert rows["gui.m.nested.text"].en_us == "Nested hello world"
        assert rows["gui.m.list[1]"].zh_tw == "第二行"


def test_translated_zip_filtered_en_raw_preserved_and_structural_not_resurrected(
    tmp_path,
):
    """翻譯 ZIP 可補回對位短字，但不能復活 raw 結構值。"""
    en = {
        "gui.m.wiki.tooltip": "Wiki",
        "block.createaddition.barbed_wire.tooltip": "BARBED_WIRE",
        "gui.m.resource.tooltip": "minecraft:stone",
        "gui.m.placeholder.tooltip": "%s",
        "gui.m.url.tooltip": "https://example.com/help",
        "gui.m.todo.tooltip": "TODO",
    }
    zh = {key: "中文" for key in en}
    jar = _jar(tmp_path / "m.jar", lang_en=en, zh=zh)

    rows = {item.key: item for item in _scan(jar, translated=True)}

    assert rows["gui.m.wiki.tooltip"].en_us == "Wiki"
    assert rows["block.createaddition.barbed_wire.tooltip"].en_us == "BARBED_WIRE"
    for key in (
        "gui.m.resource.tooltip",
        "gui.m.placeholder.tooltip",
        "gui.m.url.tooltip",
        "gui.m.todo.tooltip",
    ):
        assert key not in rows


def test_translated_zip_conflicting_en_source_records_change_without_translation(
    tmp_path,
):
    jar = _jar(
        tmp_path / "m.jar",
        lang_en={"item.m.sword": "New English"},
        zh={"item.m.sword": "新翻譯"},
    )
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(VERSION, [ScanItem("lang", "m", "item.m.sword", "Old English")])

    db.ingest(VERSION, _scan(jar, translated=True))

    assert (
        db._one("SELECT COUNT(*) FROM src_change WHERE key = ?", ("item.m.sword",))[0]
        == 1
    )
    assert (
        db._one(
            "SELECT COUNT(*) FROM translation t JOIN entry e ON e.id=t.entry_id "
            "WHERE e.key=? AND t.zh_tw=?",
            ("item.m.sword", "新翻譯"),
        )[0]
        == 0
    )


def test_translated_zip_existing_en_is_not_blank_and_reimport_is_idempotent(tmp_path):
    en = {"gui.m.wiki.tooltip": "Wiki"}
    zh = {"gui.m.wiki.tooltip": "維基"}
    jar = _jar(tmp_path / "m.jar", lang_en=en, zh=zh)
    db = TranslationDB(tmp_path / "t.db")

    items = _scan(jar, translated=True)
    db.ingest(VERSION, items)
    first = db._q(
        "SELECT e.en_us, COUNT(t.id) FROM entry e LEFT JOIN translation t "
        "ON t.entry_id=e.id WHERE e.key=? GROUP BY e.id",
        ("gui.m.wiki.tooltip",),
    )
    db.ingest(VERSION, items)
    second = db._q(
        "SELECT e.en_us, COUNT(t.id) FROM entry e LEFT JOIN translation t "
        "ON t.entry_id=e.id WHERE e.key=? GROUP BY e.id",
        ("gui.m.wiki.tooltip",),
    )

    assert first == second == [("Wiki", 1)]


def test_translated_zip_adopts_safe_raw_english_for_an_existing_empty_row(tmp_path):
    """翻譯 ZIP 的對位原文可補進舊的空英文列，但不改人工譯文契約。"""
    en = {"gui.m.wiki.tooltip": "Wiki"}
    zh = {"gui.m.wiki.tooltip": "維基"}
    jar = _jar(tmp_path / "m.jar", lang_en=en, zh=zh)
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        VERSION,
        [
            ScanItem(
                "lang",
                "m",
                "gui.m.wiki.tooltip",
                "",
                zh_tw="既有譯文",
                source=SRC_MANUAL,
            )
        ],
    )

    db.ingest(VERSION, _scan(jar, translated=True))

    row = db._one(
        "SELECT e.en_us, t.zh_tw FROM entry e "
        "JOIN translation t ON t.entry_id=e.id "
        "WHERE e.key=? AND t.source=?",
        ("gui.m.wiki.tooltip", SRC_MANUAL),
    )
    assert row == ("Wiki", "既有譯文")


def test_translated_zip_patchouli_stale_alignment_never_uses_raw_text(tmp_path):
    """手冊頁數不同時仍保留 stale guard，不把同索引英文配給錯誤譯文。"""
    jar = tmp_path / "stale-book.zip"
    en = {
        "name": "Getting Started",
        "pages": [
            {"type": "patchouli:text", "text": "First page text."},
            {"type": "patchouli:text", "text": "Second page text."},
        ],
    }
    zh = {
        "name": "入門",
        "pages": [{"type": "patchouli:text", "text": "只有一頁。"}],
    }
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(
            "assets/m/patchouli_books/b/en_us/entries/start.json",
            json.dumps(en),
        )
        zf.writestr(
            "assets/m/patchouli_books/b/zh_tw/entries/start.json",
            json.dumps(zh, ensure_ascii=False),
        )

    rows = _scan(jar, translated=True)

    assert {item.key for item in rows} >= {
        "patchouli_books/b/entries/start.json#pages[0].text",
        "patchouli_books/b/entries/start.json#pages[1].text",
    }
    assert all(not item.zh_tw for item in rows)


def test_translated_zip_patchouli_structural_values_do_not_resurrect(tmp_path):
    """翻譯 ZIP 不得因 raw fallback 復活 multiblock pattern 或 mapping。"""
    jar = tmp_path / "book-structure.zip"
    en = {
        "name": "Structure Guide",
        "pages": [
            {
                "type": "patchouli:multiblock",
                "pattern": [[" A "], [" B "]],
                "mapping": {"A": "minecraft:stone", "B": "minecraft:dirt"},
            },
        ],
    }
    zh = {
        "name": "結構指南",
        "pages": [
            {
                "type": "patchouli:multiblock",
                "pattern": [["甲"]],
                "mapping": {"甲": "minecraft:stone"},
            },
        ],
    }
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(
            "assets/m/patchouli_books/b/en_us/entries/start.json",
            json.dumps(en),
        )
        zf.writestr(
            "assets/m/patchouli_books/b/zh_tw/entries/start.json",
            json.dumps(zh, ensure_ascii=False),
        )

    rows = _scan(jar, translated=True)

    assert all("pattern" not in item.key and "mapping" not in item.key for item in rows)


def test_translated_zip_cn_only_keeps_missing_english_empty(tmp_path):
    jar = tmp_path / "cn-only.zip"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(
            "assets/m/lang/zh_cn.json",
            json.dumps({"item.m.sword": "鐵劍"}, ensure_ascii=False),
        )

    rows = _scan(jar, translated=True)

    assert [(item.en_us, item.zh_tw, item.zh_cn) for item in rows] == [("", "", "鐵劍")]


def test_translated_zip_does_not_bring_cleaned_rows_back(tmp_path):
    """翻譯 ZIP 逐字收 zh_tw，但清理規則判定不是文字的列（含原文為空、只能看鍵與譯文的）不收。"""
    jar = _jar(tmp_path / "m.jar", zh=ZH)
    for kw in (
        {"translated": True, "clean_english": False},
        {"translated": True},
    ):
        items = _scan(jar, **kw)
        keys = _keys(items)
        assert not keys & JUNK_LANG, kw
        assert "item.m.sword" in keys  # 正常譯文照收
        db = TranslationDB(tmp_path / f"t{len(kw)}.db")
        db.ingest(VERSION, items)
        assert _report_keys(db, tmp_path, f"r{len(kw)}.json") == set()
        assert _report_keys(db, tmp_path, f"p{len(kw)}.json", protected=True) == set()


def test_existing_rows_without_original_are_cleaned_by_key_and_translation(tmp_path):
    """舊資料裡原文為空的垃圾列（翻譯 ZIP 先建立的）：看鍵與目前譯文，列在已有譯文的檢查檔。"""
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        VERSION,
        [
            ScanItem("lang", "m", "_comment", "", zh_tw="備註", source=5),
            ScanItem("lang", "m", "gui.m.arrows", "", zh_tw="← →", source=5),
            ScanItem("lang", "m", "gui.m.angle", "", zh_tw="180°", source=5),
            ScanItem("lang", "m", "item.m.sword", "", zh_tw="鐵劍", source=5),
        ],
    )
    protected = _report_keys(db, tmp_path, "p.json", protected=True)
    assert protected == {"_comment", "gui.m.arrows", "gui.m.angle"}
    path = tmp_path / "p.json"
    _delete_all(path)
    plan = db.preview_cleanup(path)
    assert plan.deletable == 3
    db.apply_cleanup(path, plan.token)
    assert _all_keys(db) == {"item.m.sword"}
