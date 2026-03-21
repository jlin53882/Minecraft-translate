import os
import re
from pathlib import Path
from collections import defaultdict

EXCLUDE_DIRS = {'.venv', '__pycache__', 'tests', '.git', 'backups', 'tools'}
EXCLUDE_FILES = {
    'test_main.py', 'analyze_all.py', 'analyze_coverage.py', 
    'analyze_coverage2.py', 'analyze_coverage3.py', 'analyze_coverage_final.py',
    'fix_merge_ui.py', 'tools/fix_test.py', 'tools/gap_analysis.py', 
    'tools/test_all_features.py'
}

root = Path('.')
py_files = []
for f in root.rglob('*.py'):
    if any(ex in f.parts for ex in EXCLUDE_DIRS):
        continue
    rel_str = str(f).replace('\\', '/')
    fname = rel_str.split('/')[-1]
    if fname in EXCLUDE_FILES:
        continue
    py_files.append(f)

# Build module -> imports map
imports_by_file = {}
all_modules = set()
for f in py_files:
    try:
        content = f.read_text(encoding='utf-8')
    except:
        try:
            content = f.read_text(encoding='latin-1')
        except:
            continue
    
    rel = f.relative_to(root)
    module = str(rel).replace('\\', '/').replace('/', '.').replace('.py', '')
    all_modules.add(module)
    
    imports = set()
    for line in content.split('\n'):
        line = line.strip()
        m = re.match(r'^from\s+(\.+[\w.]+)\s+import', line)
        if m:
            imp = m.group(1).strip()
            imports.add(imp)
        m2 = re.match(r'^import\s+([\w.]+)', line)
        if m2:
            imports.add(m2.group(1).strip())
    
    if imports:
        imports_by_file[module] = imports

# Normalize relative imports to absolute
# e.g., .log_colors -> app.logging.log_colors (relative to module's parent)
def resolve_import(importer_module, imp):
    if imp.startswith('.'):
        # Relative import
        parts = importer_module.split('.')
        depth = 0
        for c in imp:
            if c == '.': depth += 1
            else: break
        if depth > 0:
            parent_parts = parts[:-depth] if depth < len(parts) else []
            rest = imp[depth:]
            if rest:
                resolved = '.'.join(parent_parts + [rest])
            else:
                resolved = '.'.join(parent_parts)
            return resolved
        return imp
    return imp

# Build resolved imports (only internal)
def get_internal_imports(importer, imports):
    resolved = []
    for imp in imports:
        res = resolve_import(importer, imp)
        # Check if it maps to an actual module
        if res in all_modules or any(m.endswith(res) for m in all_modules):
            resolved.append(res)
        elif imp in all_modules:
            resolved.append(imp)
    return resolved

# Build edges: (importer, imported)
edges = []
for mod, imps in imports_by_file.items():
    for imp in imps:
        res = resolve_import(mod, imp)
        if res in all_modules:
            edges.append((mod, res))
        elif imp in all_modules:
            edges.append((mod, imp))

# ==== CIRCULAR DEPENDENCIES ====
print("=" * 80)
print("CIRCULAR DEPENDENCIES")
print("=" * 80)

# Build adjacency
adj = defaultdict(set)
for src, dst in edges:
    adj[src].add(dst)

# Also build reverse adjacency
rev = defaultdict(set)
for src, dst in edges:
    rev[dst].add(src)

# Find cycles using DFS
visited = set()
rec_stack = set()
cycles = []

def dfs(node, path):
    visited.add(node)
    rec_stack.add(node)
    path.append(node)
    for neighbor in adj[node]:
        if neighbor not in visited:
            dfs(neighbor, path[:])
        elif neighbor in rec_stack:
            # Found cycle
            idx = path.index(neighbor)
            cycle = path[idx:] + [neighbor]
            cycles.append(cycle)
    rec_stack.discard(node)

for node in list(adj.keys()):
    if node not in visited:
        dfs(node, [])

if cycles:
    for i, cyc in enumerate(cycles, 1):
        print(f"\nCycle {i}: {' -> '.join(cyc)}")
else:
    print("None found")

# ==== GOD MODULES (imported by > 5 others) ====
print("\n" + "=" * 80)
print("GOD MODULES (imported by > 5 other modules)")
print("=" * 80)

in_degree = defaultdict(int)
for src, dst in edges:
    in_degree[dst] += 1

god_modules = [(m, deg) for m, deg in in_degree.items() if deg > 5]
god_modules.sort(key=lambda x: -x[1])

if god_modules:
    for m, deg in god_modules:
        importers = [src for src, dst in edges if dst == m]
        print(f"\n{m} (imported by {deg} modules)")
        print(f"  Importers: {sorted(importers)}")
else:
    print("None found")

# ==== ORPHAN MODULES (not imported by anyone) ====
print("\n" + "=" * 80)
print("ORPHAN MODULES (not imported by any other module)")
print("=" * 80)

importers = set(src for src, dst in edges)
orphans = sorted(all_modules - importers)
print(f"Count: {len(orphans)}")
for m in orphans:
    print(f"  - {m}")

# ==== SRC IMPORT TEST ====
print("\n" + "=" * 80)
print("PRODUCTION -> TEST IMPORTS (src imports test)")
print("=" * 80)
# We don't have a tests directory here, but check for any test-related modules
# Actually, we excluded tests dir. Check if any module named test_* imports
test_imports = [(src, dst) for src, dst in edges if 'test' in src.lower() or 'test' in dst.lower()]
if test_imports:
    for s, d in test_imports:
        print(f"  {s} -> {d}")
else:
    print("None found")

# ==== CROSS-DIRECTORY COUPLING ====
print("\n" + "=" * 80)
print("CROSS-DIRECTORY COUPLING MATRIX")
print("=" * 80)

dirs = ['app', 'translation_tool']
cross_edges = defaultdict(lambda: defaultdict(int))

for src, dst in edges:
    src_dir = src.split('.')[0] if '.' in src else src
    dst_dir = dst.split('.')[0] if '.' in dst else dst
    if src_dir != dst_dir:
        cross_edges[src_dir][dst_dir] += 1

print("\nImporter (row) -> Imported (col): count")
print(f"{'':20} ", end='')
for d in dirs:
    print(f"{d[:18]:>20}", end='')
print()
for d1 in dirs:
    print(f"{d1:20} ", end='')
    for d2 in dirs:
        print(f"{cross_edges[d1][d2]:>20}", end='')
    print()

# ==== MODULES IMPORTED BY MOST CATEGORIES ====
print("\n" + "=" * 80)
print("HIGH-FAN-IN MODULES (imported from multiple top-level dirs)")
print("=" * 80)

cross_imports = defaultdict(set)
for src, dst in edges:
    src_dir = src.split('.')[0] if '.' in src else src
    dst_dir = dst.split('.')[0] if '.' in dst else dst
    if src_dir != dst_dir:
        cross_imports[dst].add(src_dir)

for m, dirs_set in sorted(cross_imports.items(), key=lambda x: -len(x[1])):
    if len(dirs_set) > 1:
        print(f"  {m}: {sorted(dirs_set)}")

# ==== KEY SERVICE MODULES ====
print("\n" + "=" * 80)
print("KEY SERVICE LAYER ANALYSIS (translation_tool/core/)")
print("=" * 80)

core_modules = [m for m in all_modules if m.startswith('translation_tool.core.')]
core_importers = defaultdict(list)
for src, dst in edges:
    if dst.startswith('translation_tool.core.'):
        core_importers[dst].append(src)

for cm in sorted(core_modules):
    imps_from = imports_by_file.get(cm, set())
    external = [(s, d) for s, d in edges if d == cm and not s.startswith('translation_tool.core.')]
    print(f"\n{cm}")
    print(f"  -> imports: {sorted(imps_from)}")
    print(f"  -> imported by ({len(external)} external): {[s for s, _ in external]}")

# ==== APP/VIEWS DIRECT BUSINESS LOGIC COUPLING ====
print("\n" + "=" * 80)
print("APP/VIEWS -> BUSINESS LOGIC COUPLING")
print("=" * 80)

app_views = [m for m in all_modules if m.startswith('app.views.')]
tt_modules = [m for m in all_modules if m.startswith('translation_tool.')]

views_to_tt = []
for v in app_views:
    for imp in imports_by_file.get(v, []):
        res = resolve_import(v, imp)
        if res in tt_modules or imp in tt_modules:
            views_to_tt.append((v, res if res in tt_modules else imp))

# Group by view
from collections import GroupBy
view_groups = defaultdict(list)
for v, t in views_to_tt:
    view_groups[v].append(t)

for v, tts in sorted(view_groups.items()):
    print(f"\n{v} -> {sorted(set(tts))}")

# ==== TOP IMPORTERS ====
print("\n" + "=" * 80)
print("TOP IMPORTERS (out-degree)")
print("=" * 80)

out_degree = defaultdict(int)
for src, dst in edges:
    out_degree[src] += 1

top_importers = sorted(out_degree.items(), key=lambda x: -x[1])[:20]
for m, deg in top_importers:
    imps = sorted([dst for src, dst in edges if src == m])
    print(f"\n{m} ({deg} imports)")
    print(f"  -> {imps}")
