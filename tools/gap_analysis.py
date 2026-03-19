# -*- coding: utf-8 -*-
"""Gap Analysis: 檢查前30個JAR的lang抽取完整性"""
import sys, zipfile, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

MODS_DIR = Path(r"C:\Users\admin\Desktop\.minecraft\versions\All the Mods 10 4.3\mods")
jars = sorted(MODS_DIR.glob("*.jar"))[:30]

all_lang_pattern = re.compile(
    r"(?:assets/([^/]+)/)?lang/([^/]+)\.(json|lang)$", re.IGNORECASE
)

target_langs = {"en_us", "zh_cn", "zh_tw"}

print("=" * 60)
print("Gap Analysis: 前 30 個 JAR 的 Lang 檔抽取完整性")
print("=" * 60)

total_missing = 0
all_missing_langs = {}

for jar in jars:
    all_langs_in_jar = {}
    try:
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                m = all_lang_pattern.match(name)
                if m:
                    lang = m.group(2).lower()
                    if lang not in all_langs_in_jar:
                        all_langs_in_jar[lang] = []
                    all_langs_in_jar[lang].append(name)
    except:
        continue

    if not all_langs_in_jar:
        print(f"  [無lang]  {jar.name}")
        continue

    extracted = {k for k in all_langs_in_jar if k in target_langs}
    missing = {k for k in all_langs_in_jar if k not in target_langs}
    total_missing += len(missing)

    for m in missing:
        if m not in all_missing_langs:
            all_missing_langs[m] = []
        all_missing_langs[m].append(jar.name)

    lang_summary = ", ".join(f"{k}({len(v)}檔)" for k, v in sorted(all_langs_in_jar.items()))
    status = "✅" if not missing else f"⚠️ 缺：{', '.join(sorted(missing))}"
    print(f"  {status} {jar.name}")
    print(f"         抓到：{lang_summary}")

print()
if all_missing_langs:
    print(f"⚠️  共有 {total_missing} 個 非 en_us/zh_cn/zh_tw 的 lang 檔被正則跳過")
    for lang, jars_with_lang in sorted(all_missing_langs.items()):
        print(f"   {lang}: {len(jars_with_lang)} 個 JAR — 例如：{jars_with_lang[0]}")
else:
    print("✅ 所有 lang 檔都已被 en_us/zh_cn/zh_tw 正則覆蓋")

print()
print("=" * 60)
print("其他可抽取的翻譯資源（前30個JAR）")
print("=" * 60)

translatable_patterns = {
    "kubejs/lang":        re.compile(r"kubejs/lang/([^/]+)\.(json|lang)$", re.IGNORECASE),
    "config/*/lang":      re.compile(r"config/([^/]+)/lang/([^/]+)\.(json|lang)$", re.IGNORECASE),
    "resources/lang":     re.compile(r"resources/([^/]+)/lang/([^/]+)\.(json|lang)$", re.IGNORECASE),
    "patchouli_books":    re.compile(r"(?:data/([^/]+)/)?patchouli_books/([^/]+)/book\.json$", re.IGNORECASE),
    "ftb_quests":         re.compile(r"config/ftbquests/quests/chapters/([^/]+\.snbt)$", re.IGNORECASE),
}

found_counts = {k: set() for k in translatable_patterns}

for jar in jars:
    try:
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                for pname, pat in translatable_patterns.items():
                    if pat.search(name):
                        found_counts[pname].add(jar.name)
    except:
        continue

for pname, jars_with in found_counts.items():
    if jars_with:
        print(f"  {pname}: {len(jars_with)} 個 JAR — 例如：{sorted(jars_with)[0]}")
