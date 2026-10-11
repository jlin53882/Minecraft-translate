"""文件與程式碼的一致性檢查。

- 文件中以 `app/...`、`translation_tool/...` 等反引號標示的程式碼路徑必須存在。
- 文件之間的連結（Markdown 連結與反引號標示的 `*.md` 檔名）必須指向存在的檔案。
- `docs/page_flows/<key>.md` 中反引號標示的名稱必須存在於「該頁面的程式碼範圍或共用層」；
  只認程式碼本身，註解與 docstring 裡的字不算。
- 文件中的 `Class.member` 必須是該類別（或其父類別）真的有的成員。
- 維護文件只描述現況，不留 PR／issue 編號。

CHANGELOG 記錄的是「當時」的結構，不檢查；`docs/adr/` 是決策紀錄，允許引用 issue。
"""

from __future__ import annotations

import ast
import keyword
import re
from functools import cache
from pathlib import Path

import flet as ft

from tests.test_view_docs_identifiers import DOC_SCOPES, SHARED_SCOPE

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = {"CHANGELOG.md"}
PATH = re.compile(
    r"`((?:app|translation_tool|tools|tests)/[A-Za-z0-9_./\-]+?\.(?:py|md|json|bat))`"
)
BACKTICK = re.compile(r"`([^`\n]+)`")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
MD_NAME = re.compile(r"[A-Za-z0-9_./\-]+\.md")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PR_OR_ISSUE = re.compile(
    r"\bPR ?#?\d+|\bPR-[A-Z]\b|本 PR|\bissue ?#\d+|(?<![\w/&])#\d{2,4}\b", re.IGNORECASE
)
CODE_GLOBS = ("main.py", "app/**/*.py", "translation_tool/**/*.py")

# 流程文件 key → 對應 View 架構文件的範圍；沒有架構文件的頁面直接列出
PAGE_SCOPES: dict[str, tuple[str, ...]] = {
    "dashboard": ("app/views/dashboard_view.py", "app/views/dashboard/**/*.py"),
    "moddb": ("app/views/moddb_view.py", "app/views/moddb/**/*.py"),
    "pipeline": DOC_SCOPES["PIPELINE_VIEW_ARCHITECTURE.md"],
    "extractor": DOC_SCOPES["EXTRACTOR_VIEW_ARCHITECTURE.md"],
    "merge": DOC_SCOPES["MERGE_VIEW_ARCHITECTURE.md"],
    "lm": DOC_SCOPES["LM_VIEW_ARCHITECTURE.md"],
    "translation": DOC_SCOPES["TRANSLATION_VIEW_ARCHITECTURE.md"],
    "qc": DOC_SCOPES["QC_VIEW_ARCHITECTURE.md"],
    "icon_preview": DOC_SCOPES["ICON_VIEW_ARCHITECTURE.md"],
    "cache": DOC_SCOPES["CACHE_VIEW_ARCHITECTURE.md"],
    "rules": DOC_SCOPES["RULES_VIEW_ARCHITECTURE.md"],
    "lookup": DOC_SCOPES["LOOKUP_VIEW_ARCHITECTURE.md"],
    "bundler": DOC_SCOPES["BUNDLER_VIEW_ARCHITECTURE.md"],
    "config": DOC_SCOPES["CONFIG_VIEW_ARCHITECTURE.md"],
}
# 每份流程文件都可以引用：導覽外殼、頁面註冊表與共用層
PAGE_SHARED = (*SHARED_SCOPE, "app/view_registry.py")


def _docs() -> list[Path]:
    found = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    return sorted(p for p in found if p.relative_to(ROOT).as_posix() not in HISTORICAL)


def _files(globs: tuple[str, ...]) -> list[Path]:
    return sorted({p for g in globs for p in ROOT.glob(g) if p.is_file()})


@cache
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        first = body[0] if isinstance(body, list) and body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            ids.add(id(first.value))
    return ids


@cache
def _code_names(path: Path) -> frozenset[str]:
    """程式碼中出現的名稱（不含註解與 docstring）；字串常數內的識別字（設定鍵等）也算。"""
    tree = _tree(path)
    docstrings = _docstring_ids(tree)
    names = {path.stem}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg) or isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.update((node.asname or node.name).split("."))
            names.update(node.name.split("."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            names.update(IDENTIFIER.findall(node.value))
    return frozenset(names)


def _scope_names(globs: tuple[str, ...]) -> set[str]:
    names: set[str] = set()
    for path in _files(globs):
        names |= _code_names(path)
    return names


def _documented_names(token: str) -> list[str]:
    """反引號內容中要對照程式碼的名稱；路徑、萬用字元與字首片段不檢查。"""
    token = token.strip()
    if any(ch in token for ch in "/\\*{ <>=") or token.endswith(("_", ".md", ".json")):
        return []
    head = token.split("(", 1)[0].removeprefix("self.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", head):
        return []
    parts = head.split(".")
    return [p for p in parts if len(p) >= 4 and not keyword.iskeyword(p)]


# ---------------------------------------------------------------- 類別成員


@cache
def _classes() -> dict[str, list[ast.ClassDef]]:
    found: dict[str, list[ast.ClassDef]] = {}
    for path in _files(CODE_GLOBS):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                found.setdefault(node.name, []).append(node)
    return found


def _own_members(cls: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            members.add(node.name)
        elif isinstance(node, ast.Assign):
            members.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members.add(node.target.id)
    for node in ast.walk(cls):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    members.add(target.attr)
    return members


def _has_member(
    class_name: str, member: str, seen: frozenset[str] = frozenset()
) -> bool:
    """類別或其父類別有這個成員；父類別無法解析（第三方）時視為無法判定、不擋。"""
    for cls in _classes().get(class_name, []):
        if member in _own_members(cls):
            return True
        for base in cls.bases:
            if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                if base.value.id == "ft":
                    if hasattr(getattr(ft, base.attr, None), member):
                        return True
                    continue
                return True  # 其他第三方基底：無法判定
            if isinstance(base, ast.Name):
                if base.id in _classes() and base.id not in seen:
                    if _has_member(base.id, member, seen | {class_name}):
                        return True
                elif base.id not in _classes() and base.id != "object":
                    return True  # 標準函式庫或第三方基底：無法判定
    return False


# ---------------------------------------------------------------- 測試


def test_documented_code_paths_exist():
    missing: list[str] = []
    for doc in _docs():
        for match in PATH.finditer(doc.read_text(encoding="utf-8")):
            rel = match.group(1)
            if not (ROOT / rel).exists():
                missing.append(f"{doc.relative_to(ROOT).as_posix()}: {rel}")
    assert missing == [], "文件引用了不存在的路徑：\n" + "\n".join(missing)


def _md_targets(token: str) -> bool:
    """反引號內要檢查的文件名稱：裸檔名（`GH_WORKFLOW.md`）或 `docs/` 路徑；產出物路徑不算。"""
    return bool(MD_NAME.fullmatch(token)) and (
        "/" not in token or token.startswith("docs/")
    )


def test_doc_links_point_to_existing_files():
    missing: list[str] = []
    for doc in _docs():
        rel = doc.relative_to(ROOT).as_posix()
        text = doc.read_text(encoding="utf-8")
        for target in MD_LINK.findall(BACKTICK.sub("", text)):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            if path and not (doc.parent / path).exists():
                missing.append(f"{rel}: ({target})")
        for token in filter(_md_targets, BACKTICK.findall(text)):
            if not any((b / token).exists() for b in (doc.parent, ROOT / "docs", ROOT)):
                missing.append(f"{rel}: `{token}`")
    assert missing == [], "文件連到不存在的文件：\n" + "\n".join(missing)


def test_every_page_flow_has_a_code_scope():
    keys = {p.stem for p in (ROOT / "docs" / "page_flows").glob("*.md")} - {"README"}
    assert keys == set(PAGE_SCOPES), "流程文件與 PAGE_SCOPES 不一致"
    for key, globs in PAGE_SCOPES.items():
        assert _files(globs), f"{key} 的範圍沒有對應檔案"


def test_page_flow_names_exist_in_their_page_scope():
    shared = _scope_names(PAGE_SHARED)
    missing: list[str] = []
    for key, globs in sorted(PAGE_SCOPES.items()):
        own = _scope_names(globs)
        doc = ROOT / "docs" / "page_flows" / f"{key}.md"
        for token in BACKTICK.findall(doc.read_text(encoding="utf-8")):
            for name in _documented_names(token):
                if name not in own and name not in shared:
                    missing.append(f"{doc.name}: {name}")
    assert missing == [], (
        "流程文件提到「該頁程式範圍或共用層」裡不存在的名稱"
        "（只在註解／docstring 或別的頁面出現也算不存在）：\n" + "\n".join(missing)
    )


def test_page_flow_scope_ignores_comments_docstrings_and_other_pages():
    """防止退化：別頁的方法、或只出現在 docstring 的字，不能讓流程文件通過。"""
    cache_scope = _scope_names(PAGE_SCOPES["cache"]) | _scope_names(PAGE_SHARED)
    assert "will_unmount" in _code_names(ROOT / "app" / "views" / "lm_view.py")
    assert "will_unmount" in (ROOT / "app" / "ui" / "poller.py").read_text("utf-8")
    assert "will_unmount" not in cache_scope


def test_documented_class_members_exist():
    classes = _classes()
    missing: list[str] = []
    for doc in _docs():
        rel = doc.relative_to(ROOT).as_posix()
        for token in BACKTICK.findall(doc.read_text(encoding="utf-8")):
            head = token.strip().split("(", 1)[0]
            match = re.fullmatch(
                r"([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", head
            )
            if (
                match
                and match.group(1) in classes
                and not re.fullmatch(
                    r"_?x+", match.group(2)
                )  # `CacheView._xxx` 這類示意
                and not _has_member(match.group(1), match.group(2))
            ):
                missing.append(f"{rel}: `{head}`")
    assert missing == [], "文件提到類別沒有的成員：\n" + "\n".join(missing)


def test_class_member_check_detects_missing_members():
    assert _has_member("PipelineConfig", "translate_output_dir")
    assert not _has_member("PipelineConfig", "will_unmount")
    assert _has_member("CacheView", "did_mount")
    assert _has_member("CacheView", "update")  # 來自 ft.Column


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
