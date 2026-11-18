"""單一 ZIP 的 KubeJS／FTB Quests 抽出與掃描。"""

from __future__ import annotations

import json
import sqlite3
import stat
import zipfile
from pathlib import Path

from translation_tool.translation_db.pack_archive import (
    extract_pack_members,
    zip_pack_scope,
)
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import (
    ScanOptions,
    scan_folder_generator,
    scan_jar,
)

SNBT = '{\n\ttitle: "Hello"\n\tquests: [{\n\t\tid: "AAAA"\n\t}]\n}\n'
LANG = '{\n\tchapter.AAAA.title: "歡迎"\n\tquest.BBBB.title: "任務"\n}\n'


def _make_zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _pack_zip(path: Path) -> Path:
    return _make_zip(
        path,
        {
            "Pack/config/ftbquests/quests/lang/zh_tw/chapter.snbt": LANG,
            "Pack/kubejs/assets/foo/lang/zh_tw.json": json.dumps({"a.b": "鋼"}),
            "Pack/config/other.json": "{}",
            "Pack/kubejs/big.png": b"\x89PNG",
        },
    )


def test_extract_keeps_only_pack_sources(tmp_path):
    zpath = _pack_zip(tmp_path / "p.zip")
    dest = tmp_path / "out"
    dest.mkdir()
    assert extract_pack_members(zpath, dest) == []
    files = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert files == {
        "Pack/config/ftbquests/quests/lang/zh_tw/chapter.snbt",
        "Pack/kubejs/assets/foo/lang/zh_tw.json",
    }


def test_extract_rejects_traversal_and_symlinks(tmp_path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../kubejs/evil.js", "x")
        zf.writestr("/abs/kubejs/evil.js", "x")
        zf.writestr("C:/kubejs/evil.js", "x")
        link = zipfile.ZipInfo("kubejs/link.js")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
        zf.writestr("kubejs/ok.js", "ok")
    dest = tmp_path / "out"
    dest.mkdir()
    issues = extract_pack_members(zpath, dest)
    assert {i["reason"] for i in issues} == {
        "archive_member_unsafe",
        "source_link_skipped",
    }
    assert len(issues) == 4
    assert [p.name for p in dest.rglob("*") if p.is_file()] == ["ok.js"]
    assert not (tmp_path / "kubejs").exists()


def test_extract_reports_bad_archive(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    issues = extract_pack_members(bad, tmp_path)
    assert [i["reason"] for i in issues] == ["archive_read_failed"]


def _scan(db, zpath):
    opts = ScanOptions("1.21.1", translated=True, include_pack_content=True)
    last = None
    for update in scan_folder_generator(db, zpath, opts):
        last = update
    return last["report"]


def test_single_zip_pack_scan_imports_and_is_idempotent(tmp_path):
    zpath = _pack_zip(tmp_path / "p.zip")
    db = TranslationDB(tmp_path / "t.db")
    report = _scan(db, zpath)
    assert report.pack_items_found >= 2 and report.stats.new_entries >= 2
    assert report.pack_root.startswith(str(zpath))
    assert str(tmp_path / "moddb_pack_") not in report.pack_root
    conn = sqlite3.connect(tmp_path / "t.db")
    scopes = {
        r[0] for r in conn.execute("select distinct pack_scope from entry_origin")
    }
    assert scopes == {zip_pack_scope(zpath)}
    count = conn.execute("select count(*) from entry_origin").fetchone()[0]
    again = _scan(db, zpath)
    assert again.stats.new_entries == 0
    assert conn.execute("select count(*) from entry_origin").fetchone()[0] == count


def test_single_zip_without_pack_sources_is_not_an_error(tmp_path):
    zpath = _make_zip(tmp_path / "x.zip", {"readme.txt": "hi"})
    report = _scan(TranslationDB(tmp_path / "t.db"), zpath)
    assert report.pack_items_found == 0 and report.stats.new_entries == 0
    assert not report.pack_root


def _pack_files(locale: str, values: dict[str, str]) -> dict[str, str]:
    body = "{\n" + "".join(f'\t{k}: "{v}"\n' for k, v in values.items()) + "}\n"
    return {f"config/ftbquests/quests/lang/{locale}/chapter.snbt": body}


def _import_english_pack(db, folder: Path, values: dict[str, str]) -> None:
    for name, body in _pack_files("en_us", values).items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    opts = ScanOptions("1.21.1", include_pack_content=True)
    for _ in scan_folder_generator(db, folder, opts):
        pass


def test_translated_zip_fills_zh_tw_into_matching_existing_pack(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    _import_english_pack(
        db, tmp_path / "orig", {"chapter.A.title": "Hello", "quest.B.title": "Bye"}
    )
    zpath = _make_zip(
        tmp_path / "zh.zip",
        {
            "Pack/" + n: b
            for n, b in _pack_files(
                "zh_tw", {"chapter.A.title": "你好", "quest.B.title": "再見"}
            ).items()
        },
    )
    report = _scan(db, zpath)
    assert report.stats.new_entries == 0 and report.stats.en_changed == 0
    conn = sqlite3.connect(tmp_path / "t.db")
    rows = conn.execute(
        "select e.en_us, t.zh_tw from entry e join translation t on t.entry_id=e.id "
        "where e.kind='ftb_quest_lang' order by e.key"
    ).fetchall()
    assert rows == [("Hello", "你好"), ("Bye", "再見")]
    assert conn.execute("select count(*) from src_change").fetchone()[0] == 0


def test_translated_zip_without_a_unique_match_adds_new_entries(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    _import_english_pack(db, tmp_path / "orig", {"chapter.X.title": "Other"})
    zpath = _make_zip(
        tmp_path / "zh.zip",
        {
            "Pack/" + n: b
            for n, b in _pack_files("zh_tw", {"chapter.A.title": "你好"}).items()
        },
    )
    report = _scan(db, zpath)
    assert report.stats.new_entries == 1
    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute(
        "select count(*) from entry where mod_id like 'ftbquests@zh.zip%'"
    ).fetchone() == (1,)


def test_translated_zip_ignores_earlier_import_that_stored_chinese_as_source(tmp_path):
    """先前匯入把中文存成原文的同一個 ZIP 身分，不能被當成「同一個模組包」。"""
    db = TranslationDB(tmp_path / "t.db")
    _import_english_pack(db, tmp_path / "orig", {"chapter.A.title": "Hello"})
    zpath = _make_zip(
        tmp_path / "zh.zip",
        {
            "Pack/" + n: b
            for n, b in _pack_files("zh_tw", {"chapter.A.title": "你好"}).items()
        },
    )
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute(
        "insert into entry (kind, mc_version, mod_id, key, en_us) values "
        "('ftb_quest_lang','1.21.1',?, 'chapter.snbt|lang:chapter.A.title','你好')",
        (f"ftbquests@{zip_pack_scope(zpath)}",),
    )
    conn.commit()
    report = _scan(db, zpath)
    assert report.stats.new_entries == 0
    rows = conn.execute(
        "select e.mod_id, t.zh_tw from entry e join translation t on t.entry_id=e.id "
        "where e.kind='ftb_quest_lang'"
    ).fetchall()
    assert len(rows) == 1 and rows[0][1] == "你好"
    assert "orig" in rows[0][0]


def test_single_zip_issue_paths_do_not_leak_temp_directory(tmp_path):
    zpath = _make_zip(
        tmp_path / "bad.zip",
        {
            "Pack/config/ftbquests/quests/lang/zh_tw/chapter.snbt": LANG + "}\n",
        },
    )
    report = _scan(TranslationDB(tmp_path / "t.db"), zpath)
    issues = [i for i in report.issues if i["reason"] == "snbt_trailing_braces_ignored"]
    assert issues
    assert all("moddb_pack_" not in str(v) for i in issues for v in i.values())
    assert all(str(zpath) in i["source_archive"] for i in issues)


def _kubejs_files(tip: str, title: str, desc: str) -> dict[str, str]:
    return {
        "kubejs/client_scripts/a/main.js": (
            "ItemEvents.tooltip(event => {\n"
            f"  event.add('demo:item', Text.of('{tip}'));\n"
            "});\n"
        ),
        "config/ftbquests/quests/chapters/a.snbt": (
            '{id:"CH1",title:"'
            + title
            + '",quests:[{id:"Q1",title:"'
            + title
            + '",description:["'
            + desc
            + '"]}]}'
        ),
    }


def test_in_place_translated_pack_fills_zh_tw_into_existing_entries(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    orig = tmp_path / "orig"
    for name, body in _kubejs_files(
        "Tooltip text", "First Quest", "Do the thing"
    ).items():
        target = orig / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    for _ in scan_folder_generator(
        db, orig, ScanOptions("1.21.1", include_pack_content=True)
    ):
        pass
    conn = sqlite3.connect(tmp_path / "t.db")
    kinds = {"kubejs_script", "ftb_quests"}
    before = conn.execute(
        "select kind, count(*) from entry where kind in ('kubejs_script','ftb_quests') "
        "group by 1"
    ).fetchall()
    assert {k for k, _ in before} == kinds
    hashes = conn.execute(
        "select source_path, selector, source_hash from entry_origin order by 1,2"
    ).fetchall()

    zpath = _make_zip(
        tmp_path / "zh.zip",
        {
            "Pack/" + n: b
            for n, b in _kubejs_files("提示文字", "第一個任務", "完成這件事").items()
        },
    )
    report = _scan(db, zpath)
    assert report.stats.new_entries == 0 and report.stats.en_changed == 0
    rows = conn.execute(
        "select e.kind, e.en_us, t.zh_tw from entry e join translation t "
        "on t.entry_id=e.id where e.kind in ('kubejs_script','ftb_quests') "
        "order by e.kind, e.key"
    ).fetchall()
    assert {r[0] for r in rows} == kinds
    assert all(r[1] and r[2] for r in rows)
    assert any(r[2] == "提示文字" for r in rows) and any(
        r[2] == "第一個任務" for r in rows
    )
    assert conn.execute("select count(*) from src_change").fetchone()[0] == 0
    after = conn.execute(
        "select source_path, selector, source_hash from entry_origin order by 1,2"
    ).fetchall()
    assert after == hashes  # 既有英文來源的雜湊與來源紀錄不被譯文檔蓋掉


def test_kubejs_lang_translation_only_zip_fills_zh_tw_into_existing_entries(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    orig = tmp_path / "orig"
    files = {
        "kubejs/assets/demo/lang/en_us.json": json.dumps({"item.demo.a": "Alpha"}),
        "kubejs/client_scripts/lang.js": (
            "ClientEvents.lang('en_us', event => {\n"
            "  event.add('item.demo.name', 'Demo Name');\n"
            "});\n"
        ),
    }
    for name, body in files.items():
        target = orig / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    for _ in scan_folder_generator(
        db, orig, ScanOptions("1.21.1", include_pack_content=True)
    ):
        pass
    zpath = _make_zip(
        tmp_path / "zh.zip",
        {
            "Pack/kubejs/assets/demo/lang/zh_tw.json": json.dumps(
                {"item.demo.a": "甲"}
            ),
            "Pack/kubejs/client_scripts/lang.js": (
                "ClientEvents.lang('zh_tw', event => {\n"
                "  event.add('item.demo.name', '示範名稱');\n"
                "});\n"
            ),
        },
    )
    report = _scan(db, zpath)
    assert (
        report.stats.en_changed == 0
    )  # ZIP 內的 kubejs/assets/<ns>/lang 另會被一般語言檔掃描匯入為 lang，這裡只看模組包條目
    rows = (
        sqlite3.connect(tmp_path / "t.db")
        .execute(
            "select e.en_us, t.zh_tw from entry e join translation t on t.entry_id=e.id "
            "where e.kind='kubejs_lang' order by e.key"
        )
        .fetchall()
    )
    assert rows == [("Alpha", "甲"), ("Demo Name", "示範名稱")]
    assert (
        sqlite3.connect(tmp_path / "t.db")
        .execute("select count(*) from entry where kind='kubejs_lang'")
        .fetchone()[0]
        == 2
    )


def test_kubejs_lang_archive_members_are_not_scanned_as_regular_lang(tmp_path):
    """KubeJS 語系只由模組包掃描，不能冒充一般 lang（即使兩者 namespace 相同）。"""
    zpath = _make_zip(
        tmp_path / "mixed.zip",
        {
            "overrides/kubejs/assets/demo/lang/zh_tw.json": json.dumps(
                {"item.demo.widget": "KubeJS 翻譯"}, ensure_ascii=False
            ),
            "assets/demo/lang/zh_tw.json": json.dumps(
                {"item.demo.widget": "一般語系翻譯"}, ensure_ascii=False
            ),
        },
    )

    result = scan_jar(
        zpath,
        ScanOptions("1.21.1", translated=True, include_pack_content=False),
        (),
    )

    assert [(item.kind, item.zh_tw) for item in result.items] == [
        ("lang", "一般語系翻譯")
    ]


def test_extract_reports_file_directory_collision(tmp_path):
    zpath = _make_zip(
        tmp_path / "clash.zip",
        {"kubejs/a.js": "x", "kubejs/a.js/b.js": "y", "kubejs/ok.js": "z"},
    )
    dest = tmp_path / "out"
    dest.mkdir()
    issues = extract_pack_members(zpath, dest)
    assert [i["reason"] for i in issues] == ["archive_write_failed"]
    assert (dest / "kubejs" / "ok.js").is_file()


def test_translated_zip_does_not_touch_original_origins_or_references(
    tmp_path, monkeypatch
):
    db = TranslationDB(tmp_path / "t.db")
    _import_english_pack(db, tmp_path / "orig", {"chapter.A.title": "Hello"})
    conn = sqlite3.connect(tmp_path / "t.db")
    before = conn.execute(
        "select source_path, source_hash from entry_origin order by 1"
    ).fetchall()
    assert before
    calls = []
    original = TranslationDB.record_source_references
    monkeypatch.setattr(
        TranslationDB,
        "record_source_references",
        lambda self, *a, **k: calls.append(1) or original(self, *a, **k),
    )
    zpath = _make_zip(
        tmp_path / "zh.zip",
        {
            "Pack/" + n: b
            for n, b in _pack_files("zh_tw", {"chapter.A.title": "你好"}).items()
        },
    )
    _scan(db, zpath)
    after = conn.execute(
        "select source_path, source_hash from entry_origin order by 1"
    ).fetchall()
    assert after == before
    assert calls == []


def test_zh_cn_only_zip_is_ignored_when_zh_cn_reading_is_off(tmp_path):
    zpath = _make_zip(
        tmp_path / "cn.zip",
        {
            "Pack/" + n: b
            for n, b in _pack_files("zh_cn", {"chapter.A.title": "你好"}).items()
        },
    )
    db = TranslationDB(tmp_path / "t.db")
    opts = ScanOptions(
        "1.21.1",
        translated=True,
        include_pack_content=True,
        read_jar_translations=False,
    )
    report = None
    for update in scan_folder_generator(db, zpath, opts):
        report = update.get("report", report)
    assert report.stats.new_entries == 0


def test_match_total_counts_distinct_keys(tmp_path):
    from translation_tool.translation_db.models import ScanItem
    from translation_tool.translation_db.pack_scope_match import match_existing_scope

    class FakeDB:
        def pack_entry_keys(self, version, kind, prefix):
            return {"pack1": {"k": "K"}}

    items = [
        ScanItem(
            kind="kubejs_lang",
            mod_id=f"ns@pack@{i}",
            key="k",
            en_us="你",
            source_locale="zh_tw",
        )
        for i in range(3)
    ]
    match = match_existing_scope(FakeDB(), "1.21.1", items)
    assert match is not None and match.total == 1 and match.overlap == 1


def test_collision_issue_does_not_leak_temp_path(tmp_path):
    zpath = _make_zip(
        tmp_path / "clash.zip", {"kubejs/a.js": "x", "kubejs/a.js/b.js": "y"}
    )
    dest = tmp_path / "out"
    dest.mkdir()
    issues = extract_pack_members(zpath, dest)
    assert issues and str(dest) not in " ".join(issues[0].values())


def test_keep_existing_originals_drops_source_of_every_item():
    from translation_tool.translation_db.models import ScanItem
    from translation_tool.translation_db.pack_scope_match import (
        ScopeMatch,
        keep_existing_originals,
    )

    def item(key, text):
        return ScanItem(
            kind="ftb_quests",
            mod_id="q@p@1",
            key=key,
            en_us=text,
            source_path="zh.snbt",
            source_selector=key,
            source_hash="H2",
        )

    match = ScopeMatch(
        ("p@1",), "p@1", {("ftb_quests", "q", "title"): {"p@1": "Title"}}, 1, 1
    )
    adopted = keep_existing_originals(
        [item("title", "標題"), item("sub", "Sub"), item("extra", "多的")], match
    )
    out = adopted.items
    assert [(i.en_us, i.zh_tw) for i in out] == [("", "標題"), ("Sub", "")]
    assert all(not i.source_path and not i.source_selector for i in out)
    # 既有模組包沒有這個 key、又只有中文：不能把中文存成該模組包的原文
    assert [i.key for i in adopted.without_original] == ["extra"]


def test_zh_cn_off_reads_only_zh_tw_with_matching_origin(tmp_path):
    files = {
        "Pack/" + n: b
        for loc, text in (("zh_cn", "简"), ("zh_tw", "繁"))
        for n, b in _pack_files(loc, {"chapter.A.title": text}).items()
    }
    zpath = _make_zip(tmp_path / "both.zip", files)
    db = TranslationDB(tmp_path / "t.db")
    opts = ScanOptions(
        "1.21.1",
        translated=True,
        include_pack_content=True,
        read_jar_translations=False,
    )
    report = None
    for update in scan_folder_generator(db, zpath, opts):
        report = update.get("report", report)
    assert report.stats.new_entries == 1
    assert not [i for i in report.issues if "zh_cn" in str(i)]
    conn = sqlite3.connect(tmp_path / "t.db")
    paths = [r[0] for r in conn.execute("select source_path from entry_origin")]
    assert paths and all("zh_tw" in p for p in paths)


def _write_files(folder: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def test_same_folder_translated_in_place_fills_zh_tw(tmp_path):
    """原資料夾先掃英文、就地翻成中文後以翻譯來源再匯入：只補譯文，不記成原文已變動。"""
    db = TranslationDB(tmp_path / "t.db")
    pack = tmp_path / "pack"
    _write_files(pack, _kubejs_files("Tooltip text", "First Quest", "Do the thing"))
    for _ in scan_folder_generator(
        db, pack, ScanOptions("1.21.1", include_pack_content=True)
    ):
        pass
    conn = sqlite3.connect(tmp_path / "t.db")
    origins = conn.execute(
        "select source_path, selector, source_hash from entry_origin order by 1,2"
    ).fetchall()

    _write_files(pack, _kubejs_files("提示文字", "第一個任務", "完成這件事"))
    report = _scan(db, pack)

    assert report.stats.new_entries == 0 and report.stats.en_changed == 0
    assert conn.execute("select count(*) from src_change").fetchone()[0] == 0
    rows = conn.execute(
        "select e.en_us, t.zh_tw from entry e join translation t on t.entry_id=e.id"
    ).fetchall()
    assert len(rows) == 4
    assert {r[0] for r in rows} == {"Tooltip text", "First Quest", "Do the thing"}
    assert {r[1] for r in rows} == {"提示文字", "第一個任務", "完成這件事"}
    after = conn.execute(
        "select source_path, selector, source_hash from entry_origin order by 1,2"
    ).fetchall()
    assert after == origins


def test_same_folder_translated_in_place_only_updates_that_pack(tmp_path):
    """同一模組包就地翻譯重掃時，不應把譯文同步到其他版本。"""
    db = TranslationDB(tmp_path / "t.db")
    _import_pack_versions(
        db,
        tmp_path,
        {
            "pack82": [("Q1", "First Quest"), ("Q2", "Second Quest")],
            "pack85": [("Q1", "First Quest"), ("Q2", "Second Quest")],
        },
    )

    _write_files(
        tmp_path / "pack82",
        _quest_file([("Q1", "第一個任務"), ("Q2", "第二個任務")]),
    )
    report = _scan(db, tmp_path / "pack82")

    assert report.stats.new_entries == 0 and report.stats.en_changed == 0
    assert _translations_by_pack(tmp_path / "t.db") == {
        "pack82": {"First Quest": "第一個任務", "Second Quest": "第二個任務"},
        "pack85": {"First Quest": None, "Second Quest": None},
    }


def test_same_zip_reimport_without_translations_stays_a_normal_import(tmp_path):
    """同一個 ZIP 再匯入、內容不是譯文：照一般匯入，來源紀錄仍在。"""
    zpath = _make_zip(
        tmp_path / "p.zip",
        _pack_files("zh_tw", {"chapter.A.title": "FTB", "quest.B.title": "OK"}),
    )
    db = TranslationDB(tmp_path / "t.db")
    _scan(db, zpath)
    conn = sqlite3.connect(tmp_path / "t.db")
    count = conn.execute("select count(*) from entry_origin").fetchone()[0]
    assert count == 2

    again = _scan(db, zpath)

    assert again.stats.new_entries == 0 and again.stats.en_changed == 0
    assert conn.execute("select count(*) from entry_origin").fetchone()[0] == count
    assert not [
        i for i in again.issues if i["reason"] == "translation_without_original"
    ]


def test_translated_zip_skips_keys_missing_from_matched_pack(tmp_path):
    """對到既有模組包後，該模組包沒有的 key 不匯入：否則中文會被存成它的原文。"""
    db = TranslationDB(tmp_path / "t.db")
    orig = tmp_path / "orig"
    _import_english_pack(db, orig, {"chapter.A.title": "Hello", "quest.B.title": "Bye"})
    zpath = _make_zip(
        tmp_path / "zh.zip",
        _pack_files(
            "zh_tw",
            {
                "chapter.A.title": "你好",
                "quest.B.title": "再見",
                "quest.C.title": "新任務",
                "quest.D.title": "另一個",
            },
        ),
    )

    report = _scan(db, zpath)

    conn = sqlite3.connect(tmp_path / "t.db")
    rows = conn.execute("select en_us from entry order by key").fetchall()
    assert [r[0] for r in rows] == ["Hello", "Bye"]
    assert report.stats.new_entries == 0
    skipped = [
        i for i in report.issues if i["reason"] == "translation_without_original"
    ]
    assert len(skipped) == 1 and skipped[0]["detail"].startswith("2 筆")
    assert str(tmp_path / "moddb_pack_") not in skipped[0]["source_archive"]

    # 原模組包之後補上這個 key 的英文：正常新增，不會被先前的中文卡成原文已變動
    _import_english_pack(
        db,
        orig,
        {"chapter.A.title": "Hello", "quest.B.title": "Bye", "quest.C.title": "New"},
    )
    assert conn.execute("select count(*) from src_change").fetchone()[0] == 0
    assert conn.execute(
        "select en_us from entry where key like '%quest.C.title'"
    ).fetchall() == [("New",)]


def _quest_file(quests: list[tuple[str, str]]) -> dict[str, str]:
    body = ",".join(f'{{id:"{qid}",title:"{title}"}}' for qid, title in quests)
    return {"config/ftbquests/quests/chapters/a.snbt": f'{{id:"CH1",quests:[{body}]}}'}


def _import_pack_versions(db, tmp_path: Path, packs: dict[str, list[tuple[str, str]]]):
    for name, quests in packs.items():
        _write_files(tmp_path / name, _quest_file(quests))
        for _ in scan_folder_generator(
            db, tmp_path / name, ScanOptions("1.21.1", include_pack_content=True)
        ):
            pass


def _translations_by_pack(db_path: Path) -> dict[str, dict[str, str | None]]:
    """``{模組包資料夾名: {英文原文: 譯文}}``。"""
    out: dict[str, dict[str, str | None]] = {}
    conn = sqlite3.connect(db_path)
    for mod_id, en_us, zh_tw in conn.execute(
        "select e.mod_id, e.en_us, t.zh_tw from entry e "
        "left join translation t on t.entry_id=e.id"
    ):
        out.setdefault(mod_id.split("@")[1], {})[en_us] = zh_tw
    return out


def test_translated_zip_fills_every_pack_version_with_the_same_original(tmp_path):
    """同版本並存兩份內容相同的模組包：譯文兩份都補，不因平手而整批以新身分新增。"""
    db = TranslationDB(tmp_path / "t.db")
    quests = [("Q1", "First Quest"), ("Q2", "Second Quest")]
    _import_pack_versions(db, tmp_path, {"pack82": quests, "pack85": quests})
    zpath = _make_zip(
        tmp_path / "zh.zip", _quest_file([("Q1", "第一個任務"), ("Q2", "第二個任務")])
    )

    report = _scan(db, zpath)

    filled = {"First Quest": "第一個任務", "Second Quest": "第二個任務"}
    assert _translations_by_pack(tmp_path / "t.db") == {
        "pack82": filled,
        "pack85": filled,
    }
    assert report.stats.new_entries == 0 and report.stats.en_changed == 0
    assert not [i for i in report.issues if i["reason"].startswith("translation_")]


def test_translated_zip_fills_only_best_pack_where_originals_differ(tmp_path):
    """英文在兩份模組包不同的條目，只補進 key 相符最多的那一份，並列入問題。"""
    db = TranslationDB(tmp_path / "t.db")
    _import_pack_versions(
        db,
        tmp_path,
        {
            "pack82": [("Q1", "First Quest"), ("Q2", "Old wording")],
            "pack85": [
                ("Q1", "First Quest"),
                ("Q2", "New wording"),
                ("Q3", "Third Quest"),
            ],
        },
    )
    zpath = _make_zip(
        tmp_path / "zh.zip",
        _quest_file([("Q1", "第一個任務"), ("Q2", "新的說法"), ("Q3", "第三個任務")]),
    )

    report = _scan(db, zpath)

    assert _translations_by_pack(tmp_path / "t.db") == {
        "pack82": {"First Quest": "第一個任務", "Old wording": None},
        "pack85": {
            "First Quest": "第一個任務",
            "New wording": "新的說法",
            "Third Quest": "第三個任務",
        },
    }
    assert report.stats.en_changed == 0
    differs = [
        i for i in report.issues if i["reason"] == "translation_original_differs"
    ]
    assert len(differs) == 1 and differs[0]["detail"].startswith("1 筆")


def test_translated_zip_skips_differing_originals_when_packs_tie(tmp_path):
    """兩份模組包 key 相符數相同、英文又不同：不猜，該筆不補進任何一份。"""
    db = TranslationDB(tmp_path / "t.db")
    _import_pack_versions(
        db,
        tmp_path,
        {
            "pack82": [("Q1", "First Quest"), ("Q2", "Old wording")],
            "pack85": [("Q1", "First Quest"), ("Q2", "New wording")],
        },
    )
    zpath = _make_zip(
        tmp_path / "zh.zip", _quest_file([("Q1", "第一個任務"), ("Q2", "新的說法")])
    )

    report = _scan(db, zpath)

    assert _translations_by_pack(tmp_path / "t.db") == {
        "pack82": {"First Quest": "第一個任務", "Old wording": None},
        "pack85": {"First Quest": "第一個任務", "New wording": None},
    }
    assert report.stats.new_entries == 0 and report.stats.en_changed == 0
    assert [i["reason"] for i in report.issues if i["reason"].startswith("trans")] == [
        "translation_original_differs"
    ]


def test_unknown_original_is_only_filled_in_the_best_pack():
    """原文是空的條目無從比對：不因另一份有英文就跟著補，除非它在 key 相符最多的那一份。"""
    from translation_tool.translation_db.models import ScanItem
    from translation_tool.translation_db.pack_scope_match import (
        ScopeMatch,
        keep_existing_originals,
    )

    item = ScanItem(
        kind="ftb_quest_lang",
        mod_id="ftbquests@zh.zip@0",
        key="k",
        en_us="譯文",
        source_locale="zh_tw",
    )
    originals = {("ftb_quest_lang", "ftbquests", "k"): {"a@1": "", "b@2": "Text"}}

    def filled(best):
        match = ScopeMatch(("a@1", "b@2"), best, originals, 1, 1)
        adopted = keep_existing_originals([item], match)
        return sorted(i.mod_id for i in adopted.items), len(adopted.original_differs)

    assert filled("b@2") == (["ftbquests@b@2"], 1)
    assert filled("a@1") == (["ftbquests@a@1", "ftbquests@b@2"], 0)
    assert filled(None) == (["ftbquests@b@2"], 1)


def test_unsupported_kubejs_lang_members_remain_general_lang(tmp_path):
    """未被模組包抽取器支援的舊式／深層成員不可因排除規則而消失。"""
    zpath = _make_zip(
        tmp_path / "legacy.zip",
        {
            "overrides/kubejs/assets/legacy/lang/zh_tw.lang": "item.legacy=舊式翻譯\n",
            "a/b/c/d/e/kubejs/assets/deep/lang/zh_tw.json": json.dumps(
                {"item.deep": "深層翻譯"}, ensure_ascii=False
            ),
        },
    )

    result = scan_jar(
        zpath,
        ScanOptions("1.21.1", translated=True, include_pack_content=False),
        (),
    )

    assert {(item.mod_id, item.key, item.zh_tw) for item in result.items} == {
        ("legacy", "item.legacy", "舊式翻譯"),
        ("deep", "item.deep", "深層翻譯"),
    }


def test_translated_folder_does_not_pair_regular_english_with_kubejs_translation(
    tmp_path,
):
    folder = tmp_path / "translations"
    folder.mkdir()
    _make_zip(
        folder / "mixed.zip",
        {
            "assets/demo/lang/en_us.json": json.dumps({"item.same": "Regular English"}),
            "kubejs/assets/demo/lang/zh_tw.json": json.dumps(
                {"item.same": "KubeJS 翻譯"}, ensure_ascii=False
            ),
        },
    )
    db = TranslationDB(tmp_path / "t.db")

    for _ in scan_folder_generator(
        db,
        folder,
        ScanOptions("1.21.1", translated=True, include_pack_content=True),
        workers=1,
    ):
        pass

    rows = (
        sqlite3.connect(tmp_path / "t.db")
        .execute(
            "select e.en_us, coalesce(t.zh_tw, '') from entry e "
            "left join translation t on t.entry_id=e.id "
            "where e.kind='lang' and e.key='item.same'"
        )
        .fetchall()
    )
    assert rows == [("Regular English", "")]


def test_single_zip_without_pack_scanner_reads_kubejs_lang_as_regular_lang(tmp_path):
    zpath = _make_zip(
        tmp_path / "translations.zip",
        {
            "kubejs/assets/demo/lang/zh_tw.json": json.dumps(
                {"item.only": "只有翻譯"}, ensure_ascii=False
            )
        },
    )
    db = TranslationDB(tmp_path / "t.db")

    for _ in scan_folder_generator(
        db,
        zpath,
        ScanOptions("1.21.1", translated=True, include_pack_content=False),
        workers=1,
    ):
        pass

    row = (
        sqlite3.connect(tmp_path / "t.db")
        .execute(
            "select e.kind, e.en_us, t.zh_tw from entry e "
            "join translation t on t.entry_id=e.id where e.key='item.only'"
        )
        .fetchone()
    )
    assert row == ("lang", "", "只有翻譯")


def test_extract_stops_once_pack_budget_is_exhausted(tmp_path, monkeypatch):
    from translation_tool.translation_db import pack_archive
    from translation_tool.utils.zip_safety import ZipReadBudget

    zpath = _make_zip(
        tmp_path / "budget.zip",
        {
            "kubejs/a.js": "1234",
            "kubejs/b.js": "5678",
            "kubejs/c.js": "90ab",
        },
    )

    class SmallBudget:
        @classmethod
        def for_pack(cls, label):
            return ZipReadBudget(max_bytes=6, max_members=50, label=label)

    monkeypatch.setattr(pack_archive, "ZipReadBudget", SmallBudget)
    dest = tmp_path / "out"
    dest.mkdir()

    issues = pack_archive.extract_pack_members(zpath, dest)

    assert [issue["reason"] for issue in issues] == ["archive_budget"]
    assert {p.name for p in dest.rglob("*") if p.is_file()} == {"a.js"}


def test_pack_scan_cancelled_after_analysis_does_not_write(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    _write_files(
        tmp_path,
        _kubejs_files("Tooltip text", "First Quest", "Do the thing"),
    )
    state = {"cancel": False}

    def should_cancel():
        return state["cancel"]

    updates = []
    for update in scan_folder_generator(
        db,
        tmp_path,
        ScanOptions("1.21.1", include_pack_content=True),
        should_cancel=should_cancel,
    ):
        updates.append(update)
        if "模組包來源：開始分析" in update.get("log", ""):
            state["cancel"] = True

    assert updates[-1]["report"].cancelled is True
    assert db.count_entries() == 0
    assert db.last_scans() == []


def test_translated_kubejs_pack_prefers_direct_zh_tw_over_converted_zh_cn(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    zpath = _make_zip(
        tmp_path / "both-kubejs.zip",
        {
            "Pack/kubejs/assets/demo/lang/zh_cn.json": json.dumps(
                {"item.demo.widget": "简体"}, ensure_ascii=False
            ),
            "Pack/kubejs/assets/demo/lang/zh_tw.json": json.dumps(
                {"item.demo.widget": "繁體"}, ensure_ascii=False
            ),
        },
    )

    _scan(db, zpath)

    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute(
        "select t.zh_tw from entry e join translation t on t.entry_id=e.id "
        "where e.kind='kubejs_lang' and e.key='item.demo.widget'"
    ).fetchall() == [("繁體",)]


def test_mixed_pack_items_skip_translation_when_existing_original_differs(tmp_path):
    """譯文輸入自帶的原文與既有原文不同時，不把它的譯文掛到既有原文上。"""
    from translation_tool.translation_db.models import ScanItem
    from translation_tool.translation_db.pack_scope_match import (
        keep_existing_originals,
        match_existing_scope,
    )
    from translation_tool.translation_db.schema import (
        KIND_FTB_QUEST_LANG,
        KIND_KUBEJS_LANG,
    )

    db = TranslationDB(tmp_path / "mixed-pack.db")
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_FTB_QUEST_LANG,
                "ftbquests@existing",
                "chapter.snbt|lang:chapter.title",
                "FTB English",
            ),
            ScanItem(
                KIND_KUBEJS_LANG,
                "demo@existing",
                "item.demo.title",
                "Different English",
            ),
        ],
    )
    items = [
        ScanItem(
            KIND_FTB_QUEST_LANG,
            "ftbquests@incoming",
            "chapter.snbt|lang:chapter.title",
            "FTB English",
            source_locale="zh_tw",
        ),
        ScanItem(
            KIND_KUBEJS_LANG,
            "demo@incoming",
            "item.demo.title",
            "Input English",
            zh_tw="輸入翻譯",
            source_locale="en_us",
        ),
    ]

    match = match_existing_scope(db, "1.21.1", items)
    assert match is not None and match.best == "existing"
    adopted = keep_existing_originals(items, match)
    assert len(adopted.items) == 1
    assert adopted.items[0].kind == KIND_FTB_QUEST_LANG
    assert [item.kind for item in adopted.original_differs] == [KIND_KUBEJS_LANG]


def test_same_scope_rejects_translation_for_old_or_whitespace_original(tmp_path):
    """原文已變動時不補伴隨的譯文；空白原文視為未知，補上原文與譯文。"""
    from translation_tool.translation_db.models import ScanItem
    from translation_tool.translation_db.pack_sources import pack_scope_for_root
    from translation_tool.translation_db.schema import KIND_KUBEJS_LANG

    pack = tmp_path / "pack"
    scope = pack_scope_for_root(pack)
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_KUBEJS_LANG,
                f"demo@{scope}",
                "item.demo.existing",
                "Existing English",
            ),
            ScanItem(
                KIND_KUBEJS_LANG,
                f"demo@{scope}",
                "item.demo.old",
                "舊中文原文",
            ),
            ScanItem(
                KIND_KUBEJS_LANG,
                f"demo@{scope}",
                "item.demo.blank",
                "   ",
            ),
        ],
    )
    _write_files(
        pack,
        {
            "kubejs/assets/demo/lang/en_us.json": json.dumps(
                {
                    "item.demo.existing": "Existing English",
                    "item.demo.old": "New English",
                    "item.demo.blank": "Filled English",
                }
            ),
            "kubejs/assets/demo/lang/zh_tw.json": json.dumps(
                {
                    "item.demo.old": "不應附著",
                    "item.demo.blank": "填入翻譯",
                }
            ),
        },
    )

    list(
        scan_folder_generator(
            db,
            pack,
            ScanOptions("1.21.1", translated=True, include_pack_content=True),
        )
    )
    conn = sqlite3.connect(tmp_path / "t.db")
    assert conn.execute(
        "select e.en_us, t.zh_tw from entry e "
        "left join translation t on t.entry_id=e.id "
        "where e.key='item.demo.old'"
    ).fetchall() == [("舊中文原文", None)]
    assert conn.execute(
        "select e.en_us, t.zh_tw from entry e "
        "left join translation t on t.entry_id=e.id "
        "where e.key='item.demo.blank'"
    ).fetchall() == [("Filled English", "填入翻譯")]
