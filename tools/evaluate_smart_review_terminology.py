"""Evaluate smart-review output against reviewed translation references.

This is a semantic/reference check, not a response-format check.  It reports
whole-value exact matches for candidates with a unique reviewed translation for
the same kind/module/key, plus a named-key subset and reviewed glossary-term
hits when the candidate carried glossary context.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from translation_tool.translation_db import TranslationDB, load_db_settings
from translation_tool.utils.config_manager import load_config

ROOT = Path(__file__).resolve().parents[1]
NAMED_KEY_PREFIXES = (
    "item.",
    "block.",
    "entity.",
    "biome.",
    "effect.",
    "fluid.",
    "enchantment.",
    "dimension.",
    "painting.",
    "trim_pattern.",
    "instrument.",
    "banner.",
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _references(path: Path) -> dict[tuple[str, str, str], str]:
    uri = f"file:{quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        targets: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
        for kind, mod_id, key, zh_tw in connection.execute(
            "SELECT e.kind, e.mod_id, e.key, f.zh_tw "
            "FROM entry e JOIN effective f ON f.entry_id=e.id "
            "WHERE f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
            "AND e.en_us<>''"
        ):
            targets[(kind, mod_id, key)].add(_compact(zh_tw))
        return {
            key: next(iter(values))
            for key, values in targets.items()
            if len(values) == 1
        }
    finally:
        connection.close()


def _candidate_output(candidate) -> str | None:
    if candidate.status == "no_change" or candidate.decision == "no_change":
        return candidate.current_zh_tw
    if candidate.status == "ready" and candidate.suggested_zh_tw:
        return candidate.suggested_zh_tw
    return None


def _is_named_key(candidate) -> bool:
    return candidate.kind == "lang" and candidate.key.startswith(NAMED_KEY_PREFIXES)


def _summary(rows: list[tuple[object, str]]) -> dict[str, object]:
    total = len(rows)
    emitted = 0
    exact = 0
    baseline_exact = 0
    improved = 0
    regressed = 0
    for candidate, reference in rows:
        baseline_hit = _compact(candidate.current_zh_tw) == reference
        if baseline_hit:
            baseline_exact += 1
        output = _candidate_output(candidate)
        if output is None:
            continue
        emitted += 1
        model_hit = _compact(output) == reference
        if model_hit:
            exact += 1
        if not baseline_hit and model_hit:
            improved += 1
        elif baseline_hit and not model_hit:
            regressed += 1
    return {
        "gold_scored": total,
        "emitted_output": emitted,
        "invalid_or_no_output": total - emitted,
        "baseline_exact": baseline_exact,
        "model_exact": exact,
        "baseline_exact_rate": round(baseline_exact / total, 4) if total else None,
        "model_exact_rate_on_all": round(exact / total, 4) if total else None,
        "model_exact_rate_on_emitted": (round(exact / emitted, 4) if emitted else None),
        "improved": improved,
        "regressed": regressed,
    }


def _glossary_summary(candidates: list[object]) -> dict[str, object]:
    total = emitted = baseline_hits = baseline_emitted_hits = model_hits = 0
    for candidate in candidates:
        output = _candidate_output(candidate)
        for item in candidate.context:
            if item.get("kind") != "reviewed_glossary":
                continue
            expected = _compact(str(item.get("translation", "")))
            if not expected:
                continue
            total += 1
            baseline_hit = expected in _compact(candidate.current_zh_tw)
            if baseline_hit:
                baseline_hits += 1
            if output is not None:
                emitted += 1
                if baseline_hit:
                    baseline_emitted_hits += 1
                if expected in _compact(output):
                    model_hits += 1
    return {
        "term_references": total,
        "term_emitted": emitted,
        "baseline_term_hits": baseline_hits,
        "model_term_hits": model_hits,
        "baseline_term_hit_rate": (round(baseline_hits / total, 4) if total else None),
        "model_term_hit_rate_on_all": (round(model_hits / total, 4) if total else None),
        "model_term_hit_rate_on_emitted": (
            round(model_hits / emitted, 4) if emitted else None
        ),
        "baseline_term_hit_rate_on_emitted": (
            round(baseline_emitted_hits / emitted, 4) if emitted else None
        ),
    }


def main() -> int:
    args = _args()
    config = load_config(args.config)
    settings = load_db_settings(config)
    db_path = settings.resolved_path()
    db = TranslationDB(db_path, priority=settings.priority, create=False)
    try:
        run = db.get_smart_review_run(args.run_id)
        if run is None:
            raise SystemExit(f"找不到 smart review run：{args.run_id}")
        candidates = db.list_smart_review_candidates(args.run_id)
    finally:
        db.close()

    references = _references(db_path)
    gold_rows = [
        (
            candidate,
            references[(candidate.kind, candidate.mod_id, candidate.key)],
        )
        for candidate in candidates
        if (candidate.kind, candidate.mod_id, candidate.key) in references
    ]
    named_rows = [
        (candidate, reference)
        for candidate, reference in gold_rows
        if _is_named_key(candidate)
    ]
    suggestion_rows = [
        (candidate, reference)
        for candidate, reference in gold_rows
        if candidate.decision == "suggest_revision"
    ]
    named_suggestion_rows = [
        (candidate, reference)
        for candidate, reference in suggestion_rows
        if _is_named_key(candidate)
    ]
    decision_counts = Counter(
        candidate.decision or "invalid_or_unparsed" for candidate in candidates
    )
    report = {
        "run_id": args.run_id,
        "provider": run["provider"],
        "model": run["model"],
        "selected_count": len(candidates),
        "gold_reference_candidates": len(gold_rows),
        "no_reference_candidates": len(candidates) - len(gold_rows),
        "decision_counts": dict(decision_counts),
        "all_reference_exact_match": _summary(gold_rows),
        "named_key_exact_match": _summary(named_rows),
        "suggestion_exact_match": _summary(suggestion_rows),
        "named_key_suggestion_exact_match": _summary(named_suggestion_rows),
        "reviewed_glossary_term_match": _glossary_summary(candidates),
        "note": (
            "Exact matches use unique reviewed same-key translations as reference; "
            "this is semantic/reference evidence, not a claim about every language item."
        ),
    }
    output = args.output or (
        ROOT / ".artifacts" / "smart-review" / f"terminology-{args.run_id}.json"
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
