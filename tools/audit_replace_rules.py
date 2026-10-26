"""審查 replace_rules.json：以官方語料（可選模組資料庫）量測規則品質。

語料是「簡中原文 → 參考繁中」的配對，輸出一律走專案真實路徑：
``safe_convert_text``（CJK 片段 s2twp）→ ``apply_replace_rules``（最長匹配、單次掃描、目的詞受保護）。

- 官方語料：``assets/official_locales/minecraft/zh_cn.json`` 對 ``zh_tw.json`` 的同名 key。
- 模組語料（``--mod-db``）：Mod 資料庫中 1.20.1 的 JAR 自帶 ``zh_cn``／``zh_tw``（``source=1``），
  以 ``(zh_cn, zh_tw)`` 去重；JAR 譯文本身可能不一致，只用來偵測規則是否傷害複合詞。

用法::

    python tools/audit_replace_rules.py                       # 官方語料指標 + 靜態檢查
    python tools/audit_replace_rules.py --mod-db moddb.db     # 同時量測模組語料
    python tools/audit_replace_rules.py --per-rule            # 逐條規則變好／變差（較慢）
    python tools/audit_replace_rules.py --rules other.json --json report.json

退出碼恆為 0；此工具只報告，不修改規則檔。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from translation_tool.utils.replace_rules import (  # noqa: E402
    ReplaceRules,
    apply_replace_rules,
)
from translation_tool.utils.replace_rules_diagnostics import (  # noqa: E402
    CROSS_REFERENCE,
    DUPLICATE_FROM,
    EDGE_SPACE,
    diagnose_rules,
)
from translation_tool.utils.text_processor import (  # noqa: E402
    load_replace_rules,
    safe_convert_text,
)

DEFAULT_RULES = REPO / "replace_rules.json"
OFFICIAL_CN = REPO / "assets/official_locales/minecraft/zh_cn.json"
OFFICIAL_TW = REPO / "assets/official_locales/minecraft/zh_tw.json"

#: 「名稱類」官方 key 前綴：這些 key 的譯文是單一名詞，規則最該做到完全一致。
NAME_KEY_PREFIXES = (
    "block.",
    "item.",
    "entity.",
    "effect.",
    "enchantment.",
    "biome.",
    "container.",
    "potion.",
    "painting.",
    "trim_pattern.",
    "trim_material.",
    "instrument.",
    "banner.",
    "dimension.",
    "fluid.",
    "jukebox_song.",
)
REGEX_CHARS = ".?*[]()\\"


@dataclass(frozen=True)
class Pair:
    """一筆語料：簡中原文、s2twp 後的文字與參考繁中。"""

    corpus: str  # "official" | "mod"
    key: str
    cn: str
    ref: str
    base: str  # safe_convert_text(cn)

    @property
    def is_name(self) -> bool:
        return self.key.startswith(NAME_KEY_PREFIXES)


def load_official_pairs() -> list[Pair]:
    """官方 zh_cn／zh_tw 同名 key 的配對（兩邊皆非空）。"""
    cn = json.loads(OFFICIAL_CN.read_text(encoding="utf-8"))
    tw = json.loads(OFFICIAL_TW.read_text(encoding="utf-8"))
    return [
        Pair("official", key, text, tw[key], safe_convert_text(text))
        for key, text in cn.items()
        if text and tw.get(key)
    ]


def load_mod_pairs(db_path: str | Path, mc_version: str = "1.20.1") -> list[Pair]:
    """Mod 資料庫的 JAR 自帶 zh_cn／zh_tw 配對，依 (zh_cn, zh_tw) 去重。"""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """SELECT e.key, t.zh_cn, t.zh_tw
               FROM translation t JOIN entry e ON e.id = t.entry_id
               WHERE e.mc_version = ? AND t.source = 1
                 AND t.zh_tw <> '' AND t.zh_cn <> ''""",
            (mc_version,),
        ).fetchall()
    finally:
        con.close()
    seen: set[tuple[str, str]] = set()
    pairs: list[Pair] = []
    for key, cn, tw in rows:
        if (cn, tw) in seen:
            continue
        seen.add((cn, tw))
        pairs.append(Pair("mod", key, cn, tw, safe_convert_text(cn)))
    return pairs


def similarity(a: str, b: str) -> float:
    """字元序列相似度（0~1）；完全相同為 1。"""
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def convert_all(pairs: Sequence[Pair], rules: Sequence[Mapping[str, str]]) -> list[str]:
    """對每筆語料套用規則，回傳與 pairs 同順序的輸出。"""
    return [apply_replace_rules(p.base, rules) for p in pairs]


def _bucket(pairs: Sequence[Pair], outputs: Sequence[str], select) -> dict:
    n = exact = 0
    sim_sum = 0.0
    for pair, out in zip(pairs, outputs):
        if not select(pair):
            continue
        n += 1
        if out == pair.ref:
            exact += 1
            sim_sum += 1.0
        else:
            sim_sum += similarity(out, pair.ref)
    return {
        "n": n,
        "exact": exact,
        "exact_rate": round(exact / n, 4) if n else None,
        "mean_sim": round(sim_sum / n, 4) if n else None,
    }


def corpus_metrics(pairs: Sequence[Pair], outputs: Sequence[str]) -> dict:
    """全部 key／名稱類 key 的完全一致率與平均相似度，依語料分開。"""
    result: dict[str, dict] = {}
    for corpus in sorted({p.corpus for p in pairs}):
        result[corpus] = {
            "all": _bucket(pairs, outputs, lambda p, c=corpus: p.corpus == c),
            "name": _bucket(
                pairs, outputs, lambda p, c=corpus: p.corpus == c and p.is_name
            ),
        }
    return result


def compare_outputs(
    pairs: Sequence[Pair], before: Sequence[str], after: Sequence[str]
) -> dict:
    """比較兩組輸出：完全一致方向（變好／變差）的 key 數，依語料分開。"""
    result: dict[str, dict] = {}
    for corpus in sorted({p.corpus for p in pairs}):
        better = worse = 0
        worse_keys: list[str] = []
        for p, b, a in zip(pairs, before, after):
            if p.corpus != corpus or a == b:
                continue
            if a == p.ref:
                better += 1
            elif b == p.ref:
                worse += 1
                worse_keys.append(p.key)
            elif similarity(a, p.ref) > similarity(b, p.ref):
                better += 1
            elif similarity(a, p.ref) < similarity(b, p.ref):
                worse += 1
                worse_keys.append(p.key)
        result[corpus] = {
            "better": better,
            "worse": worse,
            "worse_keys": worse_keys[:20],
        }
    return result


# ---------------------------------------------------------------- 靜態檢查
def _reachable(src: str) -> bool:
    """from 是否可能出現在 s2twp 之後：某種簡體寫法經 s2twp 能回到 from 本身。"""
    if not _REACH_CONVERTERS:
        from opencc import OpenCC

        _REACH_CONVERTERS.extend(OpenCC(cfg) for cfg in ("t2s", "tw2sp"))
    return any(
        safe_convert_text(conv.convert(src)) == src for conv in _REACH_CONVERTERS
    )


_REACH_CONVERTERS: list = []


def _is_regex(rule: Mapping[str, str]) -> bool:
    return any(ch in rule["from"] for ch in REGEX_CHARS)


def static_checks(rules: Sequence[Mapping[str, str]]) -> dict[str, list]:
    """不需要語料的結構檢查，每項回傳問題清單（空清單代表通過）。

    - ``edge_whitespace``：from／to 前後有空白。
    - ``inner_space_in_to``：to 內含空白（除非 from 也有空白或 to 含英文字母，通常是多餘空白）。
    - ``unreachable_from``：from 不是任何簡體寫法經 s2twp 後的結果（t2s／tw2sp 回推再轉換無法回到 from），永遠不會命中。
    - ``output_differs_from_to``：from 單獨套用全部規則後的輸出不等於 to（被其他規則二次正規化）。
    - ``duplicate_from``：同一個 from 出現多次。
    - ``conflicts``：to 內含其他規則 from（交叉引用，由 ``diagnose_rules`` 偵測）。
    """
    literal = [(i, r) for i, r in enumerate(rules) if not _is_regex(r)]
    report: dict[str, list] = defaultdict(list)
    loaded = ReplaceRules(dict(r) for r in rules)
    for i, r in literal:
        src, dst = r["from"], r["to"]
        if (
            " " in dst
            and " " not in src
            and not any(c.isascii() and c.isalpha() for c in dst)
        ):
            report["inner_space_in_to"].append({"index": i, "from": src, "to": dst})
        if not _reachable(src):
            report["unreachable_from"].append(
                {"index": i, "from": src, "to": dst, "s2twp": safe_convert_text(src)}
            )
        else:
            out = apply_replace_rules("「" + src + "」", loaded)[1:-1]
            if out != dst:
                report["output_differs_from_to"].append(
                    {"index": i, "from": src, "to": dst, "output": out}
                )
    names = {
        DUPLICATE_FROM: "duplicate_from",
        EDGE_SPACE: "edge_whitespace",
        CROSS_REFERENCE: "conflicts",
    }
    for idx, issues in diagnose_rules(rules).items():
        for issue in issues:
            if issue.kind in names:
                report[names[issue.kind]].append(
                    {
                        "index": idx,
                        "from": rules[idx]["from"],
                        "to": rules[idx]["to"],
                        "other": issue.other,
                        "message": issue.message,
                    }
                )
    return dict(report)


# ---------------------------------------------------------------- 逐條規則歸因
def per_rule_effect(
    pairs: Sequence[Pair], rules: Sequence[Mapping[str, str]]
) -> list[dict]:
    """逐條規則：移除該規則後，與參考相比哪些 key 變差（＝規則讓它變好）或變好（＝規則有害）。

    只重算 base 含該規則 from 的語料。回傳 ``worse``（規則讓結果變差的 key 數）與
    ``better``（規則讓結果變好的 key 數），依官方／模組分開。
    """
    full = ReplaceRules(dict(r) for r in rules)
    full_out = convert_all(pairs, full)
    rows: list[dict] = []
    for idx, rule in enumerate(rules):
        if _is_regex(rule) or not rule["from"]:
            continue
        hit = [i for i, p in enumerate(pairs) if rule["from"] in p.base]
        if not hit:
            rows.append(
                {"index": idx, "from": rule["from"], "to": rule["to"], "hits": 0}
            )
            continue
        variant = ReplaceRules(dict(r) for j, r in enumerate(rules) if j != idx)
        stat = {
            "better_official": 0,
            "worse_official": 0,
            "better_mod": 0,
            "worse_mod": 0,
        }
        for i in hit:
            p = pairs[i]
            without = apply_replace_rules(p.base, variant)
            if without == full_out[i]:
                continue
            sf, sw = similarity(full_out[i], p.ref), similarity(without, p.ref)
            suffix = "official" if p.corpus == "official" else "mod"
            if sf > sw:
                stat["better_" + suffix] += 1
            elif sf < sw:
                stat["worse_" + suffix] += 1
        rows.append(
            {
                "index": idx,
                "from": rule["from"],
                "to": rule["to"],
                "hits": len(hit),
                **stat,
            }
        )
    return rows


# ---------------------------------------------------------------- 輸出
def _format_metrics(metrics: dict) -> list[str]:
    lines = []
    for corpus, buckets in metrics.items():
        for name, b in buckets.items():
            label = {"all": "全部", "name": "名稱類"}[name]
            rate = (
                f"{b['exact_rate'] * 100:.2f}%" if b["exact_rate"] is not None else "-"
            )
            lines.append(
                f"{corpus:8s} {label:4s} 完全一致 {b['exact']}/{b['n']} ({rate})"
                f"  平均相似度 {b['mean_sim']}"
            )
    return lines


def run_audit(
    rules_path: Path,
    mod_db: str | None = None,
    per_rule: bool = False,
    reference_rules: Iterable[Mapping[str, str]] | None = None,
) -> dict:
    """執行完整審查並回傳可序列化的結果。"""
    rules = load_replace_rules(str(rules_path), force_reload=True, source="audit")
    pairs = load_official_pairs()
    if mod_db:
        pairs += load_mod_pairs(mod_db)
    outputs = convert_all(pairs, rules)
    result: dict = {
        "rules_path": str(rules_path),
        "rule_count": len(rules),
        "baseline_s2twp_only": corpus_metrics(pairs, [p.base for p in pairs]),
        "with_rules": corpus_metrics(pairs, outputs),
        "static": static_checks(rules),
    }
    if reference_rules is not None:
        ref_out = convert_all(pairs, ReplaceRules(dict(r) for r in reference_rules))
        result["vs_reference"] = compare_outputs(pairs, ref_out, outputs)
    if per_rule:
        rows = per_rule_effect(pairs, rules)
        result["harmful_rules"] = sorted(
            (
                r
                for r in rows
                if r.get("worse_official", 0) + r.get("worse_mod", 0)
                > r.get("better_official", 0) + r.get("better_mod", 0)
            ),
            key=lambda r: (
                -(
                    r["worse_official"]
                    + r["worse_mod"]
                    - r["better_official"]
                    - r["better_mod"]
                )
            ),
        )
        result["dead_rules"] = [r for r in rows if r["hits"] == 0]
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES,
        help="規則檔（預設 repo 根目錄 replace_rules.json）",
    )
    parser.add_argument("--mod-db", help="Mod 資料庫路徑（可選，量測模組語料）")
    parser.add_argument("--per-rule", action="store_true", help="逐條規則歸因（較慢）")
    parser.add_argument(
        "--reference", type=Path, help="另一份規則檔，列出相對它變好／變差的 key 數"
    )
    parser.add_argument("--json", type=Path, help="把完整結果寫成 JSON")
    args = parser.parse_args(argv)

    reference = None
    if args.reference:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
    result = run_audit(args.rules, args.mod_db, args.per_rule, reference)

    print(f"規則檔：{result['rules_path']}（{result['rule_count']} 條）")
    print("-- 只做 s2twp（不套規則）")
    print("\n".join(_format_metrics(result["baseline_s2twp_only"])))
    print("-- 套用規則後")
    print("\n".join(_format_metrics(result["with_rules"])))
    if "vs_reference" in result:
        print("-- 相對參考規則檔")
        for corpus, v in result["vs_reference"].items():
            print(
                f"{corpus}: 變好 {v['better']} 變差 {v['worse']} {v['worse_keys'][:5]}"
            )
    print("-- 靜態檢查")
    for name, items in result["static"].items():
        print(f"{name}: {len(items)}")
        for item in items[:5]:
            print("   ", item)
    if "harmful_rules" in result:
        print(
            f"-- 有害規則 {len(result['harmful_rules'])} 條；未命中規則 {len(result['dead_rules'])} 條"
        )
        for r in result["harmful_rules"][:10]:
            print("   ", r)
    if args.json:
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
