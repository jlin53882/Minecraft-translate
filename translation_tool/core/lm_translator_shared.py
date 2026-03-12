"""translation_tool/core/lm_translator_shared.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# translation_tool/core/lm_translator_shared.py
# ------------------------------------------------------------
# 共享的語言模型 (LM) 翻譯迴圈邏輯 (含 ETA 計算版本)
# ------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Any , Set
from pathlib import Path
import csv
import json

from translation_tool.utils.log_unit import log_info,log_debug,log_error,log_warning

from translation_tool.utils.cache_manager import get_cache_dict_ref, get_cache_entry  # 新增
from translation_tool.core.lm_config_rules import value_fully_translated
import time


from translation_tool.utils.cache_manager import (
    get_from_cache,
    add_to_cache,
    save_translation_cache,
    reload_translation_cache,
)
from translation_tool.utils.config_manager import load_config


# ---------------------------------------------------------------------
# 資料型態定義 (Types)
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class CacheRule:
    """
    定義快取鍵值的生成規則。
    key_mode:
      - "path"            : 僅使用 item["path"] 作為快取鍵
      - "path|source_text": 使用路徑加原文作為鍵（最安全）
    """
    key_mode: str = "path|source_text"

    def make_key(self, item: Dict[str, Any]) -> str:
        """`make_key`
        
        用途：
        - 建立此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`str`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        path = str(item.get("path") or "")
        src = str(item.get("source_text") or "")
        if self.key_mode == "path":
            return path
        return f"{path}|{src}"


# ---------------------------------------------------------------------
# 快取拆分邏輯 (Cache split)
# ---------------------------------------------------------------------

def get_default_cache_rules() -> Dict[str, CacheRule]:
    # 每次回傳新 dict，避免外部修改污染全域
    """`get_default_cache_rules`
    
    用途：
    - 取得此函式的主要流程（細節以程式碼為準）。
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    return {
        "lang": CacheRule("path"),
        "patchouli": CacheRule("path|source_text"),
        "ftbquests": CacheRule("path|source_text"),
        "kubejs": CacheRule("path|source_text"),
        "md": CacheRule("path|source_text"),
    }



# cache 命中判定（需比對 src）
STRICT_SRC_TYPES = {"lang","kubejs","ftbquests","md"}   # 之後要加很容易，例如 {"lang", "md"}
def _is_valid_hit(dst: str, entry: dict, item: dict) -> bool:
    # 1️⃣ dst 本身必須是有效翻譯
    """`_is_valid_hit`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    if not value_fully_translated(dst):
        return False

    # 2️⃣ cache 類型（預設 lang）
    ctype = item.get("cache_type") or "lang"

    # 3️⃣ 只有「指定類型」才做嚴格 src 檢查
    if ctype in STRICT_SRC_TYPES:
        item_src = (
            item.get("source_text")
            or item.get("source")
            or item.get("src_text")
            or ""
        )
        entry_src = entry.get("src") or ""

        # 沒有原文 → 不信任 cache
        if not item_src:
            return False

        # src 必須完全一致
        return entry_src == item_src

    # 4️⃣ 其他類型 → 一律信任（key 已含 src）
    return True


ValidHitFn = Callable[[str, Dict[str, Any], Dict[str, Any]], bool]

def fast_split_items_by_cache(
    all_items: Iterable[Dict[str, Any]],
    *,
    cache_rules: Optional[Dict[str, CacheRule]] = None,
    is_valid_hit: Optional[ValidHitFn] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    高速版：直接讀底層 cache dict 分流（避免逐筆 get_from_cache 的函式成本）
    回傳 (cached_items, items_to_translate)
    """
    if cache_rules is None:
        cache_rules = get_default_cache_rules()

    cached_items: List[Dict[str, Any]] = []
    items_to_translate: List[Dict[str, Any]] = []

    checker = is_valid_hit or _is_valid_hit

    # cache_type -> dict reference（一次取好）
    cache_refs: Dict[str, Dict[str, Any]] = {}

    for it in all_items:
        if not isinstance(it, dict):
            continue

        ctype = str(it.get("cache_type") or "lang")
        rule = cache_rules.get(ctype) or CacheRule("path|source_text")
        key = rule.make_key(it)

        if ctype not in cache_refs:
            cache_refs[ctype] = get_cache_dict_ref(ctype)

        entry = cache_refs[ctype].get(key)
        if isinstance(entry, dict):
            dst = entry.get("dst")
            if isinstance(dst, str) and checker(dst, entry, it):
                new_it = dict(it)
                new_it["text"] = dst
                cached_items.append(new_it)
                continue

        items_to_translate.append(dict(it))

    return cached_items, items_to_translate


# ---------------------------------------------------------------------
# 觸發集 (Touch Set)

@dataclass
class TouchSet:
    """TouchSet 類別。

    用途：封裝與 TouchSet 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    touched: Set[str] = field(default_factory=set)

    def touch(self, file_id: str) -> None:
        """`touch`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if file_id:
            self.touched.add(str(file_id))

    def flush(self, writer_fn: Callable[[str], Any]) -> None:
        """
        writer_fn(file_id) 由各模組提供：寫 JSON / 寫回 SNBT / 覆寫 JS 原檔都可以
        """
        for fid in list(self.touched):
            writer_fn(fid)
        self.touched.clear()



def write_dry_run_preview(
    out_dir: str | Path,
    items: List[Dict[str, Any]],
    *,
    filename: str = "_dry_run_preview.json",
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """`write_dry_run_preview`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`Path`, `mkdir`, `write_text`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / filename

    payload = {
        "meta": meta or {},
        "count": len(items),
        "items": items,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

def write_cache_hit_preview(
    out_dir: str | Path,
    cached_items: List[Dict[str, Any]],
    *,
    filename: str = "_dry_run_cache_hit_preview.json",
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """`write_cache_hit_preview`
    
    用途：
    - 處理此函式的主要流程（細節以程式碼為準）。
    - 主要包裝/呼叫：`Path`, `mkdir`, `write_text`
    
    參數：
    - 依函式簽名。
    
    回傳：
    - 依實作回傳值（請見函式內 return path）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / filename

    # 只留你最需要看的欄位，避免爆大
    rows = []
    for it in cached_items:
        rows.append({
            "file": it.get("file"),
            "path": it.get("path"),
            "source_text": it.get("source_text"),
            "text": it.get("text"),           # cache 命中後會是 dst
            "cache_type": it.get("cache_type"),
        })

    payload = {
        "meta": meta or {},
        "count": len(rows),
        "items": rows,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p



# ---------------------------------------------------------------------
#翻譯記錄表，JSON/CSV雙格式輸出

@dataclass
class TranslationRecorder:
    """TranslationRecorder 類別。

    用途：封裝與 TranslationRecorder 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        cache_type: str,
        file_id: Optional[str],
        path: str,
        src: str,
        dst: str,
        cache_hit: bool,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """`record`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`append`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        self.rows.append({
            "cache_type": cache_type,
            "file_id": file_id or "",
            "path": path,
            "src": src,
            "dst": dst,
            "cache_hit": bool(cache_hit),
            **(extra or {}),
        })

    def export_json(self, out_path: str | Path) -> Path:
        """`export_json`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`Path`, `mkdir`, `write_text`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self.rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path

    def export_csv(self, out_path: str | Path) -> Path:
        """`export_csv`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`Path`, `mkdir`, `sorted`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cols = ["cache_type", "file_id", "path", "src", "dst", "cache_hit"]
        # 收集額外欄位（有就加）
        extra_cols = sorted({k for r in self.rows for k in r.keys()} - set(cols))
        cols = cols + extra_cols

        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in self.rows:
                w.writerow({k: r.get(k, "") for k in cols})
        return out_path



# ---------------------------------------------------------------------
# 核心翻譯迴圈 (Core translate loop)
# ---------------------------------------------------------------------

@dataclass
class TranslateLoopResult:
    """翻譯任務結束後的統計資料"""
    status: str
    processed: int
    total: int
    completed_calls: int
    elapsed_sec: float
    exhausted: bool
    last_error: Optional[str] = None


def _get_default_batch_size(cache_type: str, batch_size_by_type: Optional[Dict[str, int]]) -> int:
    """
    從設定檔讀取批次大小
    修正：同步辨識 ftbquests 與 kubejs 的專屬批次設定
    """
    if batch_size_by_type and cache_type in batch_size_by_type:
        return int(batch_size_by_type[cache_type])

    cfg = load_config()
    lm_cfg = (cfg or {}).get("lm_translator", {}) if isinstance(cfg, dict) else {}

    # --- 同步 main.py 的邏輯 ---
    if cache_type == "ftbquests":
        # 注意：這裡的 key 名稱必須與你 config.yaml 或 main.py 裡印出來的一致
        return int(lm_cfg.get("initial_batch_size_ftb", 100) or 100)
    
    if cache_type == "kubejs":
        return int(lm_cfg.get("initial_batch_size_kubejs", 200) or 200)

    if cache_type == "patchouli":
        return int(lm_cfg.get("iniital_batch_size_patchouli", 100) or 100)
    
    if cache_type == "md":
        return int(lm_cfg.get("iniital_batch_size_md", 100) or 100)

    # 預設 (Lang)
    return int(lm_cfg.get("iniital_batch_size_lang", 300) or 300)




def translate_items_with_cache_loop(
    items_to_translate: List[Dict[str, Any]],
    *,
    total_for_smart: Optional[int] = None,
    translate_batch_smart: Callable[[List[Dict[str, Any]], Optional[int]], Tuple[Optional[List[Dict[str, Any]]], str]],
    # ---- 行為控制 ----
    batch_size_by_type: Optional[Dict[str, int]] = None,
    write_new_cache: bool = True,
    # ---- 鉤子函式 (on_progress 現在多傳一個 eta_sec) ----
    on_translated_item: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_batch_flushed: Optional[Callable[[], None]] = None,
    on_progress: Optional[Callable[[float, str, float], None]] = None, # (進度%, 訊息, 剩餘秒數)
    # ---- 快取規則 ----
    cache_rules: Optional[Dict[str, CacheRule]] = None,
    # ---- 安全 ----
    sleep_seconds_between_batches: float = 0.0,
) -> TranslateLoopResult:
    """
    帶有快取機制與 ETA 計算的翻譯主迴圈
    """
    if cache_rules is None:
        cache_rules = get_default_cache_rules()

    # 每次執行都重新讀取快取分片，確保手動修改的快取可立即生效
    reload_translation_cache()
    log_info("[Translator Gen]: 重新載入快取完成")
    start_time = time.time()
    
    # 確保 total 至少為 1 避免除以零
    total = int(total_for_smart) if isinstance(total_for_smart, int) and total_for_smart > 0 else len(items_to_translate)
    remaining: List[Dict[str, Any]] = list(items_to_translate)

    processed = 0
    completed_calls = 0
    last_error: Optional[str] = None
    exhausted = False

    def emit_progress(msg: str):
        """計算 ETA 並回報進度"""
        if on_progress is not None:
            try:
                # 1. 計算進度百分比
                p = min(processed / max(total, 1), 1.0)
                
                # 2. 計算 ETA (預計剩餘秒數)
                elapsed = time.time() - start_time
                if processed > 0 and elapsed > 0:
                    speed = processed / elapsed  # 每秒翻譯幾條
                    remaining_count = total - processed
                    eta_sec = remaining_count / speed
                else:
                    eta_sec = 0.0  # 還沒開始或還沒成功翻譯前，ETA 為 0
                
                on_progress(p, msg, eta_sec)
            except Exception:
                pass

    emit_progress("🚀 [SharedLM] 準備開始翻譯工作...")

    while remaining:
        # 取得當前批次資訊
        cache_type = str(remaining[0].get("cache_type") or "lang")
        batch_size = _get_default_batch_size(cache_type, batch_size_by_type)
        if batch_size <= 0:
            batch_size = 50

        batch = remaining[:batch_size]

        # 呼叫 API 翻譯
        try:
            translated, status = translate_batch_smart(batch, total_for_smart)
        except Exception as e:
            last_error = str(e)
            emit_progress(f"❌ [SharedLM] 翻譯發生異常: {e}")
            return TranslateLoopResult(
                status="FAILED",
                processed=processed,
                total=total,
                completed_calls=completed_calls,
                elapsed_sec=time.time() - start_time,
                exhausted=False,
                last_error=last_error,
            )

        completed_calls += 1
        safe_translated = translated or []
        actual_processed_in_this_batch = 0

        # 處理翻譯結果
        for it in safe_translated:
            if not isinstance(it, dict):
                continue
            
            pth = it.get("path")
            txt = it.get("text")
            src = it.get("source_text")
            ctype = str(it.get("cache_type") or cache_type)

            if not (isinstance(pth, str) and isinstance(txt, str) and isinstance(src, str)):
                continue

            actual_processed_in_this_batch += 1
            processed += 1

            # 觸發單項完成回調
            if on_translated_item is not None:
                try:
                    on_translated_item(it)
                except Exception:
                    pass

            # 寫入快取記憶體
            rule = cache_rules.get(ctype) or CacheRule("path|source_text")
            cache_key = rule.make_key({"path": pth, "source_text": src})
            try:
                add_to_cache(ctype, cache_key, src, txt)
            except Exception:
                pass

        # ✅ 根據實際產出更新剩餘清單
        remaining = remaining[actual_processed_in_this_batch:]

        # 批次存檔
        try:
            save_translation_cache(cache_type, write_new_shard=write_new_cache)
        except Exception:
            pass

        if on_batch_flushed is not None:
            try:
                on_batch_flushed()
            except Exception:
                pass

        # 回報進度 (包含新的 ETA 計算)
        msg = (
            f"✅ 批次完成 ({cache_type}) | "
            f"成功: {actual_processed_in_this_batch} | 總進度: {processed}/{total}"
        )
        emit_progress(msg)

        # 狀態分析
        st = (status or "").upper()
        if st == "ALL_KEYS_EXHAUSTED":
            exhausted = True
            emit_progress("⚠️ [SharedLM] API 額度用盡，停止工作。")
            break

        if st in ("FAILED", "FATAL", "ERROR"):
            last_error = f"API 回傳失敗狀態: {status}"
            emit_progress(f"❌ [SharedLM] 終止: {last_error}")
            return TranslateLoopResult(
                status="FAILED",
                processed=processed,
                total=total,
                completed_calls=completed_calls,
                elapsed_sec=time.time() - start_time,
                exhausted=False,
                last_error=last_error,
            )

        # 安全延遲
        if sleep_seconds_between_batches > 0:
            time.sleep(sleep_seconds_between_batches)

        # 防無窮迴圈
        if actual_processed_in_this_batch == 0:
            last_error = f"此批次未能翻譯任何內容 (Status: {status})"
            emit_progress(f"❌ [SharedLM] {last_error}")
            return TranslateLoopResult(
                status="FAILED",
                processed=processed,
                total=total,
                completed_calls=completed_calls,
                elapsed_sec=time.time() - start_time,
                exhausted=False,
                last_error=last_error,
            )

    # 結束工作
    final_status = "ALL_KEYS_EXHAUSTED" if exhausted else "DONE"
    emit_progress(f"🏁 任務結束 | 狀態: {final_status}")

    return TranslateLoopResult(
        status=final_status,
        processed=processed,
        total=total,
        completed_calls=completed_calls,
        elapsed_sec=time.time() - start_time,
        exhausted=exhausted,
        last_error=last_error,
    )
