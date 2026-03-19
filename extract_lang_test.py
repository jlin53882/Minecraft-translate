#!/usr/bin/env python3
"""Extract lang files from first 30 JAR files and print their contents."""

import sys
import os
import re
import json

# Add translation_tool to path
sys.path.insert(0, r'C:\Users\admin\Desktop\minecraft_translator_flet')

from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.core.jar_processor_extract import extract_from_jar_impl

# Paths
mods_dir = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods'
output_dir = r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test\lang_out'

# Lang regex
lang_regex = re.compile(
    r'(?:assets/([^/]+)/)?lang/(en_us|zh_cn|zh_tw)\.(json|lang)$',
    re.IGNORECASE,
)

# Get first 30 jars
all_jars = find_jar_files(mods_dir)
jars_30 = all_jars[:30]
print(f'Total jars found: {len(all_jars)}')
print(f'Processing first 30 jars...')
print('=' * 60)

# Create output directory
os.makedirs(output_dir, exist_ok=True)

total_jars_processed = 0
jars_with_lang_files = []

for i, jar_path in enumerate(jars_30):
    jar_name = os.path.basename(jar_path)
    print(f'\n[{i+1}/30] Processing: {jar_name}')

    result = extract_from_jar_impl(jar_path, output_dir, lang_regex)
    total_jars_processed += 1

    extracted = result.get('extracted', 0)
    print(f'  -> Extraction result: extracted={extracted}, status={result.get("status")}')

    if extracted > 0:
        jars_with_lang_files.append((jar_name, jar_path, extracted))

print('\n' + '=' * 60)
print('SUMMARY REPORT')
print('=' * 60)
print(f'Total jars processed: {total_jars_processed}')
print(f'Jars with lang files: {len(jars_with_lang_files)}')
print('Jars with lang files list:')
for j, _, _ in jars_with_lang_files:
    print(f'  - {j}')

print('\n' + '=' * 60)
print('EXTRACTED FILE CONTENTS')
print('=' * 60)

# Now find all extracted lang files in output_dir and print content
total_lang_files = 0
for root, dirs, files in os.walk(output_dir):
    for fname in files:
        if fname.endswith(('.json', '.lang')):
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, output_dir)
            total_lang_files += 1
            print(f'\n--- {rel_path} ---')
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                print(content[:3000])  # Print first 3000 chars
                if len(content) > 3000:
                    print(f'... [truncated, total {len(content)} chars]')
            except Exception as e:
                print(f'Error reading file: {e}')

print('\n' + '=' * 60)
print(f'TOTAL lang files extracted: {total_lang_files}')
