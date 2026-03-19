import sys, os, json, zipfile
sys.path.insert(0, 'src')

from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor import BOOK_PATH_REGEX_DUAL_STRUCTURE

# Output directory
out_dir = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test\book_out'
os.makedirs(out_dir, exist_ok=True)

# Get first 30 jars
jars = find_jar_files(r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods')[:30]
print(f'Processing {len(jars)} JARs...')

all_results = []

for jar_path in jars:
    jar_name = os.path.basename(jar_path)
    jar_book_files = []
    
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                normalized_path = member.filename.replace('\\', '/')
                if not BOOK_PATH_REGEX_DUAL_STRUCTURE.search(normalized_path):
                    continue
                jar_book_files.append(normalized_path)
    except Exception as e:
        print(f'Error reading {jar_name}: {e}')
        continue
    
    if jar_book_files:
        all_results.append((jar_name, jar_path, jar_book_files))

print(f'\n=== SUMMARY: {len(all_results)} JARs had book files ===')
for jar_name, jar_path, files in all_results:
    print(f'{jar_name}: {len(files)} book files')

# Now extract and display content for each JAR
print('\n\n=== DETAILED CONTENT BY JAR ===')
for jar_name, jar_path, book_paths in all_results:
    print(f'\n\n=== JAR: {jar_name} ===')
    print(f'  Extracted {len(book_paths)} book files:')
    
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for book_path in book_paths:
                try:
                    with zf.open(book_path) as f:
                        content = f.read().decode('utf-8', errors='replace')
                    
                    # Determine output path
                    if book_path.startswith('assets/'):
                        out_path = os.path.join(out_dir, book_path)
                    else:
                        jar_base = os.path.basename(jar_path).replace('.jar', '')
                        out_path = os.path.join(out_dir, f'{jar_base}_extracted', book_path)
                    
                    # Write to output
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    
                    print(f'\n  - {book_path} -> {os.path.relpath(out_path, out_dir)}')
                    print(f'    CONTENT ({len(content)} chars):')
                    preview = content[:600]
                    print(f'    {preview}')
                    if len(content) > 600:
                        print(f'    ... [truncated, total {len(content)} chars]')
                except Exception as e:
                    print(f'\n  - {book_path} [error reading: {e}]')
    except Exception as e:
        print(f'  Error opening JAR: {e}')

print('\n\n=== EXTRACTION COMPLETE ===')
print(f'Output directory: {out_dir}')
