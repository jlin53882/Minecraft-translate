"""tools/ 下的獨立腳本：``--help`` 只印說明，不掃描、不寫檔，也不含個人路徑。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    "__code_scan.py",
    "analyze_all.py",
    "gap_analysis.py",
    "test_all_features.py",
    "test_main.py",
    "verify_patchouli_final.py",
]


@pytest.mark.parametrize("name", SCRIPTS)
def test_help_has_no_side_effects(name, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / name), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout
    assert list(tmp_path.iterdir()) == [], "--help 不應產生任何檔案"


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_do_not_hardcode_personal_paths(name):
    text = (ROOT / "tools" / name).read_text(encoding="utf-8")
    assert "C:\\Users\\admin" not in text and r"C:\Users" not in text
