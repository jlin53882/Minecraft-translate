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
