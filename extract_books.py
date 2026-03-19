import sys, os, json
sys.path.insert(0, 'src')

from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor import BOOK_PATH_REGEX_DUAL_STRUCTURE
from translation_tool.core.jar_processor_extract import extract_from_jar_impl

# Output directory
out_dir = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test\book_out'
os.makedirs(out_dir, exist_ok=True)

# Get first 30 jars
jars = find_jar_files(r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods')[:30]
print(f'Processing {len(jars)} JARs...')

results = []
for jar_path in jars:
    jar_name = os.path.basename(jar_path)
    # Correct order: jar_path, output_root, target_regex
    result = extract_from_jar_impl(jar_path, out_dir, BOOK_PATH_REGEX_DUAL_STRUCTURE)
    if result.get('extracted', 0) > 0 or result.get('skipped', 0) > 0:
        results.append((jar_name, jar_path, result))

print(f'\n=== SUMMARY: {len(results)} JARs had book files ===')
for jar_name, jar_path, res in results:
    print(f'{jar_name}: extracted={res.get("extracted",0)}, skipped={res.get("skipped",0)}')

# Now read and display extracted files
print('\n\n=== DETAILED CONTENT ===')
for jar_name, jar_path, res in results:
    jar_name_lower = jar_name.lower()
    print(f'\n=== JAR: {jar_name} ===')
    # Find extracted files for this jar
    # Check both assets/ and data/ paths
    for root, dirs, files in os.walk(out_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, out_dir)
            # Check if this file is from the current jar
            # assets/modid/patchouli_books/... or data/modid/book/...
            parts = rel.split(os.sep)
            if len(parts) >= 2:
                # Try to match: jar with _extracted suffix
                jar_base = jar_name_lower.replace('.jar', '').replace('-neoforge', '').replace('-forge', '')
                if any(keyword in rel.lower() for keyword in ['patchouli_books', '/book/', '/manual/', '/guidebook/']):
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        print(f'  File: {rel}')
                        print(f'  Content ({len(content)} chars):')
                        print(content[:2000])
                        print('---')
                    except Exception as e:
                        print(f'  File: {rel} [binary or error: {e}]')
