"""產生 docs/EXCEPTION_INVENTORY.md：逐項列出所有寬鬆例外豁免（#135）。

每一列：位置（檔案:函式）｜規則｜分類｜處理方式。分類由例外處理的內容判定：

- 已記錄／回報：except 區塊內有 log、logger、show_snack、yield 錯誤、raise 等
- UI／畫面保護：區塊內只有 pass/continue/return 常數，且位於 UI 層（app/）
- 盡力而為（靜默）：區塊內只有 pass/continue/return 常數，位於引擎層
- 原因：行內 noqa 的說明文字（若有）

用法：``python tools/gen_exception_inventory.py``（覆寫文件）；
``tests/test_exception_inventory.py`` 檢查整份文件與 ``render(collect())`` 逐字一致。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "EXCEPTION_INVENTORY.md"
NOQA = re.compile(r"noqa:\s*([A-Z0-9, ]+?)(?:\s+-\s+(.*))?\s*$")
RULES = {"BLE001", "S110", "S112"}
LOG_HINTS = (
    "log_",
    "logger",
    "logging",
    "log.",
    "_logger",
    "show_snack",
    "print(",
    "yield",
    "raise",
    "add_log",
    "set_status",
    "_set_status",
    "report",
)


def _enclosing(tree: ast.AST) -> dict[int, str]:
    names: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                walk(child, qual)
            else:
                if isinstance(child, ast.ExceptHandler):
                    names[child.lineno] = prefix or "<module>"
                walk(child, prefix)

    walk(tree, "")
    return names


def collect() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    paths = [
        *(REPO_ROOT / "app").rglob("*.py"),
        *(REPO_ROOT / "translation_tool").rglob("*.py"),
    ]
    paths.append(REPO_ROOT / "main.py")
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        if "noqa" not in text:
            continue
        lines = text.splitlines()
        tree = ast.parse(text)
        handlers = {
            h.lineno: h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
        }
        owners = _enclosing(tree)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(lines, 1):
            m = NOQA.search(line)
            if not m:
                continue
            codes = {c.strip() for c in m.group(1).split(",")} & RULES
            if not codes:
                continue
            reason = (m.group(2) or "").strip()
            handler = handlers.get(lineno)
            if handler is None:
                kind = "（非 except 行）"
            else:
                body = "\n".join(lines[handler.body[0].lineno - 1 : handler.end_lineno])
                trivial = all(
                    isinstance(s, (ast.Pass, ast.Continue, ast.Break))
                    or (
                        isinstance(s, ast.Return)
                        and (
                            s.value is None
                            or isinstance(
                                s.value, (ast.Constant, ast.Dict, ast.List, ast.Tuple)
                            )
                        )
                    )
                    for s in handler.body
                )
                if trivial and not any(h in body for h in LOG_HINTS):
                    kind = (
                        "UI／畫面保護" if rel.startswith("app/") else "盡力而為（靜默）"
                    )
                else:
                    kind = "已記錄／回報"
            rows.append(
                {
                    "file": rel,
                    "line": str(lineno),
                    "func": owners.get(lineno, "<module>"),
                    "rules": "/".join(sorted(codes)),
                    "kind": kind,
                    "reason": reason or "（未寫原因；見分類）",
                }
            )
    return rows


def render(rows: list[dict[str, str]]) -> str:
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    unexplained = sum(1 for r in rows if r["reason"].startswith("（未寫原因"))
    out = [
        "# 寬鬆例外逐項盤點（#135）",
        "",
        "> 由 `tools/gen_exception_inventory.py` 產生；`tests/test_exception_inventory.py` 檢查整份文件與程式碼逐字一致（不含行號，避免普通修改造成漂移）。",
        "> 範圍：`app/`、`translation_tool/`、`main.py` 內所有帶 `noqa: BLE001／S110／S112` 的位置。",
        "> 命令列 QA 工具（`md_extract_qa.py`、`md_inject_qa.py`）的 `print` 為刻意保留，不在此表。",
        "",
        f"共 **{len(rows)}** 項；其中 **{unexplained}** 項尚未在程式碼內寫明原因（以「分類」說明處理方式）。",
        "",
        "| 分類 | 數量 | 意義 |",
        "|---|---|---|",
        f"| 已記錄／回報 | {by_kind.get('已記錄／回報', 0)} | 例外處理本身有 log、提示、回報錯誤事件或重新丟出；寬鬆捕捉是為了不中斷整批流程 |",
        f"| UI／畫面保護 | {by_kind.get('UI／畫面保護', 0)} | UI 層的畫面更新、icon 快取等；失敗只影響顯示，不影響資料 |",
        f"| 盡力而為（靜默） | {by_kind.get('盡力而為（靜默）', 0)} | 引擎層、只有 `pass`／`continue`／回傳常數；失敗不影響結果（例如進度回報、還原失敗時以原始例外為準） |",
        "",
        "| 位置 | 規則 | 分類 | 原因／處理 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| `{r['file']}:{r['func']}` | {r['rules']} | {r['kind']} | {r['reason']} |"
        )
    out.append("")
    return "\n".join(out)


def main() -> int:
    rows = collect()
    OUT.write_text(render(rows), encoding="utf-8")
    print(f"寫入 {OUT}（{len(rows)} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
