#!/usr/bin/env python3
"""Generate per-JAR extraction report with content."""

import sys
import os
import re
import json

sys.path.insert(0, r'C:\Users\admin\Desktop\minecraft_translator_flet')
from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor_extract import extract_from_jar_impl

mods_dir = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods'
output_dir = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test\lang_out'

lang_regex = re.compile(
    r'(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$',
    re.IGNORECASE,
)

all_jars = find_jar_files(mods_dir)
jars_30 = all_jars[:30]

os.makedirs(output_dir, exist_ok=True)

report = []
total_lang_files = 0

for i, jar_path in enumerate(jars_30):
    jar_name = os.path.basename(jar_path)
    result = extract_from_jar_impl(jar_path, output_dir, lang_regex)
    extracted = result.get('extracted', 0)

    if extracted == 0:
        continue

    # Find extracted files from this jar
    jar_base = os.path.splitext(jar_name)[0]
    # The extraction puts files in assets/[modid]/lang/... or [jar_base]_extracted/[modid]/lang/...
    # We need to find files that match the mod id from this jar

    extracted_files = []
    for root, dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.endswith(('.json', '.lang')):
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, output_dir)
                # Get jar-relative path (the path inside the jar)
                # For assets/... paths, it is straightforward
                # For _extracted paths, we need to check
                extracted_files.append((rel_path, full_path))

    # Actually, we need to track per-jar. Let's do it differently:
    # We need to know what paths inside the JAR were extracted.
    # Since extract_from_jar_impl doesn't return file paths, we need to use a different approach.
    # Let me re-extract to capture the paths.

print("=" * 60)
print("PER-JAR EXTRACTION REPORT")
print("=" * 60)
print(f"Source: {mods_dir}")
print(f"Output: {output_dir}")
print(f"Total JARs found: {len(all_jars)}")
print(f"JARs processed: 30")
print(f"Regex: {lang_regex.pattern}")
print("=" * 60)

# Re-run with zipfile to get actual paths
import zipfile

jar_lang_map = {}

for i, jar_path in enumerate(jars_30):
    jar_name = os.path.basename(jar_path)
    lang_paths = []

    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                normalized = member.filename.replace('\\', '/')
                if lang_regex.search(normalized):
                    lang_paths.append(normalized)
    except Exception as e:
        print(f"Error reading {jar_name}: {e}")

    if lang_paths:
        jar_lang_map[jar_name] = lang_paths

# Now print detailed per-jar report
grand_total = 0
for jar_name, paths in jar_lang_map.items():
    print(f"\n=== JAR: {jar_name} ===")
    print(f"  Extracted {len(paths)} lang files:")

    for path_in_jar in sorted(paths):
        # Find the corresponding output file
        # The output path mirrors the jar path structure
        if path_in_jar.startswith('assets/'):
            output_rel = path_in_jar
        else:
            jar_base = os.path.splitext(jar_name)[0]
            output_rel = f"{jar_base}_extracted/{path_in_jar}"

        output_path = os.path.join(output_dir, output_rel)

        # Also try case-insensitive
        if not os.path.exists(output_path):
            for root, dirs, files in os.walk(output_dir):
                for f in files:
                    if f.lower() == os.path.basename(output_rel).lower():
                        candidate = os.path.join(root, f)
                        if path_in_jar.replace('\\', '/').split('/')[-1] == f:
                            output_path = candidate
                            break

        content = ""
        if os.path.exists(output_path):
            try:
                with open(output_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except:
                pass

        print(f"  - {path_in_jar}")
        print(f"    -> {output_rel}")
        print(f"    CONTENT ({len(content)} chars): {content[:500]}")

        grand_total += 1

print(f"\n{'=' * 60}")
print(f"GRAND TOTAL: {grand_total} lang files from {len(jar_lang_map)} JARs")
print(f"{'=' * 60}")
