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
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator

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
