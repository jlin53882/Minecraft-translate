"""translation_tool/core/lang_merge_content.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import zipfile
from typing import Any, Dict, List

import orjson as json

from ..utils.config_manager import load_config
from ..utils.text_processor import apply_replace_rules, recursive_translate_dict
from .lang_codec import normalize_patchouli_book_root
from .lang_processing_format import dump_json_bytes, get_text_processor
from .lang_merge_zip_io import _read_text_from_zip, _write_bytes_atomic, _write_text_atomic, quarantine_copy_from_zip

logger = logging.getLogger(__name__)

def _patch_localized_content_json(zf: zipfile.ZipFile,cn_path: str,tw_output_path: str,
rules: list,
    log_prefix: str ,
    output_dir: str   # ⭐ 新增
) -> Dict[str, Any]:
    """
    模式二擴展：本地化 JSON（如書籍內容、配方描述）→ S2TW 轉換與格式化輸出。
    主要改善：
    1. 來源 JSON 若是單行／亂格式 → 自動正規化（pretty print）。
    2. TW 檔案比對前也正規化，避免因格式差異而反覆覆蓋。
    3. 最終輸出永遠使用 OPT_INDENT_2，美觀易讀。
    """

    try:
        # -----------------------------------------------------------
        # 1. 讀取 zh_cn JSON 原始內容
        # -----------------------------------------------------------
        with zf.open(cn_path) as f:
            raw_text = f.read().decode("utf-8")

        # 來源可能是單行 / 無縮排 / 髒格式 → 先解析
        try:
            cn_data = json.loads(raw_text)
        #except Exception:
        #    logger.warning(f"{log_prefix} zh_cn JSON 格式異常，嘗試以 strict=False 再次解析")
        #    cn_data = json.loads(raw_text, strict=False)
        except Exception as e:
            logger.warning(f"{log_prefix} zh_cn JSON 無法解析，已跳過該檔案: {e}")

            quarantine_copy_from_zip(
                zf=zf,
                zip_path=cn_path,
                output_dir=output_dir,
                #source_lang="zh_cn",
                reason=f"json_parse_failed: {type(e).__name__}: {e}"
            )

            # 安全跳過，不影響整體流程
            return {
                "success": True,
                #"log": f"{log_prefix} JSON 格式錯誤，已隔離並略過",
                "pending_count": 0
            }

        # -----------------------------------------------------------
        # 2. 遞迴 S2TW 轉換
        # -----------------------------------------------------------
        translated_data = recursive_translate_dict(cn_data, rules)

        # -----------------------------------------------------------
        # 3. 將轉換後內容序列化為「標準格式」（用於寫入與比對）
        # -----------------------------------------------------------
        new_content_bytes = json.dumps(translated_data, option=json.OPT_INDENT_2)

        should_write = True
        log_msg = None

        # -----------------------------------------------------------
        # 4. 若 TW 檔案存在 → 讀取後 Normalized 再比對
        # -----------------------------------------------------------
        if os.path.exists(tw_output_path):
            try:
                with open(tw_output_path, "rb") as f:
                    existing_raw = f.read().decode("utf-8")

                # 解析現有的 TW JSON
                try:
                    existing_data = json.loads(existing_raw)
                except Exception:
                    logger.warning(f"{log_prefix} TW JSON 無法解析，將覆蓋修復")
                    existing_data = None

                if existing_data is not None:
                    # 標準化現有 TW JSON 格式
                    existing_normalized_bytes = json.dumps(existing_data, option=json.OPT_INDENT_2)

                    # ⚠ 只比對 "標準化後" 的內容 → 格式不同不會誤判
                    if new_content_bytes == existing_normalized_bytes:
                        should_write = False

            except Exception as e:
                logger.warning(f"{log_prefix} 無法載入現有 TW 檔案 ({e})，將覆蓋寫入")

        # -----------------------------------------------------------
        # 5. 寫入（如果內容有異動）
        # -----------------------------------------------------------
        if should_write:
            os.makedirs(os.path.dirname(tw_output_path), exist_ok=True)
            with open(tw_output_path, "wb") as f:
                f.write(new_content_bytes)

            log_msg = f"{log_prefix} 內容 JSON 已 S2TW 轉換並寫入（格式化）"
        else:
            logger.debug(f"{log_prefix} 內容 JSON 無變動，略過寫入")

        logger.info(log_msg)
        return {
            "success": True,
            #"log": log_msg,
            "pending_count": 0
        }

    except Exception as exc:
        logger.error(f"處理內容 JSON 檔案 {cn_path} 發生錯誤: {exc}", exc_info=True)
        return {
            "success": False,
            #"log": f"{log_prefix} 處理失敗: {exc}",
            "error": True
        }

def _process_content_or_copy_file(
        zf: zipfile.ZipFile, 
        input_path: str, 
        rules: list, 
        output_dir: str,
        only_process_lang: bool = False,
        all_files_cache: List[str] = None  # ✅ 新增快取參數
        ) -> Dict[str, Any]:
    """
    【模式二：智能複製、增量補缺與直接 S2TW 轉換】 
    處理所有非標準語言 JSON 檔案。
    - 本函式主要處理非 /lang/ 的檔案、圖片、以及本地化內容檔案 (zh_cn.*)
    """
    # --- 新增過濾邏輯 ---
    # 將路徑轉為小寫並統一使用斜線，判斷是否包含 "/lang/"
    normalized_path = input_path.lower().replace('\\', '/')
    logger.debug(f"[Patchouli DEBUG] 原始 input_path = {input_path}")
    logger.debug(f"[Patchouli DEBUG] normalized_path(初始) = {normalized_path}")

    assets_idx = normalized_path.find("/assets/")
    logger.debug(f"[Patchouli DEBUG] assets_idx = {assets_idx}")

    if assets_idx != -1:
        normalized_path = normalized_path[assets_idx + 1:]
    logger.debug(f"[Patchouli DEBUG] normalized_path(裁切後) = {normalized_path}")

    if only_process_lang:
        # 1. 檢查是否在 lang 資料夾內
        if "/lang/" not in f"/{normalized_path}":
            return {"success": True, "log": None}
        
        # 2. 【新增】檢查檔名是否為我們要的語言
        # 取得純檔名 (例如: zh_cn.json -> zh_cn)
        file_stem = os.path.splitext(os.path.basename(normalized_path))[0].lower()
        
        # 定義允許留下的語言白名單
        lang_whitelist = ['zh_cn', 'zh_tw', 'en_us']
        
        if file_stem not in lang_whitelist:
            # 如果是 fr_fr, de_de, ja_jp 等檔案，直接跳過不處理也不複製
            return {"success": True, "log": None}

    # [新增] 1. 過濾邏輯：直接忽略包含 /en_us/ 的路徑
    # 這可以防止將 Patchouli 書籍等純英文內容複製到 zh_tw 目標中
    # --- Patchouli Book 特殊規則修正 ---
    def get_patchouli_book_root(path: str):
        """`get_patchouli_book_root`
        
        用途：
        - 取得此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`lower`, `find`, `get`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        p = path.replace("\\", "/").lower()
        if not p.startswith("/"):
            p = "/" + p

        idx = p.find("/assets/")
        if idx == -1:
            return None

        p_sub = p[idx + 1:]  # assets/...

        PATCHOULI_DIRS = load_config().get("lm_translator", {}).get("patchouli", {}).get(
            "dir_names", ["patchouli_books"]
        )
        if not isinstance(PATCHOULI_DIRS, list):
            PATCHOULI_DIRS = [PATCHOULI_DIRS]

        LANG_DIRS = {"zh_cn", "zh_tw", "en_us"}

        for dir_name in PATCHOULI_DIRS:
            marker = f"/{dir_name}/"
            if marker in p_sub:
                parts = p_sub.split(marker, 1)
                rest = parts[1].lstrip("/")          # dir_name 後面的路徑
                first = rest.split("/", 1)[0] if rest else ""

                # ✅ 情況1：dir_name 後面直接是語系資料夾（沒有 book_id）
                if first in LANG_DIRS:
                    book_root = parts[0] + marker    # 只到 .../<dir_name>/
                    return (book_root, dir_name)

                # ✅ 情況2：正常 patchouli 結構，有 book_id
                book_id = first
                book_root = parts[0] + marker + book_id + "/"
                return (book_root, dir_name)

        return None


    hit = get_patchouli_book_root(normalized_path)
    book_root, matched_dir_name = hit if hit else (None, None)

    if book_root:
        # 1. 🔍 檢查此手冊是否有任何中文化檔案
        # 為了效能，我們檢查 zf.namelist()

        #has_cn_or_tw = any(
        #    (f"{book_root}zh_cn/".lower() in n.lower().replace("\\", "/")) or 
        #    (f"{book_root}zh_tw/".lower() in n.lower().replace("\\", "/"))
        #    for n in zf.namelist()
        #)

        # ✅ 優化點：直接使用傳入的快取清單進行判斷
        has_cn_or_tw = False
        if all_files_cache:
            has_cn_or_tw = any(
                n.startswith(book_root) and ("/zh_cn/" in n or "/zh_tw/" in n)
                for n in all_files_cache
            )

        # 2. 🚀 處理邏輯分歧
        # 如果有中文，但現在是英文檔 -> 略過英文，避免覆蓋翻譯
        if has_cn_or_tw and "/en_us/" in normalized_path.lower():
            return {"success": True, "log": f"[Patchouli] 跳過已有翻譯的英文原件: {normalized_path}"}

        ## 3. 📝 計算目標路徑
        ## 移除 book_root 前綴
        #rel_path = normalized_path[len(book_root):]
        ## 移除語言前綴 (en_us/, zh_cn/, zh_tw/)
        #for lang_dir in ["en_us/", "zh_cn/", "zh_tw/"]:
        #    if rel_path.lower().startswith(lang_dir):
        #        rel_path = rel_path[len(lang_dir):]
        #        break
#
        ## 取得配置的資料夾名稱
        ##pending_name = load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯")
        #normalized_root = normalize_patchouli_book_root(book_root).strip("/")
#
        #if not has_cn_or_tw:
        #    # 🎯 沒中文化：輸出到 Patchouli/手冊名/待翻譯/...
        #    target = os.path.join(output_dir, "Patchouli", normalized_root , rel_path)
        #    action_log = "歸檔至待翻譯"
        #else:
        #    # 🎯 有中文化：輸出到 Patchouli/手冊名/... (正常路徑)
        #    target = os.path.join(output_dir, "Patchouli", normalized_root, rel_path)
        #    action_log = "轉換中文化"

        ## 3. 📝 計算目標路徑
        #rel_path = normalized_path[len(book_root):]
#
        #rel_low = rel_path.lower()
#
        ## A) 保留語系，但把 zh_cn 映射成 zh_tw（避免「資料夾叫 zh_cn 但內容是繁中」）
        #if rel_low.startswith("zh_cn/"):
        #    rel_path = "zh_tw/" + rel_path[len("zh_cn/"):]
        #elif rel_low.startswith("zh_tw/"):
        #    rel_path = rel_path  # 原樣
        #elif rel_low.startswith("en_us/"):
        #    rel_path = rel_path  # 原樣（給待翻譯用）
        #else:
        #    # 沒有語系資料夾的情況，原樣保留
        #    rel_path = rel_path
#
        #normalized_root = normalize_patchouli_book_root(book_root).strip("/")
#
        ## B) 沒中文化 → 明確分流到「待翻譯」子資料夾
        #pending_name = load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯")
#
        #if not has_cn_or_tw:
        #    target = os.path.join(output_dir, "Patchouli", normalized_root, pending_name, rel_path)
        #    action_log = "歸檔至待翻譯"
        #else:
        #    target = os.path.join(output_dir, "Patchouli", normalized_root, rel_path)
        #    action_log = "轉換中文化"

        # 3. 📝 計算目標路徑
        # 移除 book_root 前綴（此時 rel_path 可能會是 en_us/... 或 zh_cn/... 或 zh_tw/...）
        rel_path = normalized_path[len(book_root):]
        rel_low = rel_path.lower()

        normalized_root = normalize_patchouli_book_root(book_root).strip("/")

        # Patchouli 只保留兩個頂層：Patchouli/待翻譯 與 Patchouli/assets
        pending_name = load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯")
        PATCHOULI_DIRS = load_config().get("lm_translator", {}).get("patchouli", {}).get("dir_names", ["patchouli_books"])
        patchouli_root_dir = matched_dir_name if isinstance(PATCHOULI_DIRS, list) and PATCHOULI_DIRS else PATCHOULI_DIRS

        # A) 有中文化：輸出到 Patchouli/assets/...
        if has_cn_or_tw:
            # 1) 若是英文原件，且同書已有中文化 → 跳過（避免覆蓋）
            if rel_low.startswith("en_us/"):
                return {"success": True, "log": f"[Patchouli] 跳過已有翻譯的英文原件: {normalized_path}"}

            # 2) zh_cn → 輸出路徑改成 zh_tw（避免「資料夾叫 zh_cn 但內容是繁中」）
            if rel_low.startswith("zh_cn/"):
                rel_path = "zh_tw/" + rel_path[len("zh_cn/"):]
            # 3) zh_tw 保持 zh_tw/
            elif rel_low.startswith("zh_tw/"):
                rel_path = rel_path
            else:
                # 少數情況：沒有語系資料夾（就原樣輸出在 assets 下）
                rel_path = rel_path

            target = os.path.join(output_dir, patchouli_root_dir, normalized_root, rel_path)
            action_log = "轉換中文化"

        # B) 沒中文化：輸出到 Patchouli/待翻譯/...
        else:
            # 沒中文化通常是 en_us/ 開頭；保留 en_us/ 方便後續翻譯流程辨識來源語系
            target = os.path.join(output_dir, patchouli_root_dir, pending_name, normalized_root, rel_path)
            action_log = "歸檔至待翻譯"


        # 4. 💾 執行寫入
        os.makedirs(os.path.dirname(target), exist_ok=True)
        ext = os.path.splitext(input_path)[1].lower()

        if ext in ['.json', '.md', '.txt']:
            try:
                raw_text = _read_text_from_zip(zf, input_path)
                # 即使是英文，也跑一遍規則（處理專有名詞替換）
                tw_content = recursive_translate_dict(raw_text, rules)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(tw_content)
            except Exception as e:
                logger.error(f"[Patchouli] 寫入失敗: {e}")
                with zf.open(input_path) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            # 圖片等資源
            with zf.open(input_path) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

        return {"success": True, "log": f"[Patchouli] {action_log}: {target}"}

    log_prefix = f"處理內容檔案 '{input_path}':"
    
    # 3. 判定檔案屬性
    file_name = os.path.basename(input_path)
    ext = os.path.splitext(file_name)[1].lower()
    
    # 檢查路徑中是否包含 zh_cn/ (例如：.../docs/zh_cn/...)
    is_path_localized = 'zh_cn/' in normalized_path 
    
    # *** 修改正則表達式，允許 zh_cn 和擴展名之間有其他內容 (例如 zh_cn.flatten.json5) ***
    is_filename_localized = re.search(r'zh_cn.*?\.(lang|md|txt|snbt|json|properties|json5|gui|hl)$', file_name, re.IGNORECASE) is not None
    is_localized_cn_file = is_path_localized or is_filename_localized
    
    # 強制 S2TW 的檔案類型
    FORCE_S2TW_EXTENSIONS = {
        '.md', '.json5', '.gui', '.lang', '.snbt', '.txt', '.properties', '.hl'
    }
    is_forced_s2tw = ext in FORCE_S2TW_EXTENSIONS
    
    # is_forced_s2tw 是「語意旗標」
    # 表示：即使不是 zh_cn 本地化檔案，這些副檔名的內容
    # 在設計上也被視為「需要強制做 S2TW 的文字內容」
    # 實際轉換行為在非本地化分支中是預設發生的

    logger.debug(f"DEBUG: 進入處理函數，檔案: {input_path}")
    logger.debug(f"DEBUG: 檔案 '{input_path}' 是否為本地化 (zh_cn/ 或 zh_cn.*.ext): {is_localized_cn_file}")
    logger.debug(f"DEBUG: 檔案 '{input_path}' 是否為強制 S2TW: {is_forced_s2tw}")

    # 4. 決定目標路徑 (zh_cn -> zh_tw)
    tw_path = input_path
    if is_localized_cn_file:
        # 這是簡體中文檔案，需要轉換路徑為繁體中文
        if is_path_localized:
            tw_path = input_path.replace('\\', '/').replace('zh_cn/', 'zh_tw/')
        
        # 處理 zh_cn.*.ext 的情況
        tw_path = re.sub(r'zh_cn(\..*)$', r'zh_tw\1', tw_path, flags=re.IGNORECASE)

        final_output_path = os.path.join(output_dir, tw_path)
        os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
    else:
        # 非本地化內容: 保持原路徑不變
        final_output_path = os.path.join(output_dir, tw_path)
        
    output_dir_path = os.path.dirname(final_output_path)
    os.makedirs(output_dir_path, exist_ok=True)
    
    
    try:
        # === 非本地化檔案 (目標路徑不含 zh_cn/ 或 zh_cn.*.ext) ===
        if not is_localized_cn_file:
            # 1. 處理所有 JSON 檔案（包括 /lang/ 和非 /lang/）
            if ext == '.json':
                try:
                    # 建議改用已有的 helper 函式，它會處理 BOM
                    text = _read_text_from_zip(zf, input_path)
                    source_data = json.loads(text)
                    #source_data = json.loads(raw_bytes)
                except Exception as e:
                    # 1. 整理詳細報錯資訊
                    error_detail = f"Exception: {type(e).__name__}\nMessage: {str(e)}\nPath: {input_path}"

                    # 2. 判斷來源語言並放入 reason 中，讓變數有意義
                    lang = "unknown"
                    for possible_lang in ['zh_cn', 'zh_tw', 'en_us']:
                        if possible_lang in normalized_path:
                            lang = possible_lang
                            break

                    quarantine_copy_from_zip(
                        zf=zf,
                        zip_path=input_path,
                        output_dir=output_dir,
                        #reason=f"json_parse_failed: {type(e).__name__}: {e}",
                        reason=f"JSON解析失敗 (語言: {lang})", # 將變數活用於此
                        extra_text=error_detail                # 善用您定義的 extra_text
                    )

                    logger.warning(f"{log_prefix} JSON 無法解析，已跳過並隔離: {e}")
                    return {
                        "success": True,
                        #"log": f"{log_prefix} JSON 格式錯誤，已隔離並略過"
                    }

                log_message = ""
                
                # 【A】 處理 /lang/ 內的 zh_tw.json (增量補缺)
                if '/lang/' in normalized_path and file_name.lower() == 'zh_tw.json':
                    # 執行 zh_tw 增量補缺 (保留已存在的翻譯)
                    if os.path.exists(final_output_path):
                        try:
                            with open(final_output_path, "rb") as f:
                                existing = json.loads(f.read())
                        except:
                            existing = {}
                    else:
                        existing = {}

                    final_data = dict(existing)
                    # 僅對缺少的 key 進行補缺，並對新內容進行 S2TW
                    for k, v in source_data.items():
                        if k not in final_data:
                            # 對於新的內容，進行 S2TW 轉換
                            final_data[k] = recursive_translate_dict(v, rules)
                    
                    log_message = f"{log_prefix} 非本地化 zh_tw JSON 增量補缺 (新內容已 S2TW) 與格式化完成。"

                
                # 【B】 處理其他所有 JSON (包括非 /lang/ 的數據 JSON, 需 S2TW)
                else:
                    # 這就是您要求的部分：對 JSON 內容進行 S2TW 轉換 (遞迴翻譯所有值)
                    # 這裡使用 recursive_translate_dict 來安全地處理 JSON 結構中的所有字符串值
                    final_data = recursive_translate_dict(source_data, rules)
                    log_message = f"{log_prefix} JSON 檔案已 S2TW 轉換、格式化並複製完成。"
                
                # 執行標準格式化
                final_bytes = json.dumps(final_data, option=json.OPT_INDENT_2)
                should_write = True
                
                # 寫入前比對（強制格式化）
                if os.path.exists(final_output_path):
                    try:
                        with open(final_output_path, 'rb') as f:
                            existing_bytes = f.read()
                        
                        # 解析現有檔案並重新格式化（Normalize）
                        existing_normalized_data = json.loads(existing_bytes)
                        existing_normalized_bytes = json.dumps(existing_normalized_data, option=json.OPT_INDENT_2)
                        
                        # 若標準格式化後的內容一致，則跳過寫入
                        if existing_normalized_bytes == final_bytes:
                             should_write = False
                    except Exception:
                        should_write = True # 讀取失敗或格式錯誤，強制寫入
                
                if should_write:
                    _write_bytes_atomic(final_output_path, final_bytes)
                    logger.info(log_message)
                    return {"success": True}
                else:
                    logger.debug(f"{log_prefix} JSON 檔案內容和格式一致，略過寫入。")
                    return {"success": True, "log": None} # 靜默跳過


            # 2. PNG / 二進位 → 不轉換，直接複製 (保持不變)       
            if ext == '.png':
                with zf.open(input_path) as src:
                    with open(final_output_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                logger.debug(f"DEBUG: 本地化圖片檔案 {file_name} 複製完成: {final_output_path}")
                logger.info(f"{log_prefix} PNG 檔案直接複製。")
                return {"success": True, 
                        #"log": f"{log_prefix} PNG 檔案直接複製。"
                        }

            # 3. .mcmeta → 不轉換，直接複製 (保持不變)
            if ext == '.mcmeta':
                with zf.open(input_path) as src:
                    with open(final_output_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)

                logger.info(f"{log_prefix} .mcmeta 檔案複製完成: {final_output_path}")
                return {"success": True, 
                        #"log": f"{log_prefix} .mcmeta 檔案直接複製。"
                        }

            # 4. 其他所有純文字檔 → 一律做 S2TW
            processor = get_text_processor(ext)
            raw = _read_text_from_zip(zf, input_path)
            raw = raw.replace('\r\n', '\n').replace('\r', '\n')
            if processor:
                tw_content = processor(raw, recursive_translate_dict, rules, input_path)
            else:
                # 用最基本的 S2TW（遞迴）處理純文字
                tw_content = recursive_translate_dict(raw, rules)

            # === 寫入前：若內容相同則不複製、不覆蓋 ===

            should_write = True
            if os.path.exists(final_output_path):
                try:
                    with open(final_output_path, "r", encoding="utf-8") as f:
                        existing_content = f.read().replace("\r\n", "\n").replace("\r", "\n")
                    if existing_content == tw_content:
                        should_write = False
                except:
                    should_write = True
            
            if should_write:
                _write_text_atomic(final_output_path, tw_content)
                logger.info(f"{log_prefix} 非本地化純文字檔案 S2TW 轉換完成。")
                return {"success": True, 
                        #"log": f"{log_prefix} 非本地化純文字檔案 S2TW 轉換完成。"
                        }
            else:
                # 不寫入 → 回傳靜默（或你想加 log 也可以）
                logger.debug(f"{log_prefix} 檔案內容一致，略過寫入。")
                return {"success": True, "log": None}


        # --- 以下為本地化檔案 (is_localized_cn_file = True) 或 強制 S2TW 檔案 (is_forced_s2tw = True) 的轉換/處理邏輯 ---

        # 1. 圖片/二進位檔案
        if ext == '.png':
            try:
                # 所有從 ZIP 讀取的動作都放在這裡面
                # 圖片/二進位檔案: 智能複製邏輯
                log_msg = None # 預設為 None

                if not os.path.exists(final_output_path):
                    # 輸出檔案不存在，直接複製
                    with zf.open(input_path) as src:
                        #with open(final_output_path, 'wb') as dst:
                        with zf.open(input_path) as src, open(final_output_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    log_msg = f"{log_prefix} 圖片檔案 (.png) 複製完成 (新檔案)。"
                else:
                    # 直接進行內容比對，不需要先讀取 size 變數
                    input_content = zf.read(input_path)
                    with open(final_output_path, 'rb') as f:
                        output_content = f.read()

                    if input_content != output_content:
                        # 內容不同，進行覆蓋
                        with open(final_output_path, 'wb') as dst:
                            dst.write(input_content)
                        log_msg = f"{log_prefix} 圖片檔案 (.png) 內容不同，執行覆蓋。"
                    else:
                        # *** 日誌優化：將略過複製降級為 DEBUG ***
                        log_msg = f"{log_prefix} 圖片檔案 (.png) 內容相同，跳過複製。"

                logger.debug(f"DEBUG: 本地化圖片檔案 {file_name} 處理完成: {final_output_path}")
                logger.info(log_msg)
                return {"success": True, 
                        #"log": log_msg
                        }
            except (zipfile.BadZipFile, EOFError) as e:
                # 專門捕捉 ZIP 相關的損毀錯誤
                error_log = f"跳過損毀的 ZIP 內檔案 {input_path}: {e}"
                logger.error(error_log)
                return {"success": False,  "error": True}
            except Exception as e:
                # 捕捉其他意外錯誤（如權限問題）
                error_log = f"處理 {input_path} 時發生未預期錯誤: {e}"
                logger.error(error_log)
                return {"success": False,  "error": True}

        # 2. JSON 檔案 (執行增量補缺)
        elif ext == '.json' and is_localized_cn_file:
            # *** 針對所有本地化 JSON 檔案進行增量補缺 (無論是 zh_cn/ 或 zh_cn.json 格式) ***
            return _patch_localized_content_json(zf, input_path, final_output_path, rules, log_prefix, output_dir)
        
        # 3. .mcmeta 檔案
        elif ext == '.mcmeta':
            # .mcmeta 檔案直接複製，避免 S2TW 破壞結構
            with zf.open(input_path) as src:
                with open(final_output_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
            logger.debug(f"DEBUG: 本地化 .mcmeta 檔案 {file_name} 複製完成: {final_output_path}")
            logger.info(f"{log_prefix} 本地化檔案類型 ({ext}) 已被排除 S2TW 轉換，執行直接複製。")
            return {"success": True}
        

        # 4. 其他可翻譯純文字檔案 (.json5, .gui, .lang, .md etc.)
        # 這裡處理：純文字檔案使用 processor 進行結構化 S2TW 翻譯
        else:
            processor = get_text_processor(ext)
            
            if processor:
                # 讀取原始內容（標準化換行）
                raw = _read_text_from_zip(zf, input_path)
                raw = raw.replace('\r\n', '\n').replace('\r', '\n')
                tw_content = processor(raw, recursive_translate_dict, rules, input_path)
                
                should_write = True
                log_msg = None
                if os.path.exists(final_output_path):
                    try:
                        with open(final_output_path, 'r', encoding='utf-8') as f:
                            existing_content = f.read().replace('\r\n', '\n').replace('\r', '\n')
                        if existing_content == tw_content:
                            should_write = False
                            logger.debug(f"{log_prefix} 內容檔案 ({ext}) S2TW 轉換後內容無變動，略過寫入。")
                    except Exception:
                        should_write = True
                
                if should_write:
                    _write_text_atomic(final_output_path, tw_content)
                    if is_forced_s2tw and not is_localized_cn_file:
                        log_msg = f"{log_prefix} 非本地化檔案 ({ext}) S2TW 結構化轉換完成。"
                    else:
                        log_msg = f"{log_prefix} 內容檔案 ({ext}) S2TW 結構化轉換完成。"
                
                logger.debug(f"DEBUG: 檔案 {file_name} S2TW 處理完成。")
                logger.info(log_msg)
                return {"success": True}
            
            else:
                # 其他未指定檔案類型，為安全起見直接複製 
                with zf.open(input_path) as src:
                    with open(final_output_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                logger.info(f"{log_prefix} 未知本地化檔案類型 ({ext}) 直接複製完成。")
                return {"success": True}


    except Exception as exc:
        logger.error(f"處理內容檔案 {input_path} 時發生錯誤: {exc}", exc_info=True)
        return {"success": False,  "error": True}

def remove_empty_dirs(root_dir: str):
    """
    遞迴刪除空資料夾 (由內而外)。
    如果在 must_translate_dir 中某個模組沒有產出 en_us.json，
    這會將該模組留下的空目錄結構刪除。
    """
    if not os.path.exists(root_dir):
        return

    # topdown=False 表示先走訪子目錄，再走訪父目錄
    # 這樣當子目錄被刪除後，父目錄變空了也能被順利刪除
    for dirpath, _, _ in os.walk(root_dir, topdown=False):
        if dirpath == root_dir:
            continue  # 選擇性：保留最外層的根目錄
        try:
            # 如果目錄內沒有任何檔案或子目錄，就刪除
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError as e:
            logger.warning(f"刪除空目錄失敗 {dirpath}: {e}")

def export_filtered_pending(pending_root: str, output_root: str, min_count: int):
    """
    掃描 pending_root 下所有 pending.json，
    若條目數 >= min_count，則輸出到 output_root（重新整理後乾淨輸出）。
    
    - 會先刪除 output_root 的所有舊資料（完整移除）
    - 然後重新輸出符合條件的 pending.json
    """

    # 若 pending_root 本身不存在 → 直接返回
    if not os.path.isdir(pending_root):
        return

    # ------------------------------
    # ★ 1. 清空 output_root（避免殘留舊資料）
    # ------------------------------
    if os.path.exists(output_root):
        shutil.rmtree(output_root)

    os.makedirs(output_root, exist_ok=True)

    # ------------------------------
    # ★ 2. 掃描 pending_root，輸出符合條件者
    # ------------------------------
    for dirpath, _, filenames in os.walk(pending_root):
        for filename in filenames:
            if not filename.lower().endswith(".json"):
                continue

            pending_path = os.path.join(dirpath, filename)

            # 讀取 pending 資料
            try:
                with open(pending_path, "rb") as f:
                    data = json.loads(f.read())
            except Exception:
                continue

            # 只輸出 >= min_count 的 pending.json
            if len(data) >= int(min_count):

                # 保留 pending_root 之後的相對路徑
                rel_path = os.path.relpath(pending_path, pending_root).lstrip(os.sep)

                out_path = os.path.join(output_root, rel_path)

                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                with open(out_path, "wb") as f:
                    f.write(json.dumps(data, option=json.OPT_INDENT_2))
