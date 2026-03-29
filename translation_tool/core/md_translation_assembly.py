"""translation_tool/core/md_translation_assembly.py 模組。

用途：作為 Markdown pipeline 的相容入口，保留既有 step 與 run_md_pipeline 契約。
維護注意：progress / stats / steps 已拆到 md_translation_* 子模組。
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Dict, Optional

import json
import orjson

from translation_tool.plugins.md.md_extract_qa import (
    build_pending_json,
    contains_cjk,
    detect_lang_segment,
    extract_blocks,
    iter_md_files,
    map_rel_lang_path,
    safe_relpath,
)
from translation_tool.plugins.md.md_inject_qa import (
    apply_item_to_md_lines,
    iter_json_files,
    load_items_from_json,
    map_lang_in_rel_path_allow_zh,
)
from translation_tool.plugins.md.md_lmtranslator import translate_md_pending
from translation_tool.core.lm_translator_shared import _get_default_batch_size
from translation_tool.core.md_translation_progress import _ProgressProxy
from translation_tool.core.md_translation_stats import (
    _LANG_MODE_LABELS,
    count_json_files as _count_json_files,
    count_md_pending_docs as _count_md_pending_docs,
    log_md_step2_stats as _log_md_step2_stats_impl,
    normalize_lang_mode as _normalize_lang_mode,
)
from translation_tool.core.md_translation_steps import (
    step1_extract_impl,
    step2_translate_impl,
    step3_inject_impl,
)
from translation_tool.utils.log_unit import (
    get_formatted_duration,
    log_info,
    log_warning,
    progress,
)

def _log_md_step2_stats(step2_res: Dict[str, Any]) -> None:
    _log_md_step2_stats_impl(step2_res, log_info_fn=log_info)

def step1_extract(
    *,
    input_dir: str,
    pending_dir: str,
    lang_mode: str = "non_cjk_only",
    session=None,
    progress_base: float = 0.0,
    progress_span: float = 0.33,
) -> Dict[str, Any]:
    return step1_extract_impl(
        input_dir=input_dir,
        pending_dir=pending_dir,
        lang_mode=lang_mode,
        session=session,
        progress_base=progress_base,
        progress_span=progress_span,
        iter_md_files_fn=iter_md_files,
        safe_relpath_fn=safe_relpath,
        extract_blocks_fn=extract_blocks,
        detect_lang_segment_fn=detect_lang_segment,
        map_rel_lang_path_fn=map_rel_lang_path,
        contains_cjk_fn=contains_cjk,
        build_pending_json_fn=build_pending_json,
        progress_fn=progress,
        log_warning_fn=log_warning,
    )

def step2_translate(
    *,
    pending_dir: str,
    translated_dir: str,
    session=None,
    progress_base: float = 0.33,
    progress_span: float = 0.33,
    dry_run: bool = False,
    write_new_cache: bool = True,
) -> Dict[str, Any]:
    return step2_translate_impl(
        pending_dir=pending_dir,
        translated_dir=translated_dir,
        session=session,
        progress_base=progress_base,
        progress_span=progress_span,
        dry_run=dry_run,
        write_new_cache=write_new_cache,
        progress_proxy_cls=_ProgressProxy,
        translate_md_pending_fn=translate_md_pending,
        progress_fn=progress,
    )

def step3_inject(
    *,
    input_dir: str,
    json_dir: str,
    final_dir: str,
    session=None,
    progress_base: float = 0.66,
    progress_span: float = 0.33,
) -> Dict[str, Any]:
    return step3_inject_impl(
        input_dir=input_dir,
        json_dir=json_dir,
        final_dir=final_dir,
        session=session,
        progress_base=progress_base,
        progress_span=progress_span,
        iter_json_files_fn=iter_json_files,
        load_items_from_json_fn=load_items_from_json,
        apply_item_to_md_lines_fn=apply_item_to_md_lines,
        map_lang_in_rel_path_allow_zh_fn=map_lang_in_rel_path_allow_zh,
        progress_fn=progress,
    )

def run_md_pipeline(
    input_dir: str,
    session=None,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
    lang_mode: str = "non_cjk_only",
) -> Dict[str, Any]:
    start_tick = time.perf_counter()
    lang_mode = _normalize_lang_mode(lang_mode)

    base = Path(input_dir).resolve()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"找不到輸入資料夾：{base}")

    output_root = Path(output_dir).resolve() if output_dir else (base / "Output")
    out_root = output_root / "md"
    pending_dir = out_root / "待翻譯"
    translated_dir = out_root / "LM翻譯後"
    final_dir = out_root / "完成"

    pending_dir.mkdir(parents=True, exist_ok=True)
    translated_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Any] = {
        "paths": {
            "input": str(base),
            "out_root": str(out_root),
            "pending": str(pending_dir),
            "translated": str(translated_dir),
            "final": str(final_dir),
        }
    }

    log_info("[MD] 流程開始：模式=%s", _LANG_MODE_LABELS.get(lang_mode, lang_mode))

    if step_extract:
        log_info("[MD] [1/3] 開始抽取待翻譯區塊")
        result["step1"] = step1_extract(
            input_dir=str(base),
            pending_dir=str(pending_dir),
            lang_mode=lang_mode,
            session=session,
            progress_base=0.0,
            progress_span=0.33,
        )
    else:
        log_info("[MD] [1/3] 已略過抽取")
        result["step1"] = {"skipped": True, "reason": "step_extract_disabled"}
        progress(session, 0.33)

    pending_json_count = _count_md_pending_docs(pending_dir)
    if step_translate:
        if pending_json_count == 0:
            log_info("[MD] [2/3] 已略過翻譯：沒有可翻譯 JSON")
            result["step2"] = {"skipped": True, "reason": "no_pending_json"}
            progress(session, 0.66)
        else:
            log_info("[MD] [2/3] 開始 LM 翻譯")
            result["step2"] = step2_translate(
                pending_dir=str(pending_dir),
                translated_dir=str(translated_dir),
                session=session,
                progress_base=0.33,
                progress_span=0.33,
                dry_run=dry_run,
                write_new_cache=write_new_cache,
            )
            _log_md_step2_stats(result["step2"])
    else:
        log_info("[MD] [2/3] 已略過翻譯")
        result["step2"] = {"skipped": True, "reason": "step_translate_disabled"}
        progress(session, 0.66)

    if dry_run:
        log_info("[MD] [3/3] 已略過回寫：dry-run 模式")
        result["step3"] = {"skipped": True, "reason": "dry_run"}
    elif not step_inject:
        log_info("[MD] [3/3] 已略過回寫")
        result["step3"] = {"skipped": True, "reason": "step_inject_disabled"}
    else:
        translated_json_count = _count_md_pending_docs(translated_dir)
        inject_json_root = translated_dir if translated_json_count > 0 else pending_dir
        if _count_md_pending_docs(inject_json_root) == 0:
            log_info("[MD] [3/3] 已略過回寫：沒有可用來源 JSON")
            result["step3"] = {"skipped": True, "reason": "no_inject_source_json"}
        else:
            log_info("[MD] [3/3] 開始回寫 md 檔案")
            result["step3"] = step3_inject(
                input_dir=str(base),
                json_dir=str(inject_json_root),
                final_dir=str(final_dir),
                session=session,
                progress_base=0.66,
                progress_span=0.33,
            )

    step2_summary = result.get("step2", {}) if isinstance(result.get("step2"), dict) else {}
    if step2_summary and not step2_summary.get("skipped"):
        total_blocks = step2_summary.get("total_blocks")
        cache_hit = step2_summary.get("cache_hit")
        cache_miss = step2_summary.get("cache_miss")
        files = step2_summary.get("files", step2_summary.get("written_files"))
        batch_size = _get_default_batch_size("md", None)
        est_batches = math.ceil(cache_miss / batch_size) if isinstance(cache_miss, int) and batch_size > 0 else None
        log_info(
            "\n🧾 [MD] 摘要：📁 共 %s 個檔案、🔢 總計 %s 個 Block；✅ 快取命中 %s；🤖 需要 AI 翻譯 %s 條；🧮 預估批次 %s 次。",
            files,
            total_blocks,
            cache_hit,
            cache_miss,
            est_batches,
        )

    progress(session, 0.999)
    log_info("[MD] 流程完成，耗時：%s", get_formatted_duration(start_tick))
    return result

__all__ = [
    "_ProgressProxy",
    "step1_extract",
    "step2_translate",
    "step3_inject",
    "run_md_pipeline",
]
