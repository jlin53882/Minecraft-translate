"""文件與程式碼的一致性檢查。

- 文件中以 `app/...`、`translation_tool/...` 等反引號標示的程式碼路徑必須存在。
- `docs/page_flows/` 中反引號標示的函式、類別與屬性名稱必須存在於程式碼中。
- 維護文件只描述現況，不留 PR／issue 編號。

CHANGELOG 記錄的是「當時」的結構，不檢查；`docs/adr/` 是決策紀錄，允許引用 issue。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = {"CHANGELOG.md"}
PATH = re.compile(
    r"`((?:app|translation_tool|tools|tests)/[A-Za-z0-9_./\-]+?\.(?:py|md|json|bat))`"
)
BACKTICK = re.compile(r"`([^`\n]+)`")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PR_OR_ISSUE = re.compile(
    r"\bPR ?#?\d+|\bPR-[A-Z]\b|本 PR|\bissue ?#\d+|(?<![\w/&])#\d{2,4}\b", re.IGNORECASE
)


def _docs() -> list[Path]:
    found = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    return sorted(p for p in found if p.relative_to(ROOT).as_posix() not in HISTORICAL)


def _code_words() -> set[str]:
    words: set[str] = set()
    sources = [ROOT / "main.py"]
    for package in ("app", "translation_tool"):
        sources.extend((ROOT / package).rglob("*.py"))
    for path in sources:
        words.update(IDENTIFIER.findall(path.read_text(encoding="utf-8")))
    return words


def _documented_names(token: str) -> list[str]:
    """反引號內容中要對照程式碼的名稱；路徑、萬用字元與字首片段不檢查。"""
    token = token.strip()
    if any(ch in token for ch in "/\\*{ <>=") or token.endswith(("_", ".md", ".json")):
        return []
    head = token.split("(", 1)[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", head):
        return []
    return [part for part in head.split(".") if len(part) >= 4]


def test_documented_code_paths_exist():
    missing: list[str] = []
    for doc in _docs():
        for match in PATH.finditer(doc.read_text(encoding="utf-8")):
            rel = match.group(1)
            if not (ROOT / rel).exists():
                missing.append(f"{doc.relative_to(ROOT).as_posix()}: {rel}")
    assert missing == [], "文件引用了不存在的路徑：\n" + "\n".join(missing)


def test_page_flow_names_exist_in_code():
    words = _code_words()
    missing: list[str] = []
    for doc in sorted((ROOT / "docs" / "page_flows").glob("*.md")):
        for token in BACKTICK.findall(doc.read_text(encoding="utf-8")):
            for name in _documented_names(token):
                if name not in words:
                    missing.append(f"{doc.name}: {name}")
    assert missing == [], "流程文件提到程式中不存在的名稱：\n" + "\n".join(missing)


def test_maintenance_docs_have_no_pr_or_issue_numbers():
    docs = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]
    found: list[str] = []
    for doc in docs:
        rel = doc.relative_to(ROOT).as_posix()
        if rel.startswith("docs/adr/"):
            continue
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if PR_OR_ISSUE.search(line):
                found.append(f"{rel}:{lineno}: {line.strip()[:80]}")
    assert found == [], "維護文件只描述現況，請移除 PR／issue 編號：\n" + "\n".join(
        found
    )
