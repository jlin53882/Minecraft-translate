"""Gap Analysis：檢查前 N 個 JAR 的 lang 抽取完整性（唯讀，只輸出到終端機）。

用法：
    python tools/gap_analysis.py --mods-dir <mods 資料夾> [--limit 30]
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="檢查 mods 資料夾中前 N 個 JAR 的 lang 抽取完整性（唯讀）。"
    )
    parser.add_argument("--mods-dir", required=True, type=Path, help="mods 資料夾")
    parser.add_argument(
        "--limit", type=int, default=30, help="檢查的 JAR 數量（預設 30）"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    jars = sorted(args.mods_dir.glob("*.jar"))[: args.limit]

    all_lang_pattern = re.compile(
        r"(?:assets/([^/]+)/)?lang/([^/]+)\.(json|lang)$", re.IGNORECASE
    )

    target_langs = {"en_us", "zh_cn", "zh_tw"}

    print("=" * 60)
    print(f"Gap Analysis: 前 {len(jars)} 個 JAR 的 Lang 檔抽取完整性")
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
        except (zipfile.BadZipFile, OSError):
            continue

        if not all_langs_in_jar:
            print(f"  [無lang]  {jar.name}")
            continue

        missing = {k for k in all_langs_in_jar if k not in target_langs}
        total_missing += len(missing)

        for m in missing:
            if m not in all_missing_langs:
                all_missing_langs[m] = []
            all_missing_langs[m].append(jar.name)

        lang_summary = ", ".join(
            f"{k}({len(v)}檔)" for k, v in sorted(all_langs_in_jar.items())
        )
        status = "✅" if not missing else f"⚠️ 缺：{', '.join(sorted(missing))}"
        print(f"  {status} {jar.name}")
        print(f"         抓到：{lang_summary}")

    print()
    if all_missing_langs:
        print(f"⚠️  共有 {total_missing} 個 非 en_us/zh_cn/zh_tw 的 lang 檔被正則跳過")
        for lang, jars_with_lang in sorted(all_missing_langs.items()):
            print(
                f"   {lang}: {len(jars_with_lang)} 個 JAR — 例如：{jars_with_lang[0]}"
            )
    else:
        print("✅ 所有 lang 檔都已被 en_us/zh_cn/zh_tw 正則覆蓋")

    print()
    print("=" * 60)
    print(f"其他可抽取的翻譯資源（前 {len(jars)} 個 JAR）")
    print("=" * 60)

    translatable_patterns = {
        "kubejs/lang": re.compile(r"kubejs/lang/([^/]+)\.(json|lang)$", re.IGNORECASE),
        "config/*/lang": re.compile(
            r"config/([^/]+)/lang/([^/]+)\.(json|lang)$", re.IGNORECASE
        ),
        "resources/lang": re.compile(
            r"resources/([^/]+)/lang/([^/]+)\.(json|lang)$", re.IGNORECASE
        ),
        "patchouli_books": re.compile(
            r"(?:data/([^/]+)/)?patchouli_books/([^/]+)/book\.json$", re.IGNORECASE
        ),
        "ftb_quests": re.compile(
            r"config/ftbquests/quests/chapters/([^/]+\.snbt)$", re.IGNORECASE
        ),
    }

    found_counts = {k: set() for k in translatable_patterns}

    for jar in jars:
        try:
            with zipfile.ZipFile(jar) as zf:
                for name in zf.namelist():
                    for pname, pat in translatable_patterns.items():
                        if pat.search(name):
                            found_counts[pname].add(jar.name)
        except (zipfile.BadZipFile, OSError):
            continue

    for pname, jars_with in found_counts.items():
        if jars_with:
            print(f"  {pname}: {len(jars_with)} 個 JAR — 例如：{min(jars_with)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
