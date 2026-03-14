#!/usr/bin/env python3
"""Scan for problematic docstrings"""
import sys
from pathlib import Path

def scan_file(path):
    """Scan a single file for problematic docstrings"""
    problems = []
    content = path.read_text(encoding='utf-8', errors='ignore')
    lines = content.split('\n')
    
    for i, line in enumerate(lines):
        # Check for "處理函數"
        if '處理函數' in line and line.strip().startswith('"""'):
            # Find function name
            for j in range(i-1, max(0, i-5), -1):
                if 'def ' in lines[j] or 'async def ' in lines[j]:
                    func_def = lines[j].strip()
                    problems.append({
                        'line': i+1,
                        'func': func_def,
                        'type': '處理函數'
                    })
                    break
        
        # Check for empty docstring (just """)
        stripped = line.strip()
        if stripped == '"""' or stripped == '""""':
            # Check next line
            if i+1 < len(lines) and lines[i+1].strip() == '"""':
                # Find function name
                for j in range(i-1, max(0, i-5), -1):
                    if 'def ' in lines[j] or 'async def ' in lines[j]:
                        func_def = lines[j].strip()
                        problems.append({
                            'line': i+1,
                            'func': func_def,
                            'type': 'EMPTY'
                        })
                        break
    
    return problems

# Scan both folders
all_problems = {}
base_path = Path(r"C:\Users\admin\Desktop\minecraft_translator_flet")
for folder in ['app', 'translation_tool']:
    folder_path = base_path / folder
    if not folder_path.exists():
        continue
    
    for py_file in folder_path.rglob('*.py'):
        problems = scan_file(py_file)
        if problems:
            rel_path = str(py_file.relative_to(base_path))
            all_problems[rel_path] = problems

# Output
total = 0
for filepath in sorted(all_problems.keys()):
    print(f"\n=== {filepath} ===")
    for p in all_problems[filepath]:
        print(f"  Line {p['line']}: {p['func']} [{p['type']}]")
        total += 1

print(f"\n=== Total: {total} issues in {len(all_problems)} files ===")
