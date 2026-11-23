"""translation_tool/core/md_translation_steps.py 模組。

用途：Markdown 翻譯流程的三個步驟實作（抽取、翻譯、注入）。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from translation_tool.core.md_output_guard import OutputGuard
from translation_tool.core.md_pipeline_files import (
    MANIFEST_NAME,
    REASON_INCOMPLETE,
    REASON_PENDING_MISMATCH,
    REASON_STALE_SOURCE,
    is_inside,
    is_safe_relative_path,
    read_pending_schema_doc,
    remove_legacy_nested_translated,
    remove_stale_pending,
    verify_translated_doc,
    write_json_atomic,
)
from translation_tool.core.md_pipeline_pairing import (
    is_zh_cn_source,
    plan_sources,
    select_untranslated_blocks,
)
from translation_tool.core.md_pipeline_zh_cn import (
    base_is_stale,
    build_base,
    check_zh_cn_alignment,
    convert_markdown_lines,
)
from translation_tool.plugins.md.md_extract_qa import map_rel_lang_path
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.text_processor import convert_text


def _finish_pending_dir(
    pending_root: Path,
    manifest: dict[str, Any],
    written: set[Path],
    *,
    remove_stale: bool = True,
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
    removed = remove_stale_pending(pending_root, written) if remove_stale else 0
    manifest["stale_removed"] = removed
    write_json_atomic(manifest_path, manifest)
    if removed:
        log_info(
            "[MD-抽取] 已清除 %s 個過期的待翻譯檔（來源已刪除或不再有可翻譯區塊）",
            removed,
        )
    return removed


@dataclass
class _PairingReport:
    """Step 1 配對結果的統計，寫進 manifest 與回傳值。"""

    conflicts: list[list[str]] = field(default_factory=list)
    paired_docs: int = 0
    zh_cn_skipped: int = 0
    zh_cn_based_docs: list[str] = field(default_factory=list)
    zh_cn_unaligned: list[dict[str, str]] = field(default_factory=list)
    ambiguous_kept: int = 0
    structure_mismatch: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "paired_docs": self.paired_docs,
            "zh_cn_skipped": self.zh_cn_skipped,
            "zh_cn_based_docs": self.zh_cn_based_docs,
            "zh_cn_unaligned": self.zh_cn_unaligned,
            "output_conflicts": self.conflicts,
            "ambiguous_kept": self.ambiguous_kept,
            "structure_mismatch": self.structure_mismatch,
        }


def _read_text_lenient(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


@dataclass
class _CnBase:
    """以 zh_cn 為底稿的一份文件：要送翻的殘留區塊與 pending 的 base 欄位。"""

    items: list[Any]
    base: dict[str, Any]
    rel_en: str


def _decide_zh_cn_bases(
    plan,
    *,
    in_root: Path,
    lang_mode: str,
    extract_blocks_fn,
    report: _PairingReport,
) -> dict[str, _CnBase]:
    """沒有 zh_tw、有 en_us 與 zh_cn 時，決定 zh_cn 能不能整份當底稿（只在 non_cjk_only）。

    結構對得上才採用（見 check_zh_cn_alignment）；對不上整份退回英文送 LM，並記錄原因。
    只有與英文原文完全相同的殘留區塊會送 LM，其餘 zh_cn 內容於 Step 3 整檔簡轉繁。
    """
    bases: dict[str, _CnBase] = {}
    if lang_mode != "non_cjk_only":
        return bases
    for rel_cn, rel_en in sorted(plan.pair_cn.items()):
        en_text = _read_text_lenient(in_root / rel_en)
        cn_text = _read_text_lenient(in_root / rel_cn)
        reason = check_zh_cn_alignment(en_text, cn_text)
        if reason is not None:
            report.zh_cn_unaligned.append({"zh_cn": rel_cn, "reason": reason})
            log_warning(
                "[MD-抽取] zh_cn 與英文結構對不上，不採用它補譯，改用英文送翻：%s（%s）",
                rel_cn,
                reason,
            )
            continue
        en_items = extract_blocks_fn(en_text, rel_en, lang_mode="all")
        cn_items = extract_blocks_fn(cn_text, rel_cn, lang_mode="all")
        pending, ambiguous = select_untranslated_blocks(en_items, cn_items)
        report.ambiguous_kept += ambiguous
        bases[rel_cn] = _CnBase(
            items=pending,
            base=build_base(rel_en, in_root / rel_en, in_root / rel_cn),
            rel_en=rel_en,
        )
        report.zh_cn_based_docs.append(rel_cn)
    return bases


def _is_skipped_source(
    rel_md: str,
    plan,
    cn_base: _CnBase | None,
    en_covered_by_cn: set[str],
    report: _PairingReport,
) -> bool:
    """這個來源檔 Step 1 不產生 pending 嗎？同時更新統計。

    - 與 zh_tw／zh_cn 底稿配對的英文檔：輸出由底稿負責
    - 同語系重複（輸出會互相覆蓋）：全部略過
    - 沒被採用為底稿的 zh_cn 檔：Step 3 不會寫入它，送翻只是白花 API
    """
    if rel_md in plan.skip:
        if plan.skip[rel_md] == "paired_en":
            report.paired_docs += 1
        return True
    if rel_md in en_covered_by_cn:
        report.paired_docs += 1
        return True
    if cn_base is None and is_zh_cn_source(rel_md):
        report.zh_cn_skipped += 1
        return True
    return False


def _items_for_paired_zh(
    rel_zh: str,
    rel_en: str,
    zh_text: str,
    *,
    in_root: Path,
    lang_mode: str,
    extract_blocks_fn,
    report: _PairingReport,
) -> list[Any]:
    """成對的 zh_tw 檔以自己為底稿；non_cjk_only 時只挑出仍與英文原文相同的區塊。"""
    if lang_mode != "non_cjk_only":
        return extract_blocks_fn(zh_text, rel_zh, lang_mode=lang_mode)
    en_items = extract_blocks_fn(
        _read_text_lenient(in_root / rel_en), rel_en, lang_mode="all"
    )
    zh_items = extract_blocks_fn(zh_text, rel_zh, lang_mode="all")
    if len(en_items) != len(zh_items):
        report.structure_mismatch.append(rel_zh)
        log_warning(
            "[MD-抽取] 英文與繁中的區塊數不同（en=%s zh=%s），只翻繁中裡仍是英文原文的區塊，"
            "英文新增的段落不會自動補進去，請人工比對：%s",
            len(en_items),
            len(zh_items),
            rel_zh,
        )
    pending, ambiguous = select_untranslated_blocks(en_items, zh_items)
    report.ambiguous_kept += ambiguous
    if ambiguous:
        log_warning(
            "[MD-抽取] %s 有 %s 個區塊沒有中文、也和英文原文不同（可能是舊版英文或品牌名），"
            "不會送翻，請人工確認",
            rel_zh,
            ambiguous,
        )
    return pending


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
    remove_stale: bool = True,
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
        # 常見原因：.md 不在語言資料夾或指南資料夾下，會被略過；要讓使用者知道，而非靜默成功
        log_warning(
            "[MD] ⚠️ 找不到可處理的 .md：只處理路徑中含 en_us / zh_tw 資料夾的 Markdown"
            "（例如 docs/en_us/intro.md），或位在 assets/<模組>/ 之下、沒有語言資料夾、內容像指南敘述文字的英文 Markdown"
            "（例如 assets/ae2/ae2guide/intro/index.md），請確認輸入資料夾：%s",
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
        stale_removed = _finish_pending_dir(
            pending_root, manifest, set(), remove_stale=remove_stale
        )
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

    plan = plan_sources([safe_relpath_fn(p, in_root) for p in md_files])
    report = _PairingReport(conflicts=plan.conflicts)
    cn_bases = _decide_zh_cn_bases(
        plan,
        in_root=in_root,
        lang_mode=lang_mode,
        extract_blocks_fn=extract_blocks_fn,
        report=report,
    )
    en_covered_by_cn = {base.rel_en for base in cn_bases.values()}
    for group in plan.conflicts:
        log_warning_fn(
            "[MD-抽取] 這些檔案會寫到同一個輸出，全部略過（請只留一份）：%s",
            " | ".join(group),
        )

    total_files = len(md_files)
    for i, md_path in enumerate(md_files, start=1):
        rel_md = safe_relpath_fn(md_path, in_root)
        cn_base = cn_bases.get(rel_md)
        if _is_skipped_source(rel_md, plan, cn_base, en_covered_by_cn, report):
            progress_fn(
                session, progress_base + progress_span * (i / max(1, total_files))
            )
            continue
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            md_text = md_path.read_text(encoding="utf-8", errors="replace")

        items = extract_blocks_fn(md_text, rel_md, lang_mode=lang_mode)

        if cn_base is not None:
            items = cn_base.items
        elif rel_md in plan.pair_en:
            items = _items_for_paired_zh(
                rel_md,
                plan.pair_en[rel_md],
                md_text,
                in_root=in_root,
                lang_mode=lang_mode,
                extract_blocks_fn=extract_blocks_fn,
                report=report,
            )

        total_blocks += len(items)
        for it in items:
            if it.content_hash in seen_hashes:
                dup_blocks += 1
            else:
                seen_hashes.add(it.content_hash)
                unique_blocks += 1

        if not items and cn_base is None:
            skipped_empty += 1
        else:
            out_json_path = pending_root / (rel_md + ".json")
            payload = build_pending_json_fn(rel_md, md_path, items, lang_mode)
            if cn_base is not None:
                payload["base"] = cn_base.base  # 零殘留也要寫：Step 3 靠它整檔轉繁
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
        **report.as_dict(),
    }
    stale_removed = _finish_pending_dir(
        pending_root, manifest, written_paths, remove_stale=remove_stale
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
        "stale_removed": stale_removed,
        "manifest_path": str(manifest_path),
        **report.as_dict(),
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
    translated_root = Path(translated_dir).resolve()
    translated_root = Path(translated_dir).resolve()
    proxy = progress_proxy_cls(session, progress_base, progress_span)
    result = translate_md_pending_fn(
        pending_dir=str(Path(pending_dir).resolve()),
        out_dir=str(Path(translated_dir).resolve()),
        write_new_cache=bool(write_new_cache),
        dry_run=bool(dry_run),
        session=proxy,
    )
    # 舊雙層譯後檔要等新譯文完整寫好才清：翻譯失敗時舊檔仍是唯一可用的譯文；dry-run 不動任何檔案
    if not dry_run and isinstance(result, dict) and result.get("status") == "DONE":
        legacy_removed = remove_legacy_nested_translated(translated_root)
        if legacy_removed:
            log_info(
                "[MD-翻譯] 已清除舊版多一層資料夾內的 %s 個譯後檔（現在直接寫入 %s）",
                legacy_removed,
                translated_root.name,
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
    convert_zh_cn_fn: Callable[[str], str] | None = None,
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
        "skipped_output_conflict": 0,
        "skipped_superseded": 0,
        "skipped_manual_edit": 0,
        "written_from_zh_cn": 0,
    }
    written_outputs: set[str] = set()
    outcome_counter = {
        "written": "written_files",
        "written_zh_cn": "written_files",
        "error": "error_files",
        "missing_source": "skipped_missing_source",
        "lang_status": "skipped_lang_status",
        "unsafe_path": "skipped_unsafe_path",
        REASON_INCOMPLETE: "skipped_incomplete",
        REASON_PENDING_MISMATCH: "skipped_pending_mismatch",
        REASON_STALE_SOURCE: "skipped_stale_source",
        "output_conflict": "skipped_output_conflict",
        "superseded": "skipped_superseded",
        "manual_edit": "skipped_manual_edit",
    }
    guard = OutputGuard(out_done)
    json_files = list(iter_json_files_fn(jroot))
    pre_skips = _plan_inject_skips(json_files, jroot, src_root, pending_root)
    total = len(json_files)
    try:
        for idx, jp in enumerate(json_files, start=1):
            outcome = pre_skips.get(jp) or _inject_one_doc(
                jp,
                src_root=src_root,
                out_done=out_done,
                pending_root=pending_root,
                content_hash_fn=content_hash_fn,
                load_items_from_json_fn=load_items_from_json_fn,
                apply_item_to_md_lines_fn=apply_item_to_md_lines_fn,
                map_lang_fn=map_lang_in_rel_path_allow_zh_fn,
                written_outputs=written_outputs,
                convert_zh_cn_fn=convert_zh_cn_fn or (lambda s: convert_text(s, None)),
                guard=guard,
            )
            stats[outcome_counter[outcome]] += 1
            if outcome == "written_zh_cn":
                stats["written_from_zh_cn"] += 1
            if outcome not in ("written", "written_zh_cn"):
                stats["skipped_files"] += 1
            progress_fn(session, progress_base + progress_span * (idx / max(1, total)))
    finally:
        # 中途失敗也要存下已寫出的檔案紀錄，否則下一輪會把工具自己寫的檔誤判為手動修改
        guard.save()
    # 被 zh_tw／zh_cn 底稿取代的舊英文譯後檔是預期的略過，不算「沒更新」
    not_updated = (
        stats["skipped_files"]
        - stats["skipped_lang_status"]
        - stats["skipped_superseded"]
        - stats["skipped_manual_edit"]  # 已有專屬警告，重跑抽取與翻譯也不會改變結果
    )
    if not_updated > 0:
        log_warning(
            f"[MD] 有 {not_updated} 個檔案沒有更新（來源已變更、譯文未完成或路徑不安全等）；"
            "完成資料夾內對應的檔案仍是上一次的結果，請重跑抽取與翻譯。"
        )
    return {**stats, "json_root": str(jroot), "final_root": str(out_done)}


def _zh_cn_base_of(doc: dict[str, Any] | None, source_md: str) -> dict[str, Any] | None:
    """譯後檔／pending 若是以 zh_cn 為底稿，回傳其 ``base``；否則 None（來源路徑也必須是 zh_cn）。"""
    base = doc.get("base") if doc else None
    if (
        isinstance(base, dict)
        and base.get("lang") == "zh_cn"
        and is_zh_cn_source(source_md)
    ):
        return base
    return None


def _zh_cn_doc_is_current(source_md: str, pending_root: Path | None) -> bool:
    """zh_cn 來源要有現行的 zh_cn 底稿 pending 才算數；其他來源不受影響（沒給 pending 資料夾時一律算數）。

    舊版會替 zh_cn 檔產生沒有 ``base`` 的 pending，那種不是底稿，不能讓它擋掉英文的譯後檔。
    """
    if pending_root is None or not is_zh_cn_source(source_md):
        return True
    doc = read_pending_schema_doc(
        pending_root / (source_md.replace("\\", "/") + ".json")
    )
    return _zh_cn_base_of(doc, source_md) is not None


def _plan_inject_skips(
    json_files: list[Path],
    jroot: Path,
    src_root: Path,
    pending_root: Path | None = None,
) -> dict[Path, str]:
    """決定哪些譯後 JSON 不能寫入：輸出由誰寫，與檔案列舉順序無關。

    - en_us／zh_tw 同名且都有譯後檔（舊版留下）：以 zh_tw 為底稿，en_us 那份略過。
      配對只看輸入資料夾現在還存在的來源：zh_tw 檔已移出輸入資料夾時，譯後資料夾裡
      殘留的舊 zh_tw 譯後檔不能讓英文那份被略過
    - 同一個 source_md 有多份譯後檔（例如舊雙層資料夾）：只用正規位置
      `<譯後資料夾>/<source_md>.json` 那份，其他略過
    - 沒有 zh_tw 時，英文與 zh_cn 都有譯後檔：以 zh_cn 為底稿，英文那份略過。zh_cn 譯後檔
      只有在現行 pending 還在時才算數（Step 1 退回英文時不會有 zh_cn pending）
    - 同語系重複（輸出會互相覆蓋）：全部略過
    被底稿取代的英文譯後檔計入 skipped_superseded，其餘被略過的檔案計入 skipped_output_conflict 並警告。
    """
    by_source: dict[str, list[Path]] = {}
    for jp in json_files:
        doc = read_pending_schema_doc(jp)
        source_md = doc.get("source_md") if doc else None
        if isinstance(source_md, str):
            by_source.setdefault(source_md, []).append(jp)

    live_sources = [
        source_md
        for source_md in by_source
        if is_safe_relative_path(source_md)
        and (src_root / source_md).is_file()
        and _zh_cn_doc_is_current(source_md, pending_root)
    ]
    plan = plan_sources(live_sources)
    skips: dict[Path, str] = {}
    for rel_cn, rel_en in plan.pair_cn.items():
        if rel_cn in by_source and rel_en in by_source:
            log_warning(f"[MD] 略過 {rel_en} 的譯後檔：輸出由 zh_cn 底稿 {rel_cn} 負責")
            skips.update(dict.fromkeys(by_source[rel_en], "superseded"))
    for source_md, paths in by_source.items():
        reason = plan.skip.get(source_md)
        if reason is not None:
            log_warning(
                f"[MD] 略過 {source_md} 的譯後檔：輸出由同名的 zh_tw 檔或其他來源負責（{reason}）"
            )
            outcome = "superseded" if reason == "paired_en" else "output_conflict"
            skips.update(dict.fromkeys(paths, outcome))
            continue
        if len(paths) > 1:
            canonical = jroot / (source_md.replace("\\", "/") + ".json")
            keep = canonical if canonical in paths else min(paths)
            for jp in paths:
                if jp != keep:
                    log_warning(f"[MD] 略過重複的譯後檔 {jp}（已使用 {keep}）")
                    skips[jp] = "output_conflict"
    return skips


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
    written_outputs: set[str],
    convert_zh_cn_fn: Callable[[str], str],
    guard: OutputGuard,
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

    doc = read_pending_schema_doc(jp)
    base = _zh_cn_base_of(doc, source_md)
    from_zh_cn = base is not None
    if from_zh_cn and base_is_stale(base, src_root, source_md):
        log_warning(
            f"[MD] 略過 {source_md}：{_REASON_TEXT[REASON_STALE_SOURCE]}（{jp.name}）"
        )
        return REASON_STALE_SOURCE
    if pending_root is not None:
        reason = _verify_against_pending(
            jp, source_md, pending_root, md_lines, content_hash_fn
        )
        if reason is not None:
            log_warning(f"[MD] 略過 {source_md}：{_REASON_TEXT[reason]}（{jp.name}）")
            return reason

    if from_zh_cn:
        # 先整檔轉繁（行數不變），再填入 LM 的譯文：避免把 LM 已產生的繁體再轉一次
        md_lines = convert_markdown_lines(md_lines, convert_zh_cn_fn)
    # 插入／刪行會讓後面的行號位移：由後往前套用
    for it in sorted(items, key=lambda x: getattr(x, "start_line", 0), reverse=True):
        apply_item_to_md_lines_fn(md_lines, it)

    if from_zh_cn:
        out_rel, status = map_rel_lang_path(source_md, "zh_cn", "zh_tw"), "SRC_ZH"
    else:
        out_rel, status = map_lang_fn(source_md, src_lang="en_us", dst_lang="zh_tw")
    if status not in ("SRC_EN", "SRC_ZH"):
        return "lang_status"
    out_path = out_done / out_rel
    if not is_safe_relative_path(out_rel) or not is_inside(out_done, out_path):
        log_warning(f"[MD] 略過超出輸出資料夾的輸出路徑：{out_rel!r} ({jp})")
        return "unsafe_path"
    # 同一輪內已有別的來源寫過這個輸出（Windows 不分大小寫）：不覆蓋，留給使用者處理
    out_key = os.path.normcase(os.path.abspath(out_path))
    if out_key in written_outputs:
        log_warning(
            f"[MD] 略過 {source_md}：輸出 {out_rel} 已由另一個來源寫入（{jp.name}）"
        )
        return "output_conflict"
    if guard.is_manually_edited(out_rel, out_path):
        log_warning(
            f"[MD] 略過 {source_md}：完成檔 {out_rel} 在上次寫入後被手動修改過，不覆蓋；"
            "要重新產生請先刪除或移走該檔"
        )
        return "manual_edit"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ends_with_nl = original_text.endswith("\n")
    out_path.write_text(
        "\n".join(md_lines) + ("\n" if ends_with_nl else ""), encoding="utf-8"
    )
    written_outputs.add(out_key)
    guard.record(out_rel, out_path)
    return "written_zh_cn" if from_zh_cn else "written"


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
    if pending_doc is not None and doc.get("base") != pending_doc.get("base"):
        return REASON_PENDING_MISMATCH  # 底稿來源（zh_cn／英文）與現行 pending 不同
    return verify_translated_doc(
        doc,
        pending_doc=pending_doc,
        source_lines=md_lines,
        content_hash_fn=content_hash_fn,
    )
