import os
import re
import ast
from pathlib import Path

# Exclude patterns
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

# Parse imports from each file
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
        # from X import Y
        m = re.match(r'^from\s+(\.+[\w.]+)\s+import', line)
        if m:
            imp = m.group(1).strip()
            imports.add(imp)
        # import X
        m2 = re.match(r'^import\s+([\w.]+)', line)
        if m2:
            imports.add(m2.group(1).strip())
    
    if imports:
        imports_by_file[module] = imports

# Print
for mod in sorted(imports_by_file.keys()):
    imps = sorted(imports_by_file[mod])
    print(f"{mod} -> {imps}")

print(f"\n--- Total modules with imports: {len(imports_by_file)} ---")
print(f"--- Total unique modules: {len(all_modules)} ---")
