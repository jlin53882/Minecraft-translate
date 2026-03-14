import ast
import os
import re

def check_missing_or_empty_docstrings(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        tree = ast.parse(content)
    
    # 建立 parent 關係
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node
    
    missing = []
    empty_or_template = []
    
    # 模板模式
    template_patterns = [
        r'^"""回傳：.*"""$',
        r'^"""$',
        r'^""".*"""$',
    ]
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 跳过类方法
            if hasattr(node, 'parent') and node.parent and isinstance(node.parent, ast.ClassDef):
                continue
            # 跳过特殊方法
            if node.name.startswith('__') and node.name.endswith('__'):
                continue
            
            docstring = ast.get_docstring(node)
            if not docstring:
                missing.append(node.name)
            else:
                # 檢查是否為空或模板
                is_template = False
                for pattern in template_patterns:
                    if re.match(pattern, docstring.strip()):
                        is_template = True
                        break
                if is_template or not docstring.strip():
                    empty_or_template.append(node.name)
    
    return missing, empty_or_template

files = [
    'core/jar_processor_preview.py',
    'plugins/md/md_extract_qa.py', 
    'core/ftb_translator.py',
    'core/ftb_translator_clean.py',
    'core/md_translation_assembly.py',
    'plugins/md/md_lmtranslator.py'
]

for f in files:
    if os.path.exists(f):
        missing, empty_or_template = check_missing_or_empty_docstrings(f)
        print(f'{f}:')
        print(f'  完全缺少: {missing}')
        print(f'  空/模板: {empty_or_template}')
        print()
    else:
        print(f'{f}: 檔案不存在')
