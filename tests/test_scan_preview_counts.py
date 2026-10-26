"""掃描「先預覽」與「開始掃描」的 KPI 必須一致：預覽用同一套分類規則，只是不寫入。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict
from pathlib import Path

from translation_tool.translation_db.models import ScanItem
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator
from translation_tool.translation_db.schema import KIND_LANG

VERSION = "1.21.1"


def _jar(path: Path, files: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))


def _scan(db, folder: Path, *, dry_run: bool):
    updates = list(
        scan_folder_generator(
            db, folder, ScanOptions(VERSION, dry_run=dry_run), workers=1
        )
    )
    return updates[-1]["report"], updates


def _seed(db: TranslationDB, tmp_path: Path) -> None:
    """既有資料：a、b（b 已有模組繁中）、c 只有翻譯 ZIP 的繁中（原文未知）、d 原文舊版。"""
    first = tmp_path / "first"
    _jar(
        first / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {
                "item.foo.a": "Steel Casing",
                "item.foo.b": "Infused Alloy",
                "item.foo.d": "Old Text",
            },
            "assets/foo/lang/zh_tw.json": {"item.foo.b": "注入合金"},
        },
    )
    _scan(db, first, dry_run=False)
    db.ingest(VERSION, [ScanItem(KIND_LANG, "foo", "item.foo.c", "", "翻譯包譯文", "")])


def _second_folder(tmp_path: Path) -> Path:
    """第二次掃描：涵蓋每一種分類，且同一個模組分散在兩個 jar（後者要看到前者新增的條目）。"""
    folder = tmp_path / "second"
    _jar(
        folder / "foo-1.jar",
        {
            "assets/foo/lang/en_us.json": {
                "item.foo.a": "Steel Casing",  # 已存在，沒有新譯文 → 略過
                "item.foo.b": "Infused Alloy",  # 已存在，同來源譯文已有 → 略過
                "item.foo.c": "Copper Wire",  # 原文原本未知 → 補上原文
                "item.foo.d": "New Text",  # 原文已變動
                "item.foo.e": "Brand New",  # 新增
            },
            "assets/foo/lang/zh_tw.json": {
                "item.foo.a": "鋼製外殼",  # a 補入新來源譯文
                "item.foo.e": "全新",
            },
        },
    )
    _jar(
        folder / "foo-2.jar",
        {
            "assets/foo/lang/en_us.json": {
                # 另一個 jar 也有 e：先處理的算新增，後處理的看到它已存在 → 略過
                # （jar 平行讀取、完成順序不固定，所以兩邊帶相同譯文，結果才與順序無關）
                "item.foo.e": "Brand New",
                "item.foo.f": "Another",  # 新增
            },
            "assets/foo/lang/zh_tw.json": {"item.foo.e": "全新"},
            "assets/foo/lang/zh_cn.json": {"item.foo.f": "另一个"},
        },
    )
    return folder


def test_preview_counts_match_the_real_scan(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    try:
        _seed(db, tmp_path)
        folder = _second_folder(tmp_path)
        entries_before = db.count_entries()

        preview, preview_updates = _scan(db, folder, dry_run=True)
        assert db.count_entries() == entries_before  # 預覽不寫入

        real, real_updates = _scan(db, folder, dry_run=False)
        assert asdict(preview.stats) == asdict(real.stats)
        assert preview.stats.new_entries == 2
        assert preview.stats.en_changed == 1 and preview.stats.adopted == 1
        assert preview.stats.added_translations >= 1 and preview.stats.existing >= 1
        assert preview.items_found == real.items_found

        # 最後一行摘要使用相同的欄位與用語，只差「預覽／未寫入」
        preview_line = preview_updates[-1]["log"]
        real_line = real_updates[-1]["log"]
        for label in ("新增", "補入", "略過", "補上原文", "原文已變動"):
            assert label in preview_line and label in real_line
        assert "未寫入" in preview_line and "未寫入" not in real_line
    finally:
        db.close()


def test_preview_without_a_database_counts_everything_as_new(tmp_path):
    folder = _second_folder(tmp_path)
    report, _ = _scan(None, folder, dry_run=True)
    # e 在兩個 jar 重複出現：和實際寫入一樣只算一次新增
    assert report.stats.new_entries == 6
    assert report.stats.existing == 1
