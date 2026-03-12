"""translation_tool/core/ftb_translator.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import os
import time
import math
import shutil
from typing import Generator, Dict, Any, List
import concurrent.futures
from pathlib import Path
import re

import orjson
from ..utils.config_manager import load_config
from ..utils.text_processor import (
    load_replace_rules,
    load_custom_translations,
    recursive_translate,   # JSON 結構用
    safe_convert_text,     # SNBT/純文字用
    convert_text,          # SNBT/純文字用
    orjson_dump_file,
    orjson_pretty_str,
)
from ..plugins.ftbquests.ftbquests_snbt_extractor import process_quest_folder
from translation_tool.core.lm_translator_shared import _get_default_batch_size

# 導入我們自訂的日誌工具
from translation_tool.utils.log_unit import (
    log_info,  
    log_error, 
    log_warning, 
    log_debug, 
    progress, 
    get_formatted_duration
)

def _translate_single_file(
    file_path: str,
    input_dir: str,
    output_dir: str,
    rules: List[Dict[str, str]],
    custom_translations: Dict[str, str],
) -> str:
    """
    翻譯單一檔案，並寫入輸出目錄。
    回傳處理日誌字串。
    """
    relative_path = os.path.relpath(file_path, input_dir)
    output_path = os.path.join(output_dir, relative_path).replace("zh_cn", "zh_tw")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    log_msg = f"正在翻譯: {relative_path}"
    log_info(log_msg)

    try:
        # ✅ json 用 bytes 讀更省一次 encode
        if file_path.endswith(".json"):
            with open(file_path, "rb") as f:
                data = orjson.loads(f.read())

            # ✅ 外部 text_processor：OpenCC + 規則 + 自訂翻譯
            translated_data = recursive_translate(data, rules, custom_translations)

            with open(output_path, "wb") as f:
                orjson_dump_file(translated_data, f)

        elif file_path.endswith((".snbt", ".snbt.qkdownloading", ".js", ".md")):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # ✅ 外部 text_processor：OpenCC + replace rules（純文字/SNBT）
            translated_content = convert_text(content, rules)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(translated_content)

        return log_msg

    except Exception as e:
        log_error("處理檔案 %s 時發生錯誤: %s", relative_path, e)
        return f"處理 {relative_path} 失敗: {e}"



def translate_directory_generator(input_dir: str) -> Generator[Dict[str, Any], None, None]:
    """
    主翻譯流程，使用生成器回傳進度，並支援多執行緒處理。
    """
    output_dir_name = load_config().get("translator", {}).get("output_dir_name", "FTB任務翻譯輸出")
    #output_dir_name = load_config().get("output_dir_name", "zh_tw_generated")
    output_dir = os.path.join(os.path.dirname(input_dir), output_dir_name)
    os.makedirs(output_dir, exist_ok=True)
    
    log_info(f"FTB 翻譯開始 (多執行緒模式)，來源: {input_dir}")

    # 確保配置載入
    rules = load_replace_rules(load_config().get("translator", {}).get("replace_rules_path", "replace_rules.json"))
    custom_translations = load_custom_translations(load_config().get("translator", {}).get("custom_translator_folder", "custom_translators"))

    
    # 1. 尋找所有要翻譯的檔案
    files_to_translate = []
    # 2. 尋找所有檔案 (用於後續複製)
    all_files_to_copy = []

    for root, _, files in os.walk(input_dir):
        for file in files:
            full_path = os.path.join(root, file)
            all_files_to_copy.append(full_path)
            
            # 篩選出需要翻譯的檔案：
            # 現在對所有 .json, .snbt, 和 .snbt.qkdownloading 文件進行轉換。
            if file.endswith(('.json', '.snbt', '.snbt.qkdownloading',".js",".md")):
                files_to_translate.append(full_path)

    total_files = len(files_to_translate)
    if total_files == 0:
        log_warning(f"在指定目錄中沒有找到任何 .json、.snbt 或 .snbt.qkdownloading 檔案。")
        # 即使沒有翻譯檔案，也需要複製其餘檔案
        pass # 讓流程繼續到複製階段

    start_time = time.time()
    
    if total_files > 0:
        log_info(f"找到 {total_files} 個檔案，開始並行翻譯...")
        yield {"progress": 0.0}
        
        processed_count = 0
        # 預設使用 CPU 核心數，最低為 1
        #max_workers = load_config().get("translator", {}).get("parallel_execution_workers") or os.cpu_count() or 1
        
        max_workers = min(2, os.cpu_count() or 1) # FTB Quests 翻譯預設最多 2 個執行緒

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                #executor.submit(_translate_single_file, fp, input_dir, output_dir, converter, rules, custom_translations): fp
                executor.submit(_translate_single_file, fp, input_dir, output_dir, rules, custom_translations): fp
                for fp in files_to_translate
            }
            
            for future in concurrent.futures.as_completed(future_to_file):
                processed_count += 1
                progress = processed_count / total_files
                log_msg = future.result()
                log_info(f"[{processed_count}/{total_files}] {log_msg}")
                yield {"progress": progress}

    log_info(f"翻譯階段完成，開始複製其餘檔案...")
    yield {"progress": 1.0}
    
    # --- 複製其餘檔案階段 ---
    copied_count = 0
    for src_path in all_files_to_copy:
        # 檢查檔案是否已在翻譯清單中處理過
        is_translated_file = src_path in files_to_translate
        
        if not is_translated_file:
            try:
                rel_path = os.path.relpath(src_path, input_dir)
                # 複製時，仍然檢查路徑中是否有 zh_cn，並替換為 zh_tw
                # 這是為了處理那些非 json/snbt 且在 zh_cn 資料夾內的檔案 (e.g., textures, images)
                dst_path = os.path.join(output_dir, rel_path).replace('zh_cn', 'zh_tw')
                
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                copied_count += 1
            except Exception as e:
                log_error(f"複製檔案 {src_path} 失敗: {e}")

    log_info(f"複製階段完成，總共複製了 {copied_count} 個非翻譯檔案。")
    yield {"progress": 1.0}

    duration = time.time() - start_time
    final_log_msg = f"--- 所有 {total_files} 個檔案翻譯和 {copied_count} 個檔案複製完成，總耗時 {duration:.2f} 秒 ---"
    log_info(final_log_msg)
    yield {"progress": 1.0}

#--------
"""三語合併工具"""
"""處理 FTB Quests 抽取結果，做三語合併補洞"""

def deep_merge_3way(zh_tw: dict, zh_cn: dict, en_us: dict) -> dict:
    """
    優先順序：zh_tw > (zh_cn 轉繁) > en_us
    支援巢狀 dict 遞迴補洞
    """
    def is_empty(v):
        """判斷此函式的工作（細節以程式碼為準）。
        
        回傳：依函式內 return path。
        """
        return v is None or v == "" or v == {} or v == []

    def merge(a, b, c):
        # 三者都是 dict → 遞迴合併 key union
        """處理此函式的工作（細節以程式碼為準）。
        
        回傳：依函式內 return path。
        """
        if isinstance(a, dict) or isinstance(b, dict) or isinstance(c, dict):
            a = a if isinstance(a, dict) else {}
            b = b if isinstance(b, dict) else {}
            c = c if isinstance(c, dict) else {}
            out = {}
            for k in set(a.keys()) | set(b.keys()) | set(c.keys()):
                out[k] = merge(a.get(k), b.get(k), c.get(k))
            return out

        # 到這裡：a/b/c 都不是 dict（可能是 str/int/list/...）
        if not is_empty(a):
            return a

        if not is_empty(b):
            # zh_cn 只對字串做轉繁；其他型別原樣用
            return safe_convert_text(b) if isinstance(b, str) else b

        return c  # en_us 保底（可能是空也沒辦法）

    return merge(zh_tw, zh_cn, en_us)

def prune_en_us_by_zh_tw(en_us: Any, zh_tw: Any) -> Any:
    """
    從 en_us 中刪除「zh_tw 已經有內容」的部分，回傳剩下需要翻譯的 en_us。
    規則：只要 zh_tw 該位置是「有內容」（非 None/""/{}/[]），就視為已翻，en_us 刪掉。
    """
    def is_filled(v: Any) -> bool:
        """判斷此函式的工作（細節以程式碼為準）。
        
        回傳：依函式內 return path。
        """
        return v is not None and v != "" and v != {} and v != []

    # dict：逐 key 扣掉
    if isinstance(en_us, dict) and isinstance(zh_tw, dict):
        out = {}
        for k, v in en_us.items():
            zh_v = zh_tw.get(k)

            # zh_tw 已有內容 → 跳過（不需要翻）
            if is_filled(zh_v):
                continue

            pruned = prune_en_us_by_zh_tw(v, zh_v)
            if is_filled(pruned):
                out[k] = pruned
        return out

    # list：保留原樣（FTB 多半是結構資料；若你確定 list 內也要扣，我再給進階版）
    if isinstance(en_us, list):
        return en_us

    # leaf：原樣回傳
    return en_us

def resolve_ftbquests_quests_root(base_dir: str) -> str:
    """
    給一個起點資料夾，往下遞迴尋找：
      **/config/ftbquests/quests

    回傳找到的 quests_root（字串路徑）。
    找不到就丟 FileNotFoundError。
    """
    base = Path(base_dir).expanduser().resolve()

    # 允許你直接傳到 .../config 也可以
    direct = base / "ftbquests" / "quests"
    if direct.is_dir():
        return str(direct)

    # 遞迴找 **/config/ftbquests/quests
    candidates = list(base.rglob("config/ftbquests/quests"))

    # 有些系統 rglob pattern 對大小寫敏感，保險再補一次（較慢但穩）
    if not candidates:
        for p in base.rglob("*"):
            if p.is_dir():
                parts = [x.lower() for x in p.parts[-3:]]
                # .../ftbquests/quests 但需要上一層是 config
                if parts == ["ftbquests", "quests"] and len(p.parts) >= 3 and p.parts[-3].lower() == "config":
                    candidates.append(p)

    if not candidates:
        raise FileNotFoundError(f"找不到 config\\ftbquests\\quests (base_dir={base})")

    # 多個候選時：優先選路徑最短（離 base 最近），同長度就選字典序最小（穩定）
    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    return str(candidates[0])


#外部呼叫 抓取前置翻譯結果
def export_ftbquests_raw_json(base_dir: str, *, output_dir: str | None = None) -> dict:
    """
    base_dir: 模組包根目錄（用來 resolve quests_root）
    output_dir: UI 指定輸出資料夾（若為 None，預設 base_dir/Output）
    """
    quests_root = resolve_ftbquests_quests_root(base_dir)
    extracted = process_quest_folder(quests_root)

    out_root = output_dir or os.path.join(base_dir, "Output")

    raw_root = os.path.join(out_root,"ftbquests" ,"raw", "config", "ftbquests", "quests", "lang")
    os.makedirs(raw_root, exist_ok=True)

    written_langs = []

    for lang, data in extracted.items():
        # ⚠️ 只寫「真的有內容」的語言
        if not data.get("lang") and not data.get("quests"):
            continue

        lang_dir = os.path.join(raw_root, lang)
        os.makedirs(lang_dir, exist_ok=True)

        with open(os.path.join(lang_dir, "ftb_lang.json"), "wb") as f:
            orjson_dump_file(data.get("lang", {}), f)
        with open(os.path.join(lang_dir, "ftb_quests.json"), "wb") as f:
            orjson_dump_file(data.get("quests", {}), f)

        written_langs.append(lang)

    log_info("FTB raw 輸出語言：%s", written_langs)

    return {
        "raw_root": raw_root,
        "out_root": out_root,
        "quests_root": quests_root,
        "written_langs": written_langs,
    }




#--- 三語合併主流程 ---
_LANG_REF_RE = re.compile(r"^\{ftbquests\..+\}$")

def _is_filled_text(v) -> bool:
    """判斷此函式的工作（細節以程式碼為準）。
    
    - 主要包裝：`strip`
    
    回傳：依函式內 return path。
    """
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    if _LANG_REF_RE.match(s):
        return False
    return True

def prune_flat_en_by_tw(en_map: dict, tw_available: dict) -> dict:
    """
    針對你 extractor 產物（大多是扁平 dict[str,str]）最穩：
    只要 tw_available[key] 有內容，就從 en_map 移除 key。
    """
    out = {}
    for k, v in en_map.items():
        tw_v = tw_available.get(k)
        if _is_filled_text(tw_v):
            continue
        out[k] = v
    return out


# 資料清理主流程
def clean_ftbquests_from_raw(base_dir: str, *, output_dir: str | None = None) -> dict:
    """
    base_dir: 模組包根目錄（用來找 quests_root / raw_root 的相對位置）
    output_dir: UI 指定輸出資料夾（若為 None，預設 base_dir/Output）
    """
    out_root = output_dir or os.path.join(base_dir, "Output")

    raw_root = os.path.join(out_root,"ftbquests", "raw", "config", "ftbquests", "quests", "lang")

    def load_json(lang: str, name: str):
        """載入此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`join`
        
        回傳：依函式內 return path。
        """
        path = os.path.join(raw_root, lang, name)
        if not os.path.isfile(path):
            return {}
        with open(path, "rb") as f:
            return orjson.loads(f.read())

    # en_us 一定是主來源（至少 pending 需要它）
    en_lang = load_json("en_us", "ftb_lang.json")
    en_quests = load_json("en_us", "ftb_quests.json")

    # tw/cn 可能不存在
    cn_lang = load_json("zh_cn", "ftb_lang.json")
    cn_quests = load_json("zh_cn", "ftb_quests.json")
    tw_lang = load_json("zh_tw", "ftb_lang.json")
    tw_quests = load_json("zh_tw", "ftb_quests.json")

    # ✅ 判斷是否真的有 tw/cn 來源
    has_twcn_source = bool(tw_lang or tw_quests or cn_lang or cn_quests)

    # -------------------------
    # 1) pending 永遠要輸出
    # -------------------------
    if has_twcn_source:
        # 有 tw/cn → 用「可用繁中」扣掉不用翻的英文
        available_lang_tw = deep_merge_3way(tw_lang, cn_lang, {})        # 不含 en
        available_quests_tw = deep_merge_3way(tw_quests, cn_quests, {})  # 不含 en

        pending_lang = prune_flat_en_by_tw(en_lang, available_lang_tw)
        pending_quests = prune_flat_en_by_tw(en_quests, available_quests_tw)
    else:
        # 只有 en_us → pending 就是完整 en_us
        pending_lang = en_lang
        pending_quests = en_quests

    pending_en_root = os.path.join(out_root,"ftbquests","待翻譯", "config", "ftbquests", "quests", "lang", "en_us")
    os.makedirs(pending_en_root, exist_ok=True)

    with open(os.path.join(pending_en_root, "ftb_lang.json"), "wb") as f:
        orjson_dump_file(pending_lang, f)
    with open(os.path.join(pending_en_root, "ftb_quests.json"), "wb") as f:
        orjson_dump_file(pending_quests, f)

    # -------------------------
    # 2) zh_tw：只有在有 tw/cn 時才輸出
    #    且不吃 en_us fallback
    # -------------------------
    zh_tw_root = os.path.join(out_root,"ftbquests","整理後", "config", "ftbquests", "quests", "lang", "zh_tw")

    if has_twcn_source:
        final_lang_tw = deep_merge_3way(tw_lang, cn_lang, {})        # ✅ 不含 en
        final_quests_tw = deep_merge_3way(tw_quests, cn_quests, {})  # ✅ 不含 en

        os.makedirs(zh_tw_root, exist_ok=True)
        with open(os.path.join(zh_tw_root, "ftb_lang.json"), "wb") as f:
            orjson_dump_file(final_lang_tw, f)
        with open(os.path.join(zh_tw_root, "ftb_quests.json"), "wb") as f:
            orjson_dump_file(final_quests_tw, f)

        return {
            "raw_root": raw_root,
            "out_root": out_root,
            "en_pending_dir": pending_en_root,
            "zh_tw_dir": zh_tw_root,
            "has_twcn_source": True,
        }

    # 沒有 tw/cn → 不建立 zh_tw
    log_info("FTB Quests 只有 en_us：已輸出待翻譯，跳過 zh_tw 產出")
    return {
        "raw_root": raw_root,
        "out_root": out_root,
        "en_pending_dir": pending_en_root,
        "zh_tw_dir": None,
        "has_twcn_source": False,
    }



def prepare_ftbquests_lang_template_only(
    input_config_dir: str,
    output_config_dir: str,
    *,
    prefer_lang: str = "zh_cn",
) -> dict:
    """
    只把「FTB Quests 的模板語系」補到輸出資料夾（不複製其它語系、不碰 zh_tw）。

    支援兩種結構：
    1) 資料夾制：config/ftbquests/quests/lang/zh_cn/*.snbt
    2) 單檔制： config/ftbquests/quests/lang/zh_cn.snbt

    回傳：複製結果資訊 dict（方便你 log）
    """
    src_ftb = os.path.join(input_config_dir, "ftbquests")
    dst_ftb = os.path.join(output_config_dir, "ftbquests")

    if not os.path.isdir(src_ftb):
        raise FileNotFoundError(f"找不到來源 ftbquests: {src_ftb}")

    # 輸出 lang 根目錄：.../翻譯後/config/ftbquests/quests/lang
    src_lang_root = os.path.join(src_ftb, "quests", "lang")
    dst_lang_root = os.path.join(dst_ftb, "quests", "lang")
    os.makedirs(dst_lang_root, exist_ok=True)

    prefer = prefer_lang.lower()

    # ---- 1) 資料夾制（優先）----
    src_prefer_dir = os.path.join(src_lang_root, prefer)
    src_fallback_dir = os.path.join(src_lang_root, "en_us")

    if os.path.isdir(src_prefer_dir) or os.path.isdir(src_fallback_dir):
        src_dir = src_prefer_dir if os.path.isdir(src_prefer_dir) else src_fallback_dir
        dst_dir = os.path.join(dst_lang_root, os.path.basename(src_dir))

        # ✅ 合併 copy，不刪、不碰 zh_tw
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

        return {
            "mode": "dir",
            "template_used": src_dir,
            "template_copied_to": dst_dir,
        }

    # ---- 2) 單檔制（fallback）----
    src_prefer_file = os.path.join(src_lang_root, f"{prefer}.snbt")
    src_fallback_file = os.path.join(src_lang_root, "en_us.snbt")

    if os.path.isfile(src_prefer_file) or os.path.isfile(src_fallback_file):
        src_file = src_prefer_file if os.path.isfile(src_prefer_file) else src_fallback_file
        dst_file = os.path.join(dst_lang_root, os.path.basename(src_file))

        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        shutil.copy2(src_file, dst_file)

        return {
            "mode": "file",
            "template_used": src_file,
            "template_copied_to": dst_file,
        }

    raise FileNotFoundError(
        "找不到 FTB Quests 模板語系（資料夾制/單檔制都沒有）:\n"
        f"- dir: {src_prefer_dir}\n"
        f"- dir: {src_fallback_dir}\n"
        f"- file: {src_prefer_file}\n"
        f"- file: {src_fallback_file}\n"
    )




# ============================================================
# ✅ 對外統一入口（方案 A：給 app/services.py 呼叫）
# ============================================================

# NOTE: FTB pipeline does NOT use translate_directory_generator

def run_ftb_pipeline(
    directory_path: str,
    session=None,
    output_dir: str | None = None,
    dry_run: bool = False,
    step_export: bool = True,
    step_clean: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,   # ✅ 新增
) -> dict:
    """
    FTB Quests 全流程 Pipeline（UI / services / CLI 都可用）

    steps:
      - step_export  : 抽取 raw（Output/raw/...）
      - step_clean   : 三語合併 + 產生待翻譯（Output/待翻譯/...）+ 產生最終 zh_tw（Output/config/...）
      - step_translate: 跑你既有 translate_directory_generator（完全不動你原本翻譯流程）
      - step_inject  : 將 zh_tw JSON 寫回 quests/lang/zh_tw/*.snbt（使用 ftbquests_snbt_inject）
    """
    result: dict = {}

    # ----------------------------
    # Step 1: Export Raw
    # ----------------------------

    if step_export:
        log_info("📦 [步驟 1/4] 開始抽取 FTB Quests 原始文本...")
        start_time = time.perf_counter()
        raw_paths = export_ftbquests_raw_json(directory_path, output_dir=output_dir) 
        result["raw_paths"] = raw_paths
        log_info(f"✅ 原始資料抽取完成！\n  儲存位置: {raw_paths.get('raw_root')}")

    # ----------------------------
    # Step 2: Clean
    # ----------------------------

    if step_clean:
        log_info("🧹 [步驟 2/4] 執行資料清洗與三語合併 (正在對比現有繁簡中文)...")
        clean_paths = clean_ftbquests_from_raw(directory_path, output_dir=output_dir)
        result["clean_paths"] = clean_paths
        log_info(
            "✅ 資料清洗完畢：\n"
            f"  1. 生成待翻譯檔 (en_us): {clean_paths.get('en_pending_dir')}\n"
            f"  2. 整合現有中文 (zh_tw): {clean_paths.get('zh_tw_dir')}"
        )

    # ----------------------------
    # Step 3: LM Translate pending/en_us -> output_dir/config/.../lang/zh_tw
    # ----------------------------
    if step_translate:
        # 需要 Step2 的輸出路徑
        clean_paths = result.get("clean_paths") if isinstance(result.get("clean_paths"), dict) else {}
        en_pending_dir = (clean_paths or {}).get("en_pending_dir")

        if not en_pending_dir:
            raise RuntimeError("Step3 需要 Step2 Clean 先產出 en_pending_dir（請先勾 Step2）")

        # ✅ input 要傳到 lang 這層（讓 rel 有 en_us/）
        input_lang_dir = os.path.dirname(en_pending_dir)  # .../quests/lang

        # ✅ output 固定到 output_dir/config/.../quests/lang（裡面會產 zh_tw/）
        out_root = output_dir or os.path.join(directory_path, "Output")
        output_lang_dir = os.path.join(out_root,"ftbquests","LM翻譯輸出","config", "ftbquests", "quests", "lang")

        log_info(f"🌐 [步驟 3/4] 啟動 Gemini AI 翻譯階段... (模擬模式: {'開啟' if dry_run else '關閉'})")

        from ..plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw

        lm_res = translate_ftb_pending_to_zh_tw(
            input_lang_dir=input_lang_dir,
            output_lang_dir=output_lang_dir,
            session=session,
            dry_run=dry_run,
            write_new_cache=write_new_cache,
        )
        result["lm_translate"] = lm_res
        #log_info("✅ [FTB] Step3 完成：\n" + orjson_pretty_str(lm_res)) #json 格式寫法 出現log
        # 預估批次（以 cache_miss / batch_size 計算）
        try:
            cache_miss = lm_res.get("cache_miss") if isinstance(lm_res, dict) else None
            batch_size = _get_default_batch_size("ftbquests", None)
            est_batches = (
                math.ceil(cache_miss / batch_size)
                if isinstance(cache_miss, int) and batch_size > 0
                else None
            )
            if isinstance(lm_res, dict):
                lm_res = dict(lm_res)
                lm_res["estimated_batches"] = est_batches
        except Exception:
            pass

        log_info(f"✅ AI 翻譯階段結束。詳細統計：\n{orjson_pretty_str(lm_res)}")



    # ----------------------------
    # Step 4: Inject
    # ----------------------------
    if dry_run:
        log_info("🧪 偵測到模擬模式 (Dry-run)：跳過步驟 4 Inject (不實際寫入檔案)。")
        result["inject"] = {"skipped": True, "reason": "dry_run"}
        return result
    
    # ----------------------------
    if step_inject:
        try:
            from ..plugins.ftbquests.ftbquests_snbt_inject import inject_ftbquests_zh_tw_from_jsons , inject_ftbquests_quests_from_zh_tw_json   # ✅ 新增
        except Exception:
            raise

        # ✅ Step4 永遠使用 Step3 的固定輸出位置
        out_root = output_dir or os.path.join(directory_path, "Output")
        zh_tw_dir = os.path.join(out_root,"ftbquests","LM翻譯輸出","config", "ftbquests", "quests", "lang", "zh_tw")

        zh_tw_lang_json_path = os.path.join(zh_tw_dir, "ftb_lang.json")
        zh_tw_quests_json_path = os.path.join(zh_tw_dir, "ftb_quests.json")

        lang_exists = os.path.isfile(zh_tw_lang_json_path)
        quests_exists = os.path.isfile(zh_tw_quests_json_path)

        # ✅ Gate：至少要有一個 JSON 才能注入（避免空資料夾）
        if not lang_exists and not quests_exists:
            try:
                existing_jsons = (
                    [f for f in os.listdir(zh_tw_dir) if f.lower().endswith(".json")]
                    if os.path.isdir(zh_tw_dir)
                    else []
                )
            except Exception:
                existing_jsons = []

            raise FileNotFoundError(
                "找不到 Step3 產出的任何 zh_tw JSON：\n"
                f"- {zh_tw_lang_json_path}\n"
                f"- {zh_tw_quests_json_path}\n"
                + (f"\n（目前 zh_tw 目錄內的 json：{existing_jsons}）" if existing_jsons else "")
                + "\n（請先勾 Step3：Gemini 翻譯 pending/en_us → 輸出到 output_dir/config/.../lang/zh_tw/）"
            )
    
        # ✅ 中文 log：一眼看懂這次會注入哪些
        log_info(
            "🧩 [步驟 4/4] 開始將翻譯結果注入 SNBT 檔案...\n"
            f"  - 介面文字 (ftb_lang)：{'✅ 找到' if lang_exists else '❌ 缺少'}（ftb_lang.json）\n"
            f"  - 任務內容 (ftb_quests)：{'✅ 找到' if quests_exists else '❌ 缺少'}（ftb_quests.json）"
        )

        # ----------------------------
        # Step4 Inject：只寫入輸出資料夾
        # ----------------------------

        # ✅ 來源（只讀）
        input_config_dir = os.path.join(directory_path, "config")

        # ✅ 輸出（mirror 結構）
        output_config_dir = os.path.join(output_dir,"ftbquests","完成", "config")

        log_info(f"📁 Inject template from = {input_config_dir}")
        log_info(f"📁 Inject write to     = {output_config_dir}")
        result.setdefault("inject", {})


        # ------------------------------------------------------------
        # A) ✅ quests 本體注入（不依賴 lang 模板）
        # ------------------------------------------------------------
        if quests_exists:
            quests_inject_res = inject_ftbquests_quests_from_zh_tw_json(
                input_config_dir=input_config_dir,
                output_config_dir=output_config_dir,
                zh_tw_quests_json_path=zh_tw_quests_json_path,
            )
            result["inject"]["quests"] = quests_inject_res
            log_info(
                "✅ [任務注入] 任務本體 (.snbt) 處理完成\n"
                f"  輸出目錄：{quests_inject_res.get('output_quests_dir')}\n"
                f"  複製檔案：已準備 {quests_inject_res.get('template_files_copied')} 個任務模板檔\n"
                f"  修改檔案：已成功注入 {quests_inject_res.get('patched_files')} 個任務檔案\n"
                f"  翻譯條目：成功更新 {quests_inject_res.get('patched_keys_changed')} 條文本 "
                f"(總計可對應數: {quests_inject_res.get('patched_keys_candidates')})\n"
                f"  遺失檔案：有 {quests_inject_res.get('missing_source_files')} 個來源檔不存在"
            )

        # ------------------------------------------------------------
        # B) lang 注入（可選）：需要模板才做，沒模板就跳過，但不要中斷 quests
        # ------------------------------------------------------------
        if lang_exists:
            try:
                tpl_info = prepare_ftbquests_lang_template_only(
                    input_config_dir,
                    output_config_dir,
                    prefer_lang="zh_cn",
                )
                log_info(f"📄 Template prepared: {tpl_info}")

                lang_inject_res = inject_ftbquests_zh_tw_from_jsons(
                    output_config_dir,        # 寫入輸出 config
                    zh_tw_lang_json_path,     # ftb_lang.json
                    None,                     # quests 已由 A) 處理
                    template_prefer="zh_cn",
                    overwrite_template_copy=True,
                )
                result.setdefault("inject", {})
                result["inject"]["lang"] = lang_inject_res
                log_info("✅ [FTB-INJECT-LANG] 完成")

            except FileNotFoundError as e:
                log_warning(
                    "⚠️ 找不到 FTB Quests lang 模板（zh_cn/en_us 的資料夾制/單檔制都沒有），"
                    "已略過 lang 注入（quests 注入不受影響）。\n%s",
                    e,
                )
                result.setdefault("inject", {})
                result["inject"]["lang"] = {"skipped": True, "reason": "no_lang_template"}



        log_info("✅ Output lang dir = " + os.path.join(output_config_dir, "ftbquests", "quests", "lang"))
        
        duration = get_formatted_duration(start_time)
        
        quests_summary = result.get("inject", {}).get("quests", {}) or {}
        lang_summary = result.get("inject", {}).get("lang", {}) or {}
        patched_changed = int(quests_summary.get("patched_keys_changed") or 0)
        patched_candidates = int(quests_summary.get("patched_keys_candidates") or 0)
        missing_updated = max(patched_candidates - patched_changed, 0)
        coverage = (patched_changed / patched_candidates * 100.0) if patched_candidates > 0 else 0.0
        

        log_info(
                "🎉 --- FTB Quests 翻譯流程全部完成 --- \n"
                f" 📁 最終輸出路徑: {output_config_dir}\n"
                f" 📄 任務修改總數: {quests_summary.get('patched_files')} 個檔案\n"
                f" 🔑 翻譯覆蓋：已更新 {patched_changed} / 可翻譯 {patched_candidates}（{coverage:.1f}%），未更新 {missing_updated}\n"
                f" ⚠️ 缺失源檔數: {quests_summary.get('missing_source_files')}\n"
                f" 🏷️ 語言模板注入: {'已跳過' if lang_summary.get('skipped') else '執行成功' if lang_summary else '無資料'}\n"
                f" ⏱️ 總共執行耗時: {duration}\n"
                "--------------------------------------"
            )

    
    return result
