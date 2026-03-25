from pathlib import Path
import re

p = Path(r'translation_tool/plugins/md/md_lmtranslator.py')
text = p.read_text(encoding='utf-8')

pattern = re.compile(
    r'for h, src in hash_to_src\.items\(\):\n'
    r'\s+if is_already_zh\(src\):\n'
    r'\s+already_zh_skipped \+= 1\n'
    r'\s+continue\n'
    r'\s+all_unique_items\.append\(\n'
    r'\s+\{\n'
    r'\s+"cache_type": "md",\n'
    r'\s+"file": "md_pending_blocks",\n'
    r'\s+"path": h,  # .*?\n'
    r'\s+"source_text": src,\n'
    r'\s+"text": src,\n'
    r'\s+\}\n'
    r'\s+\)',
    re.S,
)
replacement = '''for h, src in hash_to_src.items():
        if is_already_zh(src):
            already_zh_skipped += 1
            continue

        shielded = shield_text(src)
        translate_text = shielded.clean
        if shielded.skip_reason is not None:
            translate_text = src

        all_unique_items.append(
            {
                "cache_type": "md",
                "file": "md_pending_blocks",
                "path": h,  # ✅ 用 content_hash 當 path（去重 + 快取 key 的一部分）
                "source_text": src,
                "text": translate_text,
                "_shielded": shielded,
            }
        )'''
text, n = pattern.subn(replacement, text, count=1)
if n != 1:
    raise SystemExit(f'pattern replace 1 failed: {n}')

text = text.replace(
'''    hash_to_dst: Dict[str, str] = {}
    for it in cached_items:
        h = str(it.get("path") or "")
        dst = str(it.get("text") or "")
        if h and dst:
            hash_to_dst[h] = dst
''',
'''    hash_to_dst: Dict[str, str] = {}
    for it in cached_items:
        h = str(it.get("path") or "")
        dst = str(it.get("text") or "")
        if h and dst:
            shielded = it.get("_shielded")
            if shielded is not None and getattr(shielded, "shields", None):
                try:
                    dst = unshield_text(dst, shielded.shields)
                except Exception:
                    pass
            hash_to_dst[h] = dst
''')

text = text.replace(
'''    def on_translated_item(it: Dict[str, Any]) -> None:
        """處理翻譯結果。"""
        h = str(it.get("path") or "")
        dst = str(it.get("text") or "")
        src_text = str(it.get("source_text") or "")
        if h and dst:
            try:
                shielded_src = shield_text(src_text)
                dst = unshield_text(dst, shielded_src.shields)
            except Exception:
                pass
            hash_to_dst[h] = dst
''',
'''    def on_translated_item(it: Dict[str, Any]) -> None:
        """處理翻譯結果。"""
        h = str(it.get("path") or "")
        dst = str(it.get("text") or "")
        src_text = str(it.get("source_text") or "")
        if h and dst:
            shielded = it.get("_shielded")
            if shielded is not None and getattr(shielded, "shields", None):
                try:
                    dst = unshield_text(dst, shielded.shields)
                except Exception:
                    pass
            else:
                try:
                    shielded_src = shield_text(src_text)
                    dst = unshield_text(dst, shielded_src.shields)
                except Exception:
                    pass
            hash_to_dst[h] = dst
''')

p.write_text(text, encoding='utf-8')
print('patched md_lmtranslator')
