import ast
import os

def check_missing_docstrings(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 跳过类方法
            if hasattr(node, 'parent') and node.parent and isinstance(node.parent, ast.ClassDef):
                continue
            # 检查是否有 docstring
            if not ast.get_docstring(node):
                missing.append(node.name)
    return missing

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
        missing = check_missing_docstrings(f)
        print(f'{f}: {len(missing)} 處 -> {missing}')
    else:
        print(f'{f}: 檔案不存在')
