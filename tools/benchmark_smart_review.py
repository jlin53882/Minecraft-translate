"""針對設定的 ModDB 執行有界的即時智慧校對基準測試。

從 repository root 執行範例：

    uv run python -m tools.benchmark_smart_review \
        --model gemini-3.1-flash-lite --limit 1000
    uv run python -m tools.benchmark_smart_review \
        --provider chatgpt --model gpt-5.6-luna --limit 1000
    uv run python -m tools.benchmark_smart_review \
        --model gemini-3.1-flash-lite --limit 1000 \
        --reference-mode same-key-reviewed

測試不會套用候選，只保存候選執行記錄，並在 ``.artifacts/`` 寫入不含憑證的彙總指標。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from translation_tool.core import lm_config_rules
from translation_tool.translation_db import TranslationDB, load_db_settings
from translation_tool.translation_db.smart_review import run_smart_review
from translation_tool.utils.config_manager import load_config

ROOT = Path(__file__).resolve().parents[1]


def _config_key(config: dict[str, Any]) -> str:
    """取得目前設定使用的模型或 API 金鑰。"""
    keys = config.get("lm_translator", {}).get("keys", [])
    if not isinstance(keys, list):
        return ""
    return next(
        (value.strip() for value in keys if isinstance(value, str) and value.strip()),
        "",
    )


def _args() -> argparse.Namespace:
    """解析本工具的命令列參數。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--provider", choices=("gemini", "chatgpt"), default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--version", default=None)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument(
        "--reference-mode",
        choices=("all", "same-key-reviewed"),
        default="all",
        help="只選取有唯一已審核同 key 譯文可作 gold 的未審核項目",
    )
    parser.add_argument(
        "--terminology-first",
        action="store_true",
        help="優先選取 item/block/entity 等命名 key，再補足其他 gold 項目",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    """解析命令列參數並執行目前工具。"""
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
        / (f"{provider}-{args.model.replace('/', '_')}-{args.reference_mode}.json")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    db = TranslationDB(db_path, priority=settings.priority, create=False)
    try:
        with patch.object(lm_config_rules, "load_config", return_value=config):
            result = run_smart_review(
                db,
                model_name=args.model,
                # Let the shared ApiKeyCycle use the selected config and rotate
                # keys between batches when a request is rate-limited.
                api_key="",
                provider=provider,
                version=args.version or settings.version or None,
                limit=args.limit,
                batch_size=args.batch_size,
                require_reviewed_reference=args.reference_mode == "same-key-reviewed",
                prioritize_named_keys=args.terminology_first,
                lm_config=config.get("lm_translator", {}),
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
        "reference_mode": args.reference_mode,
        "terminology_first": args.terminology_first,
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
