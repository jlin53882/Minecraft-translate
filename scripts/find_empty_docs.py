import re
from pathlib import Path

path = Path(r'C:\Users\admin\Desktop\minecraft_translator_flet\app\views\cache_view.py')
content = path.read_text(encoding='utf-8', errors='ignore')
lines = content.split('\n')

print('=== Empty docstrings in cache_view.py ===')
for i, line in enumerate(lines):
    if re.match(r'^\s+"""\s*$', line):
        # Check if it's a function docstring
        for j in range(i-1, max(0, i-10), -1):
            if 'def ' in lines[j]:
                print(f'Line {i+1}: {lines[j].strip()}')
                break
