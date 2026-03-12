"""translation_tool/plugins/md/md_lmtranslator.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# md_lmtranslator.py
# ------------------------------------------------------------
# 讀取 md_extract_qa.py 產生的 pending json（schema=md_pending_blocks_v1）
# 用 content_hash 做全域去重 + cache_type="md" 接入 cache_manager 分片快取
# 輸出到：<輸出>/LM翻譯後/<相同子路徑>.json
# ------------------------------------------------------------

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import time

from translation_tool.utils.log_unit import log_info, log_warning, log_error,get_formatted_duration,progress
from translation_tool.core.lm_config_rules import validate_api_keys, value_fully_translated
from translation_tool.core.lm_translator_main import translate_batch_smart
from translation_tool.core.lm_translator_shared import (
    CacheRule,
    TouchSet,
    TranslationRecorder,
    fast_split_items_by_cache,
    translate_items_with_cache_loop,
    write_dry_run_preview,          # ✅ NEW
    write_cache_hit_preview,        # ✅ 新增：cache hit preview 檔
    _is_valid_hit                   # ✅ 新增：cache hit 判斷

)
from translation_tool.plugins.shared.lang_text_rules import _strip_fmt, is_already_zh



# -------------------------
# basic io
# -------------------------

def read_json(path: Path) -> Dict[str, Any]:
    """`read_json`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def write_json(path: Path, data: Dict[str, Any]) -> None:
    """`write_json`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`mkdir`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def collect_pending_json_files(pending_root: Path) -> List[Path]:
    """`collect_pending_json_files`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`sorted`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    files = sorted(pending_root.rglob("*.json"))
    # 跳過 manifest
    files = [p for p in files if p.name.lower() != "_manifest.json"]
    return files


# -------------------------
# zh detection（避免已中文又送）
# -------------------------

# -------------------------
# pending model
# -------------------------

@dataclass
class PendingItem:
    """PendingItem 類別。

    用途：封裝與 PendingItem 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    id: str
    text: str
    content_hash: str
    start_line: int
    end_line: int

def load_pending_doc(path: Path) -> Tuple[Dict[str, Any], List[PendingItem]]:
    """`load_pending_doc`
    
    用途：
    - 載入此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`read_json`, `get`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    data = read_json(path)
    if data.get("schema") != "md_pending_blocks_v1":
        raise ValueError(f"schema 不符：{path}")

    items: List[PendingItem] = []
    for it in data.get("items", []):
        items.append(PendingItem(
            id=str(it.get("id", "")),
            text=str(it.get("text", "")),
            content_hash=str(it.get("content_hash", "")),
            start_line=int(it.get("start_line", 0) or 0),
            end_line=int(it.get("end_line", 0) or 0),
        ))
    return data, items

def compute_out_json_path(src_json: Path, in_pending_root: Path, out_root: Path) -> Path:
    """`compute_out_json_path`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`relative_to`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    rel = src_json.relative_to(in_pending_root)
    return out_root / "LM翻譯後" / rel

def translate_md_pending(
    *,
    pending_dir: str | Path,
    out_dir: str | Path,
    write_new_cache: bool = True,
    dry_run: bool = False,
    session=None,
) -> Dict[str, Any]:
    """`translate_md_pending`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`validate_api_keys`, `perf_counter`, `resolve`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    validate_api_keys()
    start_time=time.perf_counter()


    in_pending_root = Path(pending_dir).resolve()
    out_root = Path(out_dir).resolve()
    (out_root / "LM翻譯後").mkdir(parents=True, exist_ok=True)

    if not in_pending_root.exists() or not in_pending_root.is_dir():
        raise FileNotFoundError(f"pending_dir 不存在或不是資料夾：{in_pending_root}")

    json_files = collect_pending_json_files(in_pending_root)
    if not json_files:
        raise FileNotFoundError(f"找不到 pending json：{in_pending_root}")

    # -------------------------
    # 1) 全域收集：hash -> 原文（去重基礎）
    # -------------------------
    hash_to_src: Dict[str, str] = {}
    total_blocks = 0
    empty_blocks = 0

    for jp in json_files:
        try:
            _, items = load_pending_doc(jp)
        except Exception:
            continue

        for it in items:
            if not it.text.strip():
                empty_blocks += 1
                continue
            total_blocks += 1
            h = it.content_hash.strip() or f"NOHASH::{it.id}"
            # 只取第一個版本（同文 hash 一樣）
            if h not in hash_to_src:
                hash_to_src[h] = it.text

    unique_blocks = len(hash_to_src)
    dup_blocks = max(total_blocks - unique_blocks, 0)

    log_info(
        f"\n🔎 [MD-LM][掃描] 檔案：{len(json_files)}"
        f"\n📦 [MD-LM][抽取] 總 blocks：{total_blocks}（空白略過 {empty_blocks}）"
        f"\n🧩 [MD-LM][去重] 唯一：{unique_blocks} | 重複：{dup_blocks}（同文只翻一次）"
    )

    if unique_blocks == 0:

        progress(session, 1.0)
        log_info("ℹ️ [MD-LM] 無唯一 blocks（可能全部空白/被過濾），不需翻譯。")
        log_info("總花費時間：%s", get_formatted_duration(start_time))
        return {"written_files": 0, "total_blocks": total_blocks, "unique_blocks": 0, "duplicate_blocks": dup_blocks}

    # -------------------------
    # 2) 準備 unique items → shared cache split
    # -------------------------
    cache_rules = {"md": CacheRule("path|source_text")}

    all_unique_items: List[Dict[str, Any]] = []
    already_zh_skipped = 0

    for h, src in hash_to_src.items():
        if is_already_zh(src):
            already_zh_skipped += 1
            continue
        all_unique_items.append({
            "cache_type": "md",
            "file": "md_pending_blocks",
            "path": h,            # ✅ 用 content_hash 當 path（去重 + 快取 key 的一部分）
            "source_text": src,
            "text": src,
        })

    cached_items, items_to_translate = fast_split_items_by_cache(
        all_unique_items,
        cache_rules=cache_rules,
        is_valid_hit=_is_valid_hit,
    )

    log_info(
        f"✅ [MD-LM][分析] cache hit：{len(cached_items)} | "
        f"需 AI 翻譯：{len(items_to_translate)} | "
        f"已中文跳過：{already_zh_skipped}"
    )
    
    
    if dry_run:
        meta = {
            "files": len(json_files),
            "total_blocks": total_blocks,
            "unique_blocks": unique_blocks,
            "duplicate_blocks": dup_blocks,
            "already_zh_skipped": already_zh_skipped,
            "cache_hit": len(cached_items),
            "cache_miss": len(items_to_translate),
        }

        try:
            # A) 待翻譯 preview（list）
            p1 = write_dry_run_preview(
                out_root,
                items_to_translate,
                meta=meta,
                filename="_md_dry_run_preview.json",
            )

            # B) cache hit preview（list）
            p2 = write_cache_hit_preview(
                out_root,
                cached_items,
                meta=meta,
                filename="_md_dry_run_cache_hit_preview.json",
            )

            log_info(f"🧪 [MD-LM] DRY-RUN preview：{p1}")
            log_info(f"🧪 [MD-LM] DRY-RUN cache-hit preview：{p2}")

        except Exception as e:
            log_warning(f"⚠️ [MD-LM] DRY-RUN preview 輸出失敗：{e}")

        log_info("ℹ️ [MD-LM] dry-run 模式：不翻譯、不寫檔。")
        log_info("總花費時間：%s", get_formatted_duration(start_time))
        return {
            "dry_run": True,
            "files": len(json_files),
            "total_blocks": total_blocks,
            "unique_blocks": unique_blocks,
            "duplicate_blocks": dup_blocks,
            "already_zh_skipped": already_zh_skipped,
            "cache_hit": len(cached_items),
            "cache_miss": len(items_to_translate),
        }

    # -------------------------
    # 3) 翻譯：建立 hash -> dst（先塞 cache hit，再跑 shared loop 補 miss）
    # -------------------------
    hash_to_dst: Dict[str, str] = {}
    for it in cached_items:
        h = str(it.get("path") or "")
        dst = str(it.get("text") or "")
        if h and dst:
            hash_to_dst[h] = dst

    rec = TranslationRecorder()
    touch = TouchSet()

    # md 是「最後一次寫出全部檔案」即可，所以 touched writer 這裡先做 noop
    def _writer(_fid: str) -> None:
        """`_writer`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        return

    def on_translated_item(it: Dict[str, Any]) -> None:
        """`on_translated_item`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`str`, `record`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        h = str(it.get("path") or "")
        dst = str(it.get("text") or "")
        if h and dst:
            hash_to_dst[h] = dst
        # 這裡 recorder 的 cache_type 用 md（方便你日後 QC）
        try:
            rec.record(
                cache_type="md",
                file_id="md_pending_blocks",
                path=h,
                src=str(it.get("source_text") or ""),
                dst=dst,
                cache_hit=False,
                extra={},
            )
        except Exception:
            pass

    def on_batch_flushed() -> None:
        """`on_batch_flushed`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`touch`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        try:
            touch.touch("noop")
            touch.flush(_writer)
        except Exception:
            pass

    def _fmt_eta(sec: float) -> str:
        """`_fmt_eta`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`divmod`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        if sec <= 0:
            return ""
        m, s = divmod(int(sec), 60)
        return f"{m}m{s:02d}s" if m > 0 else f"{s}s"

    def on_progress(p: float, msg: str, eta_sec: float) -> None:
        """`on_progress`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`_fmt_eta`, `log_info`, `progress`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        eta_txt = _fmt_eta(eta_sec)
        log_info(
            "⏳ [MD-LM] %s%s",
            msg,
            f" | ETA：{eta_txt}" if eta_txt else "",
        )
        progress(session, p)


    avg_batch_sec = None
    if items_to_translate:
        res = translate_items_with_cache_loop(
            items_to_translate,
            total_for_smart=len(items_to_translate),
            translate_batch_smart=lambda batch, total: translate_batch_smart(batch, total=total),
            write_new_cache=bool(write_new_cache),   # ✅ 這裡會走 add_to_cache('md') + save_translation_cache('md')
            cache_rules=cache_rules,
            on_translated_item=on_translated_item,
            on_batch_flushed=on_batch_flushed,
            on_progress=on_progress,
        )
        avg_batch_sec = (res.elapsed_sec / res.completed_calls) if res.completed_calls else None
        log_info(f"✨ [MD-LM] 翻譯迴圈結束：processed={int(res.processed or 0)} status={res.status}")
        log_info("總花費時間：%s", get_formatted_duration(start_time))

    else:
        progress(session, 1.0)
        log_info("ℹ️ [MD-LM] 全命中快取或已中文，無需調用 AI。")
        log_info("總花費時間：%s", get_formatted_duration(start_time))

    # -------------------------
    # 4) 回填到每個檔案並輸出（保持 id/順序/數量，只改 text）
    # -------------------------
    written_files = 0
    missing = 0

    for jp in json_files:
        try:
            data, items = load_pending_doc(jp)
        except Exception as e:
            log_warning(f"[MD-LM] 略過讀取失敗：{jp} ({e})")
            continue

        out_items: List[Dict[str, Any]] = []
        for it in items:
            new_text = it.text
            if it.text.strip() and (not is_already_zh(it.text)):
                h = it.content_hash.strip() or f"NOHASH::{it.id}"
                dst = hash_to_dst.get(h)
                if isinstance(dst, str) and dst.strip():
                    new_text = dst
                else:
                    missing += 1

            out_items.append({
                "id": it.id,
                "text": new_text,
                "content_hash": it.content_hash,
                "start_line": it.start_line,
                "end_line": it.end_line,
            })

        out_payload = dict(data)
        out_payload["items"] = out_items
        out_payload.setdefault("stats", {})
        out_payload["stats"].update({
            "blocks": len(out_items),
            "total_blocks_global": total_blocks,
            "unique_blocks_global": unique_blocks,
            "duplicate_blocks_global": dup_blocks,
            "cache_hit_global": len(cached_items),
            "cache_miss_global": len(items_to_translate),
            "already_zh_skipped_global": already_zh_skipped,
        })

        out_path = compute_out_json_path(jp, in_pending_root, out_root)
        write_json(out_path, out_payload)
        written_files += 1

    # recorder 輸出（可選）
    try:
        rec.export_json(out_root / "LM翻譯後" / "translation_map_md.json")
        rec.export_csv(out_root / "LM翻譯後" / "translation_map_md.csv")
    except Exception:
        pass

    if missing:
        log_warning(f"⚠️ [MD-LM] 有 {missing} 個 item 沒拿到翻譯結果（已保留原文）。")

    log_info(
        f"\n✅ [MD-LM] 完成：輸出檔案 {written_files} 個"
        f"\n📊 blocks：總 {total_blocks} | 唯一 {unique_blocks} | 重複 {dup_blocks}"
        f"\n🧠 cache：hit {len(cached_items)} | miss {len(items_to_translate)} | 已中文跳過 {already_zh_skipped}"
        f"\n📁 out：{(out_root / 'LM翻譯後').as_posix()}"
    )

    return {
        "written_files": written_files,
        "total_blocks": total_blocks,
        "unique_blocks": unique_blocks,
        "duplicate_blocks": dup_blocks,
        "cache_hit": len(cached_items),
        "cache_miss": len(items_to_translate),
        "already_zh_skipped": already_zh_skipped,
        "missing_hash": missing,
        "avg_batch_sec": avg_batch_sec,
        "out_dir": str(out_root),
    }


def main():
    """`main`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`log_info`, `strip`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - None
    """
    log_info("=== MD Pending Blocks -> LM 翻譯（md cache 全接 + content_hash 去重）===")

    log_info("請輸入待翻譯根目錄（pending）")
    pending_dir = input("> ").strip().strip('"').strip("'")

    log_info("請輸入輸出根目錄（LM翻譯後）")
    out_dir = input("> ").strip().strip('"').strip("'")

    log_info("dry-run? (y/N)")
    dry = input("> ").strip().lower() == "y"

    log_info("寫入快取分片? (Y/n)")
    write_cache = (input("> ").strip().lower() != "n")

    res = translate_md_pending(
        pending_dir=pending_dir,
        out_dir=out_dir,
        dry_run=dry,
        write_new_cache=write_cache,
        session=None,
    )

    log_info("=== 結果 ===")
    for k, v in res.items():
        log_info("%s: %s", k, v)



if __name__ == "__main__":
    main()
