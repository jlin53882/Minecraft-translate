"""
Patchouli zh_tw 完整性驗證腳本
目標：確認 patchouli_output/zh_tw/ 每個檔案都對應到正確的 zh_cn 來源，無漏掉、無錯誤

比對邏輯：
1. 來源 ZIP 的 zh_cn entry/category JSON → 對應輸出 zh_tw entry/category JSON
2. 逐 key 比對：key 是否全部存在、value 是否為繁體中文（無簡體殘留）
3. 找出：來源有但輸出沒有的（漏掉的）、來源沒有但輸出有的（多餘的）
4. 統計每個模組的問題數

輸出：JSON 格式，方便後續處理
"""
import json, re, zipfile, sys
from pathlib import Path
from collections import defaultdict

BASE = Path(r'C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3_test')
BOOK_ZIP = BASE / 'mods_提取book_輸出.zip'
OUT = BASE / '輸出測試'
po = OUT / 'patchouli_output'
pb = po / 'patchouli_books'

def parse_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return None

def to_translation_path(cn_name):
    """
    將來源 zh_cn 的 path 轉換成輸出的 zh_tw path
    來源: assets/{mod}/patchouli_books/{book}/zh_cn/entries/abc.json
    輸出: assets/{mod}/patchouli_books/{book}/zh_tw/entries/abc.json
    """
    return cn_name.replace('/zh_cn/', '/zh_tw/')

def get_mod_book(cn_name):
    """從來源路徑抽出 mod 和 book 名稱"""
    parts = cn_name.replace('\\', '/').split('/')
    # assets/{mod}/patchouli_books/{book}/zh_cn/entries/abc.json
    if len(parts) >= 4:
        return parts[1], parts[2]
    return None, None

# 簡體特徵字
SIMP_ONLY = set('开关心错为们么过来时还又学觉里给长短点几')

print("開始驗證...", flush=True)

# 建立來源索引
src_cn = {}  # path -> content
with zipfile.ZipFile(BOOK_ZIP, 'r') as zf:
    for name in zf.namelist():
        if '/zh_cn/' in name and name.endswith('.json'):
            try:
                src_cn[name] = zf.read(name).decode('utf-8', errors='replace')
            except Exception:
                pass

print(f"來源 zh_cn 檔案數: {len(src_cn)}", flush=True)

# 建立輸出索引
out_zh_tw = {}
for f in pb.rglob('zh_tw.json'):
    try:
        with open(f, 'r', encoding='utf-8') as fp:
            out_zh_tw[str(f.relative_to(pb))] = fp.read()
    except Exception:
        pass

print(f"輸出 zh_tw 檔案數: {len(out_zh_tw)}", flush=True)

# 逐檔比對
results = []
total_checks = 0
total_ok = 0
total_issues = 0

for src_path, src_content in src_cn.items():
    mod, book = get_mod_book(src_path)
    if not mod:
        continue

    # 對應輸出路徑
    tw_path = to_translation_path(src_path)
    tw_content = out_zh_tw.get(tw_path)

    src_data = None
    try:
        src_data = json.loads(src_content)
    except Exception:
        results.append({'mod': mod, 'book': book, 'file': src_path, 'status': 'ERROR', 'detail': '來源 JSON 無法解析'})
        continue

    if tw_content is None:
        # 來源有，但輸出沒有 = 漏掉了
        results.append({
            'mod': mod, 'book': book, 'file': src_path,
            'status': 'MISSING',
            'detail': f'來源有 {len(src_data)} 個 key，輸出無對應檔案'
        })
        total_issues += 1
        continue

    tw_data = None
    try:
        tw_data = json.loads(tw_content)
    except Exception:
        results.append({
            'mod': mod, 'book': book, 'file': tw_path,
            'status': 'ERROR',
            'detail': '輸出 JSON 無法解析'
        })
        total_issues += 1
        continue

    # 逐 key 比對
    src_keys = set(src_data.keys())
    tw_keys = set(tw_data.keys())

    missing_keys = src_keys - tw_keys
    extra_keys = tw_keys - src_keys
    common_keys = src_keys & tw_keys

    # 檢查 values
    cjk_issues = []
    for k in common_keys:
        v_src = src_data.get(k, '')
        v_tw = tw_data.get(k, '')
        if not isinstance(v_tw, str):
            continue
        # 檢查 value 是否有簡體殘留
        for c in v_tw:
            if c in SIMP_ONLY:
                cjk_issues.append((k, c, v_tw[:30]))
                break

    total_checks += 1
    if missing_keys or extra_keys or cjk_issues:
        total_issues += 1
        total_ok += 1
        results.append({
            'mod': mod, 'book': book,
            'file': src_path,
            'status': 'ISSUE',
            'missing_keys': list(missing_keys)[:10],  # 最多10個
            'extra_keys': list(extra_keys)[:10],
            'cjk_issues': cjk_issues[:5],
            'src_key_count': len(src_keys),
            'tw_key_count': len(tw_keys),
        })
    else:
        total_ok += 1

    # 每 50 個輸出進度
    if total_checks % 50 == 0:
        print(f"  已檢查 {total_checks}/{len(src_cn)} 檔案，目前問題數: {total_issues}", flush=True)

# 輸出報告
report = {
    'summary': {
        '來源 zh_cn 檔案': len(src_cn),
        '輸出 zh_tw 檔案': len(out_zh_tw),
        '已比對': total_checks,
        '正常': total_ok,
        '有問題': total_issues,
    },
    'issues_by_mod': {},
    'all_issues': results
}

# 按模組彙總
for r in results:
    mod = r['mod']
    if mod not in report['issues_by_mod']:
        report['issues_by_mod'][mod] = {'count': 0, 'files': []}
    report['issues_by_mod'][mod]['count'] += 1
    report['issues_by_mod'][mod]['files'].append({
        'file': r['file'],
        'status': r['status'],
        'detail': r.get('detail', ''),
        'missing_keys': r.get('missing_keys', []),
        'extra_keys': r.get('extra_keys', []),
    })

# 寫出報告
report_path = OUT / 'patchouli_zhTW_verification_report.json'
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\n驗證完成！", flush=True)
print(f"來源 zh_cn: {len(src_cn)} 檔", flush=True)
print(f"輸出 zh_tw: {len(out_zh_tw)} 檔", flush=True)
print(f"已比對: {total_checks}", flush=True)
print(f"正常: {total_ok}", flush=True)
print(f"有問題: {total_issues}", flush=True)
print(f"\n報告已寫入: {report_path}", flush=True)

if total_issues > 0:
    print(f"\n有問題的模組:")
    for mod, data in sorted(report['issues_by_mod'].items(), key=lambda x: -x[1]['count']):
        print(f"  {mod}: {data['count']} 個檔案有問題")
