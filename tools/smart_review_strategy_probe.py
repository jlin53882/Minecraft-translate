"""離線分析並可選擇性 A/B 測試智慧校對修復策略。

預設只讀 Smart Review 的 JSON 批次歷史，不會呼叫模型、不會寫入 SQLite，適合
先確認問題主要來自輸出契約、批次大小，還是驗證與重試策略。

從 repository root 執行離線分析：

    uv run python -m tools.smart_review_strategy_probe \
        --history "C:/path/to/<run-id>.json"

明確加上 ``--live`` 才會使用 ``config.json`` 對相同輸入做小樣本 A/B。Live
模式只呼叫 provider，結果只寫入報告，不會套用候選、寫入 SQLite 或保存完整模型回應：

    uv run python -m tools.smart_review_strategy_probe \
        --history "C:/path/to/<run-id>.json" \
        --config config.json --live --sample-size 40 --batch-size 20

報告會區分「歷史可直接觀察的事實」與「需要 live A/B 才能確認的假設」，避免把
放寬驗證後的數字誤當成準確率改善。
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_BATCH_SIZES = (20, 40, 50, 80, 100)
_STRATEGIES = ("baseline", "reason-contract")


def _args() -> argparse.Namespace:
    """解析診斷腳本的命令列參數。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        type=Path,
        required=True,
        help="既有 Smart Review 的單批次 JSON 歷史檔。",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.json",
        help="Live A/B 使用的設定檔；離線模式不會讀取。",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="對選定的小樣本實際呼叫 provider；不寫入資料庫。",
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "chatgpt"),
        default=None,
        help="覆寫歷史記錄的 provider；未指定時沿用歷史值。",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="覆寫歷史記錄的模型；未指定時沿用歷史值。",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=40,
        help="Live A/B 的最大條目數；0 代表使用全部條目。",
    )
    parser.add_argument(
        "--sample-mode",
        choices=("stratified", "first", "invalid-first"),
        default="stratified",
        help="Live 樣本選取方式；stratified 會保留不同歷史錯誤類型。",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Live A/B 每次 provider 請求的條目數。",
    )
    parser.add_argument(
        "--strategies",
        default=",".join(_STRATEGIES),
        help="Live A/B 策略，逗號分隔；可用 baseline,reason-contract。",
    )
    parser.add_argument(
        "--max-live-batches",
        type=int,
        default=0,
        help="每個策略最多執行的批次數；0 代表不額外限制。",
    )
    parser.add_argument(
        "--batch-sizes",
        default=",".join(str(value) for value in _DEFAULT_BATCH_SIZES),
        help="離線批次規劃比較，例如 20,40,50,80,100。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="報告輸出路徑；未指定時寫入 .artifacts/smart-review/。",
    )
    return parser.parse_args()


def _read_history(path: Path) -> dict[str, Any]:
    """讀取並驗證 Smart Review JSON 歷史的基本結構。"""
    if not path.is_file():
        raise SystemExit(f"找不到 Smart Review 歷史檔：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"無法讀取 Smart Review 歷史檔：{path}（{exc}）") from exc
    if not isinstance(value, dict):
        raise SystemExit("Smart Review 歷史檔最外層必須是 JSON 物件。")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(item, dict) for item in candidates
    ):
        raise SystemExit("Smart Review 歷史檔缺少有效的 candidates 陣列。")
    return value


def _text(value: object) -> str:
    """將可選 JSON 值安全轉成去除首尾空白的文字。"""
    return value.strip() if isinstance(value, str) else ""


def _integer(value: object, default: int = 0) -> int:
    """將可選 JSON 值安全轉成整數。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_positive_ints(value: str, *, option_name: str) -> list[int]:
    """解析逗號分隔且大於零的整數清單。"""
    result: list[int] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = int(raw)
        except ValueError as exc:
            raise SystemExit(f"{option_name} 含有無效整數：{raw}") from exc
        if parsed <= 0:
            raise SystemExit(f"{option_name} 的數值必須大於 0：{parsed}")
        if parsed not in result:
            result.append(parsed)
    if not result:
        raise SystemExit(f"{option_name} 至少需要一個大於 0 的數值。")
    return result


def _error_category(value: object) -> str:
    """把 validation error 分成可比較的診斷類別。"""
    reason = _text(value)
    if not reason:
        return "未分類"
    if "reason 不可為空" in reason:
        return "reason 為空"
    if "reason 必須指出" in reason:
        return "reason 缺少可核對差異"
    if "suggest_revision 必須提供不同且非空" in reason:
        return "建議譯文為空或未變更"
    if "evidence_ids" in reason:
        return "證據 id 無效"
    return "其他驗證錯誤"


def _candidate_status(candidate: dict[str, Any]) -> str:
    """取得候選目前保存的狀態。"""
    status = _text(candidate.get("status"))
    return status or "unknown"


def _history_summary(history: dict[str, Any]) -> dict[str, Any]:
    """彙總歷史中可以不依賴模型重跑而確認的事實。"""
    candidates = [item for item in history["candidates"] if isinstance(item, dict)]
    statuses = Counter(_candidate_status(item) for item in candidates)
    invalid = [item for item in candidates if _candidate_status(item) == "invalid"]
    error_categories = Counter(
        _error_category(item.get("validation_error")) for item in invalid
    )
    exact_errors = Counter(
        _text(item.get("validation_error")) or "未記錄 validation error"
        for item in invalid
    )
    context_counts = [
        len(item.get("context", []))
        for item in candidates
        if isinstance(item.get("context"), list)
    ]
    response_count = sum(isinstance(item.get("response"), dict) for item in candidates)
    metrics = history.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    selected_count = _integer(history.get("selected_count"), len(candidates))
    invalid_count = _integer(history.get("invalid_count"), statuses.get("invalid", 0))
    reason_empty = error_categories.get("reason 為空", 0)
    return {
        "run_id": _text(history.get("id")) or _text(history.get("run_id")),
        "provider": _text(history.get("provider")) or "unknown",
        "model": _text(history.get("model")) or "unknown",
        "status": _text(history.get("status")) or "unknown",
        "selected_count": selected_count,
        "processed_count": _integer(history.get("processed_count"), len(candidates)),
        "recorded_batch_size": _integer(history.get("batch_size")),
        "recorded_batch_count": _integer(metrics.get("batch_count")),
        "status_counts": dict(sorted(statuses.items())),
        "invalid_count": invalid_count,
        "invalid_rate": round(invalid_count / selected_count, 4)
        if selected_count
        else None,
        "validation_error_categories": dict(error_categories.most_common()),
        "validation_errors": dict(exact_errors.most_common()),
        "reason_empty_count": reason_empty,
        "reason_empty_share_of_invalid": (
            round(reason_empty / invalid_count, 4) if invalid_count else None
        ),
        "candidates_with_saved_response": response_count,
        "context_count": {
            "min": min(context_counts) if context_counts else None,
            "max": max(context_counts) if context_counts else None,
            "average": (
                round(sum(context_counts) / len(context_counts), 3)
                if context_counts
                else None
            ),
        },
        "metrics": {
            key: metrics.get(key)
            for key in (
                "request_attempts",
                "smart_review_attempts",
                "network_attempts",
                "retry_count",
                "response_invalid_count",
                "shrink_count",
                "api_seconds",
                "parse_seconds",
                "result_write_seconds",
                "prompt_tokens",
                "output_tokens",
            )
            if key in metrics
        },
    }


def _batch_plan(selected_count: int, batch_size: int) -> dict[str, int | None]:
    """計算固定批次大小下的理論請求規模。"""
    if selected_count <= 0:
        return {"batch_size": batch_size, "planned_batches": 0, "last_batch_size": None}
    planned_batches = math.ceil(selected_count / batch_size)
    remainder = selected_count % batch_size
    return {
        "batch_size": batch_size,
        "planned_batches": planned_batches,
        "last_batch_size": remainder or batch_size,
    }


def _offline_strategy_report(
    history_summary: dict[str, Any], batch_sizes: Iterable[int]
) -> dict[str, Any]:
    """建立不需要再次呼叫模型的策略比較與安全性說明。"""
    selected_count = _integer(history_summary.get("selected_count"))
    invalid_count = _integer(history_summary.get("invalid_count"))
    reason_empty = _integer(history_summary.get("reason_empty_count"))
    metrics = history_summary.get("metrics", {})
    request_attempts = _integer(metrics.get("request_attempts"))
    batch_projection = [
        _batch_plan(selected_count, batch_size) for batch_size in batch_sizes
    ]
    repair_projection = [
        {
            **_batch_plan(invalid_count, batch_size),
            "additional_candidate_count": invalid_count,
            "note": "只是假設將無效項目重新送出；成功率需由 live A/B 驗證。",
        }
        for batch_size in batch_sizes
    ]
    return {
        "current_strict_validation": {
            "valid_count": max(selected_count - invalid_count, 0),
            "invalid_count": invalid_count,
            "request_attempts_recorded": request_attempts,
            "meaning": "沿用目前安全驗證，不把無效輸出直接套用。",
        },
        "relax_reason_validation_diagnostic_only": {
            "reason_empty_items": reason_empty,
            "potentially_accepted_upper_bound": reason_empty,
            "safe_to_apply": False,
            "meaning": (
                "若只忽略空 reason，最多可能少掉這些 invalid；但歷史未保存這些項目的"
                "完整原始 response，不能證明 decision、suggestion 或 evidence 其餘欄位正確。"
            ),
        },
        "invalid_only_repair": {
            "input_count": invalid_count,
            "request_plan_by_batch_size": repair_projection,
            "safe_to_apply": True,
            "meaning": "保留嚴格驗證，只重新請模型處理無效項目；需要 live 測試確認收益。",
        },
        "batch_size_projection": {
            "selected_count": selected_count,
            "plans": batch_projection,
            "meaning": (
                "這是請求數與單批輸出壓力的規劃，不代表較小批次必然提高模型正確率。"
            ),
        },
        "recommendation": _recommendation(history_summary),
    }


def _recommendation(history_summary: dict[str, Any]) -> dict[str, Any]:
    """依歷史錯誤分布產生不放寬安全性的下一步建議。"""
    invalid_count = _integer(history_summary.get("invalid_count"))
    reason_empty = _integer(history_summary.get("reason_empty_count"))
    response_invalid_count = _integer(
        (history_summary.get("metrics") or {}).get("response_invalid_count")
    )
    if invalid_count and reason_empty / invalid_count >= 0.5:
        primary = (
            "先做 reason-contract 的小樣本 live A/B，再決定是否加入無效項目專用重試。"
        )
    else:
        primary = "先做小樣本 live A/B，分別比較輸出契約與批次大小，不要先放寬驗證。"
    return {
        "primary": primary,
        "preserve": [
            "保留嚴格 schema 與逐筆 validation error。",
            "不要因為 reason 為空就直接把候選標成可套用。",
            "只把 context/input-size 類錯誤作為縮批依據。",
        ],
        "test_next": [
            "baseline 與 reason-contract 使用相同條目、相同 batch size。",
            "再用相同 prompt 比較 batch size 20、40 或 50 的 response completeness。",
            "若仍有 invalid，只重試 invalid 項目並獨立記錄第二次 validation 結果。",
        ],
        "observed_response_shape_issue": response_invalid_count > 0,
    }


def _select_sample(
    candidates: list[dict[str, Any]], sample_size: int, mode: str
) -> list[dict[str, Any]]:
    """以可重現方式選取 live A/B 樣本。"""
    ordered = sorted(candidates, key=lambda item: _integer(item.get("ordinal")))
    if sample_size <= 0 or sample_size >= len(ordered):
        return ordered
    if mode == "first":
        return ordered[:sample_size]
    if mode == "invalid-first":
        return sorted(
            ordered,
            key=lambda item: (
                _candidate_status(item) != "invalid",
                _integer(item.get("ordinal")),
            ),
        )[:sample_size]

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in ordered:
        group_key = "|".join(
            (
                _candidate_status(candidate),
                _error_category(candidate.get("validation_error")),
            )
        )
        groups[group_key].append(candidate)
    group_values = [groups[key] for key in sorted(groups)]
    selected: list[dict[str, Any]] = []
    index = 0
    while len(selected) < sample_size and group_values:
        group = group_values[index % len(group_values)]
        if group:
            selected.append(group.pop(0))
        group_values = [value for value in group_values if value]
        index += 1
    return sorted(selected, key=lambda item: _integer(item.get("ordinal")))


def _snapshot_from_candidate(candidate: dict[str, Any]) -> Any:
    """把歷史 JSON 候選轉成現有 parser 可使用的不可變快照。"""
    from translation_tool.translation_db.models import SmartReviewSnapshot

    context = candidate.get("context", [])
    safe_context = tuple(item for item in context if isinstance(item, dict))
    return SmartReviewSnapshot(
        run_id=_text(candidate.get("run_id")),
        ordinal=_integer(candidate.get("ordinal")),
        entry_id=_integer(candidate.get("entry_id")),
        kind=_text(candidate.get("kind")),
        mc_version=_text(candidate.get("mc_version")),
        mod_id=_text(candidate.get("mod_id")),
        key=_text(candidate.get("entry_key")) or _text(candidate.get("key")),
        en_us=_text(candidate.get("en_us")),
        current_zh_tw=_text(candidate.get("current_zh_tw")),
        effective_source=(
            _integer(candidate.get("effective_source"))
            if candidate.get("effective_source") is not None
            else None
        ),
        review_status=_text(candidate.get("review_status")) or None,
        effective_revision=(
            _integer(candidate.get("effective_revision"))
            if candidate.get("effective_revision") is not None
            else None
        ),
        context=safe_context,
        manual_zh_tw=_text(candidate.get("manual_zh_tw")) or None,
        manual_checker=_text(candidate.get("manual_checker")) or None,
        manual_review_status=_text(candidate.get("manual_review_status")) or None,
        manual_revision=(
            _integer(candidate.get("manual_revision"))
            if candidate.get("manual_revision") is not None
            else None
        ),
    )


def _reason_contract_prompt(base_prompt: str) -> str:
    """在不改動正式程式的前提下建立 reason 契約 A/B prompt。"""
    return (
        base_prompt
        + "\n\n"
        + "【本次 A/B 測試的輸出契約加強】\n"
        + "1. items 中每一筆都必須提供非空的 reason；不可使用空字串、null、無、N/A 或只有標點。\n"
        + "2. no_change：reason 必須說明原文與目前譯文為何相符，並指出至少一個可核對的內容。\n"
        + "3. suggest_revision：reason 必須具體指出原文或目前譯文中的差異，並說明建議如何修正；不可只寫「不自然」或「翻譯錯誤」。\n"
        + "4. needs_context：reason 必須說明目前缺少哪一種上下文，不能假裝已經判定正確譯文。\n"
        + "5. 輸出 JSON 前逐筆自我檢查 reason、suggested_zh_tw、evidence_ids 與 decision 的一致性。"
    )


def _strategy_prompt(name: str) -> str:
    """取得 live A/B 策略使用的 system prompt。"""
    from translation_tool.translation_db.smart_review import SMART_REVIEW_SYSTEM_PROMPT

    if name == "baseline":
        return SMART_REVIEW_SYSTEM_PROMPT
    if name == "reason-contract":
        return _reason_contract_prompt(SMART_REVIEW_SYSTEM_PROMPT)
    raise ValueError(f"不支援的策略：{name}")


def _api_key(config: dict[str, Any], provider: str) -> str:
    """從設定取得 provider 所需的第一個 key，但不讓 key 進入報告。"""
    if provider != "gemini":
        return ""
    lm_config = config.get("lm_translator", {})
    keys = lm_config.get("keys", []) if isinstance(lm_config, dict) else []
    if not isinstance(keys, list):
        return ""
    return next(
        (value.strip() for value in keys if isinstance(value, str) and value.strip()),
        "",
    )


def _live_strategy(
    name: str,
    snapshots: list[Any],
    *,
    config: dict[str, Any],
    provider: str,
    model: str,
    batch_size: int,
    max_batches: int,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """以不寫入資料庫的方式執行一個 live A/B 策略。"""
    from translation_tool.core.lm_api_client import call_gemini_requests
    from translation_tool.translation_db.smart_review import (
        build_smart_review_payload,
        parse_smart_review_response,
        smart_review_response_schema,
    )

    lm_config = config.get("lm_translator", {})
    if not isinstance(lm_config, dict):
        lm_config = {}
    key = _api_key(config, provider)
    if provider == "gemini" and not key:
        raise SystemExit("Live Gemini A/B 需要 config.json 的 lm_translator.keys。")
    chunks = [
        snapshots[index : index + batch_size]
        for index in range(0, len(snapshots), batch_size)
    ]
    if max_batches > 0:
        chunks = chunks[:max_batches]
    counters = Counter()
    error_types = Counter()
    validation_errors = Counter()
    finish_reasons = Counter()
    network_attempts = 0
    prompt_tokens = 0
    output_tokens = 0
    api_seconds = 0.0
    started = time.perf_counter()
    completed_batches = 0
    for batch_index, chunk in enumerate(chunks, start=1):
        if progress is not None:
            progress(
                f"{name}：執行第 {batch_index}/{len(chunks)} 批（{len(chunk)} 筆）"
            )
        payload = build_smart_review_payload(chunk)
        ids = [str(item.ordinal) for item in chunk]
        meta: dict[str, Any] = {}
        network_events: list[dict[str, object]] = []
        request_started = time.perf_counter()
        try:
            raw = call_gemini_requests(
                model_name=model,
                system_prompt=_strategy_prompt(name),
                payload=payload,
                api_key=key,
                temperature=0.2,
                max_output_tokens=_integer(
                    lm_config.get("smart_review_max_output_tokens"), 16_384
                ),
                meta_out=meta,
                provider=provider,
                lm_config=lm_config,
                response_schema=smart_review_response_schema(ids),
                network_event_callback=network_events.append,
            )
            api_seconds += time.perf_counter() - request_started
            counters["responses"] += 1
            results = parse_smart_review_response(raw, chunk)
            counters["items"] += len(results)
            for result in results:
                error = _text(result.get("validation_error"))
                if error:
                    counters["invalid_items"] += 1
                    validation_errors[error] += 1
                else:
                    counters["valid_items"] += 1
        except Exception as exc:  # noqa: BLE001
            api_seconds += time.perf_counter() - request_started
            counters["request_errors"] += 1
            error_types[type(exc).__name__] += 1
            counters["response_shape_or_request_errors"] += 1
        else:
            completed_batches += 1
        network_attempts += sum(
            1
            for event in network_events
            if event.get("kind") == "network_attempt_start"
        )
        prompt_tokens += _integer(meta.get("prompt_tokens"))
        output_tokens += _integer(
            meta.get("output_tokens") or meta.get("candidates_tokens")
        )
        finish_reason = _text(meta.get("finish_reason"))
        if finish_reason:
            finish_reasons[finish_reason] += 1
    return {
        "strategy": name,
        "sample_count": len(snapshots),
        "batch_size": batch_size,
        "batches_planned": len(chunks),
        "batches_completed": completed_batches,
        "items": counters.get("items", 0),
        "valid_items": counters.get("valid_items", 0),
        "invalid_items": counters.get("invalid_items", 0),
        "request_errors": counters.get("request_errors", 0),
        "response_shape_or_request_errors": counters.get(
            "response_shape_or_request_errors", 0
        ),
        "network_attempts_observed": network_attempts,
        "prompt_tokens": prompt_tokens or None,
        "output_tokens": output_tokens or None,
        "finish_reasons": dict(finish_reasons),
        "api_seconds": round(api_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
        "validation_errors": dict(validation_errors.most_common()),
        "request_error_types": dict(error_types.most_common()),
        "note": "此結果只代表選定樣本與本次 provider 回應，不是全量準確率。",
    }


def _parse_strategies(value: str) -> list[str]:
    """驗證 live A/B 策略清單。"""
    strategies = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in strategies if item not in _STRATEGIES]
    if unknown or not strategies:
        raise SystemExit(
            "--strategies 只能使用 baseline,reason-contract，且至少指定一個策略。"
        )
    return list(dict.fromkeys(strategies))


def _print_summary(report: dict[str, Any], output: Path) -> None:
    """輸出適合人工閱讀且不含敏感內容的摘要。"""
    summary = report["history_summary"]
    print(
        "[離線] run={} provider={} model={} selected={} invalid={} invalid_rate={}".format(
            summary["run_id"],
            summary["provider"],
            summary["model"],
            summary["selected_count"],
            summary["invalid_count"],
            summary["invalid_rate"],
        )
    )
    print(
        "[離線] 主要驗證錯誤：{}".format(
            "、".join(
                f"{key} × {value}"
                for key, value in summary["validation_error_categories"].items()
            )
            or "無"
        )
    )
    print(
        f"[離線] 建議：{report['offline_strategy_report']['recommendation']['primary']}"
    )
    live = report.get("live")
    if isinstance(live, dict):
        for result in live.get("strategies", []):
            print(
                "[live] {strategy}: valid={valid_items} invalid={invalid_items} "
                "request_error={request_errors} api={api_seconds}s".format(**result)
            )
    print(f"report={output}")


def main() -> int:
    """執行離線診斷，並依選項執行不寫入資料庫的 live A/B。"""
    args = _args()
    if args.sample_size < 0:
        raise SystemExit("--sample-size 不可小於 0。")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size 必須大於 0。")
    if args.max_live_batches < 0:
        raise SystemExit("--max-live-batches 不可小於 0。")
    history = _read_history(args.history)
    history_summary = _history_summary(history)
    batch_sizes = _parse_positive_ints(args.batch_sizes, option_name="--batch-sizes")
    report: dict[str, Any] = {
        "mode": "live" if args.live else "offline",
        "history_path": str(args.history.resolve()),
        "history_summary": history_summary,
        "offline_strategy_report": _offline_strategy_report(
            history_summary, batch_sizes
        ),
    }
    if args.live:
        from translation_tool.utils.config_manager import load_config

        config = load_config(args.config)
        provider = args.provider or history_summary["provider"]
        model = args.model or history_summary["model"]
        if provider not in {"gemini", "chatgpt"}:
            raise SystemExit(f"不支援的 provider：{provider}")
        if not model or model == "unknown":
            raise SystemExit("Live A/B 找不到模型名稱，請提供 --model。")
        candidates = [item for item in history["candidates"] if isinstance(item, dict)]
        selected = _select_sample(candidates, args.sample_size, args.sample_mode)
        snapshots = [_snapshot_from_candidate(item) for item in selected]
        strategies = _parse_strategies(args.strategies)
        live_results: list[dict[str, Any]] = []
        for strategy in strategies:
            live_results.append(
                _live_strategy(
                    strategy,
                    snapshots,
                    config=config,
                    provider=provider,
                    model=model,
                    batch_size=args.batch_size,
                    max_batches=args.max_live_batches,
                    progress=lambda message: print(f"[live] {message}"),
                )
            )
        report["live"] = {
            "provider": provider,
            "model": model,
            "sample_mode": args.sample_mode,
            "sample_ordinals": [_integer(item.get("ordinal")) for item in selected],
            "strategies": live_results,
            "note": "未保存完整 prompt、完整 response 或 API key。",
        }
    output = args.output or (
        ROOT
        / ".artifacts"
        / "smart-review"
        / f"strategy-probe-{history_summary['run_id'] or 'unknown'}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print_summary(report, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
