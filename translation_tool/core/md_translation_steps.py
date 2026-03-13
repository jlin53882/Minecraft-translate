from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Callable

def step1_extract_impl(
    *,
    input_dir: str,
    pending_dir: str,
    lang_mode: str,
    session,
    progress_base: float,
    progress_span: float,
    iter_md_files_fn,
    safe_relpath_fn,
    extract_blocks_fn,
    detect_lang_segment_fn,
    map_rel_lang_path_fn,
    contains_cjk_fn,
    build_pending_json_fn,
    progress_fn: Callable[[Any, float], None],
    log_warning_fn: Callable[..., None],
) -> Dict[str, Any]:
    in_root = Path(input_dir).resolve()
    pending_root = Path(pending_dir).resolve()
    pending_root.mkdir(parents=True, exist_ok=True)

    if not in_root.exists() or not in_root.is_dir():
        raise FileNotFoundError(f"找不到 Markdown 輸入資料夾：{in_root}")

    md_files = list(iter_md_files_fn(in_root))
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
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        progress_fn(session, progress_base + progress_span)
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
        rel_md = safe_relpath_fn(md_path, in_root)
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            md_text = md_path.read_text(encoding="utf-8", errors="replace")

        items = extract_blocks_fn(md_text, rel_md, lang_mode=lang_mode)

        if lang_mode == "non_cjk_only":
            rel_parts = rel_md.replace("\\", "/").split("/")
            lang = detect_lang_segment_fn(rel_parts)
            if lang == "en_us":
                rel_zh = map_rel_lang_path_fn(rel_md, src_lang="en_us", dst_lang="zh_tw")
                zh_path = in_root / rel_zh
                if zh_path.exists() and zh_path.is_file():
                    try:
                        zh_text = zh_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        zh_text = zh_path.read_text(encoding="utf-8", errors="replace")
                    zh_items = extract_blocks_fn(zh_text, rel_zh, lang_mode="all")
                    if len(items) == len(zh_items):
                        filtered = []
                        for en_it, zh_it in zip(items, zh_items):
                            if contains_cjk_fn(zh_it.text):
                                continue
                            filtered.append(en_it)
                        items = filtered
                    else:
                        log_warning_fn("[MD-抽取] 區塊數不一致，保留全部：%s (en=%s zh=%s)", rel_md, len(items), len(zh_items))

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
            payload = build_pending_json_fn(rel_md, md_path, items, lang_mode)
            out_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            json_written += 1

        progress_fn(session, progress_base + progress_span * (i / max(1, total_files)))

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
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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

def step2_translate_impl(*, pending_dir: str, translated_dir: str, session, progress_base: float, progress_span: float, dry_run: bool, write_new_cache: bool, progress_proxy_cls, translate_md_pending_fn, progress_fn) -> Dict[str, Any]:
    proxy = progress_proxy_cls(session, progress_base, progress_span)
    result = translate_md_pending_fn(
        pending_dir=str(Path(pending_dir).resolve()),
        out_dir=str(Path(translated_dir).resolve()),
        write_new_cache=bool(write_new_cache),
        dry_run=bool(dry_run),
        session=proxy,
    )
    progress_fn(session, progress_base + progress_span)
    return result

def step3_inject_impl(*, input_dir: str, json_dir: str, final_dir: str, session, progress_base: float, progress_span: float, iter_json_files_fn, load_items_from_json_fn, apply_item_to_md_lines_fn, map_lang_in_rel_path_allow_zh_fn, progress_fn) -> Dict[str, Any]:
    src_root = Path(input_dir).resolve()
    jroot = Path(json_dir).resolve()
    out_done = Path(final_dir).resolve()
    out_done.mkdir(parents=True, exist_ok=True)

    if not src_root.exists() or not src_root.is_dir():
        raise FileNotFoundError(f"找不到 Markdown 原始資料夾：{src_root}")
    if not jroot.exists() or not jroot.is_dir():
        raise FileNotFoundError(f"找不到翻譯 JSON 資料夾：{jroot}")

    json_files = list(iter_json_files_fn(jroot))
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
            source_md, items = load_items_from_json_fn(jp)
        except Exception:
            skipped += 1
            error_files += 1
            progress_fn(session, progress_base + progress_span * (idx / max(1, total)))
            continue

        src_md_path = src_root / source_md
        if not src_md_path.exists():
            skipped += 1
            skipped_missing_source += 1
            progress_fn(session, progress_base + progress_span * (idx / max(1, total)))
            continue

        try:
            original_text = src_md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            original_text = src_md_path.read_text(encoding="utf-8", errors="replace")

        ends_with_nl = original_text.endswith("\n")
        md_lines = original_text.splitlines(keepends=False)

        for it in items:
            apply_item_to_md_lines_fn(md_lines, it)

        out_rel, status = map_lang_in_rel_path_allow_zh_fn(source_md, src_lang="en_us", dst_lang="zh_tw")
        if status not in ("SRC_EN", "SRC_ZH"):
            skipped += 1
            skipped_lang_status += 1
            progress_fn(session, progress_base + progress_span * (idx / max(1, total)))
            continue

        out_path = out_done / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_text = "\n".join(md_lines) + ("\n" if ends_with_nl else "")
        out_path.write_text(out_text, encoding="utf-8")
        wrote += 1
        progress_fn(session, progress_base + progress_span * (idx / max(1, total)))

    return {
        "written_files": wrote,
        "skipped_files": skipped,
        "skipped_missing_source": skipped_missing_source,
        "skipped_lang_status": skipped_lang_status,
        "error_files": error_files,
        "json_root": str(jroot),
        "final_root": str(out_done),
    }
