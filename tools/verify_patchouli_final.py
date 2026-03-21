"""
Patchouli zh_tw 完整性驗證腳本 (最終版)
使用 os.normpath + os.sep 處理路徑
"""
import json, zipfile, os
from pathlib import Path

BASE = Path(r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test')
BOOK_ZIP = str(BASE / 'mods_提取book_輸出.zip')
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

print(f"來源 zh_cn: {len(src_cn)}", flush=True)

# 建立來源索引 (mod, book, rest) -> content
# rest = zh_cn/categories/adventure/adventure.json 或 zh_cn/entries/abc/xyz.json
src_index = {}
for path, content in src_cn.items():
    parts = path.replace('\\', '/').split('/')
    # ['mods_提取book_輸出', 'assets', '{mod}', 'patchouli_books', '{book}', 'zh_cn', ...]
    if len(parts) >= 6 and parts[-1].endswith('.json'):
        mod = parts[2]
        book = parts[4]  # parts[3] 是 'patchouli_books' 固定字
        rest = '/'.join(parts[5:])  # 從 zh_cn 開始
        src_index[(mod, book, rest)] = content

print(f"來源索引: {len(src_index)}", flush=True)

# 建立輸出索引 (mod, book, rest) -> file path
out_index = {}
for f in pb.rglob('*.json'):
    rel = os.path.normpath(str(f.relative_to(pb)))
    parts = rel.split(os.sep)
    # ['assets', '{mod}', 'patchouli_books', '{book}', 'zh_tw', ...]
    if len(parts) >= 5 and parts[-1].endswith('.json'):
        mod = parts[1]
        book = parts[3]  # parts[2] 是 'patchouli_books' 固定字
        rest = '/'.join(parts[4:])  # 從 zh_tw 開始，統一用 /
        out_index[(mod, book, rest)] = f

print(f"輸出索引: {len(out_index)}", flush=True)

# 比對
results = {
    'total_src': len(src_index),
    'total_out': len(out_index),
    'matched': 0,
    'missing_in_output': [],
    'output_no_source': [],
    'key_mismatch': [],
    'cjk_issues': [],
    'ok_mods': set()
}

print("\n比對中...", flush=True)
checked = 0

for (mod, book, src_rest), src_content in src_index.items():
    # src_rest: 'zh_cn/categories/adventure/adventure.json'
    # → out_rest: 'zh_tw/categories/adventure/adventure.json'
    out_rest = src_rest.replace('zh_cn/', 'zh_tw/')
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

    # 兩邊都有，精確比對
    out_file = out_index[key]
    try:
        with open(out_file, 'r', encoding='utf-8') as f:
            tw_data = json.load(f)
    except Exception:
        results['key_mismatch'].append({
            'mod': mod, 'book': book, 'file': out_rest, 'error': '輸出無法解析'
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
            'mod': mod, 'book': book, 'file': out_rest,
            'src_key_count': len(src_keys), 'tw_key_count': len(tw_keys),
            'missing_keys': list(missing_keys)[:10],
            'extra_keys': list(extra_keys)[:10]
        })
    else:
        # 檢查簡體殘留
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
                'mod': mod, 'book': book, 'file': out_rest,
                'issues': issues[:3]
            })
        else:
            results['matched'] += 1
            results['ok_mods'].add(mod)

    if checked % 100 == 0:
        print(f"  {checked}/{len(src_index)}...", flush=True)

# 輸出有沒有不應存在的
src_out_paths = {(mod, book, src_rest.replace('zh_cn/', 'zh_tw/')) for (mod, book, src_rest) in src_index}
for (mod, book, out_rest) in out_index:
    if (mod, book, out_rest) not in src_out_paths:
        results['output_no_source'].append({'mod': mod, 'book': book, 'out_path': out_rest})

results['ok_mods'] = sorted(results['ok_mods'])

print(f"\n=== 結果 ===", flush=True)
print(f"來源: {results['total_src']} | 輸出: {results['total_out']}", flush=True)
print(f"✅ 完全正常: {results['matched']}", flush=True)
print(f"❌ 缺少（來源有、輸出無）: {len(results['missing_in_output'])}", flush=True)
print(f"❌ 多餘（輸出有、無來源）: {len(results['output_no_source'])}", flush=True)
print(f"⚠️  key 不匹配: {len(results['key_mismatch'])}", flush=True)
print(f"⚠️  簡體殘留: {len(results['cjk_issues'])}", flush=True)
print(f"✅ 無問題模組: {results['ok_mods']}", flush=True)

# 寫報告
del results['ok_mods']  # set not JSON serializable
report_path = OUT / 'patchouli_zhTW_verification_report.json'
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n報告: {report_path}", flush=True)

# 顯示問題
if results['missing_in_output']:
    print(f"\n=== 缺少的檔案（按模組，Top10）===")
    by_mod = {}
    for item in results['missing_in_output']:
        by_mod.setdefault(item['mod'], []).append(item['src_path'])
    for m, files in sorted(by_mod.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"  {m}: {len(files)} 個")
        for f in files[:3]:
            print(f"    - {f}")

if results['key_mismatch']:
    print(f"\n=== key 不匹配（前15）===")
    for item in results['key_mismatch'][:15]:
        print(f"  {item['mod']}/{item['book']}: {item['file']}")
        print(f"    src={item.get('src_key_count','?')}, out={item.get('tw_key_count','?')}, missing={len(item.get('missing_keys',[]))}, extra={len(item.get('extra_keys',[]))}")
        if item.get('missing_keys'):
            print(f"    缺key: {item['missing_keys'][:3]}")
        if item.get('extra_keys'):
            print(f"    多key: {item['extra_keys'][:3]}")

if results['cjk_issues']:
    print(f"\n=== 簡體殘留（前10）===")
    for item in results['cjk_issues'][:10]:
        print(f"  {item['mod']}/{item['book']}: {item['file']}")
        for k, c, v in item['issues'][:2]:
            print(f"    {k}: ...{v}... (簡體「{c}」)")

if results['output_no_source']:
    print(f"\n=== 多餘的輸出檔案（不應存在）===")
    by_mod = {}
    for item in results['output_no_source']:
        by_mod.setdefault(item['mod'], []).append(item['out_path'])
    for m, files in sorted(by_mod.items(), key=lambda x: -len(x[1]))[:5]:
        print(f"  {m}: {len(files)} 個")
        for f in files[:3]:
            print(f"    - {f}")
