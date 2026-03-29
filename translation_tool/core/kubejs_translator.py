"""translation_tool/core/kubejs_translator.py 模組。

用途：作為 KubeJS pipeline 的相容入口，保留 step orchestration 與對外 API。
維護注意：path / io / clean helpers 已拆到 kubejs_translator_* 子模組。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable, Dict, Any
import time
import math

import orjson

from translation_tool.core.kubejs_translator_clean import (
    clean_kubejs_from_raw_impl,
    deep_merge_3way_flat_impl,
    is_filled_text_impl,
    prune_en_by_tw_flat_impl,
)
from translation_tool.core.kubejs_translator_io import (
    read_json_dict_orjson_impl,
    write_json_orjson_impl,
)
from translation_tool.core.kubejs_translator_paths import resolve_kubejs_root_impl
from translation_tool.core.lm_translator_shared import _get_default_batch_size
from translation_tool.utils.log_unit import (
    log_info,
    log_debug,
    progress,
    get_formatted_duration,
)
from translation_tool.utils.text_processor import safe_convert_text

def _is_filled_text(v: Any) -> bool:
    return is_filled_text_impl(v)

def deep_merge_3way_flat(tw: dict, cn: dict, en: dict) -> dict:
    return deep_merge_3way_flat_impl(tw, cn, en, safe_convert_text_fn=safe_convert_text)

def prune_en_by_tw_flat(en_map: dict, tw_available: dict) -> dict:
    return prune_en_by_tw_flat_impl(en_map, tw_available)

def _read_json_dict_orjson(path: Path) -> dict:
    return read_json_dict_orjson_impl(path)

def _write_json_orjson(path: Path, data: dict) -> None:
    write_json_orjson_impl(path, data)

def clean_kubejs_from_raw(
    base_dir: str,
    *,
    output_dir: str | None = None,
    raw_dir: str | None = None,
    pending_root: str | None = None,
    final_root: str | None = None,
) -> dict:
    return clean_kubejs_from_raw_impl(
        base_dir,
        output_dir=output_dir,
        raw_dir=raw_dir,
        pending_root=pending_root,
        final_root=final_root,
        read_json_dict_fn=_read_json_dict_orjson,
        write_json_fn=_write_json_orjson,
        safe_convert_text_fn=safe_convert_text,
        log_debug_fn=log_debug,
        log_info_fn=log_info,
    )

def resolve_kubejs_root(input_dir: str, *, max_depth: int = 4) -> Path:
    return resolve_kubejs_root_impl(input_dir, max_depth=max_depth)

def step1_extract_and_clean(
    *,
    pack_or_kubejs_dir: str,
    raw_dir: str,
    pending_dir: str,
    final_dir: str,
    session=None,
    progress_base: float = 0.0,
    progress_span: float = 0.33,
) -> Dict[str, Any]:
    kubejs_dir_path = Path(resolve_kubejs_root(pack_or_kubejs_dir))
    log_info(f"\n🔎 [KubeJS] 確定 KubeJS 目錄為: {kubejs_dir_path}")

    log_info(f"📦 [KubeJS] 步驟 1-1：正在提取文字至 -> {raw_dir}")
    from translation_tool.plugins.kubejs.kubejs_tooltip_extract import extract as kjs_extract

    extract_result = kjs_extract(
        source_dir=str(kubejs_dir_path),
        output_dir=str(Path(raw_dir).resolve()),
        session=session,
        progress_base=progress_base,
        progress_span=progress_span * 0.7,
    )
    log_info(
        f"✅ [KubeJS] 提取完成: 檔案數={extract_result.get('extracted_files')} 總鍵值數={extract_result.get('extracted_keys_total')}"
    )

    log_info("🧹 [KubeJS] 步驟 1-2：執行清理並分類 (三方合併邏輯)")
    modpack_root = str(kubejs_dir_path.parent)

    clean_result = clean_kubejs_from_raw(
        base_dir=modpack_root,
        raw_dir=str(Path(raw_dir).resolve()),
        pending_root=str(Path(pending_dir).resolve()),
        final_root=str(Path(final_dir).resolve()),
    )

    progress(session, min(progress_base + progress_span, 0.999))

    return {
        "extract": extract_result,
        "clean": clean_result,
        "kubejs_dir": str(kubejs_dir_path),
        "raw_dir": str(Path(raw_dir).resolve()),
        "pending_dir": str(Path(pending_dir).resolve()),
        "final_dir": str(Path(final_dir).resolve()),
    }

def step2_translate_lm(
    *,
    pending_dir: str,
    output_dir: Optional[str] = None,
    translated_dir: Optional[str] = None,
    session=None,
    progress_base: float = 0.33,
    progress_span: float = 0.33,
    dry_run: bool = False,
    write_new_cache: bool = True,
) -> Dict[str, Any]:
    out_arg = output_dir or translated_dir
    if not out_arg:
        raise ValueError("step2_translate_lm: 必須提供 output_dir 或 translated_dir")

    log_info(
        f"🧠 [KubeJS] Step2 LM translate: {pending_dir} -> {out_arg} (dry_run={dry_run}, write_new_cache={write_new_cache})"
    )

    from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import (
        translate_kubejs_pending_to_zh_tw,
    )

    in_dir = str(Path(pending_dir).resolve())
    out_dir = str(Path(out_arg).resolve())

    class _ProgressProxy:
        def __init__(self, parent, base: float, span: float):
            self.parent = parent
            self.base = float(base)
            self.span = float(span)

        def set_progress(self, p: float):
            if not self.parent or not hasattr(self.parent, "set_progress"):
                return
            try:
                p = 0.0 if p is None else float(p)
                if p < 0:
                    p = 0.0
                elif p > 1:
                    p = 1.0
                self.parent.set_progress(self.base + p * self.span)
            except Exception:
                pass

        def set_status(self, msg: str):
            if self.parent and hasattr(self.parent, "set_status"):
                try:
                    self.parent.set_status(msg)
                except Exception:
                    pass

    proxy = _ProgressProxy(session, progress_base, progress_span)

    result = translate_kubejs_pending_to_zh_tw(
        pending_dir=in_dir,
        output_dir=out_dir,
        session=proxy,
        dry_run=bool(dry_run),
        write_new_cache=bool(write_new_cache),
    )

    if session and hasattr(session, "set_progress"):
        try:
            session.set_progress(progress_base + progress_span)
        except Exception:
            pass

    return result

def step3_inject(
    *,
    pack_or_kubejs_dir: str,
    src_dir: str,
    final_dir: str,
    session=None,
    progress_base: float = 0.66,
    progress_span: float = 0.33,
) -> Dict[str, Any]:
    kubejs_dir = resolve_kubejs_root(pack_or_kubejs_dir)
    log_info(f"⚡[KubeJS] 步驟 3：開始注入翻譯 -> 目標目錄: {final_dir}")

    from translation_tool.plugins.kubejs.kubejs_tooltip_inject import inject as kjs_inject

    return kjs_inject(
        str(kubejs_dir),
        str(Path(src_dir).resolve()),
        str(Path(final_dir).resolve()),
        session=session,
        progress_base=progress_base,
        progress_span=progress_span,
    )

def run_kubejs_pipeline(
    *,
    input_dir: str,
    output_dir: Optional[str],
    session=None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    translator_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    write_new_cache: bool = False,
) -> Dict[str, Any]:
    base = Path(input_dir).resolve()
    out_root = Path(output_dir).resolve() if output_dir else (base / "Output")

    raw_dir = out_root / "kubejs" / "raw" / "kubejs"
    pending_dir = out_root / "kubejs" / "待翻譯" / "kubejs"
    translated_dir = out_root / "kubejs" / "LM翻譯後" / "kubejs"
    final_dir = out_root / "kubejs" / "完成" / "kubejs"

    for d in [raw_dir, pending_dir, translated_dir, final_dir]:
        d.mkdir(parents=True, exist_ok=True)

    log_info("🧩 [KubeJS] 流程開始啟動")
    if dry_run:
        log_info("🧪 [KubeJS] 注意：目前為 DRY-RUN 測試模式，不會執行實際動作")

    result: Dict[str, Any] = {
        "paths": {
            "input": str(base),
            "raw": str(raw_dir),
            "pending": str(pending_dir),
            "translated": str(translated_dir),
            "final": str(final_dir),
        }
    }

    start_time = time.perf_counter()
    if step_extract:
        result["step1"] = step1_extract_and_clean(
            pack_or_kubejs_dir=str(base),
            raw_dir=str(raw_dir),
            pending_dir=str(pending_dir),
            final_dir=str(final_dir),
            session=session,
            progress_base=0.0,
            progress_span=0.33,
        )
    else:
        log_info("⏭️ [KubeJS] 跳過步驟 1")
        progress(session, 0.33)

    def _count_pending_lang_keys(pending_dir: Path) -> int:
        total = 0
        for p in pending_dir.rglob("*.json"):
            try:
                data = orjson.loads(p.read_bytes())
                if isinstance(data, dict):
                    total += len(data)
            except Exception:
                pass
        return total

    def _log_kubejs_step2_stats(step2_res: Dict[str, Any]) -> None:
        if not isinstance(step2_res, dict):
            return
        if step2_res.get("skipped"):
            log_info("[KubeJS] Step2 跳過原因： %s", step2_res.get("reason"))
            return

        is_dry = bool(step2_res.get("dry_run"))
        files = step2_res.get("files", step2_res.get("written_files"))
        total_keys = step2_res.get("total_keys")
        cache_hit = step2_res.get("cache_hit")
        cache_miss = step2_res.get("cache_miss")
        api_translated = step2_res.get("api_translated")
        preview_path = step2_res.get("preview_path")
        records_json = step2_res.get("records_json")
        records_csv = step2_res.get("records_csv")
        batch_size = _get_default_batch_size("kubejs", None)
        avg_batch_sec = step2_res.get("avg_batch_sec")
        est_sec_per_batch = avg_batch_sec
        est_batches = (
            math.ceil(cache_miss / batch_size)
            if isinstance(cache_miss, int) and batch_size > 0
            else None
        )

        log_info(
            "[KubeJS] Step2 統計 %s | files=%s | total_keys=%s | cache_hit=%s | cache_miss=%s",
            "DRY-RUN" if is_dry else "翻譯",
            files,
            total_keys,
            cache_hit,
            cache_miss,
        )

        if is_dry:
            if preview_path:
                log_info("[KubeJS] DRY-RUN preview: %s", preview_path)
        else:
            if api_translated is not None:
                log_info("[KubeJS] API 翻譯: %s", api_translated)
            if records_json or records_csv:
                log_info("[KubeJS] records: json=%s | csv=%s", records_json, records_csv)
        if est_batches is not None:
            log_info("[KubeJS] 預估批次：%s (batch_size=%s)", est_batches, batch_size)
        if avg_batch_sec:
            log_info("[KubeJS] 平均每批耗時(本次)：%.2fs", avg_batch_sec)
        if est_batches is not None and est_sec_per_batch:
            total_sec = int(est_batches * est_sec_per_batch)
            m, s = divmod(total_sec, 60)
            h, m = divmod(m, 60)
            eta_txt = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
            log_info("[KubeJS] 預估總耗時：%s", eta_txt)

        per_file = step2_res.get("per_file")
        if isinstance(per_file, list) and per_file:
            log_info("[KubeJS] 檔案批次估算：")
            for row in per_file:
                if not isinstance(row, dict):
                    continue
                f = row.get("file")
                miss = row.get("cache_miss")
                dst = row.get("dst")
                f_batches = math.ceil(miss / batch_size) if isinstance(miss, int) and batch_size > 0 else None
                if f_batches is None:
                    continue
                log_info("[KubeJS] - %s | cache_miss=%s | batches=%s | dst=%s", f, miss, f_batches, dst)

    pending_lang_keys = _count_pending_lang_keys(pending_dir)
    log_info(f"🧾 [KubeJS] 統計：共有 {pending_lang_keys} 個 Key 待翻譯")

    if pending_lang_keys == 0:
        log_info("✅ [KubeJS] 無待翻譯項目，自動跳過步驟 2 (AI 翻譯)")
        result["step2"] = {"skipped": True, "reason": "pending lang keys = 0"}
        progress(session, 0.66)
    elif step_translate:
        if translator_fn is None:
            translator_fn = step2_translate_lm

        if dry_run:
            log_info("🧪 [KubeJS] 測試模式：執行 Step2 分析/報表（不送 API）")
            result["step2"] = translator_fn(
                pending_dir=str(pending_dir),
                output_dir=str(translated_dir),
                session=session,
                progress_base=0.33,
                progress_span=0.33,
                dry_run=True,
                write_new_cache=False,
            )
            _log_kubejs_step2_stats(result["step2"])
            progress(session, 0.66)
        else:
            log_info("🧠 [KubeJS] 開始 AI 翻譯流程...")
            result["step2"] = translator_fn(
                pending_dir=str(pending_dir),
                output_dir=str(translated_dir),
                session=session,
                progress_base=0.33,
                progress_span=0.33,
                dry_run=False,
                write_new_cache=write_new_cache,
            )
            _log_kubejs_step2_stats(result["step2"])
            progress(session, 0.66)
    else:
        log_info("⏭️ [KubeJS] 跳過步驟 2")
        progress(session, 0.66)

    if step_inject:
        if dry_run:
            log_info("🧪 [KubeJS] 測試模式：跳過注入操作")
            result["step3"] = {"skipped": True, "reason": "dry_run"}
        else:
            src_for_inject = translated_dir if translated_dir.exists() and any(translated_dir.rglob("*.json")) else pending_dir
            log_info(f"💉 [KubeJS] 執行注入：來源為 {src_for_inject.name}")
            result["step3"] = step3_inject(
                pack_or_kubejs_dir=str(base),
                src_dir=str(src_for_inject),
                final_dir=str(final_dir),
                session=session,
                progress_base=0.66,
                progress_span=0.33,
            )
    else:
        log_info("⏭️ [KubeJS] 跳過步驟 3")

    duration = get_formatted_duration(start_time)
    step2_summary = result.get("step2", {}) if isinstance(result.get("step2"), dict) else {}
    if step2_summary:
        log_info("✅ [KubeJS] Step2 統計明細：")
        summary = dict(step2_summary)
        summary.pop("per_file", None)
        log_info("%s", orjson.dumps(summary, option=orjson.OPT_INDENT_2).decode("utf-8"))

        if not summary.get("skipped"):
            total_keys = summary.get("total_keys")
            cache_hit = summary.get("cache_hit")
            cache_miss = summary.get("cache_miss")
            files = summary.get("files", summary.get("written_files"))
            batch_size = _get_default_batch_size("kubejs", None)
            est_batches = math.ceil(cache_miss / batch_size) if isinstance(cache_miss, int) and batch_size > 0 else None
            log_info(
                "\n🧾 [KubeJS] 摘要：📁 共 %s 個檔案、🔢 總計 %s 個 Key；✅ 快取命中 %s；🤖 需要 AI 翻譯 %s 條；🧮 預估批次 %s 次。",
                files,
                total_keys,
                cache_hit,
                cache_miss,
                est_batches,
            )

    log_info(f"🎉 [KubeJS] 任務完成！ {duration}")
    progress(session, 0.999)
    return result

__all__ = [
    "_is_filled_text",
    "deep_merge_3way_flat",
    "prune_en_by_tw_flat",
    "_read_json_dict_orjson",
    "_write_json_orjson",
    "clean_kubejs_from_raw",
    "resolve_kubejs_root",
    "step1_extract_and_clean",
    "step2_translate_lm",
    "step3_inject",
    "run_kubejs_pipeline",
]
