"""translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# ftbquests_lmtranslator.py
# ------------------------------------------------------------
# FTB Quests (Already Extracted JSON) -> translate_batch_smart -> Export translated JSON maps
# - DOES NOT run extractor
# - Reads extracted .json files containing {key: text} pairs
# - Outputs translated {key: text} JSON with same relative path structure
# - If input file is like ru_ru.json (or other lang codes), output renamed to zh_tw.json
# - total passed to smart = global total keys across all files
# ------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import math


from translation_tool.core.lm_translator_main import translate_batch_smart

from translation_tool.core.lm_config_rules import validate_api_keys, value_fully_translated

from translation_tool.utils.config_manager import load_config

from translation_tool.core.lm_translator_shared import (
    fast_split_items_by_cache,            # ✅ 新增：高速分流
    translate_items_with_cache_loop,
    CacheRule,
    TouchSet,                             # ✅ 新增：touched/flush
    TranslationRecorder,                  # ✅ 新增：翻譯記錄
    write_dry_run_preview,                # ✅ 新增：dry-run preview 檔
    write_cache_hit_preview,              # ✅ 新增：cache hit preview 檔
    _is_valid_hit,                        # ✅ 新增：cache hit 判斷
    _get_default_batch_size,
)

from translation_tool.plugins.shared.json_io import (
    read_json_dict,
    write_json_dict,
    collect_json_files,
)
from translation_tool.plugins.shared.lang_path_rules import (
    should_rename_to_zh_tw,
    is_lang_code_segment,
    replace_lang_folder_with_zh_tw,
    compute_output_path,
)
from translation_tool.plugins.shared.lang_text_rules import _strip_fmt, is_already_zh

from translation_tool.utils.log_unit import( 
    log_info, 
    log_error, 
    log_warning, 
    log_debug, 
    )

# -------------------------
# Smart 翻譯轉接器（資料格式轉換）
# -------------------------

def map_to_items(
    mapping: Dict[str, Any],
    cache_type: str,
    file_hint: str,
) -> List[Dict[str, Any]]:
    """
    將 {key: text} 的原始資料，轉換成 translate_batch_smart 可處理的 item 格式。

    每一個 item 代表「一條可翻譯文字」，會被送進：
    - cache 比對
    - batch 翻譯
    - 翻譯結果回寫

    【重要設計前提】：
    - smart 翻譯器會透過 item["file"] 判斷資料類型
    - FTB Quests 的判斷條件是：路徑中必須包含 "/ftbquests/"
    - 因此 file_hint「一定要」包含 "/ftbquests/"
    - 同時不可包含 "/lang/"，否則會被誤判為 Minecraft Lang 翻譯

    這也是為什麼 file_hint 不是實際檔案路徑，而是「刻意構造的提示路徑」。
    """
    items: List[Dict[str, Any]] = []

    for k, v in mapping.items():
        # key 必須是字串（語言 key）
        if not isinstance(k, str):
            continue

        # value 必須是非空白字串（實際要翻譯的內容）
        if not isinstance(v, str) or not v.strip():
            continue

        items.append(
            {
                # 提供 smart translator 判斷用的檔案提示路徑
                # ⚠️ 必須包含 "/ftbquests/"
                "file": file_hint,

                # 語言 key（例如 quest.xxx.title）
                "path": k,

                # 原始文字（快取與比對用）
                "source_text": v,

                # 當前文字（會被翻譯器覆寫）
                "text": v,

                # 指定快取分類（對應 cache_rules）
                "cache_type": cache_type,  # 例如 "ftbquests"
            }
        )

    return items


def count_translatable_keys(mapping: Dict[str, Any]) -> int:
    """
    計算 mapping 中「實際可翻譯的字串數量」。

    判斷條件：
    - value 必須是字串
    - 去除空白後仍有內容

    這個數量會用來：
    - 顯示進度
    - 計算 cache hit / miss
    - 作為 batch 翻譯的總量基準
    """
    return sum(
        1
        for _, v in mapping.items()
        if isinstance(v, str) and v.strip()
    )


@dataclass
class DryRunStats:
    """
    Dry-run（試跑）模式下的統計資料結構。

    用途：
    - UI 顯示
    - 預覽翻譯規模
    - 確認 cache 命中比例是否合理
    """
    files: int = 0               # 處理的檔案數
    total_keys: int = 0          # 總字串數
    cache_hit: int = 0           # 快取命中數
    cache_miss: int = 0          # 實際需翻譯數
    per_file: list[dict] = None  # 每個檔案的明細




# -------------------------
# Public API (callable from pipeline)
# -------------------------
def translate_ftb_pending_to_zh_tw(
    *,
    input_lang_dir: str | Path,
    output_lang_dir: str | Path,
    session=None,
    rename_langs: Optional[set[str]] = None,
    dry_run: bool = False,   # ✅ 新增
    write_new_cache: bool = True,   # ✅ 新增
) -> dict:
    """
    專給 UI/服務呼叫：
    - input_lang_dir: 例如 <output_dir>/待翻譯/config/ftbquests/quests/lang
                      （注意：要傳到 lang 這層，讓相對路徑含 en_us/ 才能替換成 zh_tw）
    - output_lang_dir: 例如 <output_dir>/config/ftbquests/quests/lang
                       （輸出會自動把 en_us 資料夾替換成 zh_tw）
    - session: 可選，用來 add_log / set_progress
    """
    validate_api_keys()

    in_dir = Path(input_lang_dir).resolve()
    out_dir = Path(output_lang_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    def set_prog(v: float):
        """`set_prog`
        
        用途：
        - 設定此函式的主要流程（細節以程式碼為準）。
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if session is not None and hasattr(session, "set_progress"):
            try:
                session.set_progress(v)
            except Exception:
                pass

    # rename langs 預設沿用你 CLI 的清單（但 pending 通常只有 en_us，不太會用到）
    if rename_langs is None:
        rename_langs = {
            "ru_ru","ja_jp","ko_kr","zh_cn","zh_hk","zh_sg","pt_br","es_es","en_us","fr_fr","de_de",
            "it_it","pl_pl","tr_tr","uk_ua","cs_cz","hu_hu","nl_nl","sv_se","no_no","da_dk","fi_fi"
        }

    if not in_dir.exists() or not in_dir.is_dir():
        raise FileNotFoundError(f"input_lang_dir 不存在或不是資料夾：{in_dir}")

    json_files = collect_json_files(in_dir)
    if not json_files:
        raise FileNotFoundError(f"找不到任何 .json：{in_dir}")
    

    # ---- Global total keys (raw) ----
    per_file_counts: List[Tuple[Path, int]] = []
    global_total_keys = 0

    def _count_one(src: Path) -> Tuple[Path, int]:
        """`_count_one`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`read_json_dict`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        try:
            mapping = read_json_dict(src)
            c = count_translatable_keys(mapping)
            return src, int(c)
        except Exception:
            return src, 0

    # max_workers 你可以改成 config 的 parallel_execution_workers

    max_workers =load_config().get("translator", {}).get("parallel_execution_workers", 4)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_count_one, src) for src in json_files]
        for fu in as_completed(futs):
            src, c = fu.result()
            per_file_counts.append((src, c))
            global_total_keys += c

    # 保持穩定順序（避免多執行緒導致排序亂）
    per_file_counts.sort(key=lambda x: x[0].as_posix())


    if global_total_keys == 0:
        log_info("ℹ️ [FTB-LM] 0 keys，跳過翻譯")
        return {"written_files": 0, "total_keys": 0, "out_dir": str(out_dir)}

    # ---- Cache rules ----
    cache_rules = {"ftbquests": CacheRule("path|source_text")}

    # ✅ 預先計算「實際要翻譯」總量（全局 miss）
    global_total_to_translate = 0
    global_total_hit = 0

    for src, key_count in per_file_counts:
        if key_count == 0:
            continue
        try:
            mapping = read_json_dict(src)
            rel_src = src.relative_to(in_dir).as_posix()
            file_hint = f"config/ftbquests/quests/{rel_src}"
            all_items = map_to_items(mapping, cache_type="ftbquests", file_hint=file_hint)

            cached_items, items_to_translate = fast_split_items_by_cache(
                all_items,
                cache_rules=cache_rules,
                is_valid_hit=_is_valid_hit,
            )

            # ✅ 中文/已翻譯：不送 LM，也不算 cache_miss
            already_zh_items = []
            real_to_translate = []
            for it in items_to_translate:
                s = str(it.get("source_text") or it.get("text") or "")
                if is_already_zh(s):
                    already_zh_items.append(it)
                else:
                    real_to_translate.append(it)

            global_total_hit += len(cached_items)
            global_total_to_translate += len(real_to_translate)


            
        except Exception:
            pass

    log_info(
            f"\n🔎 [FTB-LM][掃描完畢] 發現待處理檔案：{len(json_files)} 個 | 文本總條目：{global_total_keys} 條"
            f"\n✅ [FTB-LM][進度分析] 已從快取載入：{global_total_hit} 條 | 剩餘需 AI 翻譯：{global_total_to_translate} 條"
        )

    if global_total_to_translate == 0:
        log_info("ℹ️ [FTB-LM][狀態] 恭喜！所有內容皆命中快取，無需調用 AI，將直接導出檔案。")

    if global_total_to_translate == 0:
        set_prog(1.0)

    # ---- Translate per file (shared loop + cache) ----
    translated_done = 0          # ✅ 只算 API 翻譯完成（主進度分子）
    cache_hit_done_so_far = 0    # ✅ 已寫入輸出的 cache hit（整體完成用）
    total_written = 0


    # ---- Dry-run stats container ----
    per_file_rows: list[dict] = []
    # ---- Dry-run: 不翻譯、不輸出 ----
    dry_preview_items: List[Dict[str, Any]] = []

    all_cached_items: list[dict] = []

    rec = TranslationRecorder()
    touch = TouchSet()

    # touched_files 對應的 writer：最小改動版（每檔只會有一個 dst/out_map）
    _file_write_table: dict[str, tuple[Path, Dict[str, str]]] = {}

    def _writer(file_id: str) -> None:
        """`_writer`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`write_json_dict`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        dst_path, data = _file_write_table[file_id]
        write_json_dict(dst_path, data)


    for idx, (src, key_count) in enumerate(per_file_counts, start=1):
        if key_count == 0:
            continue

        mapping = read_json_dict(src)

        rel_src = src.relative_to(in_dir).as_posix()  # e.g. en_us/ftb_lang.json
        file_hint = f"config/ftbquests/quests/{rel_src}"  # ✅ include /ftbquests/ and no /lang/
        all_items = map_to_items(mapping, cache_type="ftbquests", file_hint=file_hint)

        cached_items, items_to_translate = fast_split_items_by_cache(
            all_items,
            cache_rules=cache_rules,
            is_valid_hit=_is_valid_hit,
        )

        already_zh_items = []
        real_to_translate = []
        for it in items_to_translate:
            s = str(it.get("source_text") or it.get("text") or "")
            if is_already_zh(s):
                already_zh_items.append(it)
            else:
                real_to_translate.append(it)

        items_to_translate = real_to_translate  # ✅ 覆蓋成真的要翻譯的
        hit = len(cached_items)
        miss = len(items_to_translate)
        already_zh = len(already_zh_items)



        if hit > 0:
            sample = cached_items[0]
            log_info(f"🧠 [FTB-LM] 快取命中範例：{sample.get('path')}")
        
        if miss > 0:
            sample = items_to_translate[0]
            log_info(f"✏️ [FTB-LM] 待翻譯範例：{sample.get('path')}")
        



        dst = compute_output_path(src, in_dir, out_dir, rename_langs)
        log_info(
            f"📄 [FTB-LM][檔案 {idx}/{len(per_file_counts)}] 正在處理：{rel_src}\n"
            f"📊 本檔明細：總條目 {key_count} | 載入快取 {hit} | 已含中文(跳過) {already_zh} | 需翻譯 {miss}\n"
            f"💾 輸出路徑：{dst.relative_to(out_dir).as_posix()}"
        )


        per_file_rows.append(
            {
                "file": rel_src,
                "keys": key_count,
                "cache_hit": hit,
                "cache_miss": miss,
                "dst": dst.relative_to(out_dir).as_posix(),
            }
        )

        # ---- Dry-run: 不翻譯、不輸出 ----
        if dry_run:
            # ✅ 收集 preview 樣本（避免爆大：最多 2000）
            if len(dry_preview_items) < 2000:
                dry_preview_items.extend(items_to_translate[: 2000 - len(dry_preview_items)])

                # ✅ NEW：收集 cache hit（避免爆大：最多 2000，可自行調整）
            if len(all_cached_items) < 2000:
                all_cached_items.extend(cached_items[: 2000 - len(all_cached_items)])

            translated_done += miss
            set_prog(min(translated_done / max(global_total_to_translate, 1), 1.0))

            log_info(
                f"🧪 [測試模式] 進度：{idx}/{len(per_file_counts)}\n"
                f"擬定輸出：{rel_src} ➔ {dst.relative_to(out_dir).as_posix()}\n"
                f"預估明細：總條目 {key_count} | 快取命中 {hit} | 需翻譯 {miss} | 預估批次 {math.ceil(miss / _get_default_batch_size('ftbquests', None)) if _get_default_batch_size('ftbquests', None) > 0 else 'N/A'}\n"
                f"累計預估進度：{translated_done} / {global_total_to_translate}"
            )
            continue
        

        # ============================
        # ✅ 真正翻譯（非 dry-run）
        # ============================

        # out_map：先用原文當底（避免中斷時輸出缺 key）
        out_map: Dict[str, str] = {
            k: v for k, v in mapping.items() if isinstance(k, str) and isinstance(v, str)
        }

        # cache hits 覆蓋
        for it in cached_items:
            p = it.get("path")
            t = it.get("text")
            if isinstance(p, str) and isinstance(t, str):
                out_map[p] = t
                try:
                    rec.record(
                        cache_type="ftbquests",
                        file_id=rel_src,
                        path=p,
                        src=str(it.get("source_text") or ""),
                        dst=t,
                        cache_hit=True,
                        extra={"dst_file": dst.relative_to(out_dir).as_posix()},
                    )
                except Exception:
                    pass
                

        # 全命中 cache：直接輸出
        if not items_to_translate:
            #write_json_dict(dst, out_map)
            file_id = dst.as_posix()
            _file_write_table[file_id] = (dst, out_map)

            touch.touch(file_id)
            touch.flush(_writer)   # 最小改動：等同你現在每次都寫，但走同一個管線
            total_written += 1
        
            # ✅ 這個檔案的 cache hit 已經真正寫進輸出，整體完成要加
            cache_hit_done_so_far += hit
        
            # ✅ 主進度不變（translated_done 不加）
            set_prog(min(translated_done / max(global_total_to_translate, 1), 1.0))
        
            overall_done = cache_hit_done_so_far + translated_done
            progress_pct = (overall_done / global_total_keys * 100) if global_total_keys > 0 else 0

            log_info(
                f"⚡ [快取跳過] 檔案 {idx}/{len(per_file_counts)}：{rel_src}\n"
                f"💾 導出路徑：{dst.relative_to(out_dir).as_posix()}\n"
                f"🧠 處理結果：命中快取 {hit} 條 | API 調用 0 條\n"
                f"🎯 全域完成度：{overall_done} / {global_total_keys} ({progress_pct:.1f}%)"
            )
            continue
        

        # shared while-loop（includes add_to_cache + save_translation_cache + safe slicing）
        def on_translated_item(it: Dict[str, Any]) -> None:
            """`on_translated_item`
            
            用途：
            - 處理此函式的主要流程（細節以程式碼為準）。
            - 主要包裝/呼叫：`get`
            
            參數：
            - 依函式簽名。
            
            回傳：
            - None
            """
            p = it.get("path")
            t = it.get("text")
            if isinstance(p, str) and isinstance(t, str):
                out_map[p] = t
                try:
                    rec.record(
                        cache_type="ftbquests",
                        file_id=rel_src,
                        path=p,
                        src=str(it.get("source_text") or ""),
                        dst=t,
                        cache_hit=False,
                        extra={"dst_file": dst.relative_to(out_dir).as_posix()},
                    )
                except Exception:
                    pass

            
        # ✅ 確保此檔案在翻譯路徑也有 file_id
        file_id = dst.as_posix()
        _file_write_table[file_id] = (dst, out_map)

        # 在這之前先確保 file_id/_file_write_table 設定好了（下面會說加在哪）
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
                touch.touch(file_id)
                touch.flush(_writer)     # 最小改動：每批也照樣寫，避免中斷損失
            except Exception:
                # fallback
                write_json_dict(dst, out_map)


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
            if m > 0:
                return f"{m}m{s:02d}s"
            return f"{s}s"


        def on_progress(p: float, msg: str, eta_sec: float) -> None:
            """`on_progress`
            
            用途：
            - 處理此函式的主要流程（細節以程式碼為準）。
            - 主要包裝/呼叫：`_fmt_eta`, `set_prog`
            
            參數：
            - 依函式簽名。
            
            回傳：
            - None
            """
            eta_txt = _fmt_eta(eta_sec)
            if eta_txt:
                log_info(f"⏳ [AI 翻譯中] {msg} | 預估剩餘時間：{eta_txt}")
            else:
                log_info(f"🚀 [AI 翻譯中] {msg}")
            set_prog(p)

        res = translate_items_with_cache_loop(
            items_to_translate,
            total_for_smart=global_total_to_translate,
            translate_batch_smart=lambda batch, total: translate_batch_smart(batch, total=total),
            write_new_cache=bool(write_new_cache),   # ✅ 改成吃參數
            cache_rules=cache_rules,
            on_translated_item=on_translated_item,
            on_batch_flushed=on_batch_flushed,
            on_progress=on_progress,
        )

        # final write
        touch.touch(file_id)
        touch.flush(_writer)
        total_written += 1

        # 這個檔案的 cache hit 也已寫入輸出
        cache_hit_done_so_far += hit

        # 只把 API 實際翻譯數量加進主進度
        translated_done += int(res.processed or 0)

        set_prog(min(translated_done / max(global_total_to_translate, 1), 1.0))

        overall_done = cache_hit_done_so_far + translated_done
        progress_pct = (overall_done / global_total_keys * 100) if global_total_keys > 0 else 0
        log_info(
            f"✨ [檔案完成] {rel_src} 已寫入\n"
            f"📈 翻譯數據：本次翻譯 {int(res.processed or 0)} 條 | 檔案狀態：{res.status}\n"
            f"🎯 全域進度：目前已完成 {overall_done} / {global_total_keys} ({progress_pct:.1f}%)"
        )


        if res.status == "ALL_KEYS_EXHAUSTED":
            log_info("⚠️ [FTB-LM] ALL_KEYS_EXHAUSTED：已輸出目前成果，停止。")
            break
    
    # ---- Dry-run 結尾摘要 ----
    if dry_run:
        batch_size = _get_default_batch_size("ftbquests", None)
        est_batches = (
            math.ceil(global_total_to_translate / batch_size)
            if isinstance(global_total_to_translate, int) and batch_size > 0
            else None
        )
        meta = {
            "files": len(per_file_rows),
            "total_keys": global_total_keys,
            "cache_hit": global_total_hit,
            "cache_miss": global_total_to_translate,
            "estimated_batches": est_batches,
        }
    
        try:
            # 原本：待翻譯 preview
            write_dry_run_preview(
                out_dir,
                dry_preview_items,
                meta=meta,
                filename="_ftbquests_dry_run_preview.json",  # 可選：明確檔名
            )
    
            # ✅ NEW：cache hit preview
            write_cache_hit_preview(
                out_dir,
                all_cached_items,
                filename="_ftbquests_dry_run_cache_hit_preview.json",
                meta=meta,
            )
    
        except Exception as e:
            log_error(f"⚠️ [FTB-LM] DRY-RUN preview 輸出失敗：{e}")
    
        return {
            "dry_run": True,
            "files": len(per_file_rows),
            "total_keys": global_total_keys,
            "cache_hit": global_total_hit,
            "cache_miss": global_total_to_translate,
            "estimated_batches": est_batches,
            "out_dir": str(out_dir),
            "per_file": per_file_rows,
        }

    try:
        rec.export_json(out_dir / "translation_map.json")
        rec.export_csv(out_dir / "translation_map.csv")
        log_info(f"✅ [FTB-LM] 已匯出 translation_map.json / .csv 到 {out_dir}")

    except Exception:
        log_error("⚠️ [FTB-LM] 匯出 translation_map 失敗")
        pass

    log_info(f"✅ [任務翻譯完成] 已將 {total_written} 個翻譯檔案輸出至：{out_dir}")
    log_info(f"📊 提示：您可以在該目錄下查看 translation_map.csv 來核對翻譯條目細節。")
    batch_size = _get_default_batch_size("ftbquests", None)
    est_batches = (
        math.ceil(global_total_to_translate / batch_size)
        if isinstance(global_total_to_translate, int) and batch_size > 0
        else None
    )
    return {
        "dry_run": False,
        "written_files": total_written,
        "total_keys": global_total_keys,
        "cache_hit": global_total_hit,
        "cache_miss": global_total_to_translate,
        "estimated_batches": est_batches,
        "out_dir": str(out_dir),
    }

