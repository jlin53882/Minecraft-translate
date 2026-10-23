"""Patchouli verification script: path layouts and exit-status contract."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools.verify_patchouli_final import REPORT_NAME, _patchouli_identity, main


def _write_source(path: Path, *, text: str = "繁體") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "assets/example/patchouli_books/guide/zh_cn/categories/start.json",
            json.dumps({"title": "標題"}, ensure_ascii=False),
        )
        archive.writestr(
            "assets/example/patchouli_books/guide/zh_cn/entries/a.json",
            json.dumps({"title": text}, ensure_ascii=False),
        )


def _write_output(
    root: Path, *, title: str = "標題", include_entry: bool = True
) -> None:
    base = (
        root
        / "patchouli_output"
        / "patchouli_books"
        / "assets"
        / "example"
        / "patchouli_books"
        / "guide"
        / "zh_tw"
    )
    base.mkdir(parents=True)
    (base / "categories").mkdir()
    (base / "categories" / "start.json").write_text(
        json.dumps({"title": title}, ensure_ascii=False), encoding="utf-8"
    )
    if include_entry:
        (base / "entries").mkdir()
        (base / "entries" / "a.json").write_text(
            json.dumps({"title": "內容"}, ensure_ascii=False), encoding="utf-8"
        )


def test_patchouli_identity_accepts_zip_and_output_layouts():
    assert _patchouli_identity(
        "assets/mod/patchouli_books/book/zh_cn/categories/a.json", "zh_cn"
    ) == ("mod", "book", "zh_cn/categories/a.json")
    assert _patchouli_identity(
        "extract-root/assets/mod/patchouli_books/book/zh_tw/categories/a.json",
        "zh_tw",
    ) == ("mod", "book", "zh_tw/categories/a.json")


def test_verify_patchouli_returns_nonzero_for_content_issues(tmp_path):
    source = tmp_path / "book.zip"
    output = tmp_path / "out"
    _write_source(source)
    _write_output(output, title="错", include_entry=False)

    assert main(["--book-zip", str(source), "--out", str(output)]) == 1
    report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
    assert report["cjk_issues"] or report["missing_in_output"]


def test_verify_patchouli_returns_zero_for_matching_files(tmp_path):
    source = tmp_path / "book.zip"
    output = tmp_path / "out"
    _write_source(source)
    _write_output(output)

    assert main(["--book-zip", str(source), "--out", str(output)]) == 0
