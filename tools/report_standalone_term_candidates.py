"""單獨成句官方術語候選與效果的唯讀報告。

三個子命令都只讀取資料，不寫入任何資料庫或官方語系檔：

    python -m tools.report_standalone_term_candidates candidates
        列出官方複合詞可推導的單詞候選：來源、目標、推導 key、出現次數、旗標與是否已在 allowlist。
        人工審閱時以此清單決定是否把項目加進 ``STANDALONE_ALLOWLIST``。

    python -m tools.report_standalone_term_candidates moddb --db path/to/translations.db
        以唯讀連線統計 Mod DB 中「單獨成句」條目：總數、有效未審核數、不同原文數、
        來源模組分布，以及 allowlist 放行後的受益條目數（單獨成句的 allowlist 命中）。

    python -m tools.report_standalone_term_candidates effect --fixture tests/data/standalone_term_fixture.json
        比較 allowlist 關閉／開啟時 fixture 的新增命中與新增誤匹配（誤匹配必須為 0）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from translation_tool.core import official_locale
from translation_tool.core.official_locale import (
    OfficialLocaleIndex,
    load_official_locale_index,
)
from translation_tool.core.terminology_model import (
    MATCH_STANDALONE,
    STANDALONE_ALLOWLIST,
)
from translation_tool.utils.text_matching import term_canonical_key

_REVIEWED = "reviewed"


def candidate_rows(index: OfficialLocaleIndex) -> list[dict[str, Any]]:
    """列出每個官方推導單詞的候選資訊，依出現次數與來源排序。"""
    occurrences: Counter[str] = Counter()
    wanted = set(index.derived_terms_by_en)
    for record in index.records_by_key.values():
        for token in set(official_locale._terminology_tokens(record.en_us)):
            if token in wanted:
                occurrences[token] += 1
    rows: list[dict[str, Any]] = []
    for canonical, (display, target, key) in index.derived_terms_by_en.items():
        flags: list[str] = []
        if len(target) == 1:
            flags.append("single_char_target")
        if index._unique_target_for_source(canonical):
            flags.append("has_full_translation")
        allow = STANDALONE_ALLOWLIST.get(canonical)
        if allow is not None and allow != target:
            flags.append("allowlist_target_mismatch")
        rows.append(
            {
                "source": display,
                "target": target,
                "key": key,
                "occurrences": occurrences[canonical],
                "flags": flags,
                "allowlisted": allow == target,
            }
        )
    return sorted(rows, key=lambda row: (-row["occurrences"], row["source"]))


def moddb_standalone_stats(
    db_path: str | Path, index: OfficialLocaleIndex
) -> dict[str, Any]:
    """唯讀統計 Mod DB 單獨成句條目；不建立檔案、不寫入。"""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到 Mod DB：{path}")
    candidates = set(index.derived_terms_by_en)
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT e.mod_id, e.en_us, f.zh_tw, f.review_status "
            "FROM entry e LEFT JOIN effective f ON f.entry_id = e.id "
            "WHERE e.en_us <> '' AND e.en_us NOT LIKE '% %'"
        ).fetchall()
    finally:
        conn.close()
    stats: dict[str, Any] = {
        "standalone_entries": 0,
        "valid_unreviewed": 0,
        "allowlist_hits": 0,
        "allowlist_hits_valid_unreviewed": 0,
        "candidate_hits_not_allowlisted": 0,
    }
    distinct: set[str] = set()
    by_mod: Counter[str] = Counter()
    by_term: Counter[str] = Counter()
    for mod_id, en_us, zh_tw, review_status in rows:
        canonical = term_canonical_key(
            official_locale.normalize_terminology_text(en_us)
        )
        if canonical not in candidates or index._unique_target_for_source(canonical):
            continue
        stats["standalone_entries"] += 1
        valid_unreviewed = bool(zh_tw) and review_status != _REVIEWED
        stats["valid_unreviewed"] += int(valid_unreviewed)
        if canonical in STANDALONE_ALLOWLIST:
            stats["allowlist_hits"] += 1
            stats["allowlist_hits_valid_unreviewed"] += int(valid_unreviewed)
            distinct.add(canonical)
            by_mod[mod_id] += 1
            by_term[canonical] += 1
        else:
            stats["candidate_hits_not_allowlisted"] += 1
    stats["allowlist_distinct_terms"] = len(distinct)
    stats["allowlist_by_term"] = dict(by_term.most_common())
    stats["allowlist_top_mods"] = dict(by_mod.most_common(20))
    return stats


def _matches(index: OfficialLocaleIndex, text: str) -> dict[str, str]:
    """回傳 ``{正規化來源: 目標}``，供前後比較。"""
    return {
        term_canonical_key(match.source_text): match.target_text
        for match in index.official_terminology_matches_for_item(
            {"path": "report:fixture", "source_text": text}
        )
    }


def effect_report(
    index: OfficialLocaleIndex,
    cases: Iterable[Mapping[str, Any]],
    *,
    before_allowlist: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """比較 allowlist 關閉（預設空）與目前設定：新增命中與新增誤匹配。

    fixture 每筆 ``{"source", "expected"}``：``expected`` 為期望新增的目標，``null`` 表示
    不得新增任何命中。新增誤匹配 = 新增命中中，來源不在期望或目標與期望不同者。
    """
    cases = list(cases)
    original = official_locale.STANDALONE_ALLOWLIST
    before: dict[str, dict[str, str]] = {}
    official_locale.STANDALONE_ALLOWLIST = dict(before_allowlist or {})
    try:
        for case in cases:
            before[case["source"]] = _matches(index, case["source"])
    finally:
        official_locale.STANDALONE_ALLOWLIST = original
    new_hits: list[dict[str, str]] = []
    false_matches: list[dict[str, str]] = []
    for case in cases:
        source, expected = case["source"], case.get("expected")
        after = _matches(index, source)
        for canonical, target in after.items():
            if before[source].get(canonical) == target:
                continue
            hit = {"source": source, "term": canonical, "target": target}
            new_hits.append(hit)
            if expected is None or expected != target:
                false_matches.append(hit)
    return {
        "cases": len(before),
        "new_hits": new_hits,
        "new_false_matches": false_matches,
        "new_hit_count": len(new_hits),
        "new_false_match_count": len(false_matches),
        "standalone_match_type": MATCH_STANDALONE,
    }


def _args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("candidates")
    moddb = sub.add_parser("moddb")
    moddb.add_argument("--db", type=Path, required=True)
    effect = sub.add_parser("effect")
    effect.add_argument("--fixture", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """命令列入口；輸出 JSON 到 stdout。"""
    args = _args(argv)
    index = load_official_locale_index()
    if args.command == "candidates":
        result: Any = candidate_rows(index)
    elif args.command == "moddb":
        result = moddb_standalone_stats(args.db, index)
    else:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        result = effect_report(index, fixture["cases"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "effect" and result["new_false_match_count"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
