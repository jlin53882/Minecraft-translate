"""translation_tool/utils/text_processor.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/utils/text_processor.py (最終完整版)

import os
import orjson
import logging
import re
from typing import List, Dict, Any
from opencc import OpenCC
# 從 config_manager 導入我們已經載入好的全域 config
from .config_manager import config, resolve_project_path


logger = logging.getLogger(__name__)

# ✅ 修改後：執行緒安全 (Thread-Safe)
import threading

_thread_local = threading.local()
_CJK_PATTERN = re.compile(r'([\u4e00-\u9fff]+)')

def get_converter():
    """獲取當前執行緒專用的 OpenCC 實例"""
    if not hasattr(_thread_local, "converter"):
        _thread_local.converter = OpenCC('s2twp')
    return _thread_local.converter

# =========================
# replace rules 快取 
# =========================
_LITERAL_RULES = None   # List[Tuple[str, str]]
_REGEX_RULES = None     # List[Tuple[re.Pattern, str]]
_RULE_KEYWORDS = None   # set[str]

def _init_replace_rules_cache(rules: List[Dict[str, str]]):
    """_init_replace_rules_cache 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    global _LITERAL_RULES, _REGEX_RULES, _RULE_KEYWORDS

    if _LITERAL_RULES is not None:
        return  # 已初始化過

    literal_rules = []
    regex_rules = []
    keywords = set()

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if "from" not in rule or "to" not in rule:
            continue

        src = rule["from"]
        dst = rule["to"]

        looks_like_regex = any(ch in src for ch in ".?*[]()\\")
        if looks_like_regex:
            try:
                # ⭐ 保留你原本的轉義處理
                dst_fixed = re.sub(r"\\\\(\d+)", r"\\\1", dst)
                dst_fixed = re.sub(r"\$(\d+)", r"\\\1", dst_fixed)
                pattern = re.compile(src)
                regex_rules.append((pattern, dst_fixed))
            except re.error:
                # regex 壞掉 → 當 literal
                literal_rules.append((src, dst))
                if src:
                    keywords.add(src[:2])
        else:
            literal_rules.append((src, dst))
            if src:
                keywords.add(src[:2])

    # ⭐ 長詞優先，避免「下界」吃掉「下界合金」
    literal_rules.sort(key=lambda x: len(x[0]), reverse=True)

    _LITERAL_RULES = literal_rules
    _REGEX_RULES = regex_rules
    _RULE_KEYWORDS = keywords

def apply_replace_rules(text: str, rules: List[Dict[str, str]]) -> str:
    """應用替換規則到給定的文字（舊介面，加速版）"""

    if not isinstance(text, str):
        return text

    # 初始化快取（只會做一次）
    _init_replace_rules_cache(rules)

    # ---------- 快路徑 1：極短字串 ----------
    if len(text) < 2:
        return text

    # ---------- 快路徑 2：不可能命中 ----------
    # 若 text 不含任何規則關鍵字，直接跳過
    if _RULE_KEYWORDS:
        hit = False
        for k in _RULE_KEYWORDS:
            if k in text:
                hit = True
                break
            # ⭐ 新增：忽略空白後再判斷
            if k.replace(" ", "") in text.replace(" ", ""):
                hit = True
                break
        if not hit:
            return text

    # ---------- 第一階段：固定字串（長詞優先） ----------
    for src, dst in _LITERAL_RULES:
        if src and src in text:
            text = text.replace(src, dst)

    # ---------- 第二階段：正則規則（已預編譯） ----------
    for pattern, repl in _REGEX_RULES:
        text = pattern.sub(repl, text)

    return text


# --- 檔案讀寫與文字處理工具函式 ---
def load_replace_rules(path: str) -> List[Dict[str, str]]:
    """
    從指定的 JSON 檔案載入替換規則（orjson 版），並自動進行安全排序：
    - 固定字串規則：from 長度由長到短（長詞優先）
    - 正則規則：保持原順序
    """
    resolved_path = resolve_project_path(path)
    if not resolved_path.exists():
        logger.warning("找不到替換規則檔案: %s，將略過替換處理。", resolved_path)
        return []

    try:
        with resolved_path.open("rb") as f:
            rules = orjson.loads(f.read())
    except Exception as e:
        logger.error("讀取替換規則檔案 %s 失敗: %s", resolved_path, e)
        return []

    if not isinstance(rules, list):
        logger.error("替換規則檔案格式錯誤（需為 list）: %s", resolved_path)
        return []

    fixed_rules: List[Dict[str, str]] = []
    regex_rules: List[Dict[str, str]] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if "from" not in rule or "to" not in rule:
            continue

        src = rule["from"]
        looks_like_regex = any(ch in src for ch in ".?*[]()\\")
        if looks_like_regex:
            regex_rules.append(rule)
        else:
            fixed_rules.append(rule)

    fixed_rules.sort(key=lambda r: len(r["from"]), reverse=True)
    sorted_rules = fixed_rules + regex_rules

    logger.info(
        "載入替換規則完成：固定字串 %d 條（已長詞優先排序），正則 %d 條",
        len(fixed_rules),
        len(regex_rules),
    )
    return sorted_rules


def save_replace_rules(path: str, rules: List[Dict[str, str]]):
    """將替換規則儲存到指定的 JSON 檔案（orjson 版）。"""
    resolved_path = resolve_project_path(path)
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved_path.open("wb") as f:
            f.write(orjson.dumps(
                rules,
                option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
            ))
    except Exception as e:
        logger.error("儲存替換規則到 %s 失敗: %s", resolved_path, e)


def load_custom_translations(folder_path: str, filename="table.tsv") -> Dict[str, str]:
    """從指定資料夾載入自訂的翻譯表 (TSV 格式)。"""
    custom_map = {}
    file_path = resolve_project_path(folder_path) / filename
    if not file_path.exists():
        logger.info(f"自訂翻譯檔 {file_path} 不存在，略過。")
        return custom_map
    try:
        import pandas as pd
        df = pd.read_csv(file_path, sep='\t', header=None, names=['source', 'translation'])
        for _, row in df.iterrows():
            if pd.notna(row['source']) and pd.notna(row['translation']):
                custom_map[str(row['source'])] = str(row['translation'])
        logger.info(f"成功從 {file_path} 載入 {len(custom_map)} 條自訂翻譯。")
    except Exception as e:
        logger.error(f"讀取自訂翻譯檔 {file_path} 失敗: {e}")
    return custom_map

def safe_convert_text(text: str) -> str:
    """safe_convert_text 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    if not text:
        return text
    conv = get_converter()
    return _CJK_PATTERN.sub(lambda m: conv.convert(m.group(1)), text)

def convert_text(text: str, rules: List[Dict[str, str]] | None = None) -> str:
    """
    統一的「純文字」處理入口：
    - 安全簡轉繁（CJK-only s2twp）
    - 套用 replace rules（如果有）
    用途：.snbt / .md / .js / 任何純文字
    """
    if not isinstance(text, str) or not text:
        return text

    out = safe_convert_text(text)
    if rules:
        out = apply_replace_rules(out, rules)
    return out

def convert_snbt_file_inplace(path: str, rules: List[Dict[str, str]] | None = None) -> bool:
    """
    就地轉換單一 .snbt（或任何純文字檔）內容。
    回傳：是否有變更。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        dst = convert_text(src, rules)
        if dst != src:
            with open(path, "w", encoding="utf-8") as f:
                f.write(dst)
            return True
        return False
    except Exception as e:
        logger.error("convert_snbt_file_inplace 失敗: %s (%s)", path, e)
        return False


def convert_snbt_tree_inplace(root_dir: str, rules: List[Dict[str, str]] | None = None) -> int:
    """
    遞迴掃描資料夾，把所有 .snbt 就地轉繁（CJK-only + rules）。
    回傳：有變更的檔案數。
    用途：inject copy zh_cn -> zh_tw 後，先整包轉繁再 patch
    """
    changed = 0
    for r, _, files in os.walk(root_dir):
        for fn in files:
            if fn.lower().endswith(".snbt"):
                fp = os.path.join(r, fn)
                if convert_snbt_file_inplace(fp, rules):
                    changed += 1
    return changed

def recursive_translate_dict(data: Any, rules: List[Dict[str, str]]) -> Any:
    """
    (僅用於簡轉繁) 遞迴地對一個字典或列表中的所有字串值進行 OpenCC 轉換和規則替換。
    """
    if isinstance(data, dict):
        return {k: recursive_translate_dict(v, rules) for k, v in data.items()}
    if isinstance(data, list):
        return [recursive_translate_dict(item, rules) for item in data]
    if isinstance(data, str):
        return apply_replace_rules(safe_convert_text(data), rules)
    return data

def recursive_translate(data: Any, rules: List[Dict[str, str]], custom_translations: Dict[str, str]) -> Any:
    """
    修改點：
    1. 移除 converter 參數 (不需要再從外部傳入)
    2. 遞迴呼叫時也移除 converter
    3. 字串翻譯改用 safe_convert_text
    """
    if isinstance(data, dict):
        new_dict = {}
        for key, value in data.items():
            # 優先檢查自訂翻譯
            if isinstance(value, str) and value in custom_translations:
                new_dict[key] = custom_translations[value]
            else:
                # ✅ 修改：遞迴時不再傳遞 converter
                new_dict[key] = recursive_translate(value, rules, custom_translations)
        return new_dict

    elif isinstance(data, list):
        # ✅ 修改：遞迴時不再傳遞 converter
        return [recursive_translate(item, rules, custom_translations) for item in data]

    elif isinstance(data, str):
        # ✅ 修改：使用 safe_convert_text 取代 converter.convert
        # 這會自動處理執行緒安全，並確保「内存」變「記憶體」
        translated_text = safe_convert_text(data)
        return apply_replace_rules(translated_text, rules)

    else:
        return data

def orjson_dump_file(obj, fp, *, indent2: bool = True, newline: bool = True):
    """
    用 orjson 寫入檔案物件 fp。
    - indent2=True: 等同 json.dump(..., indent=2)
    - orjson 預設就是 UTF-8 且不會把中文變成 \\uXXXX（等同 ensure_ascii=False）
    """
    option = 0
    if indent2:
        option |= orjson.OPT_INDENT_2
    if newline:
        option |= orjson.OPT_APPEND_NEWLINE

    data = orjson.dumps(obj, option=option)
    fp.write(data)


def orjson_pretty_str(obj) -> str:
    """
    orjson_pretty_str 的 Docstring
    
    :param obj: 說明
    :return: 說明
    :rtype: str
    """

    return orjson.dumps(
        obj,
        option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
    ).decode("utf-8")
