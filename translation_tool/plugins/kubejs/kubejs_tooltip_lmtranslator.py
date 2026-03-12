# kubejs_tooltip_lmtranslator.py
# ---------------------------------
"""
核心功能概覽
智慧批量翻譯 (translate_batch_smart)：利用快取機制優化翻譯流程，避免重複翻譯相同的文字，節省 API 消耗。
高速快取過濾 (fast_split_items_by_cache)：在翻譯前快速比對現有快取，將「已翻譯」與「待翻譯」的項目分離，提升處理效率。
安全循環翻譯 (translate_items_with_cache_loop)：
具備 ETA（預計剩餘時間） 顯示。
採用 安全切片（Safe Slicing） 技術，防止因單次請求過大導致失敗。
斷點續傳與即時儲存：
使用 TouchSet 與 Writer Flush 機制，翻譯過程中會即時寫入檔案，即使程式意外中斷也不會遺失已完成的進度。
預檢模式 (dry_run)：支援模擬執行並輸出預覽檔案（write_dry_run_preview），讓你在正式消耗 API 額度前確認格式是否正確。
數據導出 (TranslationRecorder)：支援將翻譯記錄導出為 JSON 或 CSV 格式，方便後續校對或二次開發。
進度追蹤 (session.set_progress)：內建進度鉤子（Hook），可對接外部 UI 或日誌系統顯示翻譯百分比。
路徑優化：自動處理語系資料夾轉換（例如將原本的 en_us 自動導向至 zh_tw 目錄）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from translation_tool.core.lm_translator_main import translate_batch_smart
from translation_tool.core.lm_config_rules import validate_api_keys
from translation_tool.utils.config_manager import load_config

from translation_tool.core.lm_translator_shared import (
    CacheRule,
    fast_split_items_by_cache,
    translate_items_with_cache_loop,
    TouchSet,
    TranslationRecorder,
    write_dry_run_preview,
    write_cache_hit_preview,  # ✅ 新增：cache hit preview 檔
    _is_valid_hit,  # ✅ 新增：cache hit 判斷
)

from translation_tool.plugins.shared.json_io import (
    read_json_dict,
    write_json_dict,
    collect_json_files,
)
from translation_tool.plugins.shared.lang_path_rules import (
    compute_output_path,
)

from translation_tool.utils.log_unit import log_info, log_warning, progress


# -------------------------
# Smart item mapping
# -------------------------
def collect_items_from_mapping(
    mapping: Dict[str, Any],
    *,
    file_hint: str,
) -> List[Dict[str, Any]]:
    """
    Convert {path_key: source_text} mapping to translate_batch_smart items.
    Must ensure smart detects KubeJS profile => item["file"] contains "/kubejs/".
    """
    items: List[Dict[str, Any]] = []
    for k, v in mapping.items():
        if not isinstance(k, str):
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        items.append(
            {
                "file": file_hint,  # important for smart profile detection
                "path": k,
                "source_text": v,
                "text": v,
                "cache_type": "kubejs",
            }
        )
    return items


def count_translatable_keys(mapping: Dict[str, Any]) -> int:
    """計算 mapping 中『可翻譯字串』的數量。

    判斷條件：
    - value 是字串
    - 去除空白後仍有內容

    用途：顯示進度 / 估算翻譯總量。
    """
    return sum(1 for _, v in mapping.items() if isinstance(v, str) and v.strip())


# -------------------------
# Dry-run stats (optional)
# -------------------------
@dataclass
class DryRunStats:
    """DryRunStats 類別。

    用途：封裝與 DryRunStats 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    files: int = 0
    total_keys: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    per_file: Optional[list[dict]] = None


# -------------------------
# Public API (for UI/pipeline)
# -------------------------
def translate_kubejs_pending_to_zh_tw(
    *,
    pending_dir: str | Path,
    output_dir: str | Path,
    session=None,
    rename_langs: Optional[set[str]] = None,
    dry_run: bool = False,
    write_new_cache: bool = False,
) -> dict:
    """
    Translate KubeJS pending JSON dir -> output dir (usually LM翻譯後).
    - pending_dir: 例如 Output/kubejs/待翻譯
    - output_dir : 例如 Output/kubejs/LM翻譯後
    """
    validate_api_keys()

    in_dir = Path(pending_dir).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if rename_langs is None:
        rename_langs = {
            "ru_ru",
            "ja_jp",
            "ko_kr",
            "zh_cn",
            "zh_hk",
            "zh_sg",
            "pt_br",
            "es_es",
            "en_us",
            "fr_fr",
            "de_de",
            "it_it",
            "pl_pl",
            "tr_tr",
            "uk_ua",
            "cs_cz",
            "hu_hu",
            "nl_nl",
            "sv_se",
            "no_no",
            "da_dk",
            "fi_fi",
        }

    if not in_dir.exists() or not in_dir.is_dir():
        raise FileNotFoundError(f"pending_dir 不存在或不是資料夾：{in_dir}")

    json_files = collect_json_files(in_dir)
    if not json_files:
        raise FileNotFoundError(f"找不到任何 .json：{in_dir}")

    # -------------------------
    # Pre-scan global total (multithread, like FTB)
    # -------------------------
    per_file_counts: List[Tuple[Path, int]] = []
    global_total_keys = 0

    def _count_one(src: Path) -> Tuple[Path, int]:
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`read_json_dict`

        回傳：依函式內 return path。
        """
        try:
            mapping = read_json_dict(src)
            return src, int(count_translatable_keys(mapping))
        except Exception:
            return src, 0

    max_workers = int(
        load_config().get("translator", {}).get("parallel_execution_workers", 4) or 4
    )
    max_workers = max(1, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_count_one, p) for p in json_files]
        for fu in as_completed(futs):
            src, c = fu.result()
            per_file_counts.append((src, c))
            global_total_keys += c

    per_file_counts.sort(key=lambda x: x[0].as_posix())

    if global_total_keys == 0:
        log_info("ℹ️ [KubeJS-LM] 0 keys，跳過翻譯")
        return {"written_files": 0, "total_keys": 0, "out_dir": str(out_dir)}

    # -------------------------
    # Cache rules
    # -------------------------
    cache_rules = {"kubejs": CacheRule("path|source_text")}

    # -------------------------
    # Pre-calc global miss/hit (so progress uses "real translate count")
    # -------------------------
    global_total_to_translate = 0
    global_total_hit = 0

    for src, key_count in per_file_counts:
        if key_count == 0:
            continue
        try:
            mapping = read_json_dict(src)
            rel_src = src.relative_to(in_dir).as_posix()
            file_hint = f"output/kubejs/{rel_src}"  # must contain /kubejs/
            all_items = collect_items_from_mapping(mapping, file_hint=file_hint)

            cached_items, items_to_translate = fast_split_items_by_cache(
                all_items,
                cache_rules=cache_rules,
                is_valid_hit=_is_valid_hit,
            )
            global_total_hit += len(cached_items)
            global_total_to_translate += len(items_to_translate)
        except Exception:
            pass

    log_info(
        f"🔎 [KubeJS-LM] 待翻譯檔案數：{len(json_files)}；總 keys：{global_total_keys}\n"
        f"✅ [KubeJS-LM] cache_hit={global_total_hit} | 實際需翻譯(cache_miss)={global_total_to_translate}"
    )

    if global_total_to_translate == 0:
        progress(1.0)

    # -------------------------
    # Global build + translate (NO per-file translate)
    # -------------------------
    translated_done = 0
    avg_batch_sec = None
    total_written = 0

    per_file_rows: list[dict] = []
    rec = TranslationRecorder()
    touch = TouchSet()
    _file_write_table: dict[str, tuple[Path, Dict[str, str]]] = {}

    def _writer(file_id: str) -> None:
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`write_json_dict`

        回傳：None
        """
        dst_path, data = _file_write_table[file_id]
        write_json_dict(dst_path, data)

    # Build phase: per-file state + global miss list
    file_states: dict[str, dict] = {}
    all_miss_items: List[Dict[str, Any]] = []
    all_hit_items: List[Dict[str, Any]] = []

    for idx, (src, key_count) in enumerate(per_file_counts, start=1):
        if key_count == 0:
            continue

        mapping = read_json_dict(src)
        rel_src = src.relative_to(in_dir).as_posix()

        file_hint = f"output/kubejs/{rel_src}"
        all_items = collect_items_from_mapping(mapping, file_hint=file_hint)

        cached_items, items_to_translate = fast_split_items_by_cache(
            all_items,
            cache_rules=cache_rules,
            is_valid_hit=_is_valid_hit,
        )

        # ✅ NEW：累積 hit items
        all_hit_items.extend(cached_items)

        hit = len(cached_items)
        miss = len(items_to_translate)

        dst = compute_output_path(src, in_dir, out_dir, rename_langs)
        per_file_rows.append(
            {
                "file": rel_src,
                "keys": key_count,
                "cache_hit": hit,
                "cache_miss": miss,
                "dst": dst.relative_to(out_dir).as_posix(),
            }
        )

        log_info(
            f"[KubeJS-LM] 檔案 {idx}/{len(per_file_counts)}：{rel_src} ｜"
            f"總字串 {key_count}，快取命中 {hit}，需翻譯 {miss} ｜"
            f"輸出 → {dst.relative_to(out_dir).as_posix()}"
        )

        # out_map base
        out_map: Dict[str, str] = {
            k: v
            for k, v in mapping.items()
            if isinstance(k, str) and isinstance(v, str)
        }

        # apply cache hits now (record as hit)
        for it in cached_items:
            p = it.get("path")
            t = it.get("text")
            if isinstance(p, str) and isinstance(t, str):
                out_map[p] = t
                try:
                    rec.record(
                        cache_type="kubejs",
                        file_id=rel_src,
                        path=p,
                        src=str(it.get("source_text") or ""),
                        dst=t,
                        cache_hit=True,
                        extra={"dst_file": dst.relative_to(out_dir).as_posix()},
                    )
                except Exception:
                    pass

        file_id = dst.as_posix()
        _file_write_table[file_id] = (dst, out_map)

        file_states[rel_src] = {
            "rel_src": rel_src,
            "dst": dst,
            "file_id": file_id,
            "out_map": out_map,
        }

        # tag miss items with file context (so callback can write back to correct out_map)
        for it in items_to_translate:
            it["file_rel"] = rel_src
            it["dst_file"] = dst.relative_to(out_dir).as_posix()

        all_miss_items.extend(items_to_translate)

    # -------------------------
    # Dry-run: preview only (no API)
    # -------------------------
    if dry_run:
        dry_preview_items = all_miss_items[:2000] if all_miss_items else []

        preview_path = None
        try:
            meta = {
                "files": len(per_file_rows),
                "total_keys": global_total_keys,
                "cache_hit": global_total_hit,
                "cache_miss": global_total_to_translate,
            }

            preview_path = write_dry_run_preview(
                out_dir,
                dry_preview_items,
                filename="_kubejs_dry_run_preview.json",
                meta=meta,
            )
            log_info(f"🧪 [DRY-RUN] preview written: {preview_path.as_posix()}")

            # ✅ NEW：cache hit preview
            hit_preview_path = write_cache_hit_preview(
                out_dir,
                all_hit_items[:2000],  # 或不切片
                filename="_kubejs_dry_run_cache_hit_preview.json",
                meta=meta,
            )
            log_info(
                f"🎯 [DRY-RUN] cache-hit preview written: {hit_preview_path.as_posix()}"
            )

        except Exception as e:
            log_warning(f"⚠️ [KubeJS-LM] DRY-RUN preview 輸出失敗：{e}")

        progress(1.0)
        return {
            "dry_run": True,
            "files": len(per_file_rows),
            "total_keys": global_total_keys,
            "cache_hit": global_total_hit,
            "cache_miss": global_total_to_translate,
            "preview_path": str(preview_path) if preview_path else None,
            "out_dir": str(out_dir),
            "per_file": per_file_rows,
        }

    # -------------------------
    # Real run: translate ONCE for all miss items
    # -------------------------
    if not all_miss_items:
        # All cache hit -> flush all files once
        for st in file_states.values():
            touch.touch(st["file_id"])
        touch.flush(_writer)
        total_written = len(file_states)
        translated_done = 0
        progress(1.0)
    else:

        def on_translated_item(it: Dict[str, Any]) -> None:
            """處理此函式的工作（細節以程式碼為準）。

            回傳：None
            """
            rel_src = it.get("file_rel")
            p = it.get("path")
            t = it.get("text")
            s = it.get("source_text")

            if not (isinstance(rel_src, str) and rel_src in file_states):
                return
            if not (isinstance(p, str) and isinstance(t, str)):
                return

            st = file_states[rel_src]
            st["out_map"][p] = t

            try:
                rec.record(
                    cache_type="kubejs",
                    file_id=rel_src,
                    path=p,
                    src=str(s or ""),
                    dst=t,
                    cache_hit=False,
                    extra={"dst_file": st["dst"].relative_to(out_dir).as_posix()},
                )
            except Exception:
                pass

            try:
                touch.touch(st["file_id"])
            except Exception:
                pass

        def on_batch_flushed() -> None:
            # write touched files each batch
            """處理此函式的工作（細節以程式碼為準）。

            - 主要包裝：`flush`

            回傳：None
            """
            try:
                touch.flush(_writer)
            except Exception:
                # fallback: write all
                for fid, (dstp, data) in _file_write_table.items():
                    write_json_dict(dstp, data)

        def _fmt_eta(sec: float) -> str:
            """處理此函式的工作（細節以程式碼為準）。

            - 主要包裝：`divmod`

            回傳：依函式內 return path。
            """
            if sec <= 0:
                return ""
            m, s = divmod(int(sec), 60)
            if m >= 60:
                h, m2 = divmod(m, 60)
                return f"{h}h{m2:02d}m{s:02d}s"
            if m > 0:
                return f"{m}m{s:02d}s"
            return f"{s}s"

        def on_progress(p: float, msg: str, eta_sec: float) -> None:
            """處理此函式的工作（細節以程式碼為準）。

            - 主要包裝：`_fmt_eta`, `log_info`, `progress`

            回傳：None
            """
            eta_txt = _fmt_eta(eta_sec)
            log_info(f"{msg}" + (f" | ETA ≈ {eta_txt}" if eta_txt else ""))
            progress(p)

        res = translate_items_with_cache_loop(
            all_miss_items,
            total_for_smart=global_total_to_translate,
            translate_batch_smart=lambda batch, total: translate_batch_smart(
                batch, total=total
            ),
            write_new_cache=bool(write_new_cache),
            cache_rules=cache_rules,
            on_translated_item=on_translated_item,
            on_batch_flushed=on_batch_flushed,
            on_progress=on_progress,
        )

        translated_done = int(res.processed or 0)
        avg_batch_sec = (
            (res.elapsed_sec / res.completed_calls) if res.completed_calls else None
        )

        # final flush all
        for st in file_states.values():
            touch.touch(st["file_id"])
        touch.flush(_writer)

        total_written = len(file_states)
        progress(1.0)

    # -------------------------
    # Export records
    # -------------------------
    rec_json = None
    rec_csv = None
    try:
        rec_json = rec.export_json(out_dir / "translation_map.json")
        rec_csv = rec.export_csv(out_dir / "translation_map.csv")
        log_info(f"🧾 [KubeJS-LM] records written: {rec_json} | {rec_csv}")
    except Exception as ex:
        log_info(f"⚠️ [KubeJS-LM] export records failed: {ex}")

    return {
        "written_files": total_written,
        "total_keys": global_total_keys,
        "cache_hit": global_total_hit,
        "cache_miss": global_total_to_translate,
        "api_translated": translated_done,
        "avg_batch_sec": avg_batch_sec,
        "out_dir": str(out_dir),
        "records_json": str(rec_json) if rec_json else None,
        "records_csv": str(rec_csv) if rec_csv else None,
        "per_file": per_file_rows,
    }
