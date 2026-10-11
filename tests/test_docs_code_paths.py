"""文件中以 `app/...`、`translation_tool/...` 等反引號標示的程式碼路徑必須存在（#120）。

CHANGELOG 記錄的是「當時」的結構，不檢查。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = {"CHANGELOG.md"}
PATH = re.compile(
    r"`((?:app|translation_tool|tools|tests)/[A-Za-z0-9_./\-]+?\.(?:py|md|json|bat))`"
)


def _docs() -> list[Path]:
    found = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    return sorted(p for p in found if p.relative_to(ROOT).as_posix() not in HISTORICAL)


def test_documented_code_paths_exist():
    missing: list[str] = []
    for doc in _docs():
        for match in PATH.finditer(doc.read_text(encoding="utf-8")):
            rel = match.group(1)
            if not (ROOT / rel).exists():
                missing.append(f"{doc.relative_to(ROOT).as_posix()}: {rel}")
    assert missing == [], "文件引用了不存在的路徑：\n" + "\n".join(missing)
