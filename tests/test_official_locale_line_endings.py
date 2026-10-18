"""官方語系檔以位元組驗 sha256，checkout 時不可被換行轉換改動。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALE_DIR = ROOT / "assets" / "official_locales"


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="需要 git 與 git 工作目錄才能讀取 .gitattributes 的實際套用結果",
)
def test_official_locales_are_not_subject_to_eol_conversion():
    files = [p.relative_to(ROOT).as_posix() for p in LOCALE_DIR.rglob("*.json")]
    assert files
    result = subprocess.run(
        ["git", "check-attr", "text", "--", *files],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        assert line.endswith(": text: unset"), line


def test_official_locales_on_disk_have_no_crlf():
    for path in LOCALE_DIR.rglob("*.json"):
        assert b"\r\n" not in path.read_bytes(), path.name
