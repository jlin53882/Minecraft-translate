"""架構指標：統計 ``app/`` 與 ``translation_tool/`` 每個模組的結構健康度。

指標（每個模組）：

- ``max_complexity``：模組內最大函式複雜度（近似 cyclomatic，算法與
  ``tests/test_view_size_limits.py`` 相同，並由 ``tests/test_architecture_metrics.py`` 比對）。
- ``public_api``：公開 API 數量（有 ``__all__`` 以其長度為準，否則為不以底線開頭的頂層 class／函式）。
- ``class_methods``：所有 class 本體直接定義的方法總數。
- ``import_depth``：模組層級 import 圖的最長路徑（邊數；循環以強連通分量收斂後計算）。
- ``top_level_defs``：頂層 class／函式數量（責任數量的近似）。
- ``annotation_coverage``：參數（不含 ``self``／``cls``）與回傳值的型別註解覆蓋率（0–1）。

Import 圖只計模組層級的 import（含 ``try``／``if`` 區塊，不含 ``if TYPE_CHECKING:`` 與函式內延遲 import），
因為只有它們會在載入時形成循環。

用法::

    python tools/architecture_metrics.py                  # 印出摘要與警告
    python tools/architecture_metrics.py --json           # 以 JSON 輸出完整指標
    python tools/architecture_metrics.py --write-baseline # 覆寫 tests/data/architecture_baseline.json

``tests/test_architecture_metrics.py`` 只對「新的循環 import」與「新增的超高複雜度函式」失敗，
其餘變差只輸出警告。
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("app", "translation_tool")
BASELINE = REPO_ROOT / "tests" / "data" / "architecture_baseline.json"

FAIL_COMPLEXITY = 20

_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.comprehension,
)
_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class ModuleMetrics:
    """單一模組的指標；欄位語意見模組說明。"""

    path: str
    max_complexity: int
    public_api: int
    class_methods: int
    import_depth: int
    top_level_defs: int
    annotation_coverage: float | None


def complexity(func: ast.AST) -> int:
    """近似 cyclomatic complexity（與 ``tests/test_view_size_limits.py`` 相同）。"""
    score = 1
    for node in ast.walk(func):
        if isinstance(node, _BRANCH_NODES):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
    return score


def iter_functions(tree: ast.AST):
    """逐一產生 ``(限定名稱, 節點)``；如 ``Class.method``、``outer.inner``。"""
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNC_NODES):
                qual = f"{prefix}{child.name}"
                yield qual, child
                stack.append((child, f"{qual}."))
            elif isinstance(child, ast.ClassDef):
                stack.append((child, f"{prefix}{child.name}."))
            else:
                stack.append((child, prefix))


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _module_level_imports(body: list[ast.stmt]):
    """產生模組載入時會執行的 import 節點；略過函式、class 與 TYPE_CHECKING 區塊。"""
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            yield stmt
        elif isinstance(stmt, ast.If):
            if not _is_type_checking(stmt.test):
                yield from _module_level_imports(stmt.body)
            yield from _module_level_imports(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            for block in (stmt.body, stmt.orelse, stmt.finalbody):
                yield from _module_level_imports(block)
            for handler in stmt.handlers:
                yield from _module_level_imports(handler.body)
        elif isinstance(stmt, ast.With):
            yield from _module_level_imports(stmt.body)


def _resolve_imports(
    name: str,
    is_package: bool,
    body: list[ast.stmt],
    known: set[str],
) -> set[str]:
    """把模組層級 import 解析成專案內的模組名稱集合（不含自己）。"""
    package = name if is_package else name.rpartition(".")[0]
    edges: set[str] = set()
    for node in _module_level_imports(body):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        else:
            if node.level:
                base_parts = package.split(".") if package else []
                if node.level > 1:
                    base_parts = base_parts[: len(base_parts) - (node.level - 1)]
                base = ".".join(base_parts)
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            targets = [module]
            targets += [f"{module}.{alias.name}" for alias in node.names]
        for target in targets:
            if target in known and target != name:
                edges.add(target)
    return edges


def _scan_modules() -> dict[str, tuple[Path, ast.Module]]:
    modules: dict[str, tuple[Path, ast.Module]] = {}
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            modules[_module_name(path)] = (path, tree)
    return modules


def build_import_graph(
    modules: dict[str, tuple[Path, ast.Module]],
) -> dict[str, set[str]]:
    """回傳模組層級 import 圖：``模組 -> 它直接 import 的專案內模組``。"""
    known = set(modules)
    return {
        name: _resolve_imports(name, path.name == "__init__.py", tree.body, known)
        for name, (path, tree) in modules.items()
    }


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan（迭代版）；回傳所有強連通分量，每個分量內的模組已排序。"""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    for start in sorted(graph):
        if start in index_of:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index_of[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph[child]))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                result.append(sorted(component))
    return result


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """循環 import：成員多於一個、或自己 import 自己的強連通分量。"""
    return sorted(
        component
        for component in strongly_connected_components(graph)
        if len(component) > 1 or component[0] in graph[component[0]]
    )


def import_depths(graph: dict[str, set[str]]) -> dict[str, int]:
    """每個模組的最長 import 路徑（邊數）；循環先收斂成單一節點，避免無限遞迴。"""
    components = strongly_connected_components(graph)
    owner = {m: i for i, comp in enumerate(components) for m in comp}
    condensed: dict[int, set[int]] = {i: set() for i in range(len(components))}
    for module, targets in graph.items():
        for target in targets:
            if owner[module] != owner[target]:
                condensed[owner[module]].add(owner[target])
    depth: dict[int, int] = {}
    # Tarjan 以反向拓撲序（被依賴者先完成）產生分量，所以依序計算即可。
    for i in range(len(components)):
        depth[i] = max((depth[c] + 1 for c in condensed[i]), default=0)
    return {module: depth[owner[module]] for module in graph}


def _annotation_coverage(tree: ast.Module) -> float | None:
    total = annotated = 0
    for _qual, func in iter_functions(tree):
        args = func.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            params.append(args.vararg)
        if args.kwarg:
            params.append(args.kwarg)
        if params and params[0].arg in {"self", "cls"}:
            params = params[1:]
        total += len(params) + 1
        annotated += sum(1 for p in params if p.annotation is not None)
        annotated += 1 if func.returns is not None else 0
    return round(annotated / total, 3) if total else None


def _public_api(tree: ast.Module) -> int:
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets)
            and isinstance(stmt.value, (ast.List, ast.Tuple))
        ):
            return len(stmt.value.elts)
    return sum(
        1
        for stmt in tree.body
        if isinstance(stmt, (*_FUNC_NODES, ast.ClassDef))
        and not stmt.name.startswith("_")
    )


def _class_methods(tree: ast.Module) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, _FUNC_NODES)
    )


def collect() -> tuple[dict[str, ModuleMetrics], list[list[str]], dict[str, int]]:
    """回傳 ``(每模組指標, 循環 import, 函式複雜度 > 20 的項目)``。

    複雜度項目的鍵為 ``路徑:限定名稱``（不含行號，一般的程式碼移動不會改變它）。
    """
    modules = _scan_modules()
    graph = build_import_graph(modules)
    depths = import_depths(graph)
    metrics: dict[str, ModuleMetrics] = {}
    high: dict[str, int] = {}
    for name, (path, tree) in modules.items():
        rel = path.relative_to(REPO_ROOT).as_posix()
        worst = 0
        for qual, func in iter_functions(tree):
            cx = complexity(func)
            worst = max(worst, cx)
            if cx > FAIL_COMPLEXITY:
                key = f"{rel}:{qual}"
                high[key] = max(high.get(key, 0), cx)
        metrics[name] = ModuleMetrics(
            path=rel,
            max_complexity=worst,
            public_api=_public_api(tree),
            class_methods=_class_methods(tree),
            import_depth=depths[name],
            top_level_defs=sum(
                isinstance(s, (*_FUNC_NODES, ast.ClassDef)) for s in tree.body
            ),
            annotation_coverage=_annotation_coverage(tree),
        )
    return metrics, find_cycles(graph), dict(sorted(high.items()))


def snapshot() -> dict:
    """可寫入基線檔的完整快照。"""
    metrics, cycles, high = collect()
    return {
        "import_cycles": cycles,
        "high_complexity": high,
        "modules": {
            name: {k: v for k, v in asdict(m).items() if k != "path"}
            for name, m in sorted(metrics.items())
        },
    }


def load_baseline() -> dict:
    if not BASELINE.is_file():
        return {"import_cycles": [], "high_complexity": {}, "modules": {}}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def write_baseline(data: dict | None = None) -> None:
    BASELINE.write_text(
        json.dumps(data or snapshot(), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def new_cycles(current: list[list[str]], baseline: list[list[str]]) -> list[list[str]]:
    """不是已記錄循環子集合的循環（成員增加或全新的循環都算變差）。"""
    recorded = [set(c) for c in baseline]
    return [c for c in current if not any(set(c) <= old for old in recorded)]


def new_high_complexity(
    current: dict[str, int], baseline: dict[str, int]
) -> dict[str, int]:
    """複雜度 > 20 且不在基線內的新函式。"""
    return {k: v for k, v in current.items() if k not in baseline}


# 越大越差／越小越差的指標；變差只警告。
_WORSE_WHEN_HIGHER = (
    "max_complexity",
    "public_api",
    "class_methods",
    "import_depth",
    "top_level_defs",
)


def regression_warnings(
    current: dict[str, ModuleMetrics], baseline_modules: dict[str, dict]
) -> list[str]:
    """與基線比較，列出變差的指標（只供警告；新模組沒有基線不比較）。"""
    warnings_: list[str] = []
    for name, m in sorted(current.items()):
        old = baseline_modules.get(name)
        if old is None:
            continue
        for field in _WORSE_WHEN_HIGHER:
            if getattr(m, field) > old.get(field, 0):
                warnings_.append(
                    f"{name}: {field} {old.get(field, 0)} → {getattr(m, field)}"
                )
        old_cov, cov = old.get("annotation_coverage"), m.annotation_coverage
        if old_cov is not None and cov is not None and cov < old_cov:
            warnings_.append(f"{name}: annotation_coverage {old_cov} → {cov}")
    return warnings_


def _summary_lines(top: int = 10) -> list[str]:
    metrics, cycles, high = collect()
    lines = [
        (
            f"模組數：{len(metrics)}；循環 import：{len(cycles)} 組；"
            f"複雜度 > {FAIL_COMPLEXITY} 的函式：{len(high)} 個"
        )
    ]
    for label, field in (
        ("複雜度最高", "max_complexity"),
        ("import 深度最深", "import_depth"),
        ("頂層責任最多", "top_level_defs"),
        ("公開 API 最多", "public_api"),
    ):
        ranked = sorted(
            metrics.items(), key=lambda item: (-getattr(item[1], field), item[0])
        )[:top]
        lines.append(f"\n[{label}]")
        lines.extend(f"  {getattr(m, field):>4}  {name}" for name, m in ranked)
    covered = [
        m.annotation_coverage
        for m in metrics.values()
        if m.annotation_coverage is not None
    ]
    if covered:
        lines.append(f"\n型別註解平均覆蓋率：{sum(covered) / len(covered):.1%}")
    if cycles:
        lines.append("\n[循環 import]")
        lines.extend("  " + " <-> ".join(c) for c in cycles)
    return lines


def main(argv: list[str] | None = None) -> int:
    """命令列入口：預設印出摘要；``--json`` 輸出完整指標；``--write-baseline`` 覆寫基線。"""
    args = sys.argv[1:] if argv is None else argv
    if "--write-baseline" in args:
        write_baseline()
        print(f"已寫入 {BASELINE}")
        return 0
    if "--json" in args:
        print(json.dumps(snapshot(), ensure_ascii=False, indent=1))
        return 0
    print("\n".join(_summary_lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
