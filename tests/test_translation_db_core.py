"""Mod 翻譯資料庫核心：身分、寫入規則、掃描、手動同步、寫回與查詢。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    KIND_PATCHOULI,
    ScanItem,
    TranslationDB,
    TranslationResolver,
    WriteBackBuffer,
    WriteBackItem,
    split_items_by_db,
)
from translation_tool.translation_db.identity import (
    classify_file,
    classify_member,
    get_by_path,
)
from translation_tool.translation_db.resolver import version_number
from translation_tool.translation_db.scanner import (
    ScanOptions,
    scan_folder_generator,
    scan_jar,
)
from translation_tool.translation_db.schema import (
    SRC_AI,
    SRC_JAR_CN,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
)


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def item(key="item.foo.a", en="Steel Casing", tw="", cn="", mod="foo", kind=KIND_LANG):
    return ScanItem(kind, mod, key, en, tw, cn)


# ---------------------------------------------------------------- 身分
def test_classify_lang_and_patchouli_members():
    lang = classify_member("assets/foo/lang/en_us.json")
    assert (lang.kind, lang.mod_id, lang.lang) == (KIND_LANG, "foo", "en_us")
    assert classify_member("assets/foo/lang/ZH_TW.json").lang == "zh_tw"
    assert classify_member("assets/foo/lang/en_us.lang") is None
    assert classify_member("assets/foo/textures/a.json") is None

    book = classify_member("assets/foo/patchouli_books/guide/en_us/entries/a/b.json")
    assert (book.kind, book.file_key) == (
        KIND_PATCHOULI,
        "patchouli_books/guide/entries/a/b.json",
    )
    # data 與 assets、不同語言資料夾 → 同一個身分
    other = classify_member("data/foo/patchouli_books/guide/zh_tw/entries/a/b.json")
    assert other.file_key == book.file_key and other.lang == "zh_tw"
    assert classify_member("assets/foo/patchouli_books/guide/entries/a.json") is None


def test_classify_file_relative_to_root(tmp_path):
    f = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    ident = classify_file(f, tmp_path)
    assert ident.mod_id == "foo" and ident.item_key("item.x") == "item.x"
    bf = tmp_path / "assets" / "foo" / "book" / "en_us" / "e.json"
    assert (
        classify_file(bf, tmp_path).item_key("pages[0].text")
        == "book/e.json#pages[0].text"
    )


def test_get_by_path_handles_dotted_keys_and_lists():
    data = {"item.foo.bar": "A", "pages": [{"text": "B"}]}
    assert get_by_path(data, "item.foo.bar") == "A"
    assert get_by_path(data, "pages[0].text") == "B"
    assert get_by_path(data, "pages[3].text") is None
    assert get_by_path(data, "missing") is None


def test_version_number_orders_labels():
    assert (
        version_number("1.21.1") > version_number("1.20.1") > version_number("1.16.5")
    )
    assert version_number("1.21 (24w18a)~1.21.1") == version_number("1.21.1")
    assert version_number("unknown") == 0


# ---------------------------------------------------------- 掃描寫入規則
def test_ingest_inserts_and_skips_existing(db):
    stats = db.ingest("1.21.1", [item(tw="鋼製外殼")])
    assert (stats.new_entries, stats.existing) == (1, 0)
    again = db.ingest("1.21.1", [item(tw="完全不同的譯文")])
    assert (again.new_entries, again.existing) == (0, 1)
    rows, total = db.list_entries("1.21.1")
    assert total == 1 and rows[0].zh_tw == "鋼製外殼"  # 沒有被覆蓋


def test_ingest_adds_missing_source_to_existing_entry(db):
    db.ingest("1.21.1", [item()])
    stats = db.ingest("1.21.1", [item(tw="鋼製外殼")])
    assert stats.added_translations == 1
    assert db.list_entries("1.21.1")[0][0].zh_tw == "鋼製外殼"


def test_ingest_records_en_change_without_touching_entry(db):
    db.ingest("1.21.1", [item(en="Steel Casing", tw="鋼製外殼")])
    stats = db.ingest("1.21.1", [item(en="Steel Casing Block", tw="鋼製外殼方塊")])
    assert stats.en_changed == 1
    row = db.list_entries("1.21.1")[0][0]
    assert (row.en_us, row.zh_tw) == ("Steel Casing", "鋼製外殼")
    assert len(db.src_changes("1.21.1")) == 1
    db.ingest("1.21.1", [item(en="Steel Casing Block")])
    assert len(db.src_changes("1.21.1")) == 1  # 同一筆變動不重複記錄


def test_ingest_converts_cn_only_when_no_tw(db):
    conv = lambda s: s.replace("钢", "鋼")
    db.ingest("1.21.1", [item(cn="钢外壳"), item(key="k2", cn="钢", tw="繁中")], conv)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert (rows["item.foo.a"].zh_tw, rows["item.foo.a"].source) == (
        "鋼外壳",
        SRC_JAR_CN,
    )
    assert (rows["k2"].zh_tw, rows["k2"].source) == ("繁中", SRC_JAR_TW)


def test_versions_are_independent(db):
    db.ingest("1.21.1", [item(tw="新版")])
    db.ingest("1.20.1", [item(tw="舊版")])
    assert sorted(db.versions()) == ["1.20.1", "1.21.1"]
    assert db.list_entries("1.20.1")[0][0].zh_tw == "舊版"
    assert db.list_entries("1.21.1")[0][0].diff is True  # 其他版本相同內容、譯文不同


def test_source_priority_picks_best_and_rebuilds(tmp_path):
    path = tmp_path / "p.db"
    d = TranslationDB(path)
    d.ingest("1.21.1", [item(tw="自帶", cn="簡")], None)
    eid = d.list_entries("1.21.1")[0][0].id
    with d._tx() as conn:  # 補一筆町宮來源
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw) VALUES (?,?,?)",
            (eid, SRC_SUBTITLE, "町宮"),
        )
        d._refresh(conn, [eid])
    assert d.get_entry(eid).zh_tw == "町宮"
    d.close()
    d2 = TranslationDB(path, priority=(SRC_JAR_TW, SRC_SUBTITLE))  # 改優先序 → 重建
    assert d2.get_entry(eid).zh_tw == "自帶"
    d2.close()


# ---------------------------------------------------------- 手動更新
def test_manual_save_propagates_to_same_content_only(db):
    db.ingest("1.21.1", [item(tw="能量")])
    db.ingest("1.20.1", [item(tw="能源")])
    db.ingest("1.16.5", [item(en="Steel Casing Old", tw="舊")])  # 原文不同
    cur = db.list_entries("1.21.1")[0][0]
    preview = db.preview_manual(cur.id, "電能")
    assert {i.mc_version for i in preview} == {"1.21.1", "1.20.1"}
    done = db.save_manual(cur.id, "電能", actor="jlin")
    assert {i.mc_version for i in done} == {"1.21.1", "1.20.1"}
    assert db.list_entries("1.20.1")[0][0].zh_tw == "電能"
    assert db.list_entries("1.20.1")[0][0].source == SRC_MANUAL
    assert db.list_entries("1.16.5")[0][0].zh_tw == "舊"
    assert db.list_entries("1.21.1")[0][0].diff is False


def test_manual_save_without_propagation_and_original_kept(db):
    db.ingest("1.21.1", [item(tw="A")])
    db.ingest("1.20.1", [item(tw="B")])
    cur = db.list_entries("1.21.1")[0][0]
    db.save_manual(cur.id, "C", propagate=False)
    assert db.list_entries("1.20.1")[0][0].zh_tw == "B"
    detail = db.entry_detail(cur.id)
    assert {t.source for t in detail.translations} == {
        SRC_JAR_TW,
        SRC_MANUAL,
    }  # 原譯文保留
    assert detail.history[0].action == "manual"


def test_revert_restores_previous_state_for_whole_batch(db):
    db.ingest("1.21.1", [item(tw="A")])
    db.ingest("1.20.1", [item(tw="B")])
    cur = db.list_entries("1.21.1")[0][0]
    db.save_manual(cur.id, "C")
    hid = db.entry_detail(cur.id).history[0].id
    assert db.revert(hid) == 2
    assert db.list_entries("1.21.1")[0][0].zh_tw == "A"
    assert db.list_entries("1.20.1")[0][0].zh_tw == "B"


def test_manual_save_rejects_empty(db):
    db.ingest("1.21.1", [item(tw="A")])
    with pytest.raises(ValueError):
        db.save_manual(db.list_entries("1.21.1")[0][0].id, "  ")


# -------------------------------------------------------- AI 寫回
def test_write_back_creates_entry_and_fills_blank_other_versions(db):
    db.ingest("1.20.1", [item()])  # 其他版本：相同內容、沒有譯文
    db.ingest("1.19.2", [item(tw="已有")])  # 其他版本：已有譯文，不可被覆蓋
    stats = db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "鋼製外殼")],
    )
    assert (stats.written, stats.filled_other) == (1, 1)
    assert db.list_entries("1.21.1")[0][0].zh_tw == "鋼製外殼"  # 目標版本條目自動建立
    assert db.list_entries("1.20.1")[0][0].source == SRC_AI
    assert db.list_entries("1.19.2")[0][0].zh_tw == "已有"


def test_write_back_is_insert_only(db):
    db.ingest("1.21.1", [item()])
    w = [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "第一次")]
    db.write_back("1.21.1", w)
    stats = db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "第二次")],
    )
    assert stats.written == 0 and stats.skipped == 1
    assert db.list_entries("1.21.1")[0][0].zh_tw == "第一次"


def test_write_back_skips_when_english_differs(db):
    db.ingest("1.21.1", [item(en="Old Text Here")])
    stats = db.write_back(
        "1.21.1", [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "New Text Here", "譯")]
    )
    assert stats.written == 0 and stats.skipped == 1


# ---------------------------------------------------------- 查詢
def test_list_entries_filters_and_detail(db):
    db.ingest(
        "1.21.1",
        [
            item(tw="A"),
            item(key="k2", en="Second Entry"),
            item(key="k3", en="Steel Casing", tw="B", mod="bar"),
        ],
    )
    assert db.list_entries("1.21.1", state="none")[1] == 1
    assert db.list_entries("1.21.1", mod_id="bar")[1] == 1
    assert db.list_entries("1.21.1", query="second")[1] == 1
    assert db.list_entries("1.21.1", query="100%")[1] == 0  # % 不是萬用字元
    first = db.list_entries("1.21.1", mod_id="foo", query="item.foo.a")[0][0]
    detail = db.entry_detail(first.id)
    assert [r.key for r in detail.same_text] == ["k3"]  # 原文相同、不同模組


def test_overview_and_stats(db):
    db.ingest("1.21.1", [item(tw="A"), item(key="k2", en="Second Entry")])
    db.ingest("1.20.1", [item(tw="B")])
    ov = db.overview()
    assert ov["mods"] == 1 and ov["diff"] == 1
    stats = {s.mc_version: s for s in db.version_stats()}
    assert stats["1.21.1"].total == 2 and stats["1.21.1"].untranslated == 1
    assert db.missing_by_mod("1.21.1")[0]["missing"] == 1


# ---------------------------------------------------------- 解析器
def test_resolver_target_first_then_cross_version(db):
    db.ingest("1.21.1", [item(tw="新")])
    db.ingest("1.20.1", [item(key="only.old", tw="舊版獨有")])
    db.ingest("1.19.2", [item(key="only.old", tw="更舊")])
    r = TranslationResolver(db, "1.21.1")
    assert r.lookup(KIND_LANG, "foo", "item.foo.a", "Steel Casing").zh_tw == "新"
    hit = r.lookup(KIND_LANG, "foo", "only.old", "Steel Casing")
    assert hit.cross and hit.zh_tw == "舊版獨有"  # 取版本最接近者
    assert r.lookup(KIND_LANG, "foo", "item.foo.a", "Changed Text") is None
    assert r.stats.en_mismatch == 1
    assert (
        TranslationResolver(db, "1.21.1", cross_version=False).lookup(
            KIND_LANG, "foo", "only.old", "Steel Casing"
        )
        is None
    )


def test_split_items_by_db_uses_root_relative_identity(db, tmp_path):
    db.ingest("1.21.1", [item(tw="鋼製外殼")])
    f = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    items = [
        {
            "file": str(f),
            "path": "item.foo.a",
            "source_text": "Steel Casing",
            "text": "Steel Casing",
            "cache_type": "lang",
        },
        {
            "file": str(f),
            "path": "item.foo.zzz",
            "source_text": "Other Thing",
            "text": "Other Thing",
            "cache_type": "lang",
        },
    ]
    hits, rest = split_items_by_db(TranslationResolver(db, "1.21.1"), items, tmp_path)
    assert [h["text"] for h in hits] == ["鋼製外殼"] and len(rest) == 1
    assert split_items_by_db(None, items, tmp_path) == ([], items)


def test_write_back_buffer_flushes_translated_items(db, tmp_path):
    f = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    buf = WriteBackBuffer(db, "1.21.1", tmp_path)
    buf.add(
        {"file": str(f), "path": "item.foo.a", "source_text": "Steel Casing"},
        "鋼製外殼",
    )
    buf.add(
        {
            "file": str(tmp_path / "x" / "y.json"),
            "path": "k",
            "source_text": "ignored text",
        },
        "不適用",
    )
    stats = buf.flush()
    assert stats.written == 1 and buf.flush().written == 0


# ---------------------------------------------------------- jar 掃描
def make_jar(
    path: Path, files: dict[str, object], nested: dict[str, bytes] | None = None
) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))
        for name, blob in (nested or {}).items():
            zf.writestr(name, blob)
    return path


def jar_bytes(files: dict[str, object]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))
    return buf.getvalue()


LANG_JAR = {
    "assets/foo/lang/en_us.json": {
        "item.foo.a": "Steel Casing",
        "item.foo.b": "Infused Alloy",
    },
    "assets/foo/lang/zh_tw.json": {"item.foo.a": "鋼製外殼"},
    "assets/foo/lang/zh_cn.json": {"item.foo.b": "注入合金"},
    "assets/foo/patchouli_books/guide/en_us/entries/intro.json": {
        "name": "Getting Started Guide",
        "pages": [{"type": "text", "text": "Welcome to the guide book."}],
    },
    "assets/foo/patchouli_books/guide/zh_tw/entries/intro.json": {
        "name": "入門指南",
        "pages": [{"type": "text", "text": "歡迎使用本書。"}],
    },
}


def test_scan_jar_reads_lang_and_patchouli(tmp_path):
    jar = make_jar(tmp_path / "foo.jar", LANG_JAR)
    res = scan_jar(jar, ScanOptions("1.21.1"), ("patchouli_books",))
    by_key = {i.key: i for i in res.items}
    assert by_key["item.foo.a"].zh_tw == "鋼製外殼"
    assert by_key["item.foo.b"].zh_cn == "注入合金" and by_key["item.foo.b"].zh_tw == ""
    page = by_key["patchouli_books/guide/entries/intro.json#pages[0].text"]
    assert (page.kind, page.zh_tw) == (KIND_PATCHOULI, "歡迎使用本書。")
    only_lang = scan_jar(
        jar, ScanOptions("1.21.1", include_patchouli=False), ("patchouli_books",)
    )
    assert all(i.kind == KIND_LANG for i in only_lang.items)


def test_scan_jar_recurses_into_jarjar_and_respects_option(tmp_path):
    inner = jar_bytes(
        {"assets/inner/lang/en_us.json": {"block.inner.x": "Inner Block Name"}}
    )
    jar = make_jar(
        tmp_path / "outer.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Outer Text Here"}},
        {"META-INF/jarjar/inner.jar": inner},
    )
    res = scan_jar(jar, ScanOptions("1.21.1"), ())
    assert {i.mod_id for i in res.items} == {"foo", "inner"} and res.nested_jars == 1
    off = scan_jar(jar, ScanOptions("1.21.1", scan_nested=False), ())
    assert {i.mod_id for i in off.items} == {"foo"}


def test_scan_jar_bad_archive_reports_error(tmp_path):
    bad = tmp_path / "bad.jar"
    bad.write_bytes(b"not a zip")
    res = scan_jar(bad, ScanOptions("1.21.1"), ())
    assert res.error and not res.items


def test_scan_folder_end_to_end_is_idempotent(db, tmp_path):
    make_jar(tmp_path / "foo.jar", LANG_JAR)
    make_jar(tmp_path / "empty.jar", {"readme.txt": "x"})
    (tmp_path / "broken.jar").write_bytes(b"nope")
    opts = ScanOptions("1.21.1")

    def run():
        last = None
        for upd in scan_folder_generator(db, tmp_path, opts, workers=2):
            last = upd
        return last["report"]

    first = run()
    assert first.jars_total == 3 and first.jars_with_lang == 1
    assert first.jars_without_lang == 1 and first.jars_failed == ["broken.jar"]
    assert first.stats.new_entries == 4  # 2 lang + 2 patchouli 欄位
    second = run()
    assert second.stats.new_entries == 0 and second.stats.existing == 4
    assert db.last_scans(1)[0]["mc_version"] == "1.21.1"


def test_scan_folder_can_cancel(db, tmp_path):
    for n in range(3):
        make_jar(
            tmp_path / f"m{n}.jar",
            {f"assets/m{n}/lang/en_us.json": {"a.b": "Some Text Here"}},
        )
    gen = scan_folder_generator(
        db, tmp_path, ScanOptions("1.21.1"), should_cancel=lambda: True, workers=1
    )
    report = [u for u in gen if "report" in u][-1]["report"]
    assert report.cancelled is True


def test_scan_dry_run_reads_but_never_writes(db, tmp_path):
    make_jar(tmp_path / "foo.jar", LANG_JAR)
    opts = ScanOptions("1.21.1", dry_run=True)
    last = None
    for upd in scan_folder_generator(db, tmp_path, opts, workers=1):
        last = upd
    report = last["report"]
    assert report.dry_run and report.items_found == 4 and report.stats.new_entries == 0
    assert db.count_entries() == 0 and db.last_scans() == []
    # 預覽不需要資料庫
    assert [u for u in scan_folder_generator(None, tmp_path, opts, workers=1)][-1][
        "report"
    ].items_found == 4
    with pytest.raises(ValueError):
        list(scan_folder_generator(None, tmp_path, ScanOptions("1.21.1")))


# ------------------------------------------------ 與語系合併相同的清理規則
RULES = [{"from": "存儲", "to": "儲存"}]


def test_scan_ignores_non_cjk_values_in_chinese_files(tmp_path):
    """zh_tw／zh_cn 裡只是英文（沒翻譯的複本）不算譯文，與語系合併的判斷一致。"""
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {
                "a.b": "Steel Casing",
                "c.d": "Infused Alloy",
            },
            "assets/foo/lang/zh_tw.json": {"a.b": "Steel Casing"},
            "assets/foo/lang/zh_cn.json": {"c.d": "Infused Alloy"},
        },
    )
    items = {i.key: i for i in scan_jar(jar, ScanOptions("1.21.1"), ()).items}
    assert (items["a.b"].zh_tw, items["a.b"].zh_cn) == ("", "")
    assert (items["c.d"].zh_tw, items["c.d"].zh_cn) == ("", "")


def test_scan_applies_replace_rules_to_traditional_text(tmp_path):
    jar = make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {"a.b": "Storage Block"},
            "assets/foo/lang/zh_tw.json": {"a.b": "存儲方塊"},
        },
    )
    on = scan_jar(jar, ScanOptions("1.21.1", rules=RULES), ())
    assert on.items[0].zh_tw == "儲存方塊"
    off = scan_jar(jar, ScanOptions("1.21.1", apply_rules=False), ())
    assert off.items[0].zh_tw == "存儲方塊"


def test_cn_conversion_also_applies_replace_rules(db, tmp_path, monkeypatch):
    from translation_tool.translation_db import scanner

    make_jar(
        tmp_path / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {"a.b": "Storage Block"},
            "assets/foo/lang/zh_cn.json": {"a.b": "存储方块"},
        },
    )
    monkeypatch.setattr(scanner, "load_rules", lambda: RULES)
    list(scan_folder_generator(db, tmp_path, ScanOptions("1.21.1"), workers=1))
    row = db.list_entries("1.21.1")[0][0]
    assert (row.zh_tw, row.source) == ("儲存方塊", SRC_JAR_CN)  # OpenCC 轉繁後再套規則
    assert db.entry_detail(row.id).translations[0].zh_cn == "存储方块"  # 簡中原文保留


# ------------------------------------------------ 換行、前後空白、格式碼原樣保留
SPECIAL = "§a哈囉§r\n第二行 %s\\n字面換行 "  # 真換行、字面 \n、§ 格式碼、%s、結尾空白


def test_values_with_newlines_codes_and_trailing_space_are_stored_verbatim(db):
    db.ingest("1.21.1", [item(en="Hello %s\nWorld", tw=SPECIAL)])
    row = db.list_entries("1.21.1")[0][0]
    assert row.zh_tw == SPECIAL and row.en_us == "Hello %s\nWorld"


def test_manual_save_and_write_back_do_not_trim_values(db):
    db.ingest("1.21.1", [item()])
    entry = db.list_entries("1.21.1")[0][0]
    db.save_manual(entry.id, SPECIAL)
    assert db.get_entry(entry.id).zh_tw == SPECIAL
    with pytest.raises(ValueError):
        db.save_manual(entry.id, " \n ")  # 全是空白仍然視為空

    db.write_back(
        "1.20.1",
        [WriteBackItem(KIND_LANG, "foo", "k.x", "Some English", " 前後空白\n")],
    )
    assert db.list_entries("1.20.1")[0][0].zh_tw == " 前後空白\n"


def test_resolver_returns_value_with_special_characters_intact(db):
    db.ingest("1.21.1", [item(en="Hello %s\nWorld", tw=SPECIAL)])
    hit = TranslationResolver(db, "1.21.1").lookup(
        KIND_LANG, "foo", "item.foo.a", "Hello %s\nWorld"
    )
    assert hit.zh_tw == SPECIAL
