# /minecraft_translator_flet/translation_tool/core/lang_merger.py
# 完整重寫版（A 方案）
import os
import logging
import zipfile
import orjson as json
import shutil
import re
from typing import Dict, Any, Generator, List
from collections import defaultdict
import concurrent.futures


from ..utils.text_processor import recursive_translate_dict, load_replace_rules, apply_replace_rules
from ..utils.config_manager import load_config
from .lang_processing_format import get_text_processor ,dump_json_bytes



logger = logging.getLogger(__name__)


# -------------------------
# Helper utilities
# -------------------------
def _read_text_from_zip(zf: zipfile.ZipFile, path: str) -> str:
    """
    從 ZipFile 物件中讀取指定路徑的檔案內容，並解碼為字串。
    Args:
        zf (zipfile.ZipFile): 已開啟的 ZipFile 物件。
        path (str): Zip 檔案內部的路徑。
    Returns:
        str: 解碼後的文字內容。
    """
    
    # 1. 以位元組形式讀取檔案的原始內容
    with zf.open(path) as f:
        raw = f.read()
    # 2. 嘗試使用 UTF-8 進行標準解碼
    # 優先使用 utf-8-sig，它會自動過濾掉 UTF-8 的 BOM (\ufeff)
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        # 如果 utf-8 失敗，嘗試繁體/簡體常用的 GBK (常見於舊模組)
        try:
            return raw.decode('gbk')
        except UnicodeDecodeError:
            # 最後才用 ignore 模式保命
            return raw.decode('utf-8', errors='replace')
    

def _read_json_from_zip(zf: zipfile.ZipFile, path: str) -> Dict[str, Any]:
    """
    從 ZipFile 中讀取指定路徑的檔案，並嘗試將其解析為 JSON 物件 (字典)。
    自動處理 UTF-8 BOM。
    採用事前清理機制，移除 BOM 與首尾空白，確保解析成功。

    Args:
        zf (zipfile.ZipFile): 已開啟的 ZipFile 物件。
        path (str): Zip 檔案內部的 JSON 檔案路徑。

    Returns:
        Dict[str, Any]: 解析後的 JSON 資料 (Python 字典)，失敗則返回空字典。
    """
    # 1. 取得原始文字
    text = _read_text_from_zip(zf, path)
    if not text:
            return {}
    
    # 2. 事前處理：徹底移除 BOM 與所有不可見字元 (空格, \n, \r, \t)
    # .strip() 移除首尾空白，.lstrip('\ufeff') 移除 UTF-8 BOM
    cleaned_text = text.strip().lstrip('\ufeff')

    # 3. 如果清理後內容為空，直接回傳
    if not cleaned_text:
        return {}

    try:
        # 使用 orjson (你 alias 為 json) 解析
        return json.loads(cleaned_text)
    except Exception as e:
        # 如果還是失敗，嘗試將錯誤資訊記錄下來，方便排查
        logger.warning(f"JSON 解析依然失敗 (路徑: {path}): {e}")
        # 在某些極端情況下，檔案可能是編碼損毀，回傳空字典避免程式崩潰
        return {}


def _write_bytes_atomic(path: str, data: bytes) -> None:
    """
    將位元組資料以「原子性」的方式寫入檔案，確保資料寫入的穩定性。
    使用「寫入臨時檔案，然後原子性替換」的模式。

    Args:
        path (str): 最終目標檔案路徑。
        data (bytes): 要寫入的位元組資料。
    """
    # 1. 確保目標檔案路徑的資料夾存在
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 2. 定義臨時檔案名稱 (用於原子性寫入)
    tmp = path + ".tmp"
    # 3. 將資料寫入臨時檔案
    with open(tmp, "wb") as f:
        f.write(data)  
    # 4. 原子性替換：將完整的臨時檔案替換為目標檔案。
    #    這個操作在大多數 OS 上是原子的，防止在寫入過程中斷電或崩潰導致檔案損壞。
    os.replace(tmp, path)


def _write_text_atomic(path: str, text: str) -> None:
    """
    將文字資料 (UTF-8 編碼) 以「原子性」的方式寫入檔案。
    實作方式與 _write_bytes_atomic 類似。

    Args:
        path (str): 最終目標檔案路徑。
        text (str): 要寫入的文字內容。
    """
    # 1. 確保目標檔案路徑的資料夾存在
    os.makedirs(os.path.dirname(path), exist_ok=True)    
    # 2. 定義臨時檔案名稱
    tmp = path + ".tmp"   
    # 3. 將文字資料寫入臨時檔案，指定 UTF-8 編碼
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    # 4. 原子性替換
    os.replace(tmp, path)

quarantine_root_name= load_config().get("lang_merger", {}).get("quarantine_folder_name", "skipped_json")

# 建立專用的.lang 讀寫助手：

JSON_LINE = re.compile(r'^\s*"(.+?)"\s*:\s*"(.+?)"\s*,?\s*$')
KEY_ZH = re.compile(r'^([a-zA-Z0-9_.-]+)([\u4e00-\u9fff].+)$')

def try_repair_lang_line(line: str):
    # JSON 風格
    m = JSON_LINE.match(line)
    if m:
        return m.group(1), m.group(2)

    # key中文黏一起
    m = KEY_ZH.match(line)
    if m:
        return m.group(1), m.group(2)

    return None

def collapse_lang_lines(text: str):
    """
    Forge .lang: 行尾 \ 表示續行
    將多行合併成實際的一行
    """
    lines = text.splitlines()
    out = []
    buf = ""

    for line in lines:
        if buf:
            buf += line.lstrip()
        else:
            buf = line

        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]   # 移除 \ 繼續
            continue
        else:
            out.append(buf)
            buf = ""

    if buf:
        out.append(buf)

    return out

def parse_lang_text(text: str, *, on_error=None) -> Dict[str, str]:
    """
    優化後的 .lang 解析：處理 BOM、註解、以及無 '=' 的長文本續行。
    將 .lang 檔案的 key=value 內容解析為字典
    """
    # 1. 移除 UTF-8 BOM
    text = text.lstrip("\ufeff")

    data = {}
    lines = text.splitlines() # 如果 collapse_lang_lines 效果不好，建議直接 split
    
    last_key = None  # 用於記錄上一個處理的 Key，處理續行問題

    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()

        # 跳過空行或註解
        if not line or line.startswith(("#", "//", "<")):
            continue

        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            
            if key:
                data[key] = val
                last_key = key
            elif on_error:
                on_error(idx, raw, "empty key")
        else:
            # 🔥 解決 [line 39] missing '=':
            # 如果這行沒有 '='，它極可能是上一行價值的延伸（續行）
            if last_key is not None:
                # 將這行內容合併到上一個 key 的 value 中
                data[last_key] += "\n" + line 
                logger.debug(f"已自動修復續行 (line {idx}): 合併至 {last_key}")
            else:
                # 如果第一行就沒有 '=' 且不是註解，這才是真正的錯誤
                if on_error:
                    on_error(idx, raw, "missing '=' at beginning")
                continue

    return data


def dump_lang_text(data: Dict[str, str]) -> str:
    """將字典轉換回 .lang 的文字格式"""
    lines = []
    # 按照 key 排序以保持檔案整潔
    for key in sorted(data.keys()):
        lines.append(f"{key}={data[key]}")
    return "\n".join(lines)


def is_mc_standard_lang_path(path: str) -> bool:
    """
    判定該路徑是否為 Minecraft 標準的語言資料夾結構。
    例如: assets/mymod/lang/zh_cn.lang -> True
    例如: assets/mymod/patchouli_books/item.lang -> False
    """
    p = path.replace("\\", "/").lower()
    # 必須在 /lang/ 資料夾內且為 .lang 結尾
    return "/lang/" in p and p.endswith(".lang")

#處理檔案問題格式錯誤

def quarantine_copy_from_zip(
    zf: zipfile.ZipFile,
    zip_path: str,
    output_dir: str,
    reason: str,
    extra_text=None
):
    """
    將解析失敗的檔案原樣複製到：
    output_dir/skipped_json/<zip 原始路徑>

    目錄結構會與「待翻譯」完全一致，方便人工比對與修復。
    """


    # skipped_json/ + 原始 zip 路徑（例如 assets/xxx）
    quarantine_root_name= load_config().get("lang_merger", {}).get("quarantine_folder_name", "skipped_json")
    quarantine_root = os.path.join(output_dir, quarantine_root_name)
    target_path = os.path.join(quarantine_root, zip_path)

    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    try:
        # 原樣複製 bytes（不 decode、不解析）
        raw_bytes = zf.read(zip_path)
        with open(target_path, "wb") as f:
            f.write(raw_bytes)

        # 附加原因說明檔（同層）
        reason_path = target_path + ".reason.txt"
        with open(reason_path, "w", encoding="utf-8") as f:
            f.write(reason)
        
        # ⭐ 新增：如果提供額外文本（如詳細報錯），則寫入 .detail.txt
        if extra_text:
            detail_path = target_path + ".detail.txt"
            with open(detail_path, "w", encoding="utf-8") as f:
                f.write(extra_text)

        logger.warning(
            f"[隔離] 檔案已複製至 {target_path}（原因: {reason}）"
        )

    except Exception as e:
        logger.error(
            f"[隔離失敗] 無法複製檔案 {zip_path}: {e}",
            exc_info=True
        )


#-------------------------
#處理空字串
#-------------------------
def pick_first_not_none(*vals):
    for v in vals:
        if v is not None:
            return v
    return ""

def _process_single_mod(
    zf: zipfile.ZipFile,
    paths: Dict[str, str],
    rules: list,
    output_dir: str,
    must_translate_dir: str
) -> Dict[str, Any]:

    CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

    #def contains_cjk(s: str) -> bool:
    #    """是否包含任意 CJK（中文/日文/韓文）"""
    #    return isinstance(s, str) and CJK_RE.search(s) is not None
#
    #def is_pure_english(s: str) -> bool:
    #    """判斷是否為純英文（不包含 CJK）"""
    #    return isinstance(s, str) and not contains_cjk(s)


    def contains_cjk(v: Any) -> bool:
        """是否包含任意 CJK（支援 str / list / dict 遞迴）"""
        if isinstance(v, str):
            return CJK_RE.search(v) is not None
        if isinstance(v, list):
            return any(contains_cjk(x) for x in v)
        if isinstance(v, dict):
            return any(contains_cjk(x) for x in v.values())
        return False

    def has_any_text(v: Any) -> bool:
        """結構內是否至少有一段可用的文字（避免空結構被當 pending）"""
        if isinstance(v, str):
            return v.strip() != ""
        if isinstance(v, list):
            return any(has_any_text(x) for x in v)
        if isinstance(v, dict):
            return any(has_any_text(x) for x in v.values())
        return False

    def is_pure_english(v: Any) -> bool:
        """
        判斷是否為「不包含 CJK」的內容（支援結構）。
        - 需要至少有一段字串
        - 且所有字串都不含 CJK
        """
        if not has_any_text(v):
            return False
        return not contains_cjk(v)



    def _safe_read_lang_json(lang_key: str) -> Dict[str, Any]:
        path = paths.get(lang_key)
        if not path:
            return {}

        try:
            if path.lower().endswith(".lang"):
                text = _read_text_from_zip(zf, path)

                bad_lines = []

                def on_error(line_no, raw, reason):
                    bad_lines.append((line_no, raw, reason))

                data = parse_lang_text(text, on_error=on_error)

                if bad_lines:
                    quarantine_copy_from_zip(
                        zf=zf,
                        zip_path=path,
                        output_dir=output_dir,
                        reason="lang_partial_parse_error",
                        extra_text="\n".join(
                            f"[line {n}] {r}: {l}"
                            for n, l, r in bad_lines
                        )
                    )

                return data

            else:
                # JSON 是結構化的，只要壞 → 整檔隔離
                return _read_json_from_zip(zf, path)

        except Exception as e:
            quarantine_copy_from_zip(
                zf=zf,
                zip_path=path,
                output_dir=output_dir,
                reason=f"lang_json_parse_failed: {e}"
            )
            return {}


    try:
        #=============================
        # 基本資訊
        #=============================
        mod_key = paths.get("zh_cn") or paths.get("zh_tw") or paths.get("en_us")
        mod_name = mod_key.split("/lang/")[0].split("/")[-1]
        log_prefix = f"處理語言模組 '{mod_name}': "

        #=============================
        # Step 1 — 讀取所有來源
        #=============================
        cn_data = _safe_read_lang_json("zh_cn")
        tw_src_data = _safe_read_lang_json("zh_tw")
        en_data = _safe_read_lang_json("en_us")

        #=============================
        # Step 2 — 決定輸出路徑
        #=============================
        base_path_hint = paths.get("zh_cn") or paths.get("zh_tw") or paths.get("en_us")
        if "/lang/" in base_path_hint:
            relative_tw_path = base_path_hint.split("/lang/")[0] + "/lang/zh_tw.json"
        else:
            relative_tw_path = os.path.join(mod_name, "lang", "zh_tw.json")

        final_output_path = os.path.join(output_dir, relative_tw_path)
        target_has_tw = os.path.exists(final_output_path)

        #=============================
        # Step 3 — 建立 final_tw
        #=============================
        # 第一優先：已存在 output zh_tw.json
        if target_has_tw:
            try:
                with open(final_output_path, "rb") as f:
                    final_tw = json.loads(f.read())
            except:
                final_tw = {}
        else:
            final_tw = {}

        #=============================
        # Step 4 — 逐條判斷合併來源（重點修改）
        #=============================
        pending = {}

        # 所有 key 的集合
        all_keys = set(cn_data.keys()) | set(tw_src_data.keys()) | set(en_data.keys())

        for key in all_keys:
            # 1. 若 final_tw 已有人工翻譯（含 CJK），不覆蓋
            #if key in final_tw and contains_cjk(final_tw[key]):
            #    continue

            # -----------------------------
            # 人工 zh_tw 保護（來源感知）
            # -----------------------------
            # 只有「已存在於 output_dir 的 zh_tw」才視為人工翻譯並保護
            # 外部 zip 內的 zh_tw（tw_src_data）仍允許再處理（套規則）

            is_from_output_dir = (
                key in final_tw
                and target_has_tw          # 代表 output_dir 已存在 zh_tw.json
            )

            if is_from_output_dir and contains_cjk(final_tw.get(key, "")):
                # ✔ 人工翻譯 → 不動
                continue

            tw_val = tw_src_data.get(key)
            cn_val = cn_data.get(key)
            en_val = en_data.get(key)

            # 2. zh_tw（ZIP）若含中文 → 優先使用
            #if contains_cjk(tw_val):
            #    #final_tw[key] = tw_val # 直接使用 ZIP 內的 zh_tw
            #    final_tw[key] = apply_replace_rules(tw_val, rules) # 進行規則處理
            #    continue

            if contains_cjk(tw_val):
                if isinstance(tw_val, str):
                    final_tw[key] = apply_replace_rules(tw_val, rules)
                else:
                    final_tw[key] = recursive_translate_dict(tw_val, rules)
                continue

            # 3. zh_cn 若含中文 → 用 S2TW 翻譯
            if contains_cjk(cn_val):
                final_tw[key] = recursive_translate_dict(cn_val, rules)
                continue

            # 4. zh_tw 與 zh_cn 皆為英文 → 視為未翻完，寫入 pending
            #english_source = en_val or cn_val or tw_val
            english_source = pick_first_not_none(en_val, cn_val, tw_val)
            if english_source is None:
                english_source = ""
            
            # -----------------------------
            # 過濾空字串（來源本來就是 ""）
            # -----------------------------
            if isinstance(english_source, str) and english_source.strip() == "":
                # 空字串不是待翻譯內容，直接跳過
                continue            
                
            if is_pure_english(english_source):
                pending[key] = english_source
                continue

            # 5. fallback 保護
            if english_source is None:
                english_source = ""
            final_tw.setdefault(key, english_source)

        #=============================
        # Step 5 — 寫入 pending.json
        #=============================
        pending_rel = relative_tw_path.replace("zh_tw.json", "en_us.json")
        pending_path = os.path.join(must_translate_dir, pending_rel)
        os.makedirs(os.path.dirname(pending_path), exist_ok=True)

        if pending:
            # ⭐ 新增排序：讓 diff 更乾淨 ⭐
            pending = dict(sorted(pending.items(), key=lambda item: item[0]))
            _write_bytes_atomic(pending_path, dump_json_bytes(pending))
            pending_count = len(pending)
        else:
            if os.path.exists(pending_path):
                os.remove(pending_path)
            pending_count = 0

        #=============================
        # Step 6 — 輸出 zh_tw.json
        #=============================

        #if final_tw:
        #    # ⭐⭐ 新增：讓 key 按英文字母排序 ⭐⭐
        #    final_tw = dict(sorted(final_tw.items(), key=lambda item: item[0]))
        #    os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
        #    _write_bytes_atomic(final_output_path, dump_json_bytes(final_tw))
                
        if final_tw:
            # 是否為 lang 格式（依原始檔案）
            is_lang_format = base_path_hint.lower().endswith(".lang")

            # ⭐ 先排序（JSON 與 lang 都要）
            final_tw = dict(sorted(final_tw.items(), key=lambda item: item[0]))

            # ⭐ 根據格式決定最終輸出路徑
            if is_lang_format:
                final_output_path = os.path.splitext(final_output_path)[0] + ".lang"
            else:
                final_output_path = os.path.splitext(final_output_path)[0] + ".json"

            # ⭐ 用最終路徑建立資料夾
            os.makedirs(os.path.dirname(final_output_path), exist_ok=True)

            # ⭐ 寫入
            if is_lang_format:
                _write_text_atomic(final_output_path, dump_lang_text(final_tw))
            else:
                _write_bytes_atomic(final_output_path, dump_json_bytes(final_tw))

        logger.info(f"{log_prefix}完成，pending 條目: {pending_count}")
        return {
            "success": True,
            #"log": f"{log_prefix}完成，pending 條目: {pending_count}",
            "pending_count": pending_count
        }

    except Exception as exc:
        logger.exception(f"{log_prefix}處理失敗: {exc}")
        return {
            "success": False,
            #"log": f"{log_prefix}處理失敗: {exc}",
            "error": True
        }




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


def normalize_patchouli_book_root(path: str) -> str:
    """
    將：
    mod_book/assets/modid/patchouli_books/book/
    → assets/modid/patchouli_books/book/
    """
    p = path.replace("\\", "/")
    idx = p.find("assets/")
    return p[idx:] if idx != -1 else p

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



def merge_zhcn_to_zhtw_from_zip(zip_file: str, output_dir: str,only_process_lang: bool = False ) -> Generator[Dict[str, Any], None, None]:
    """
    入口函式（保持名稱不變）
    - 負責掃描 ZIP、分類每個 mod 的 zh_cn/zh_tw/en_us、決定各模組執行哪些步驟
    - 最終回傳產生的 log/progress（generator）
    """
    os.makedirs(output_dir, exist_ok=True)
    must_translate_dir = os.path.join(output_dir, load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯"))
    os.makedirs(must_translate_dir, exist_ok=True)

    try:
        rules = load_replace_rules(load_config().get("replace_rules_path", "replace_rules.json"))
    except Exception as e:
        logger.error(f"載入替換規則失敗: {e}")
        yield {"progress": 0.0, "error": True}
        return

    # --- 新增：檢查 ZIP 檔案是否存在 ---
    if not os.path.exists(zip_file):
        full_path = os.path.abspath(zip_file) # 取得絕對路徑，方便除錯
        logger.warning(f"檔案不存在，已跳過: {full_path}")
        yield {
            "progress": 1.0, 
            #"log": f"跳過：找不到檔案 {full_path}", 
            "error": False  # 設為 False 是為了讓程式繼續執行下一個任務而不中斷
        }
        return # 直接結束這個產生器，不執行後面的 ZipFile 開啟動作
    # --------------------------------

    try:
        with zipfile.ZipFile(zip_file, 'r') as zf:
            yield {"progress": 0.0, "log": f"分析 ZIP 檔案: {os.path.basename(zip_file)}"}

            # 建立模組索引：以 mod_key 為單位，收集該 mod 下的 zh_cn/zh_tw/en_us 路徑
            lang_files_by_mod = defaultdict(dict)
            other_files: List[str] = []
            #for file_path in zf.namelist():
            #    normalized = file_path.replace('\\', '/')
            #    if normalized.endswith('/') or normalized == '':
            #        continue
            #    # 標準 /lang/*.json 的處理
            #    #if '/lang/' in normalized and normalized.endswith('.json'):
            #    if '/lang/' in normalized and (normalized.endswith('.json') or normalized.endswith('.lang')):
            #        # mod_key 用來區分不同模組的 lang 資料夾
            #        mod_key = normalized.split('/lang/')[0] + '/lang/'
            #        if normalized.endswith('zh_cn.json') or normalized.endswith('zh_cn.lang'):
            #            #lang_files_by_mod[normalized.split('/lang/')[0] + '/lang/']['zh_cn'] = normalized
            #            lang_files_by_mod[mod_key]['zh_cn'] = normalized
            #        elif normalized.endswith('zh_tw.json') or normalized.endswith('zh_tw.lang'):
            #            #lang_files_by_mod[normalized.split('/lang/')[0] + '/lang/']['zh_tw'] = normalized
            #            lang_files_by_mod[mod_key]['zh_tw'] = normalized
            #        elif normalized.endswith('en_us.json') or normalized.endswith('en_us.lang'):
            #            #lang_files_by_mod[normalized.split('/lang/')[0] + '/lang/']['en_us'] = normalized
            #            lang_files_by_mod[mod_key]['en_us'] = normalized
            #        #else:
            #            # 其他 lang json
            #        #    other_files.append(normalized)
            #    else:
            #        other_files.append(normalized)
                    


            for file_path in zf.namelist():
                normalized = file_path.replace("\\", "/")
                if normalized.endswith("/") or not normalized:
                    continue
                
                norm_low = normalized.lower()

                if "/lang/" in norm_low and (norm_low.endswith(".json") or norm_low.endswith(".lang")):
                    mod_key = normalized.split("/lang/")[0] + "/lang/"

                    if norm_low.endswith("zh_cn.json") or norm_low.endswith("zh_cn.lang"):
                        lang_files_by_mod[mod_key]["zh_cn"] = normalized
                    elif norm_low.endswith("zh_tw.json") or norm_low.endswith("zh_tw.lang"):
                        lang_files_by_mod[mod_key]["zh_tw"] = normalized
                    elif norm_low.endswith("en_us.json") or norm_low.endswith("en_us.lang"):
                        lang_files_by_mod[mod_key]["en_us"] = normalized
                    #else:
                    #    other_files.append(normalized)  # 🔒 保險：避免直接消失
                else:
                    other_files.append(normalized)



            # 計算任務數量（模組 + 其他檔案）
            mods_to_process = {k: v for k, v in lang_files_by_mod.items() if v}  # 只取有任何 lang 檔的 mod
            total_lang_mods = len(mods_to_process)
            total_content_files = len(other_files)
            total_tasks = total_lang_mods + total_content_files
            if total_tasks == 0:
                logger.info("未找到任何可處理的文件，處理結束。")
                yield {"progress": 1.0, "error": False}
                return
            logger.info(f"找到 {total_lang_mods} 個語言模組與 {total_content_files} 個內容檔案，開始處理...")
            yield {"progress": 0.0}

            # 使用 ThreadPoolExecutor 處理（你可以依需求調整 max_workers）
            #讀取config 設定資料
            max_workers = load_config().get("translator", {}).get("parallel_execution_workers") or os.cpu_count()

            futures = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                
                # ✅ 優化點：在啟動 ThreadPool 前，先完成一次性的路徑標準化快取
                all_files_cache = [n.lower().replace("\\", "/") for n in zf.namelist()]
                
                # 提交每個 mod 的處理（這裡每個 mod 的 paths 會包含 zh_cn/zh_tw/en_us 任一或多個）
                for mod_key, paths in mods_to_process.items():
                    futures.append(executor.submit(_process_single_mod, zf, paths, rules, output_dir, must_translate_dir))

                # 提交其他檔案處理（例如圖片、md、json5、localized files 等）
                for input_path in other_files:
                    futures.append(
                        executor.submit(
                            _process_content_or_copy_file, 
                            zf, input_path, rules, 
                            output_dir,only_process_lang,
                            all_files_cache=all_files_cache  # 傳遞快取
                            ))

                completed = 0
                for fut in concurrent.futures.as_completed(futures):
                    completed += 1
                    try:
                        res = fut.result()
                    except Exception as e:
                        logger.error(f"處理時發生未預期錯誤: {e}")
                        res = {"success": False, "error": True}

                    progress = completed / total_tasks
                    # ⭐ 修改重點：無論有沒有 log，都要 yield 進度
                    # 這樣 UI 才會收到 progress 並更新進度條

                    # 1. 準備回傳給 UI 的資料包
                    yield_data = {
                        "progress": progress,
                        "error": res.get("error", False),
                        "pending_count": res.get("pending_count", 0),
                    }

                    # 2. 終端機日誌處理
                    log_msg = res.get("log")
                    if log_msg:
                        logger.info(log_msg)
                    else:
                        logger.debug(f"靜默處理完成 (進度: {progress:.2%})")

                    # 3. 核心重點：無論有沒有 log，每一條任務完成都 yield 一次
                    # 這樣進度條 (progress) 就會隨著任務完成一個個跳動
                    yield yield_data
            # <--- 在這裡插入清理代碼 --->
            logger.info("正在清理空的待翻譯資料夾...")
            remove_empty_dirs(must_translate_dir)
            # 🔥 新增：輸出整理後的待翻譯檔案
            #讀取config 設定資料
            folder_name=load_config().get("lang_merger", {}).get("pending_organized_folder_name", "待翻譯整理需翻譯")
            filtered_pending_dir = os.path.join(output_dir, folder_name)
            logger.info("正在產生待翻譯整理需翻譯 檔案...")
            #config 讀取資料
            filtered_pending_min_count=load_config().get("lang_merger", {}).get("filtered_pending_min_count", 2)
            export_filtered_pending(must_translate_dir, filtered_pending_dir, min_count=filtered_pending_min_count)
            # <--- 插入結束 --->
            logger.info(f"--- 全部處理完成: {total_tasks} 個任務完成 ---")
            yield {"progress": 1.0}

    except zipfile.BadZipFile:
        logger.error(f"錯誤：檔案 '{zip_file}' 不是有效 ZIP。")
        yield {"progress": 1.0, "error": True}
    except Exception as e:
        logger.exception(f"處理 ZIP 發生錯誤: {e}")
        yield {"progress": 1.0, "error": True}
