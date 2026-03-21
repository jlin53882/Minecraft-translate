"""
Patchouli zh_tw 完整性驗證腳本 v3
路徑結構已修正：
  來源: mods_提取book_輸出/assets/{mod}/patchouli_books/{book}/zh_cn/{rest}
  輸出: patchouli_books/assets/{mod}/patchouli_books/{book}/zh_tw/{rest}
  book 在 parts[4]，mod 在 parts[2]
"""
import json, zipfile
from pathlib import Path

BASE = Path(r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test')
BOOK_ZIP = BASE / 'mods_提取book_輸出.zip'
OUT = BASE / '輸出測試'
pb = OUT / 'patchouli_output' / 'patchouli_books'

SIMP_ONLY = set('开关心错为们么过来时还又学觉里给长短点几')

print("載入來源 ZIP...", flush=True)
src_cn = {}
with zipfile.ZipFile(BOOK_ZIP, 'r') as zf:
    for name in zf.namelist():
        if '/zh_cn/' in name and name.endswith('.json'):
            try:
                src_cn[name] = zf.read(name).decode('utf-8', errors='replace')
            except Exception:
                pass

print(f"來源 zh_cn 檔案: {len(src_cn)}", flush=True)

# 建立來源索引：(mod, book, rest) -> content
# rest = zh_cn/entries/adventure/affix_loot/affixes.json
src_index = {}
for path, content in src_cn.items():
    parts = path.replace('\\', '/').split('/')
    # ['mods_提取book_輸出', 'assets', '{mod}', 'patchouli_books', '{book}', 'zh_cn', ...]
    if len(parts) >= 6 and parts[-1].endswith('.json'):
        mod = parts[2]
        book = parts[4]  # 不是 parts[3]！parts[3] 是 'patchouli_books' 固定字
        rest = '/'.join(parts[5:])  # zh_cn/entries/.../xxx.json
        src_index[(mod, book, rest)] = content

print(f"來源索引: {len(src_index)} 筆", flush=True)

# 建立輸出索引：(mod, book, rest) -> file path
# rest = zh_tw/entries/adventure/affix_loot/affixes.json
out_index = {}
for f in pb.rglob('*.json'):
    rel = str(f.relative_to(pb))
    parts = rel.replace('\\', '/').split('/')
    # ['assets', '{mod}', 'patchouli_books', '{book}', 'zh_tw', ...]
    if len(parts) >= 5 and parts[-1].endswith('.json'):
        mod = parts[1]
        book = parts[3]  # parts[2] 是 'patchouli_books' 固定字
        rest = '/'.join(parts[4:])  # zh_tw/entries/.../xxx.json
        out_index[(mod, book, rest)] = f

print(f"輸出 zh_tw 檔案: {len(out_index)}", flush=True)

# 比對
results = {
    'total_src': len(src_index),
    'total_out': len(out_index),
    'matched': 0,
    'missing_in_output': [],
    'output_no_source': [],
    'key_mismatch': [],
    'cjk_issues': [],
    'ok': []
}

print("\n開始比對...", flush=True)
checked = 0

for (mod, book, src_rest), src_content in src_index.items():
    out_rest = src_rest.replace('/zh_cn/', '/zh_tw/')
    key = (mod, book, out_rest)
    checked += 1

    if key not in out_index:
        try:
            src_data = json.loads(src_content)
        except Exception:
            src_data = {}
        results['missing_in_output'].append({
            'mod': mod, 'book': book,
            'src_path': src_rest,
            'src_keys': len(src_data)
        })
        continue

    # 兩邊都有，比對
    out_file = out_index[key]
    try:
        with open(out_file, 'r', encoding='utf-8') as f:
            tw_data = json.load(f)
    except Exception:
        results['key_mismatch'].append({
            'mod': mod, 'book': book,
            'file': out_rest,
            'error': '輸出無法解析'
        })
        continue

    try:
        src_data = json.loads(src_content)
    except Exception:
        continue

    src_keys = set(src_data.keys())
    tw_keys = set(tw_data.keys())
    missing_keys = src_keys - tw_keys
    extra_keys = tw_keys - src_keys

    if missing_keys or extra_keys:
        results['key_mismatch'].append({
            'mod': mod, 'book': book,
            'file': out_rest,
            'src_keys': len(src_keys),
            'tw_keys': len(tw_keys),
            'missing_keys': list(missing_keys)[:10],
            'extra_keys': list(extra_keys)[:10]
        })
    else:
        issues = []
        for k in src_keys:
            v_tw = tw_data.get(k, '')
            if not isinstance(v_tw, str):
                continue
            for c in v_tw:
                if c in SIMP_ONLY:
                    issues.append((k, c, v_tw[:50]))
                    break
        if issues:
            results['cjk_issues'].append({
                'mod': mod, 'book': book,
                'file': out_rest,
                'issues': issues[:3]
            })
        else:
            results['matched'] += 1

    if checked % 100 == 0:
        print(f"  已檢查 {checked}/{len(src_index)}", flush=True)

# 檢查輸出有沒有不應存在的（沒有對應來源的）
src_out_paths = set()
for (mod, book, src_rest) in src_index:
    out_rest = src_rest.replace('/zh_cn/', '/zh_tw/')
    src_out_paths.add((mod, book, out_rest))

for (mod, book, out_rest) in out_index:
    if (mod, book, out_rest) not in src_out_paths:
        results['output_no_source'].append({
            'mod': mod, 'book': book,
            'out_path': out_rest
        })

print(f"\n=== 比對完成 ===", flush=True)
print(f"來源 zh_cn: {results['total_src']}", flush=True)
print(f"輸出 zh_tw: {results['total_out']}", flush=True)
print(f"完全正常: {results['matched']}", flush=True)
print(f"缺少（來源有、輸出無）: {len(results['missing_in_output'])}", flush=True)
print(f"多餘（輸出有、無來源）: {len(results['output_no_source'])}", flush=True)
print(f"key 不匹配: {len(results['key_mismatch'])}", flush=True)
print(f"簡體殘留: {len(results['cjk_issues'])}", flush=True)

report_path = OUT / 'patchouli_zhTW_verification_report.json'
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n報告寫入: {report_path}", flush=True)

# 彙整
if results['missing_in_output']:
    print(f"\n=== 缺少的檔案（按模組）===")
    by_mod = {}
    for item in results['missing_in_output']:
        m = item['mod']
        by_mod[m] = by_mod.get(m, 0) + 1
    for m, c in sorted(by_mod.items(), key=lambda x: -x[1])[:15]:
        files = [item['src_path'] for item in results['missing_in_output'] if item['mod'] == m]
        print(f"  {m}: {c} 個檔案")
        for f in files[:3]:
            print(f"    - {f}")

if results['key_mismatch']:
    print(f"\n=== key 不匹配（前10）===")
    for item in results['key_mismatch'][:10]:
        print(f"  {item['mod']}/{item['book']}: {item['file']}")
        print(f"    src_keys={item.get('src_keys','?')}, tw_keys={item.get('tw_keys','?')}")

if results['cjk_issues']:
    print(f"\n=== 簡體殘留 ===")
    for item in results['cjk_issues'][:5]:
        print(f"  {item['mod']}/{item['book']}: {item['file']}")
        for k, c, v in item['issues'][:2]:
            print(f"    {k}: ...{v}... (含簡體「{c}」)")
