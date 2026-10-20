"""Evaluate smart-review output for terminology evidence and semantic labels.

The unique-reviewed-reference numbers are only reference evidence: they are
not a human-labelled semantic accuracy score.  A manual labels file can add a
bounded terminology score with accepted translations, correction rate, false
revision rate, and abstention coverage.
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
    parser.add_argument(
        "--labels",
        type=Path,
        default=None,
        help=(
            "人工專有名詞標註 JSON；每項以 kind/mod_id/key/en_us 對應，"
            "並提供 expected_terms[{source,accepted}]。"
        ),
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _references(path: Path) -> dict[tuple[str, str, str, str], str]:
    uri = f"file:{quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        targets: defaultdict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        for kind, mod_id, key, en_us, zh_tw in connection.execute(
            "SELECT e.kind, e.mod_id, e.key, e.en_us, f.zh_tw "
            "FROM entry e JOIN translation f ON f.entry_id=e.id "
            "WHERE f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
            "AND e.en_us<>''"
        ):
            targets[(kind, mod_id, key, en_us)].add(_compact(zh_tw))
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


def _summary(
    rows: list[tuple[object, str]], *, evaluation_available: bool = True
) -> dict[str, object]:
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
        if not evaluation_available:
            continue
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
    result = {
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
        "evaluation_available": evaluation_available,
    }
    if not evaluation_available:
        result.update(
            {
                "emitted_output": None,
                "invalid_or_no_output": None,
                "model_exact": None,
                "model_exact_rate_on_all": None,
                "model_exact_rate_on_emitted": None,
                "improved": None,
                "regressed": None,
            }
        )
    return result


def _glossary_summary(
    candidates: list[object], *, evaluation_available: bool = True
) -> dict[str, object]:
    total = emitted = baseline_hits = baseline_emitted_hits = model_hits = 0
    corrected = false_revision = abstained = 0
    seen: set[tuple[str, str, str, str, str, str, str]] = set()
    for candidate in candidates:
        output = _candidate_output(candidate)
        for item in candidate.context:
            if item.get("reference_type") not in {
                "reviewed_lang_glossary",
                "official_locale",
            }:
                continue
            expected = _compact(str(item.get("zh_tw", "")))
            if not expected:
                continue
            reference_key = (
                candidate.kind,
                candidate.mod_id,
                candidate.key,
                candidate.en_us,
                str(item.get("en_us", "")),
                expected,
                str(item.get("reference_type", "")),
            )
            if reference_key in seen:
                continue
            seen.add(reference_key)
            total += 1
            baseline_hit = expected in _compact(candidate.current_zh_tw)
            if baseline_hit:
                baseline_hits += 1
            if not evaluation_available:
                continue
            if output is None:
                abstained += 1
                continue
            emitted += 1
            if baseline_hit:
                baseline_emitted_hits += 1
            model_hit = expected in _compact(output)
            if model_hit:
                model_hits += 1
            corrected += int(not baseline_hit and model_hit)
            false_revision += int(baseline_hit and not model_hit)
    result = {
        "term_references": total,
        "term_emitted": emitted,
        "term_abstained": abstained,
        "term_coverage": round(emitted / total, 4) if total else None,
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
        "corrected": corrected,
        "false_revision": false_revision,
        "evaluation_available": evaluation_available,
    }
    if not evaluation_available:
        result.update(
            {
                "term_emitted": None,
                "term_abstained": None,
                "term_coverage": None,
                "model_term_hits": None,
                "model_term_hit_rate_on_all": None,
                "model_term_hit_rate_on_emitted": None,
                "baseline_term_hit_rate_on_emitted": None,
                "corrected": None,
                "false_revision": None,
            }
        )
    return result


def _label_key(item: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(item.get("kind", "")),
        str(item.get("mod_id", "")),
        str(item.get("key", "")),
        str(item.get("en_us", "")),
    )


def _load_labels(path: Path) -> dict[tuple[str, str, str, str], dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    items = value.get("items", value) if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise TypeError("labels JSON 必須是陣列或包含 items 陣列的物件")
    labels: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("labels 的每一項必須是物件")
        key = _label_key(item)
        if not all(key):
            raise ValueError("labels 每項必須包含 kind、mod_id、key、en_us")
        if key in labels:
            raise ValueError(f"labels 有重複 key：{key}")
        labels[key] = item
    return labels


def _accepted_terms(label: dict[str, object]) -> list[tuple[str, tuple[str, ...]]]:
    raw_terms = label.get("expected_terms", [])
    if not isinstance(raw_terms, list):
        return []
    result: list[tuple[str, tuple[str, ...]]] = []
    for raw in raw_terms:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source", "")).strip()
        accepted = raw.get("accepted", raw.get("accepted_zh_tw", []))
        if isinstance(accepted, str):
            accepted = [accepted]
        if not source or not isinstance(accepted, list):
            continue
        values = tuple(_compact(str(item)) for item in accepted if _compact(str(item)))
        if values:
            result.append((source, values))
    return result


def _manual_terminology_summary(
    candidates: list[object],
    labels: dict[tuple[str, str, str, str], dict[str, object]],
    *,
    evaluation_available: bool = True,
) -> dict[str, object]:
    matched = scored = correct = incorrect = abstained = 0
    baseline_correct = corrected = false_revision = term_total = 0
    emitted_term_total = term_correct = 0
    for candidate in candidates:
        label = labels.get(
            (candidate.kind, candidate.mod_id, candidate.key, candidate.en_us)
        )
        if label is None:
            continue
        terms = _accepted_terms(label)
        if not terms:
            continue
        matched += 1
        term_total += len(terms)
        baseline = _compact(candidate.current_zh_tw)
        baseline_ok = all(
            any(expected in baseline for expected in accepted)
            for _source, accepted in terms
        )
        baseline_correct += int(baseline_ok)
        if not evaluation_available:
            continue
        output = _candidate_output(candidate)
        if output is None:
            abstained += 1
            continue
        scored += 1
        emitted_term_total += len(terms)
        compact_output = _compact(output)
        model_ok = all(
            any(expected in compact_output for expected in accepted)
            for _source, accepted in terms
        )
        term_correct += sum(
            any(expected in compact_output for expected in accepted)
            for _source, accepted in terms
        )
        if model_ok:
            correct += 1
        else:
            incorrect += 1
        corrected += int(not baseline_ok and model_ok)
        false_revision += int(baseline_ok and not model_ok)
    result = {
        "labelled_candidates": matched,
        "model_scored": scored,
        "abstained_or_no_output": abstained,
        "coverage_on_labels": round(scored / matched, 4) if matched else None,
        "baseline_terminology_correct": baseline_correct,
        "model_terminology_correct": correct,
        "model_terminology_accuracy_on_emitted": (
            round(correct / scored, 4) if scored else None
        ),
        "model_term_accuracy_on_emitted": (
            round(term_correct / emitted_term_total, 4)
            if emitted_term_total
            else None
        ),
        "corrected": corrected,
        "false_revision": false_revision,
        "incorrect": incorrect,
        "evaluation_available": evaluation_available,
    }
    if not evaluation_available:
        result.update(
            {
                "model_scored": None,
                "abstained_or_no_output": None,
                "coverage_on_labels": None,
                "model_terminology_correct": None,
                "model_terminology_accuracy_on_emitted": None,
                "model_term_accuracy_on_emitted": None,
                "corrected": None,
                "false_revision": None,
                "incorrect": None,
            }
        )
    return result


def _evaluation_state(
    run: dict[str, object], candidates: list[object]
) -> tuple[bool, str | None]:
    """Keep provider failures out of model-accuracy denominators."""
    if not candidates:
        return True, None
    valid_count = int(run.get("valid_count") or 0)
    request_errors = int(run.get("request_error_count") or 0)
    status = str(run.get("status") or "")
    if status not in {"completed", "failed", "cancelled", "partial"}:
        return False, f"run is not finished (status={status or 'unknown'})"
    if valid_count > 0:
        return True, None
    if request_errors:
        return False, f"provider requests failed ({request_errors})"
    if status in {"failed", "cancelled", "partial"}:
        return False, f"run ended without valid model output (status={status})"
    if int(run.get("invalid_count") or 0) > 0:
        return False, "provider returned no valid candidate output"
    return True, None


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

    evaluation_available, evaluation_unavailable_reason = _evaluation_state(
        run, candidates
    )
    references = _references(db_path)
    gold_rows = [
        (
            candidate,
            references[
                (candidate.kind, candidate.mod_id, candidate.key, candidate.en_us)
            ],
        )
        for candidate in candidates
        if (candidate.kind, candidate.mod_id, candidate.key, candidate.en_us)
        in references
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
    labels = _load_labels(args.labels) if args.labels else {}
    report = {
        "run_id": args.run_id,
        "provider": run["provider"],
        "model": run["model"],
        "selected_count": len(candidates),
        "gold_reference_candidates": len(gold_rows),
        "no_reference_candidates": len(candidates) - len(gold_rows),
        "decision_counts": dict(decision_counts),
        "evaluation_available": evaluation_available,
        "evaluation_unavailable_reason": evaluation_unavailable_reason,
        "unique_reviewed_reference_exact_match": _summary(
            gold_rows, evaluation_available=evaluation_available
        ),
        "named_key_unique_reference_exact_match": _summary(
            named_rows, evaluation_available=evaluation_available
        ),
        "suggestion_unique_reference_exact_match": _summary(
            suggestion_rows, evaluation_available=evaluation_available
        ),
        "named_key_suggestion_unique_reference_exact_match": _summary(
            named_suggestion_rows, evaluation_available=evaluation_available
        ),
        "reviewed_glossary_term_match": _glossary_summary(
            candidates, evaluation_available=evaluation_available
        ),
        "manual_terminology_labels": _manual_terminology_summary(
            candidates, labels, evaluation_available=evaluation_available
        ),
        "note": (
            "Unique reviewed reference matching requires the same kind, module, key, "
            "and English source text. It is reference evidence, not semantic accuracy. "
            "Use --labels for human-labelled terminology accuracy, correction, "
            "false-revision, and abstention metrics."
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
