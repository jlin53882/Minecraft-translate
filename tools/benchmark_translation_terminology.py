"""使用固定的一千筆專有名詞資料測試正式翻譯路徑。

本測試刻意只把英文原文送給翻譯器；已審核翻譯與上下文會在請求完成後才作為專有名詞參考，避免模型直接複製目前中文值而通過測試。

範例：

    uv run python -m tools.benchmark_translation_terminology \
        --run-id a8795ac9296a40dfabf7e3bdecccae58 \
        --model gemini-3.1-flash-lite --label baseline

測試不會把翻譯寫回資料庫，只寫入包含輸出與專有名詞指標且不含憑證的 JSON 產物。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from translation_tool.core import lm_config_rules, lm_translator_main
from translation_tool.core.official_locale import terminology_terms_for_item
from translation_tool.translation_db import TranslationDB, load_db_settings
from translation_tool.translation_db.models import SmartReviewCandidate
from translation_tool.translation_db.schema import cache_type_for_kind
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.text_matching import contains_term

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "a8795ac9296a40dfabf7e3bdecccae58"
REFERENCE_TYPES = {"reviewed_lang_glossary", "official_locale"}


def _args() -> argparse.Namespace:
    """解析本工具的命令列參數。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--label", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--initial-batch-size", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _compact(value: str) -> str:
    """把完整結果壓縮成適合命令列輸出的摘要。"""
    return re.sub(r"\s+", " ", value).strip()


def _raw_reference_terms(candidate: SmartReviewCandidate) -> list[dict[str, str]]:
    """取得僅供診斷比較使用的原始上下文參考術語。"""
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in candidate.context:
        if raw.get("reference_type") not in REFERENCE_TYPES:
            continue
        source = str(raw.get("en_us", "")).strip()
        target = _compact(str(raw.get("zh_tw", "")))
        reference_type = str(raw.get("reference_type", ""))
        if not source or not target or not contains_term(candidate.en_us, source):
            continue
        key = (source.casefold(), target, reference_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "source": source,
                "expected": target,
                "reference_type": reference_type,
            }
        )
    return result


def _authoritative_reference_terms(item: dict[str, Any]) -> list[dict[str, str]]:
    """使用與正式翻譯路徑相同的歧義過濾規則取得權威術語。"""
    return [
        {
            "source": source,
            "expected": target,
            "reference_type": "production_terminology_plan",
        }
        for source, target in terminology_terms_for_item(item)
    ]


def _build_items(
    db: TranslationDB, candidates: list[SmartReviewCandidate]
) -> list[dict[str, Any]]:
    """建立符合 ModDB 正式翻譯路徑的純來源項目。"""
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        reviewed = db.reviewed_lang_glossary(
            candidate.mod_id,
            candidate.mc_version,
            [candidate.en_us],
        )
        items.append(
            {
                "_benchmark_id": str(candidate.ordinal),
                "_kind": candidate.kind,
                "_mod_id": candidate.mod_id,
                "_expected_version": candidate.mc_version,
                "cache_type": cache_type_for_kind(candidate.kind),
                "file": f"assets/{candidate.mod_id}/lang/en_us.json",
                "path": candidate.key,
                "key": candidate.key,
                "text": candidate.en_us,
                "source_text": candidate.en_us,
                # This is the prompt-only reviewed glossary populated by the
                # same module/version-scoped loader used by ModDB translation.
                "_reviewed_glossary": reviewed,
            }
        )
    return items


def _output_map(results: list[dict[str, Any]] | None) -> dict[str, str | None]:
    """整理模型輸出與來源識別碼的對照。"""
    mapped: dict[str, str | None] = {}
    for item in results or []:
        benchmark_id = item.get("_benchmark_id")
        if benchmark_id is None:
            continue
        value = item.get("text")
        mapped[str(benchmark_id)] = value if isinstance(value, str) else None
    return mapped


def _term_metrics(
    candidates: list[SmartReviewCandidate],
    outputs: dict[str, str | None],
    references_by_ordinal: dict[int, list[dict[str, str]]],
) -> dict[str, Any]:
    """計算單筆資料的專有名詞評估指標。"""
    total_terms = emitted_terms = term_hits = 0
    rows_with_references = 0
    for candidate in candidates:
        output = outputs.get(str(candidate.ordinal))
        references = references_by_ordinal.get(candidate.ordinal, [])
        if references:
            rows_with_references += 1
        for reference in references:
            total_terms += 1
            if not isinstance(output, str) or not output.strip():
                continue
            emitted_terms += 1
            term_hits += int(reference["expected"] in _compact(output))
    return {
        "reference_rows": rows_with_references,
        "term_references": total_terms,
        "term_emitted": emitted_terms,
        "term_hits": term_hits,
        "term_misses": emitted_terms - term_hits,
        "term_coverage": round(emitted_terms / total_terms, 4) if total_terms else None,
        "term_hit_rate_on_all": round(term_hits / total_terms, 4)
        if total_terms
        else None,
        "term_hit_rate_on_emitted": round(term_hits / emitted_terms, 4)
        if emitted_terms
        else None,
    }


def _metrics(
    candidates: list[SmartReviewCandidate],
    items: list[dict[str, Any]],
    outputs: dict[str, str | None],
) -> dict[str, Any]:
    """彙總整批資料的專有名詞評估指標。"""
    authoritative_by_ordinal = {
        candidate.ordinal: _authoritative_reference_terms(item)
        for candidate, item in zip(candidates, items, strict=True)
    }
    raw_by_ordinal = {
        candidate.ordinal: _raw_reference_terms(candidate) for candidate in candidates
    }
    authoritative = _term_metrics(candidates, outputs, authoritative_by_ordinal)
    raw = _term_metrics(candidates, outputs, raw_by_ordinal)
    output_rows = sum(
        isinstance(outputs.get(str(candidate.ordinal)), str)
        and bool(outputs.get(str(candidate.ordinal), "").strip())
        for candidate in candidates
    )
    unchanged_rows = sum(
        _compact(outputs.get(str(candidate.ordinal), "")) == _compact(candidate.en_us)
        for candidate in candidates
        if isinstance(outputs.get(str(candidate.ordinal)), str)
        and outputs.get(str(candidate.ordinal), "").strip()
    )
    return {
        "input_rows": len(candidates),
        "output_rows": output_rows,
        "output_coverage": round(output_rows / len(candidates), 4)
        if candidates
        else None,
        "source_unchanged_rows": unchanged_rows,
        **authoritative,
        "evaluable_row_coverage": round(
            authoritative["reference_rows"] / len(candidates), 4
        )
        if candidates
        else None,
        "theoretical_term_ceiling": 1.0 if authoritative["term_references"] else None,
        "raw_context": raw,
    }


def _load_candidates(
    db: TranslationDB, run_id: str, limit: int
) -> list[SmartReviewCandidate]:
    """載入評估所需的智慧校對候選資料。"""
    candidates = db.list_smart_review_candidates(run_id, limit=limit)
    if len(candidates) != limit:
        raise SystemExit(
            f"run {run_id} 只有 {len(candidates)} 筆，無法宣稱同一批 {limit} 筆"
        )
    ordinals = [candidate.ordinal for candidate in candidates]
    expected = list(range(1, limit + 1))
    if ordinals != expected:
        raise SystemExit("候選快照 ordinal 不連續，拒絕使用不同批次做比較")
    return candidates


def run_benchmark() -> int:
    """執行專有名詞基準測試並輸出比較結果。"""
    args = _args()
    config = load_config(args.config)
    settings = load_db_settings(config)
    db_path = settings.resolved_path()
    if not db_path.is_file():
        raise SystemExit(f"找不到 config.json 指定的資料庫：{db_path}")
    if str(config.get("lm_translator", {}).get("provider", "gemini")) != "gemini":
        raise SystemExit("目前 config.json provider 不是 gemini；未啟動 benchmark")

    db = TranslationDB(db_path, priority=settings.priority, create=False)
    try:
        candidates = _load_candidates(db, args.run_id, args.limit)
        items = _build_items(db, candidates)
    finally:
        db.close()

    # The production Gemini path normally uses every enabled model as a fallback
    # pool.  Pin this benchmark to the requested model without changing the
    # user's persisted config or the runtime model settings.
    runtime_config = copy.deepcopy(config)
    runtime_lm_config = runtime_config.setdefault("lm_translator", {})
    if args.temperature is not None:
        runtime_lm_config["temperature"] = args.temperature
    if args.initial_batch_size is not None:
        runtime_lm_config["initial_batch_size_lang"] = max(1, args.initial_batch_size)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                lm_translator_main,
                "get_models_config",
                return_value={args.model: {"enabled": True}},
            )
        )
        stack.enter_context(
            patch.object(lm_translator_main, "load_config", return_value=runtime_config)
        )
        stack.enter_context(
            patch.object(lm_config_rules, "load_config", return_value=runtime_config)
        )
        results, status = lm_translator_main.translate_batch_smart(
            items, total=len(items), dry_run=False
        )

    outputs = _output_map(results)
    report = {
        "benchmark": "main_translation_terminology",
        "label": args.label,
        "run_id": args.run_id,
        "provider": "gemini",
        "model": args.model,
        "temperature": runtime_lm_config.get("temperature"),
        "initial_batch_size_lang": runtime_lm_config.get("initial_batch_size_lang"),
        "database": str(db_path),
        "source_only_input": True,
        "current_zh_tw_sent_to_model": False,
        "status": status,
        "metrics": _metrics(candidates, items, outputs),
        "rows": [
            {
                "ordinal": candidate.ordinal,
                "entry_id": candidate.entry_id,
                "kind": candidate.kind,
                "mod_id": candidate.mod_id,
                "key": candidate.key,
                "en_us": candidate.en_us,
                "references": _authoritative_reference_terms(item),
                "raw_context_references": _raw_reference_terms(candidate),
                "output": outputs.get(str(candidate.ordinal)),
            }
            for candidate, item in zip(candidates, items, strict=True)
        ],
    }
    output = args.output or (
        ROOT
        / ".artifacts"
        / "translation-terminology"
        / f"{args.model}-{args.label}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "rows"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
