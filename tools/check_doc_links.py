"""檢查 Markdown 文件中的本地連結與錨點。

這是低依賴的 repository check，不會連線驗證外部 URL。它解析 README、根目錄
agent 指示文件與 docs/**/*.md 中的一般 inline Markdown links/images，並明確略過
fenced code block，避免把範例字串誤判成文件連結。
"""

from __future__ import annotations

import re
import sys
import unicodedata
from functools import cache
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
_INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]*>|[^\s)]+)")
_PROTOCOL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_EXPLICIT_ANCHOR = re.compile(r"\b(?:id|name)\s*=\s*[\"'](.*?)[\"']", re.IGNORECASE)
_ROOT_DOCUMENTS = ("README.md", "AGENTS.md", "CLAUDE.md")


def _without_fenced_code(text: str) -> list[str]:
    """保留行號、把 fenced code block 內容置空。"""
    result: list[str] = []
    fence_char: str | None = None
    fence_size = 0
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence_char is None:
            if match:
                marker = match.group(1)
                fence_char, fence_size = marker[0], len(marker)
                result.append("")
            else:
                result.append(line)
            continue

        if (
            match
            and match.group(1)[0] == fence_char
            and len(match.group(1)) >= fence_size
        ):
            fence_char = None
            fence_size = 0
        result.append("")
    return result


def _strip_inline_markup(text: str) -> str:
    text = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_~]", "", text)
    return re.sub(r"<[^>]*>", "", text)


def _github_slug(text: str) -> str:
    """產生足以檢查本專案標題的 GitHub-style slug。"""
    text = unicodedata.normalize("NFKC", _strip_inline_markup(text)).casefold()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"[\s-]+", "-", text).strip("-")


@cache
def _anchors(path: Path) -> frozenset[str]:
    anchors: list[str] = []
    seen: dict[str, int] = {}
    text = path.read_text(encoding="utf-8")
    for line in _without_fenced_code(text):
        heading = _HEADING.match(line)
        if heading:
            base = _github_slug(heading.group(1))
            if base:
                index = seen.get(base, 0)
                anchor = base if index == 0 else f"{base}-{index}"
                seen[base] = index + 1
                anchors.append(anchor)
        anchors.extend(_EXPLICIT_ANCHOR.findall(line))
    return frozenset(anchors)


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _resolve_target(document: Path, target: str, root: Path) -> tuple[Path, str]:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = unquote(target)
    path_part, separator, anchor = target.partition("#")
    path_part = path_part.split("?", 1)[0]
    if not path_part:
        candidate = document
    else:
        candidate = (document.parent / path_part).resolve()
    return candidate, unquote(anchor) if separator else ""


def _is_external(target: str) -> bool:
    return target.startswith("//") or bool(_PROTOCOL.match(target))


def _iter_documents(root: Path) -> list[Path]:
    documents = [root / name for name in _ROOT_DOCUMENTS if (root / name).is_file()]
    documents.extend(sorted((root / "docs").rglob("*.md")))
    return documents


def find_broken_links(root: Path = REPO_ROOT) -> list[str]:
    """回傳人類可讀的本地連結錯誤；空 list 代表檢查通過。"""
    root = root.resolve()
    problems: list[str] = []
    for document in _iter_documents(root):
        relative = _display_path(document, root)
        for line_number, line in enumerate(
            _without_fenced_code(document.read_text(encoding="utf-8")), 1
        ):
            for match in _INLINE_LINK.finditer(line):
                target = match.group(1).strip()
                if _is_external(target):
                    continue
                candidate, anchor = _resolve_target(document, target, root)
                try:
                    candidate.relative_to(root)
                except ValueError:
                    problems.append(
                        f"{relative}:{line_number}: link escapes repository: {target}"
                    )
                    continue
                if not candidate.exists():
                    problems.append(
                        f"{relative}:{line_number}: missing target: {target}"
                    )
                    continue
                if (
                    anchor
                    and candidate.is_file()
                    and candidate.suffix.lower() in {".md", ".mdx"}
                    and anchor.casefold()
                    not in {item.casefold() for item in _anchors(candidate)}
                ):
                    problems.append(
                        f"{relative}:{line_number}: missing anchor: {target}"
                    )
    return problems


def main() -> int:
    problems = find_broken_links()
    if problems:
        print("文件本地連結檢查失敗：", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("文件本地連結檢查通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
