"""translation_tool/core/md_translation_steps.py 模組。

用途：Markdown 翻譯流程的三個步驟實作（抽取、翻譯、注入）。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from translation_tool.core.md_pipeline_files import (
    MANIFEST_NAME,
    REASON_INCOMPLETE,
    REASON_PENDING_MISMATCH,
    REASON_STALE_SOURCE,
    is_inside,
    is_safe_relative_path,
    read_pending_schema_doc,
    remove_stale_pending,
    verify_translated_doc,
    write_json_atomic,
)
from translation_tool.utils.log_unit import log_info, log_warning


def _finish_pending_dir(
    pending_root: Path, manifest: dict[str, Any], written: set[Path]
) -> int:
    """寫入 manifest 並清掉本輪沒重寫的過期 pending，回傳清掉的檔案數。

    只在所有 pending 都寫完之後才清：中途失敗時舊檔還在，不會留下「新舊都缺」的狀態。
    """
    manifest_path = pending_root / MANIFEST_NAME
    previous_root = None
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous_root = (
            previous.get("input_root") if isinstance(previous, dict) else None
        )
    except (OSError, ValueError):
        previous = None
    if previous_root and previous_root != manifest["input_root"]:
        log_warning(
            "[MD] ⚠️ 這個輸出資料夾上次處理的是另一個輸入資料夾（%s），"
            "上次留下的待翻譯檔與譯文會被視為過期並清除／略過",
            previous_root,
        )
    removed = remove_stale_pending(pending_root, written)
    manifest["stale_removed"] = removed
    write_json_atomic(manifest_path, manifest)
    if removed:
        log_info(
            "[MD-抽取] 已清除 %s 個過期的待翻譯檔（來源已刪除或不再有可翻譯區塊）",
            removed,
        )
    return removed


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
) -> dict[str, Any]:
    """
    第一步實作：掃描 Markdown 資料夾，依 lang_mode 過濾並抽取可翻譯區塊，將結果寫入待翻譯 JSON。
    若為 non_cjk_only 模式，會比對同義的 zh_tw 檔案以過濾已譯內容；支援去重複（相同 hash 只留一份）。
    """
    in_root = Path(input_dir).resolve()
    pending_root = Path(pending_dir).resolve()
    pending_root.mkdir(parents=True, exist_ok=True)

    if not in_root.exists() or not in_root.is_dir():
        raise FileNotFoundError(f"找不到 Markdown 輸入資料夾：{in_root}")

    md_files = list(iter_md_files_fn(in_root))
    if not md_files:
        # 常見原因：.md 不在 en_us / zh_tw 資料夾下，會被略過；要讓使用者知道，而非靜默成功
        log_warning(
            "[MD] ⚠️ 找不到可處理的 .md：只處理路徑中含 en_us / zh_tw 資料夾的 Markdown"
            "（例如 docs/en_us/intro.md），請確認輸入資料夾：%s",
            in_root,
        )
        manifest_path = pending_root / MANIFEST_NAME
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
        stale_removed = _finish_pending_dir(pending_root, manifest, set())
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
            "stale_removed": stale_removed,
            "manifest_path": str(manifest_path),
        }

    written_paths: set[Path] = set()
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
                rel_zh = map_rel_lang_path_fn(
                    rel_md, src_lang="en_us", dst_lang="zh_tw"
                )
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
                        log_warning_fn(
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
            payload = build_pending_json_fn(rel_md, md_path, items, lang_mode)
            write_json_atomic(out_json_path, payload)
            written_paths.add(out_json_path)
            json_written += 1

        progress_fn(session, progress_base + progress_span * (i / max(1, total_files)))

    manifest_path = pending_root / MANIFEST_NAME
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
    stale_removed = _finish_pending_dir(pending_root, manifest, written_paths)

    return {
        "input_root": str(in_root),
        "pending_root": str(pending_root),
        "md_files_found": len(md_files),
        "json_written": json_written,
        "md_skipped_empty": skipped_empty,
        "total_blocks": total_blocks,
        "unique_blocks": unique_blocks,
        "duplicate_blocks": dup_blocks,
        "stale_removed": stale_removed,
        "manifest_path": str(manifest_path),
    }


def step2_translate_impl(
    *,
    pending_dir: str,
    translated_dir: str,
    session,
    progress_base: float,
    progress_span: float,
    dry_run: bool,
    write_new_cache: bool,
    progress_proxy_cls,
    translate_md_pending_fn,
    progress_fn,
) -> dict[str, Any]:
    """
    Step 2：翻譯待翻譯的 JSON 檔案。

    呼叫翻譯 API 將待翻譯內容翻譯為目標語言。

    參數：
        pending_dir: 待翻譯 JSON 資料夾
        translated_dir: 翻譯完成 JSON 輸出資料夾
        session: 進度會話
        progress_base: 進度起始值
        progress_span: 進度範圍
        dry_run: 是否為測試模式（不實際呼叫 API）
        write_new_cache: 是否寫入新快取
        progress_proxy_cls: 進度代理類別
        translate_md_pending_fn: 翻譯函式
        progress_fn: 進度回呼函式

    回傳：
        翻譯結果字典
    """
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


def step3_inject_impl(
    *,
    input_dir: str,
    json_dir: str,
    final_dir: str,
    session,
    progress_base: float,
    progress_span: float,
    iter_json_files_fn,
    load_items_from_json_fn,
    apply_item_to_md_lines_fn,
    map_lang_in_rel_path_allow_zh_fn,
    progress_fn,
    pending_dir: str | None = None,
    content_hash_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """
    Step 3：將翻譯結果注入回 Markdown 檔案。

    讀取原始 Markdown 和翻譯 JSON，將翻譯後的內容寫入輸出檔案。

    給了 pending_dir 時，每個譯後 JSON 注入前都會驗證（見 verify_translated_doc）：
    Step 2 是否完成、是否仍對應現行 pending、原始檔對應區塊是否被改過；不符的檔案
    略過並計入對應統計，不寫入 final_dir（保留上一份完成品）。

    回傳：
        處理結果字典，包含寫入檔案數與各種略過原因的統計
    """
    src_root = Path(input_dir).resolve()
    jroot = Path(json_dir).resolve()
    out_done = Path(final_dir).resolve()
    out_done.mkdir(parents=True, exist_ok=True)
    pending_root = Path(pending_dir).resolve() if pending_dir else None

    if not src_root.exists() or not src_root.is_dir():
        raise FileNotFoundError(f"找不到 Markdown 原始資料夾：{src_root}")
    if not jroot.exists() or not jroot.is_dir():
        raise FileNotFoundError(f"找不到翻譯 JSON 資料夾：{jroot}")

    stats = {
        "written_files": 0,
        "skipped_files": 0,
        "skipped_missing_source": 0,
        "skipped_lang_status": 0,
        "error_files": 0,
        "skipped_unsafe_path": 0,
        "skipped_incomplete": 0,
        "skipped_pending_mismatch": 0,
        "skipped_stale_source": 0,
    }
    outcome_counter = {
        "written": "written_files",
        "error": "error_files",
        "missing_source": "skipped_missing_source",
        "lang_status": "skipped_lang_status",
        "unsafe_path": "skipped_unsafe_path",
        REASON_INCOMPLETE: "skipped_incomplete",
        REASON_PENDING_MISMATCH: "skipped_pending_mismatch",
        REASON_STALE_SOURCE: "skipped_stale_source",
    }
    json_files = list(iter_json_files_fn(jroot))
    total = len(json_files)
    for idx, jp in enumerate(json_files, start=1):
        outcome = _inject_one_doc(
            jp,
            src_root=src_root,
            out_done=out_done,
            pending_root=pending_root,
            content_hash_fn=content_hash_fn,
            load_items_from_json_fn=load_items_from_json_fn,
            apply_item_to_md_lines_fn=apply_item_to_md_lines_fn,
            map_lang_fn=map_lang_in_rel_path_allow_zh_fn,
        )
        stats[outcome_counter[outcome]] += 1
        if outcome != "written":
            stats["skipped_files"] += 1
        progress_fn(session, progress_base + progress_span * (idx / max(1, total)))

    not_updated = stats["skipped_files"] - stats["skipped_lang_status"]
    if not_updated > 0:
        log_warning(
            f"[MD] 有 {not_updated} 個檔案沒有更新（來源已變更、譯文未完成或路徑不安全等）；"
            "完成資料夾內對應的檔案仍是上一次的結果，請重跑抽取與翻譯。"
        )
    return {**stats, "json_root": str(jroot), "final_root": str(out_done)}


def _inject_one_doc(
    jp: Path,
    *,
    src_root: Path,
    out_done: Path,
    pending_root: Path | None,
    content_hash_fn,
    load_items_from_json_fn,
    apply_item_to_md_lines_fn,
    map_lang_fn,
) -> str:
    """處理單一譯後 JSON，回傳結果代碼（written／error／各種略過原因）。"""
    try:
        source_md, items = load_items_from_json_fn(jp)
    except Exception as exc:  # noqa: BLE001 - 單一待注入檔讀取失敗：計入錯誤並繼續，但要留下是哪個檔
        log_warning(f"[MD] 注入時略過無法讀取的待翻譯檔 {jp}: {exc!r}")
        return "error"

    if not is_safe_relative_path(source_md):
        log_warning(
            f"[MD] 略過不安全的來源路徑（含 .. 或絕對路徑）：{source_md!r} ({jp})"
        )
        return "unsafe_path"
    src_md_path = src_root / source_md
    if not is_inside(src_root, src_md_path):
        log_warning(f"[MD] 略過超出輸入資料夾的來源路徑：{source_md!r} ({jp})")
        return "unsafe_path"
    if not src_md_path.exists():
        return "missing_source"

    raw = src_md_path.read_bytes()
    original_text = raw.decode("utf-8", errors="replace")
    md_lines = original_text.splitlines(keepends=False)

    if pending_root is not None:
        reason = _verify_against_pending(
            jp, source_md, pending_root, md_lines, content_hash_fn
        )
        if reason is not None:
            log_warning(f"[MD] 略過 {source_md}：{_REASON_TEXT[reason]}（{jp.name}）")
            return reason

    # 插入／刪行會讓後面的行號位移：由後往前套用
    for it in sorted(items, key=lambda x: getattr(x, "start_line", 0), reverse=True):
        apply_item_to_md_lines_fn(md_lines, it)

    out_rel, status = map_lang_fn(source_md, src_lang="en_us", dst_lang="zh_tw")
    if status not in ("SRC_EN", "SRC_ZH"):
        return "lang_status"
    out_path = out_done / out_rel
    if not is_safe_relative_path(out_rel) or not is_inside(out_done, out_path):
        log_warning(f"[MD] 略過超出輸出資料夾的輸出路徑：{out_rel!r} ({jp})")
        return "unsafe_path"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ends_with_nl = original_text.endswith("\n")
    out_path.write_text(
        "\n".join(md_lines) + ("\n" if ends_with_nl else ""), encoding="utf-8"
    )
    return "written"


_REASON_TEXT = {
    REASON_INCOMPLETE: "Step 2 沒有完整完成，譯文是半成品",
    REASON_PENDING_MISMATCH: "譯文與目前的待翻譯內容不一致（來源已刪除或篩選結果改變）",
    REASON_STALE_SOURCE: "原始檔在抽取後被修改，行號可能已經對不上",
}


def _verify_against_pending(
    jp: Path,
    source_md: str,
    pending_root: Path,
    md_lines: list[str],
    content_hash_fn,
) -> str | None:
    """讀出譯後 JSON 與對應 pending 並驗證；pending 以 source_md 對應，不依賴譯後檔的存放層級。"""
    doc = read_pending_schema_doc(jp)
    if doc is None:
        return REASON_PENDING_MISMATCH
    pending_path = pending_root / (source_md.replace("\\", "/") + ".json")
    pending_doc = (
        read_pending_schema_doc(pending_path)
        if is_inside(pending_root, pending_path)
        else None
    )
    return verify_translated_doc(
        doc,
        pending_doc=pending_doc,
        source_lines=md_lines,
        content_hash_fn=content_hash_fn,
    )
