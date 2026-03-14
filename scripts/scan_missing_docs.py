#!/usr/bin/env python3
"""完整扫描缺少 docstring 的函数"""
import ast
import sys
from pathlib import Path

base_path = Path(r"C:\Users\admin\Desktop\minecraft_translator_flet")
results = {}

def scan_file(filepath):
    """使用 AST 扫描文件"""
    problems = []
    try:
        content = filepath.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 跳过没有 docstring 的函数
                if not ast.get_docstring(node):
                    # 检查是否有 decorators（可能是装饰器函数）
                    if not node.decorator_list:
                        # 这是普通的函数，没有 docstring
                        problems.append({
                            'name': node.name,
                            'line': node.lineno,
                            'type': 'NO_DOCSTRING'
                        })
    except Exception as e:
        pass
    return problems

# 扫描 app/ 文件夹
app_path = base_path / 'app'
for py_file in app_path.rglob('*.py'):
    problems = scan_file(py_file)
    if problems:
        rel_path = str(py_file.relative_to(base_path))
        results[rel_path] = problems

# 输出
total = 0
for filepath in sorted(results.keys()):
    print(f"\n=== {filepath} ===")
    for p in results[filepath]:
        print(f"  Line {p['line']}: {p['name']}")
        total += 1

print(f"\n=== Total: {total} functions without docstring in {len(results)} files ===")
