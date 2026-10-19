"""Run a bounded live smart-review benchmark against the configured ModDB.

Examples (run from the repository root)::

    uv run python -m tools.benchmark_smart_review \
        --model gemini-3.1-flash-lite --limit 1000
    uv run python -m tools.benchmark_smart_review \
        --provider chatgpt --model gpt-5.6-luna --limit 1000

The benchmark never applies candidates. It persists only the candidate run and
writes aggregate, credential-free metrics under ``.artifacts/``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from translation_tool.translation_db import TranslationDB, load_db_settings
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.utils.config_manager import load_config

ROOT = Path(__file__).resolve().parents[1]


def _config_key(config: dict[str, Any]) -> str:
    keys = config.get("lm_translator", {}).get("keys", [])
    if not isinstance(keys, list):
        return ""
    return next(
        (value.strip() for value in keys if isinstance(value, str) and value.strip()),
        "",
    )


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--provider", choices=("gemini", "chatgpt"), default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--version", default=None)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _args()
    config = load_config(args.config)
    lm_config = config.get("lm_translator", {})
    provider = args.provider or str(lm_config.get("provider", "gemini"))
    if provider == "gemini" and not _config_key(config):
        raise SystemExit("config.json 沒有可用的 Gemini key；未啟動 benchmark。")

    settings = load_db_settings(config)
    db_path = settings.resolved_path()
    if not db_path.is_file():
        raise SystemExit(f"找不到 config.json 指定的資料庫：{db_path}")

    output = args.output or (
        ROOT
        / ".artifacts"
        / "smart-review"
        / f"{provider}-{args.model.replace('/', '_')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    db = TranslationDB(db_path, priority=settings.priority, create=False)
    try:
        result = run_smart_review(
            db,
            model_name=args.model,
            api_key=_config_key(config),
            provider=provider,
            version=args.version or settings.version or None,
            limit=args.limit,
            batch_size=args.batch_size,
            lm_config=config,
            progress=lambda snapshot: print(
                f"[{args.model}] {snapshot['processed']}/{snapshot['selected']} "
                f"valid={snapshot['valid']} invalid={snapshot['invalid']}"
            ),
        )
        run = db.get_smart_review_run(result.run_id) or {}
        status_counts: dict[str, int] = {}
        for candidate in db.list_smart_review_candidates(result.run_id):
            status_counts[candidate.status] = status_counts.get(candidate.status, 0) + 1
    finally:
        db.close()

    report = {
        "database": str(db_path),
        "provider": provider,
        "model": args.model,
        "version": args.version or settings.version or None,
        "requested_limit": args.limit,
        "run_id": result.run_id,
        "run": run,
        "status_counts": status_counts,
        "applied_count": 0,
        "note": "Candidates were persisted for review; no candidate was applied.",
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
