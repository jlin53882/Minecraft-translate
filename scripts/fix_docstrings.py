#!/usr/bin/env python3
"""Fix missing docstrings in translation_tool"""
import ast
from pathlib import Path

def fix_file(filepath):
    """Fix missing docstrings in a single file"""
    content = filepath.read_text(encoding='utf-8', errors='ignore')
    lines = content.split('\n')
    modified = False
    
    # Find functions without docstrings
    i = 0
    new_lines = []
    while i < len(lines):
        line = lines[i]
        
        # Check if this is a function definition
        if line.strip().startswith('def ') or line.strip().startswith('async def '):
            # Check if next non-empty line is a docstring
            j = i + 1
            while j < len(lines) and lines[j].strip() == '':
                j += 1
            
            has_docstring = False
            if j < len(lines) and '"""' in lines[j]:
                has_docstring = True
            
            if not has_docstring:
                # Add a simple English docstring
                func_name = line.strip().split('(')[0].replace('def ', '').replace('async def ', '')
                new_lines.append(line)
                new_lines.append(f'    """{func_name}"""')
                modified = True
                i += 1
                continue
        
        new_lines.append(line)
        i += 1
    
    if modified:
        filepath.write_text('\n'.join(new_lines), encoding='utf-8')
        print(f'Fixed: {filepath.name}')

# Run on translation_tool
base = Path(r'C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool')
for py_file in base.rglob('*.py'):
    try:
        fix_file(py_file)
    except Exception as e:
        print(f'Error: {py_file.name}: {e}')

print('Done!')
