"""以唯讀 ModDB 抽樣，驗證 LM 翻譯的可觀測品質指標。

這個工具刻意把目前列為 ``reviewed`` 的完整句從提示參考中排除，避免把
評估答案直接餵給模型；同模組／版本的其他已審核術語仍會注入，模擬正式
ModDB 翻譯流程。它可以觀察輸出覆蓋率、嚴格參考相同比率、術語命中率與
格式問題，但沒有人工語意標籤時，不能把任何一項宣稱成「語意準確率」。

範例（不會寫入資料庫）：

    uv run python -m tools.benchmark_translation_quality \
        --limit 1000 --batch-size 200 --model gemini-3.1-flash-lite
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from translation_tool.core import lm_config_rules, lm_translator_main
from translation_tool.core.lm_format_retry import quality_issues
from translation_tool.core.official_locale import (
    normalize_terminology_text,
    reviewed_translation_for_item,
    terminology_terms_for_item,
)
from translation_tool.translation_db import TranslationDB
from translation_tool.translation_db.schema import cache_type_for_kind
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.text_matching import term_canonical_key

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KINDS = (
    "lang",
    "guide",
    "patchouli",
    "kubejs_lang",
    "ftb_quest_lang",
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _database_path(config_path: Path, config: dict[str, Any]) -> Path:
    raw_path = str((config.get("translation_db") or {}).get("path") or "").strip()
    if not raw_path:
        raise SystemExit("config.json 沒有設定 translation_db.path")
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def _load_candidates(db: TranslationDB, limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    query = """
        SELECT e.id, e.kind, e.mod_id, e.key, e.en_us, e.mc_version,
               MIN(t.zh_tw) AS gold
        FROM entry e
        JOIN translation t ON t.entry_id = e.id
        WHERE t.review_status = 'reviewed'
          AND e.kind = ?
          AND trim(e.en_us) <> ''
          AND trim(t.zh_tw) <> ''
          AND e.en_us <> t.zh_tw
        GROUP BY e.id
        HAVING COUNT(DISTINCT t.zh_tw) = 1
        ORDER BY e.id
        LIMIT ?
    """
    for kind in DEFAULT_KINDS:
        rows = db._conn.execute(query, (kind, max(1, int(limit)))).fetchall()
        for entry_id, row_kind, mod_id, key, source, version, gold in rows:
            grouped[kind].append(
                {
                    "entry_id": int(entry_id),
                    "kind": str(row_kind),
                    "mod_id": str(mod_id or ""),
                    "key": str(key or ""),
                    "source": str(source),
                    "version": str(version) if version else None,
                    "gold": str(gold),
                }
            )

    # 先保留每種格式的固定配額，確保一批 1000 筆不會被單一種類壟斷；
    # KubeJS／FTB 若樣本較少，再用 lang 填滿剩餘數量。
    selected: list[dict[str, Any]] = []
    per_kind = max(1, int(limit) // len(DEFAULT_KINDS))
    for kind in DEFAULT_KINDS:
        selected.extend(grouped.get(kind, [])[:per_kind])
    remaining = max(0, int(limit) - len(selected))
    for kind in ("lang", "guide", "patchouli"):
        if remaining <= 0:
            break
        extra = grouped.get(kind, [])[per_kind : per_kind + remaining]
        selected.extend(extra)
        remaining -= len(extra)
    return selected[: max(1, int(limit))]


def _build_items(
    db: TranslationDB,
    candidates: list[dict[str, Any]],
    *,
    holdout_exact: bool = True,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates, start=1):
        source = candidate["source"]
        exact_glossary = db.reviewed_exact_glossary(
            candidate["mod_id"],
            candidate["version"],
            candidate["kind"],
            [source],
        )
        if holdout_exact:
            exact_glossary = []
        glossary = db.reviewed_lang_glossary(
            candidate["mod_id"], candidate["version"], [source]
        )
        if holdout_exact:
            source_key = term_canonical_key(normalize_terminology_text(source))
            # Hold out the exact source row.  Other reviewed rows from the same
            # module/version remain available as terminology references.
            glossary = [
                entry
                for entry in glossary
                if term_canonical_key(
                    normalize_terminology_text(str(entry.get("en_us") or ""))
                )
                != source_key
            ]
        items.append(
            {
                "_benchmark_id": str(ordinal),
                "_kind": candidate["kind"],
                "_mod_id": candidate["mod_id"],
                "_expected_version": candidate["version"],
                "cache_type": cache_type_for_kind(candidate["kind"]),
                "file": f"assets/{candidate['mod_id']}/lang/en_us.json",
                "path": candidate["key"],
                "key": candidate["key"],
                "text": source,
                "source_text": source,
                "_reviewed_glossary": glossary,
                "_reviewed_exact_glossary": exact_glossary,
            }
        )
    return items


def _compact(value: object) -> str:
    return " ".join(str(value or "").split())


def _output_map(results: list[dict[str, Any]] | None) -> dict[str, str | None]:
    return {
        str(item["_benchmark_id"]): item.get("text")
        for item in results or []
        if item.get("_benchmark_id") is not None
    }


def _metrics(
    candidates: list[dict[str, Any]],
    items: list[dict[str, Any]],
    outputs: dict[str, str | None],
) -> dict[str, Any]:
    rows = []
    for ordinal, (candidate, item) in enumerate(zip(candidates, items, strict=True), 1):
        output = outputs.get(str(ordinal))
        output_text = output if isinstance(output, str) else ""
        terms = terminology_terms_for_item(item)
        term_hits = sum(int(target in output_text) for _source, target in terms)
        format_issues = (
            quality_issues(item, output_text) if output_text else ["沒有輸出"]
        )
        terminology_issues = [
            f"術語未命中：{source} → {target}"
            for source, target in terms
            if target not in output_text
        ]
        rows.append(
            {
                "kind": candidate["kind"],
                "output": bool(output_text.strip()),
                "strict_exact": _compact(output_text) == _compact(candidate["gold"]),
                "source_unchanged": _compact(output_text)
                == _compact(candidate["source"]),
                "term_count": len(terms),
                "term_hits": term_hits,
                "format_or_quality_issues": [*format_issues, *terminology_issues],
                "format_issue": bool(format_issues),
                "terminology_issue": bool(terminology_issues),
            }
        )

    output_rows = sum(row["output"] for row in rows)
    exact_rows = sum(row["strict_exact"] for row in rows if row["output"])
    source_unchanged = sum(row["source_unchanged"] for row in rows if row["output"])
    total_terms = sum(row["term_count"] for row in rows)
    term_hits = sum(row["term_hits"] for row in rows)
    by_kind: dict[str, dict[str, int]] = {}
    for kind in sorted({candidate["kind"] for candidate in candidates}):
        kind_rows = [
            row
            for row, candidate in zip(rows, candidates, strict=True)
            if candidate["kind"] == kind
        ]
        by_kind[kind] = {
            "input": len(kind_rows),
            "output": sum(row["output"] for row in kind_rows),
            "strict_exact": sum(
                row["strict_exact"] for row in kind_rows if row["output"]
            ),
            "quality_issue_rows": sum(
                bool(row["format_or_quality_issues"]) for row in kind_rows
            ),
            "format_issue_rows": sum(row["format_issue"] for row in kind_rows),
            "terminology_issue_rows": sum(
                row["terminology_issue"] for row in kind_rows
            ),
        }
    return {
        "input_rows": len(rows),
        "output_rows": output_rows,
        "output_coverage": round(output_rows / len(rows), 4) if rows else None,
        "strict_exact_on_emitted": round(exact_rows / output_rows, 4)
        if output_rows
        else None,
        "source_unchanged_on_emitted": round(source_unchanged / output_rows, 4)
        if output_rows
        else None,
        "terminology_references": total_terms,
        "terminology_hit_rate": round(term_hits / total_terms, 4)
        if total_terms
        else None,
        "quality_issue_rows": sum(
            bool(row["format_or_quality_issues"]) for row in rows
        ),
        "format_issue_rows": sum(row["format_issue"] for row in rows),
        "terminology_issue_rows": sum(row["terminology_issue"] for row in rows),
        "by_kind": by_kind,
    }


def main() -> int:
    args = _args()
    if args.limit <= 0 or args.batch_size <= 0:
        raise SystemExit("--limit 與 --batch-size 必須大於 0")
    config = load_config(args.config)
    db_path = _database_path(args.config, config)
    if not db_path.is_file():
        raise SystemExit(f"找不到唯讀資料庫：{db_path}")

    db = TranslationDB(db_path, readonly=True, create=False, sync_priority=False)
    try:
        candidates = _load_candidates(db, args.limit)
        if len(candidates) < args.limit:
            raise SystemExit(
                f"只有 {len(candidates)} 筆可用 reviewed 樣本，無法完成要求的 {args.limit} 筆"
            )
        items = _build_items(db, candidates)
        consistency_items = _build_items(db, candidates, holdout_exact=False)
        reviewed_exact_rows = sum(
            reviewed_translation_for_item(item) == candidate["gold"]
            for candidate, item in zip(candidates, consistency_items, strict=True)
        )
    finally:
        db.close()

    runtime_config = copy.deepcopy(config)
    lm_config = runtime_config.setdefault("lm_translator", {})
    lm_config["temperature"] = args.temperature
    for key in (
        "initial_batch_size_lang",
        "initial_batch_size_patchouli",
        "initial_batch_size_md",
        "initial_batch_size_ftb",
        "initial_batch_size_kubejs",
    ):
        lm_config[key] = args.batch_size

    outputs: dict[str, str | None] = {}
    statuses: list[str] = []
    batch_sizes: list[int] = []
    with (
        patch.object(
            lm_translator_main,
            "get_models_config",
            return_value={args.model: {"enabled": True}},
        ),
        patch.object(lm_translator_main, "load_config", return_value=runtime_config),
        patch.object(lm_config_rules, "load_config", return_value=runtime_config),
    ):
        for start in range(0, len(items), args.batch_size):
            batch = items[start : start + args.batch_size]
            results, status = lm_translator_main.translate_batch_smart(
                batch, total=len(items), dry_run=False
            )
            outputs.update(_output_map(results))
            statuses.append(status)
            batch_sizes.append(len(batch))

    report = {
        "benchmark": "translation_quality_holdout",
        "database": str(db_path),
        "provider": "gemini",
        "model": args.model,
        "temperature": args.temperature,
        "batch_sizes": batch_sizes,
        "statuses": statuses,
        "source_only_holdout": True,
        "exact_reviewed_source_removed_from_prompt": True,
        "reviewed_exact_consistency_rows": reviewed_exact_rows,
        "reviewed_exact_consistency_rate": round(
            reviewed_exact_rows / len(candidates), 4
        ),
        "semantic_accuracy_claim": None,
        "metrics": _metrics(candidates, items, outputs),
        "sample_counts": dict(Counter(candidate["kind"] for candidate in candidates)),
    }
    output = args.output or (
        ROOT / ".artifacts" / "translation-quality" / f"{args.model}-holdout.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
