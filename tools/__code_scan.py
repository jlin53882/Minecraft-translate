import ast, os, json
from pathlib import Path

ROOT = Path(r"C:\Users\admin\Desktop\minecraft_translator_flet")
EXCLUDE_DIRS = {"__pycache__", ".venv", ".git", "backups", "tests", ".tox", "node_modules"}

def is_excluded(path):
    parts = Path(path).parts
    for d in EXCLUDE_DIRS:
        if d in parts:
            return True
    return False

def has_side_effects(path_str, src):
    """Detect side effects: file I/O, network calls, global state mutation"""
    dangerous = []
    src_lower = src.lower()
    # File write operations
    if 'open(' in src and ('w' in src or 'wb' in src or 'a' in src):
        dangerous.append("檔案寫入 (open w/a)")
    if '.write' in src and ('text' in src_lower or 'json' in src_lower or 'file' in src_lower):
        dangerous.append("檔案寫入 (.write)")
    if '.dump(' in src or '.dumps(' in src:
        dangerous.append("JSON序列化寫入")
    if '.save' in src_lower or 'shutil.copy' in src or 'zipfile' in src:
        dangerous.append("檔案/zip操作")
    # Network
    if 'requests.' in src or 'httpx' in src or 'urlopen' in src or 'urlretrieve' in src:
        dangerous.append("網路呼叫")
    if 'gemini' in src_lower or 'api_key' in src_lower:
        dangerous.append("外部API呼叫")
    # Global state
    if 'global ' in src:
        dangerous.append("修改全域狀態")
    return dangerous

def has_complex_conditionals(src):
    """Detect complex conditionals: 3+ nested ifs or 4+ branches"""
    lines = src.split('\n')
    max_depth = 0
    current_depth = 0
    branch_count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('if ') or stripped.startswith('elif '):
            current_depth += 1
            max_depth = max(max_depth, current_depth)
        elif stripped.startswith('else:') or stripped.startswith('except'):
            branch_count += 1
        elif stripped and not stripped.startswith('#'):
            current_depth = max(0, current_depth - 1)
    return max_depth >= 3 or branch_count >= 4

def has_threading_asyncio(src):
    """Detect threading/asyncio usage"""
    indicators = []
    if 'threading.' in src or 'Thread(' in src:
        indicators.append("threading")
    if 'asyncio' in src or 'async def' in src or 'await ' in src:
        indicators.append("asyncio")
    if 'Queue' in src and ('threading' in src or 'queue' in src):
        indicators.append("threading.Queue")
    return indicators

def has_external_dependencies(src):
    """Detect external API / filesystem dependencies"""
    deps = []
    if 'open(' in src:
        deps.append("檔案系統")
    if 'Path(' in src or 'pathlib' in src:
        deps.append("路徑操作")
    if 'requests.' in src or 'httpx' in src:
        deps.append("HTTP用戶端")
    if 'zipfile' in src:
        deps.append("ZIP處理")
    if 'json.load' in src or 'json.dump' in src:
        deps.append("JSON檔案")
    if 'config' in src.lower() and ('load' in src.lower() or 'save' in src.lower()):
        deps.append("設定檔I/O")
    if 'cache' in src.lower():
        deps.append("快取I/O")
    return deps

def analyze_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=str(path))
        funcs = []
        for item in ast.iter_child_nodes(tree):
            if isinstance(item, ast.FunctionDef):
                lineno = item.end_lineno - item.lineno + 1 if item.end_lineno else 0
                args = [a.arg for a in item.args.args]
                # Extract function source
                func_src = src.split('\n')[item.lineno-1:item.end_lineno] if item.end_lineno else ''
                func_src_str = '\n'.join(func_src)
                funcs.append({
                    "name": item.name,
                    "args": args,
                    "lineno": lineno,
                    "line_start": item.lineno,
                    "line_end": item.end_lineno,
                    "has_decorator": bool(item.decorator_list),
                    "has_docstring": ast.get_docstring(item) is not None,
                    "side_effects": has_side_effects(path, func_src_str),
                    "complex_conditionals": has_complex_conditionals(func_src_str),
                    "threading_asyncio": has_threading_asyncio(func_src_str),
                    "external_deps": has_external_dependencies(func_src_str),
                })
        classes = []
        for item in ast.iter_child_nodes(tree):
            if isinstance(item, ast.ClassDef):
                methods = []
                for m in ast.iter_child_nodes(item):
                    if isinstance(m, ast.FunctionDef):
                        if m.name.startswith("_") and m.name not in ("__init__", "__post_init__", "__enter__", "__exit__", "__call__", "__repr__", "__str__", "__bool__", "__eq__", "__hash__", "__len__", "__iter__", "__next__", "__getitem__", "__setitem__", "__contains__"):
                            continue
                        args = [a.arg for a in m.args.args]
                        m_src = src.split('\n')[m.lineno-1:m.end_lineno] if m.end_lineno else ''
                        m_src_str = '\n'.join(m_src)
                        methods.append({
                            "name": m.name,
                            "args": args,
                            "has_docstring": ast.get_docstring(m) is not None,
                            "side_effects": has_side_effects(path, m_src_str),
                            "complex_conditionals": has_complex_conditionals(m_src_str),
                            "threading_asyncio": has_threading_asyncio(m_src_str),
                            "external_deps": has_external_dependencies(m_src_str),
                        })
                classes.append({
                    "name": item.name,
                    "methods": methods,
                    "has_docstring": ast.get_docstring(item) is not None,
                    "line_start": item.lineno,
                    "line_end": item.end_lineno,
                })
        return {
            "path": str(path),
            "relative_path": str(Path(path).relative_to(ROOT)),
            "functions": funcs,
            "classes": classes,
            "has_docstring": ast.get_docstring(tree) is not None,
            "total_lines": len(src.splitlines()),
            "ok": True,
        }
    except Exception as e:
        return {
            "path": str(path),
            "relative_path": str(Path(path).relative_to(ROOT)),
            "error": str(e),
            "ok": False,
        }

py_files = []
for f in ROOT.rglob("*.py"):
    if not is_excluded(f):
        py_files.append(f)

results = []
for f in py_files:
    results.append(analyze_file(f))

ok_files = [r for r in results if r["ok"]]
total_classes = sum(len(r["classes"]) for r in ok_files)
total_funcs = sum(len(r["functions"]) for r in ok_files)
total_methods = sum(sum(len(c["methods"]) for c in r["classes"]) for r in ok_files)

# Build report
lines = []
lines.append("=" * 80)
lines.append("Minecraft Translator Flet — 完整模組掃描報告")
lines.append("=" * 80)
lines.append(f"掃描時間：2026-03-20")
lines.append(f"專案根目錄：{ROOT}")
lines.append(f"")
lines.append(f"全域摘要")
lines.append(f"  - 總模組檔案：{len(results)}")
lines.append(f"  - 成功解析：{len(ok_files)}")
lines.append(f"  - 解析失敗：{len(results)-len(ok_files)}")
lines.append(f"  - 總類別數：{total_classes}")
lines.append(f"  - 總函式數：{total_funcs}")
lines.append(f"  - 總方法數（公眾方法）：{total_methods}")
lines.append(f"")

# Failed files
failed = [r for r in results if not r["ok"]]
if failed:
    lines.append("=" * 80)
    lines.append(f"⚠️ 解析失敗的檔案（共 {len(failed)} 個）")
    lines.append("=" * 80)
    for f in failed:
        lines.append(f"  ❌ {f['relative_path']} — {f['error']}")
    lines.append("")

# Group by directory
from collections import defaultdict
by_dir = defaultdict(list)
for r in ok_files:
    rel = r['relative_path']
    parts = rel.split(os.sep)
    if len(parts) > 1:
        by_dir[parts[0]].append(r)
    else:
        by_dir["(root)"].append(r)

def risk_score(r):
    """Calculate risk score for a module"""
    score = 0
    for f in r['functions']:
        score += len(f['side_effects']) * 2
        if f['complex_conditionals']:
            score += 1
        score += len(f['threading_asyncio'])
        score += len(f['external_deps'])
    for c in r['classes']:
        for m in c['methods']:
            score += len(m['side_effects']) * 2
            if m['complex_conditionals']:
                score += 1
            score += len(m['threading_asyncio'])
            score += len(m['external_deps'])
    return score

# Sort by risk score descending
sorted_files = sorted(ok_files, key=risk_score, reverse=True)

# High risk modules
lines.append("=" * 80)
lines.append("🔴 高風險模組清單（副作用/並發/複雜分支 風險評分）")
lines.append("=" * 80)
for r in sorted_files[:20]:
    score = risk_score(r)
    if score > 0:
        issues = []
        for f in r['functions']:
            if f['side_effects']:
                issues.append(f"func {f['name']}: {', '.join(f['side_effects'])}")
            if f['threading_asyncio']:
                issues.append(f"func {f['name']}: {', '.join(f['threading_asyncio'])}")
        for c in r['classes']:
            for m in c['methods']:
                if m['side_effects']:
                    issues.append(f"method {c['name']}.{m['name']}: {', '.join(m['side_effects'])}")
                if m['threading_asyncio']:
                    issues.append(f"method {c['name']}.{m['name']}: {', '.join(m['threading_asyncio'])}")
        lines.append(f"  [{score:3d}分] {r['relative_path']}")
        for issue in issues[:5]:
            lines.append(f"         ⚠️ {issue}")
        if len(issues) > 5:
            lines.append(f"         ... 以及其他 {len(issues)-5} 個問題")
    if score > 20:
        lines.append("")

lines.append("")
lines.append("=" * 80)
lines.append("各模組詳細資訊（依目錄分組）")
lines.append("=" * 80)

dir_order = sorted(by_dir.keys(), key=lambda d: (0 if d == "(root)" else 1, d))

for dir_name in dir_order:
    files = sorted(by_dir[dir_name], key=lambda r: r['relative_path'])
    lines.append("")
    lines.append("-" * 80)
    lines.append(f"📁 {dir_name}/ （{len(files)} 個模組）")
    lines.append("-" * 80)
    for r in files:
        lines.append("")
        lines.append(f"### `{r['relative_path']}`")
        lines.append(f"| 項目 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 總行數 | {r['total_lines']} |")
        lines.append(f"| 模組docstring | {'有' if r['has_docstring'] else '無'} |")
        lines.append(f"| 類別數 | {len(r['classes'])} |")
        lines.append(f"| 函式數 | {len(r['functions'])} |")
        
        if r['classes']:
            lines.append("")
            lines.append("**類別：**")
            lines.append("")
            lines.append("| 類別名稱 | 行數 | docstring | 公有方法 |")
            lines.append("|---------|------|-----------|----------|")
            for c in r['classes']:
                method_names = [m['name'] for m in c['methods']]
                lines.append(f"| `{c['name']}` | L{c['line_start']}-{c['line_end']} | {'✓' if c['has_docstring'] else '✗'} | {', '.join(method_names) if method_names else '無'} |")
        
        if r['functions']:
            lines.append("")
            lines.append("**頂層函式：**")
            lines.append("")
            lines.append("| 函式名稱 | 參數 | 行數 | docstring | 副作用 | 複雜分支 | threading |")
            lines.append("|---------|------|------|-----------|--------|----------|----------|")
            for f in r['functions']:
                se = ', '.join(f['side_effects']) if f['side_effects'] else '無'
                ta = ', '.join(f['threading_asyncio']) if f['threading_asyncio'] else '無'
                cc = '⚠️' if f['complex_conditionals'] else ''
                lines.append(f"| `{f['name']}` | ({', '.join(f['args'])}) | {f['lineno']} | {'✓' if f['has_docstring'] else '✗'} | {se} | {cc} | {ta} |")
        
        lines.append("")
        lines.append(f"_解析失敗原因：{r.get('error', 'N/A')}_" if not r['ok'] else "")

# Summary statistics
lines.append("")
lines.append("=" * 80)
lines.append("附錄：目錄別模組數量統計")
lines.append("=" * 80)
for dir_name in sorted(by_dir.keys()):
    lines.append(f"  {dir_name}/ — {len(by_dir[dir_name])} 個模組")

report = '\n'.join(lines)
with open("__code_reader_report.txt", "w", encoding="utf-8") as out:
    out.write(report)

print(f"Report written to __code_reader_report.txt ({len(report.splitlines())} lines)")
print(f"Total: {len(ok_files)} modules, {total_classes} classes, {total_funcs} functions, {total_methods} methods")
print(f"High risk files (>0 score): {sum(1 for r in ok_files if risk_score(r) > 0)}")
print(f"Failed files: {len(failed)}")
for f in failed:
    print(f"  - {f['relative_path']}: {f['error']}")
