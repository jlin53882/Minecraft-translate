"""View 架構文件中以反引號標示的程式碼識別字必須仍存在於程式碼（#120）。

只檢查「長得像識別字」的片段（含底線或駝峰、至少 5 個字元）；像 ``max_lines=2000`` 這種帶值的不檢查。
過期的方法／屬性名稱會讓文件誤導維護者，所以在 CI 擋下來。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*_VIEW_ARCHITECTURE.md")) + [
    ROOT / "docs" / "UNTRANSLATED_CHECKER_ARCHITECTURE.md"
]
# 文件刻意提到、但不是程式識別字的詞（例如設定鍵、舊名稱的說明）
NOT_CODE: set[str] = set()


def _code_identifiers() -> set[str]:
    words: set[str] = set()
    paths = (
        list((ROOT / "app").rglob("*.py"))
        + list((ROOT / "translation_tool").rglob("*.py"))
        + [ROOT / "main.py"]
    )
    for path in paths:
        words.update(
            re.findall(r"[A-Za-z_][A-Za-z0-9_]*", path.read_text(encoding="utf-8"))
        )
    return words


def _candidates(text: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"`([^`\n]+)`", text):
        token = re.sub(r"\(.*\)$", "", token.strip()).replace("self.", "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", token):
            continue
        word = token.split(".")[-1]
        if len(word) >= 5 and ("_" in word or re.search(r"[a-z][A-Z]", word)):
            out.add(token)
    return out


def test_view_docs_only_mention_existing_identifiers():
    identifiers = _code_identifiers()
    problems: list[str] = []
    for doc in DOCS:
        for token in sorted(_candidates(doc.read_text(encoding="utf-8"))):
            if token in NOT_CODE:
                continue
            if token.split(".")[-1] not in identifiers:
                problems.append(f"{doc.name}: `{token}`")
    assert problems == [], "文件提到了程式碼裡不存在的識別字：\n" + "\n".join(problems)
