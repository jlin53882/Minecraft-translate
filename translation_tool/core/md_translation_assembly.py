from __future__ import annotations

import json
import time
import math
from pathlib import Path
from typing import Any, Dict, Optional

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
from translation_tool.utils.log_unit import (
    get_formatted_duration,
    log_info,
    log_warning,
    progress,
)

# 抽取模式中文顯示（內部值維持既有常數，確保相容）
_LANG_MODE_LABELS = {
    "non_cjk_only": "僅抽取非中文（non_cjk_only）",
    "cjk_only": "僅抽取中文（cjk_only）",
    "all": "抽取全部（all）",
}


def _normalize_lang_mode(lang_mode: str) -> str:
    """正規化抽取語言模式，未知值回退為 non_cjk_only。"""
    mode = (lang_mode or "").strip().lower()
    if mode in _LANG_MODE_LABELS:
        return mode
    return "non_cjk_only"


def _count_json_files(root: Path) -> int:
    """計算資料夾下所有 JSON 檔案數量。"""
    if not root.exists() or not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*.json") if p.is_file())


def _count_md_pending_docs(root: Path) -> int:
    """只統計 schema=md_pending_blocks_v1 的可用 pending 文件。"""
    if not root.exists() or not root.is_dir():
        return 0
    count = 0
    for p in root.rglob("*.json"):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("schema") == "md_pending_blocks_v1":
            count += 1
    return count


def _log_md_step2_stats(step2_res: Dict[str, Any]) -> None:
    if not isinstance(step2_res, dict):
        return

    if step2_res.get("skipped"):
        log_info("[MD] [2/3] 已略過翻譯：%s", step2_res.get("reason"))
        return

    is_dry = bool(step2_res.get("dry_run"))
    files = step2_res.get("files", step2_res.get("written_files"))
    total_blocks = step2_res.get("total_blocks")
    unique_blocks = step2_res.get("unique_blocks")
    duplicate_blocks = step2_res.get("duplicate_blocks")
    cache_hit = step2_res.get("cache_hit")
    cache_miss = step2_res.get("cache_miss")
    already_zh_skipped = step2_res.get("already_zh_skipped")
    missing_hash = step2_res.get("missing_hash")
    out_dir = step2_res.get("out_dir")
    batch_size = _get_default_batch_size("md", None)
    avg_batch_sec = step2_res.get("avg_batch_sec")
    est_sec_per_batch = avg_batch_sec
    est_batches = (
        math.ceil(cache_miss / batch_size)
        if isinstance(cache_miss, int) and batch_size > 0
        else None
    )

    log_info(
        "[MD] [2/3] 統計 %s | files=%s | total=%s | unique=%s | dup=%s",
        "DRY-RUN" if is_dry else "翻譯",
        files,
        total_blocks,
        unique_blocks,
        duplicate_blocks,
    )
    log_info(
        "[MD] [2/3] cache_hit=%s | cache_miss=%s | already_zh_skipped=%s",
        cache_hit,
        cache_miss,
        already_zh_skipped,
    )
    if missing_hash is not None:
        log_info("[MD] [2/3] missing_hash=%s", missing_hash)
    if out_dir:
        log_info("[MD] [2/3] out_dir=%s", out_dir)
    if est_batches is not None:
        log_info(
            "[MD] [2/3] 預估批次：%s (batch_size=%s)",
            est_batches,
            batch_size,
        )
    if avg_batch_sec:
        log_info("[MD] [2/3] 平均每批耗時(本次)：%.2fs", avg_batch_sec)
    if est_batches is not None and est_sec_per_batch:
        total_sec = int(est_batches * est_sec_per_batch)
        m, s = divmod(total_sec, 60)
        h, m = divmod(m, 60)
        eta_txt = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
        log_info("[MD] [2/3] 預估總耗時：%s", eta_txt)


class _ProgressProxy:
    """把 step 內部 0~1 進度轉成整體 pipeline 的區段進度。"""

    def __init__(self, parent: Any, base: float, span: float):
        self.parent = parent
        self.base = float(base)
        self.span = float(span)

    def set_progress(self, p: float):
        if not self.parent or not hasattr(self.parent, "set_progress"):
            return
        try:
            p = 0.0 if p is None else float(p)
            p = min(1.0, max(0.0, p))
            self.parent.set_progress(self.base + p * self.span)
        except Exception:
            pass


def step1_extract(
    *,
    input_dir: str,
    pending_dir: str,
    lang_mode: str = "non_cjk_only",
    session=None,
    progress_base: float = 0.0,
    progress_span: float = 0.33,
) -> Dict[str, Any]:
    # Step1：抽取 md 區塊並輸出待翻譯 JSON
    in_root = Path(input_dir).resolve()
    pending_root = Path(pending_dir).resolve()
    pending_root.mkdir(parents=True, exist_ok=True)

    if not in_root.exists() or not in_root.is_dir():
        raise FileNotFoundError(f"找不到 Markdown 輸入資料夾：{in_root}")

    md_files = list(iter_md_files(in_root))
    if not md_files:
        manifest_path = pending_root / "_manifest.json"
        manifest = {
            "schema": "md_pending_manifest_blocks_v1",
            "input_root": str(in_root),
            "pending_root": str(pending_root),
            "lang_filter_mode": lang_mode,
            "md_files_found": 0,
            "json_written": 0,
            "md_skipped_empty": 0,
            "total_blocks": 0,
            "unique_blocks": 0,
            "duplicate_blocks": 0,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        progress(session, progress_base + progress_span)
        return {
            "input_root": str(in_root),
            "pending_root": str(pending_root),
            "md_files_found": 0,
            "json_written": 0,
            "md_skipped_empty": 0,
            "total_blocks": 0,
            "unique_blocks": 0,
            "duplicate_blocks": 0,
            "manifest_path": str(manifest_path),
        }

    total_blocks = 0
    json_written = 0
    skipped_empty = 0
    seen_hashes = set()
    dup_blocks = 0
    unique_blocks = 0

    total_files = len(md_files)
    for i, md_path in enumerate(md_files, start=1):
        rel_md = safe_relpath(md_path, in_root)

        try:
            md_text = md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            md_text = md_path.read_text(encoding="utf-8", errors="replace")

        items = extract_blocks(md_text, rel_md, lang_mode=lang_mode)

        # 對齊既有規則：non_cjk_only 下，若對應 zh_tw block 已有中文，則略過該 en block
        if lang_mode == "non_cjk_only":
            rel_parts = rel_md.replace("\\", "/").split("/")
            lang = detect_lang_segment(rel_parts)

            if lang == "en_us":
                rel_zh = map_rel_lang_path(rel_md, src_lang="en_us", dst_lang="zh_tw")
                zh_path = in_root / rel_zh

                if zh_path.exists() and zh_path.is_file():
                    try:
                        zh_text = zh_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        zh_text = zh_path.read_text(encoding="utf-8", errors="replace")

                    zh_items = extract_blocks(zh_text, rel_zh, lang_mode="all")
                    if len(items) == len(zh_items):
                        filtered = []
                        for en_it, zh_it in zip(items, zh_items):
                            if contains_cjk(zh_it.text):
                                continue
                            filtered.append(en_it)
                        items = filtered
                    else:
                        log_warning(
                            "[MD-抽取] 區塊數不一致，保留全部：%s (en=%s zh=%s)",
                            rel_md,
                            len(items),
                            len(zh_items),
                        )

        total_blocks += len(items)
        for it in items:
            if it.content_hash in seen_hashes:
                dup_blocks += 1
            else:
                seen_hashes.add(it.content_hash)
                unique_blocks += 1

        if not items:
            skipped_empty += 1
        else:
            out_json_path = pending_root / (rel_md + ".json")
            out_json_path.parent.mkdir(parents=True, exist_ok=True)
            payload = build_pending_json(rel_md, md_path, items, lang_mode)
            out_json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            json_written += 1

        progress(
            session,
            progress_base + progress_span * (i / max(1, total_files)),
        )

    manifest_path = pending_root / "_manifest.json"
    manifest = {
        "schema": "md_pending_manifest_blocks_v1",
        "input_root": str(in_root),
        "pending_root": str(pending_root),
        "lang_filter_mode": lang_mode,
        "md_files_found": len(md_files),
        "json_written": json_written,
        "md_skipped_empty": skipped_empty,
        "total_blocks": total_blocks,
        "unique_blocks": unique_blocks,
        "duplicate_blocks": dup_blocks,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "input_root": str(in_root),
        "pending_root": str(pending_root),
        "md_files_found": len(md_files),
        "json_written": json_written,
        "md_skipped_empty": skipped_empty,
        "total_blocks": total_blocks,
        "unique_blocks": unique_blocks,
        "duplicate_blocks": dup_blocks,
        "manifest_path": str(manifest_path),
    }


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
    # Step2：待翻譯 JSON -> LM 翻譯後 JSON
    proxy = _ProgressProxy(session, progress_base, progress_span)
    result = translate_md_pending(
        pending_dir=str(Path(pending_dir).resolve()),
        out_dir=str(Path(translated_dir).resolve()),
        write_new_cache=bool(write_new_cache),
        dry_run=bool(dry_run),
        session=proxy,
    )
    progress(session, progress_base + progress_span)
    return result


def step3_inject(
    *,
    input_dir: str,
    json_dir: str,
    final_dir: str,
    session=None,
    progress_base: float = 0.66,
    progress_span: float = 0.33,
) -> Dict[str, Any]:
    # Step3：把翻譯後 JSON 回寫到 md 檔案
    src_root = Path(input_dir).resolve()
    jroot = Path(json_dir).resolve()
    out_done = Path(final_dir).resolve()
    out_done.mkdir(parents=True, exist_ok=True)

    if not src_root.exists() or not src_root.is_dir():
        raise FileNotFoundError(f"找不到 Markdown 原始資料夾：{src_root}")
    if not jroot.exists() or not jroot.is_dir():
        raise FileNotFoundError(f"找不到翻譯 JSON 資料夾：{jroot}")

    json_files = list(iter_json_files(jroot))
    if not json_files:
        return {
            "written_files": 0,
            "skipped_files": 0,
            "skipped_missing_source": 0,
            "skipped_lang_status": 0,
            "error_files": 0,
            "json_root": str(jroot),
            "final_root": str(out_done),
        }

    wrote = 0
    skipped = 0
    skipped_missing_source = 0
    skipped_lang_status = 0
    error_files = 0

    total = len(json_files)
    for idx, jp in enumerate(json_files, start=1):
        try:
            source_md, items = load_items_from_json(jp)
        except Exception:
            skipped += 1
            error_files += 1
            progress(session, progress_base + progress_span * (idx / max(1, total)))
            continue

        src_md_path = src_root / source_md
        if not src_md_path.exists():
            skipped += 1
            skipped_missing_source += 1
            progress(session, progress_base + progress_span * (idx / max(1, total)))
            continue

        try:
            original_text = src_md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            original_text = src_md_path.read_text(encoding="utf-8", errors="replace")

        ends_with_nl = original_text.endswith("\n")
        md_lines = original_text.splitlines(keepends=False)

        for it in items:
            apply_item_to_md_lines(md_lines, it)

        out_rel, status = map_lang_in_rel_path_allow_zh(
            source_md, src_lang="en_us", dst_lang="zh_tw"
        )
        if status not in ("SRC_EN", "SRC_ZH"):
            skipped += 1
            skipped_lang_status += 1
            progress(session, progress_base + progress_span * (idx / max(1, total)))
            continue

        out_path = out_done / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_text = "\n".join(md_lines) + ("\n" if ends_with_nl else "")
        out_path.write_text(out_text, encoding="utf-8")
        wrote += 1

        progress(session, progress_base + progress_span * (idx / max(1, total)))

    return {
        "written_files": wrote,
        "skipped_files": skipped,
        "skipped_missing_source": skipped_missing_source,
        "skipped_lang_status": skipped_lang_status,
        "error_files": error_files,
        "json_root": str(jroot),
        "final_root": str(out_done),
    }


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
    """MD 三段式流程：Extract -> Translate -> Inject。"""
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

    # Step 1：抽取待翻譯
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

    # Step 2：LM 翻譯
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

    # Step 3：回寫 md
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

    # 結尾明細（白話摘要）
    step2_summary = result.get("step2", {}) if isinstance(result.get("step2"), dict) else {}
    if step2_summary and not step2_summary.get("skipped"):
        total_blocks = step2_summary.get("total_blocks")
        cache_hit = step2_summary.get("cache_hit")
        cache_miss = step2_summary.get("cache_miss")
        files = step2_summary.get("files", step2_summary.get("written_files"))
        batch_size = _get_default_batch_size("md", None)
        est_batches = (
            math.ceil(cache_miss / batch_size)
            if isinstance(cache_miss, int) and batch_size > 0
            else None
        )
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
