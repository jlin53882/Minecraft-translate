"""translation_tool/core/lang_processing_format.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/core/lang_processing_format.py
import re
from typing import Callable, Optional, Any, Dict
import logging
import opencc # 導入 OpenCC 庫
import orjson as json
import threading

from ..utils.text_processor import apply_replace_rules

logger = logging.getLogger(__name__)


# 初始化 OpenCC 實例
converter = opencc.OpenCC("s2twp")

# 設定哪些語言標籤的程式碼區塊內容是需要翻譯的 (Metadata/文本)
TRANSLATABLE_CODE_LANGUAGES = {'json', 'yaml', 'text'} 

# 建立執行緒本地存儲物件
thread_local = threading.local()

def get_converter():
    """私有方法：確保每個執行緒都有自己的 OpenCC 實例"""
    # 檢查這個 Thread 是否已經初始化過自己的 converter
    if not hasattr(thread_local, "converter"):
        # 如果沒有，才建立一個新的（這只會在每個 Thread 第一次執行時跑一次）
        logger.debug(f"正在為執行緒 {threading.current_thread().name} 初始化 OpenCC...")
        thread_local.converter = opencc.OpenCC("s2twp")
    return thread_local.converter


def convert_only_cjk_old(text: str, rules=None) -> str:
    """只轉換中文（基本 CJK）＋ 套用自訂規則"""
    result = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            result.append(converter.convert(ch))
        else:
            result.append(ch)

    result_text = "".join(result)

    # ✅ 套用 rules
    if rules:
        result_text = apply_replace_rules(result_text, rules)

    return result_text


def convert_only_cjk(text: str, rules=None) -> str:
    """
    只轉換中文（基本 CJK）＋ 套用自訂規則
    修改版：使用正則抓取連續中文區塊進行整段轉換，以保留 OpenCC 詞彙修正功能
    """
    # 每次呼叫都透過 get_converter() 取得專屬於該執行緒的實例
    converter = get_converter()
    if not text:
        return text

    # 定義連續 CJK 字元的正則 (包含基本漢字、擴展區)
    # \u4e00-\u9fa5 是常用區，根據需求可擴大
    cjk_pattern = re.compile(r'([\u4e00-\u9fff]+)')

    def replacer(match):
        # 抓到的一整串中文字
        """`replacer`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`group`, `convert`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        cjk_chunk = match.group(1)
        # 整串丟給 OpenCC，這樣「内存」才會變「記憶體」
        return converter.convert(cjk_chunk)

    # 使用 re.sub 進行替換，非中文部分會被原樣保留
    result_text = cjk_pattern.sub(replacer, text)

    # ✅ 套用自訂取代規則 (rules)
    if rules:
        result_text = apply_replace_rules(result_text, rules)

    return result_text

def opencc_markdown_safe(md: str, rules=None) -> str:
    """
    更新後的安全轉換器：可以翻譯特定語言標籤（如 json, yaml）的程式碼區塊內容，
    同時保證不變動任何換行數，格式完全不跑掉。
    """
    # 分割三種區塊：```code block```
    parts = re.split(r'(```[\s\S]*?```)', md)

    output = []

    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            
            # --- Code Block 處理邏輯 ---
            
            # 1. 提取語言標籤 (```json\n...\n``` -> json)
            # 使用 re.match 提取開頭的語言標籤
            match = re.match(r'```(\w+)', part)
            lang = match.group(1).lower() if match else ''

            if lang in TRANSLATABLE_CODE_LANGUAGES:
                # 2. 提取內容
                # 使用 re.search 提取中間的內容
                # 假設格式是 ```lang\nCONTENT\n```
                content_match = re.search(r'```\w+\s*\n([\s\S]*?)\n```', part, re.MULTILINE)
                if content_match:
                    content = content_match.group(1)
                    # 3. 轉換內容
                    translated_content = convert_only_cjk(content, rules)
                    # 4. 重新組裝
                    output.append(f"```{lang}\n{translated_content}\n```")
                else:
                    # 如果無法解析出內容，則保留原樣作為安全措施
                    output.append(part)
            else:
                # 非翻譯語言（如 python, java, c++）直接保留
                output.append(part)
                
            # --- End Code Block 處理 ---
            
        else:
            # 處理此區塊中的 inline code `...`
            inline_parts = re.split(r'(`[^`]*`)', part)
            for seg in inline_parts:
                if seg.startswith("`") and seg.endswith("`"):
                    inner = seg[1:-1]
                    inner = convert_only_cjk(inner, rules)
                    output.append(f"`{inner}`")
                else:
                    # 其他正常 Markdown 內容 → 轉換中文
                    output.append(convert_only_cjk(seg, rules))

    return "".join(output)

def remove_translated_keys(en_dict: Dict[str, Any], tw_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    從 en_dict 中移除在 tw_dict 中已經被翻譯（存在且非空白）的 key，
    回傳新的 en_dict（淺拷貝）
    """
    result = {}
    for k, v in en_dict.items():
        tw_val = tw_dict.get(k)
        if tw_val is None or (isinstance(tw_val, str) and tw_val.strip() == ""):
            result[k] = v
        else:
            # 已被翻譯 -> 忽略
            continue
    return result

def compare_and_remove_translated_from_en(en_source: Dict[str, Any], tw_base: Dict[str, Any]) -> Dict[str, Any]:
    """
    比較英文來源 (en_source) 和繁體中文基準 (tw_base) 字典，
    並從 en_source 中移除所有 '已翻譯' (即 tw_base 中已存在的) 鍵值。

    這個函式通常用於找出在來源語言中「尚未被翻譯」到目標語言的項目。

    Args:
        en_source (Dict[str, Any]): 英文來源字典 (完整的待翻譯鍵值)。
        tw_base (Dict[str, Any]): 繁體中文基準字典 (已翻譯完成的鍵值集合)。

    Returns:
        Dict[str, Any]: 移除已翻譯鍵值後，en_source 剩餘的字典內容 (即「待翻譯」項目)。
    """
    # 實際執行移除操作，只保留 en_source 中尚未出現在 tw_base 的 key。
    # 只移除在 tw_base 中已被翻譯的 key
    return remove_translated_keys(en_source, tw_base)

def dump_json_bytes(obj: Any) -> bytes:
    """
    將 Python 物件序列化為帶有縮排的 JSON 格式位元組。

    Args:
        obj (Any): 任何可被 JSON 序列化的 Python 物件。

    Returns:
        bytes: JSON 格式的位元組資料。
    """
    # 遵循既有程式風格：使用 orjson 
    # OPT_INDENT_2 選項用於增加 2 個空格的縮排，使 JSON 易讀。
    return json.dumps(obj, option=json.OPT_INDENT_2)


# --------------------------------------------------------------------------
# I. 核心處理函式 (與 lang_merger.py 保持依賴性，需傳入翻譯規則/函式)
# --------------------------------------------------------------------------

# 調整 translate_markdown 函數簽名和邏輯
def translate_markdown(cn_content: str, translate_func: Callable[[str, Any], str], rules: Any, file_path: str = "") -> str:
    """
    處理 Markdown：
    - Patchouli Book 的 .md 不經過 Markdown Parser（避免破壞 XML tag）
    - 一般 Markdown 才使用 opencc_markdown_safe()
    """

    # === 1. Patchouli 書中特殊格式偵測 ===
    # 只要檔案路徑包含 patchouli_books，就完全避免 Markdown Parser
    if "patchouli_books" in file_path.replace("\\", "/"):
        # 只做 S2TW，不動任何 XML Tag
        return translate_func(cn_content, rules)

    # ============================
    # 以下為一般 Markdown 的正常行為
    # ============================

    # 嘗試分離 YAML Front Matter
    yaml_match = re.match(r'---\s*\n(.*?)\n---\s*\n', cn_content, re.DOTALL)

    if yaml_match:
        front_matter_raw = yaml_match.group(1).strip()
        markdown_body = cn_content[yaml_match.end():]

        # 1. 處理 Front Matter (只翻譯 title)
        front_matter_lines = []
        for line in front_matter_raw.split('\n'):
            if line.strip().lower().startswith('title:'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    title_key = parts[0]
                    title_value_cn = parts[1].strip()
                    title_value_tw = translate_func(title_value_cn, rules)
                    front_matter_lines.append(f"{title_key}: {title_value_tw}")
                    continue
            front_matter_lines.append(line)

        front_matter_tw = '\n'.join(front_matter_lines)

        # 2. Markdown body 用安全轉換器
        markdown_body_tw = opencc_markdown_safe(markdown_body, rules)

        # 3. 組合
        return f"---\n{front_matter_tw}\n---\n{markdown_body_tw}"

    else:
        # 沒有前置 YAML：整個 body 走 markdown 解析
        return opencc_markdown_safe(cn_content)


def translate_plain_text(cn_content: str, translate_func: Callable[[str, Any], str], rules: Any,file_path: str) -> str:
    """
    通用純文字處理器：對整個文件內容進行 S2TW 轉換。
    適用於非結構化 JSON (如 JSON/JSON5)、SNBT, .txt, .lang, .hl 檔案。
    通用純文字處理器：對整個文件內容進行 S2TW 轉換。
    file_path 目前不使用，但保留參數以統一 processor 介面。
    """
    # 對整個文件內容進行 S2TW 轉換。
    return translate_func(cn_content, rules)


# --------------------------------------------------------------------------
# II. 檔案類型與處理器映射 (方便集中調用)
# --------------------------------------------------------------------------

# 將文件擴展名映射到對應的處理函式。
# 所有處理函式都需接受 (cn_content, translate_func, rules) 三個參數。
TEXT_FILE_PROCESSORS: Dict[str, Callable[[str, Callable[[str, Any], str], Any], str]] = {
    # 結構化內容處理器
    '.md': translate_markdown,
    # 通用純文字處理器 (適用於 JSON 結構、SNBT 或其他鍵值對檔案)
    '.json': translate_plain_text, 
    '.json5': translate_plain_text,
    '.snbt': translate_plain_text,
    '.txt': translate_plain_text,
    #'.lang': translate_plain_text,  
    '.mcmeta': translate_plain_text, # 這裡假設 mcmeta 內容為純文字且需要翻譯
    '.hl': translate_plain_text,
    '.gui': translate_plain_text,
}

def get_text_processor(ext: str) -> Optional[Callable]:
    """根據擴展名獲取對應的文字處理函式。"""
    return TEXT_FILE_PROCESSORS.get(ext.lower())
