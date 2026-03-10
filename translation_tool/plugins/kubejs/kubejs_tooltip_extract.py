import os
import json
import re
from collections import defaultdict
from pathlib import Path

from translation_tool.utils.log_unit import( 
    log_info, 
    log_error, 
    log_warning, 
    log_debug, 
    )

def resolve_kubejs_root(input_dir: str, *, max_depth: int = 4) -> str:
    """
    自動解析並尋找 KubeJS 根目錄。
    
    搜尋策略：
    1. 檢查輸入路徑是否本身就是 kubejs/。
    2. 檢查輸入路徑的正下方是否有 kubejs/。
    3. 遞迴搜尋子目錄（受限於 max_depth），尋找名為 kubejs 的目錄。
    
    :param input_dir: 使用者選取的路徑。
    :param max_depth: 最大向下搜尋深度，避免掃描過多無關目錄（如大型模組包根目錄）。
    :return: 找到的 KubeJS 絕對路徑字串；若找不到則回傳原輸入路徑。
    """
    log_debug(f"開始解析 KubeJS 根目錄，輸入路徑: '{input_dir}'，最大搜尋深度: {max_depth}")
    
    try:
        base = Path(input_dir).resolve()
    except Exception as e:
        log_error(f"路徑解析失敗: {input_dir}, 錯誤: {str(e)}")
        return input_dir

    # 1) 情境 A：使用者直接選中了 kubejs 目錄
    if base.is_dir() and base.name.lower() == "kubejs":
        log_info(f"匹配成功：輸入路徑本身即為 KubeJS 目錄 -> {base}")
        return str(base)

    # 2) 情境 B：最常見的情況，使用者選了模組包根目錄，kubejs 就在第一層
    direct = base / "kubejs"
    if direct.is_dir():
        log_info(f"匹配成功：在第一層目錄找到 KubeJS -> {direct}")
        return str(direct)

    # 3) 情境 C：往下遞迴搜尋
    log_debug(f"直接路徑未匹配，開始在深度 {max_depth} 內搜尋子目錄...")
    
    base_parts = len(base.parts)
    best_match = None

    try:
        # 使用 rglob 遍歷，但透過深度計算手動截斷
        for p in base.rglob("*"):
            if not p.is_dir():
                continue
            
            # 計算目前深度 (相對於 base)
            current_depth = len(p.parts) - base_parts
            
            if current_depth > max_depth:
                continue
                
            if p.name.lower() == "kubejs":
                best_match = p
                log_info(f"搜尋成功：在深度 {current_depth} 處找到 KubeJS -> {p}")
                break
    except Exception as e:
        log_error(f"掃描目錄時發生異常: {str(e)}")

    if best_match:
        return str(best_match)

    # 4) 最後防線：找不到則回傳原路徑，交由後續邏輯報錯
    log_warning(f"在指定深度範圍內找不到 'kubejs' 目錄。回傳原始路徑: {base}")
    return str(base)

# ---------- 工具 ----------

def to_json_name(filename: str) -> str:
    """
    將檔名後綴統一轉換為 .json。
    例如: 'script.js' -> 'script.json', 'data' -> 'data.json'
    """
    result = filename
    if filename.endswith(".js"):
        result = filename[:-3] + ".json"
    elif filename.endswith(".json"):
        result = filename
    else:
        result = filename + ".json"
    
    log_debug(f"檔名轉換: '{filename}' -> '{result}'")
    return result


def strip_quotes(s: str) -> str:
    """
    移除字串前後成對的單引號或雙引號。
    """
    s = s.strip()
    if len(s) >= 2:
        if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
            stripped = s[1:-1]
            log_debug(f"已脫殼引號: {s} -> {stripped}")
            return stripped
    return s


def split_js_args(s: str) -> list[str]:
    """
    解析 JS 函式參數字串，能正確處理逗號分隔，並忽略括號 ()、中括號 []、大括號 {} 以及引號內的逗號。
    
    例如: 'item.of("mt:pipe", {lvl:1}), 5' 
    會被拆分為 ['item.of("mt:pipe", {lvl:1})', '5']
    """
    log_debug(f"開始拆解 JS 參數字串: {s}")
    
    args = []
    buf = ""
    depth = 0
    quote = None

    for idx, ch in enumerate(s):
        # 處理引號內的情境 (不計算深度，直到遇到結尾引號)
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue

        # 偵測引號開始
        if ch in ("'", '"', "`"):
            quote = ch
            buf += ch
            continue

        # 處理括號深度
        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1

        # 在深度為 0 時遇到逗號，代表是一個參數的結束
        if ch == "," and depth == 0:
            args.append(buf.strip())
            buf = ""
        else:
            buf += ch

    # 加入最後一個殘留的 buffer
    if buf.strip():
        args.append(buf.strip())

    if depth != 0:
        log_warning(f"JS 參數解析可能異常：括號未對齊 (剩餘深度: {depth})，原始字串: {s}")

    log_debug(f"參數拆解完成，取得 {len(args)} 個參數")
    return args


def extract_array_strings(arr: str) -> list[str]:
    """
    使用正則表達式從字串中提取所有被引號包圍的內容。
    通常用於處理 JS 陣列字串，如 '["a", "b"]' -> ['a', 'b']
    """
    try:
        matches = re.findall(r"['\"]([^'\"]+)['\"]", arr)
        log_debug(f"從陣列提取字串成功，找到 {len(matches)} 個項目")
        return matches
    except Exception as e:
        log_error(f"提取陣列字串時發生錯誤: {str(e)}")
        return []



# ---------- Patchouli 指令過濾 ----------
_PATCHOULI_COMMAND_ONLY = re.compile(
    r"^\{[a-zA-Z0-9_:-]+(?::[^\s{}]+)?(?:\s+[a-zA-Z0-9_:-]+:[^{}\s]+)*\}$"
)

def is_patchouli_command_only(s: str) -> bool:
    """
    判斷字串是否整段僅由 Patchouli 指令組成（例如：$(br)、$(l:...)、$(img) 等）。
    這通常用於過濾不需要進行翻譯處理的文本行。
    
    True  = 整段都是指令，無需翻譯。
    False = 包含一般文字或非指令內容。
    """
    # 預處理：去除空白並確保不是 None
    clean_s = (s or "").strip()
    
    if not clean_s:
        # 空字串不視為指令
        return False

    # 進行全文匹配
    # 使用 fullmatch 確保字串從頭到尾都符合 Patchouli 指令格式
    is_match = bool(_PATCHOULI_COMMAND_ONLY.fullmatch(clean_s))
    
    if is_match:
        # 如果整段都是指令，用 debug 紀錄即可，避免干擾主要資訊
        log_debug(f"偵測到純 Patchouli 指令段落，將跳過翻譯: '{clean_s}'")
    
    return is_match



# ---------- Lang Key 過濾 ----------
# 例: tooltip.xxx.yyy / item.kubejs.fake_mob_masher / block.modid.name ...
_LANG_KEY_LIKE = re.compile(r"^(?:[a-z0-9_]+)(?:\.[a-z0-9_]+)+$")

def is_lang_key_like(s: str) -> bool:
    """
    判斷字串是否「像」一個 Minecraft 的翻譯鍵（Translation Key）。
    
    目的：過濾掉如 'item.minecraft.iron_ingot' 這種 key，避免將其視為一般句子進行翻譯。
    判斷基準：
    1. 不得為空。
    2. 不得包含空格、換行或 Tab（Key 通常是連續字串）。
    3. 長度需至少為 6（避免誤殺如 'Apple', 'Stone' 等可能的顯示名稱）。
    4. 必須完全匹配指定的正規表示式（通常是小寫字母、數字、底線與點號）。
    """
    s = (s or "").strip()
    if not s:
        return False

    # 包含空白通常代表是自然語言語句，而非系統 Key
    if " " in s or "\t" in s or "\n" in s:
        return False

    # 太短的字串（如 'item'）可能是普通的單字，不要當作 Key 過濾掉
    if len(s) < 6:
        return False

    # 進行正規表示式匹配
    is_key = bool(_LANG_KEY_LIKE.fullmatch(s))
    
    if is_key:
        log_debug(f"跳過翻譯 Key: '{s}' (符合 Key 格式條件)")
    
    return is_key


def is_lang_key_ref_like(s: str) -> bool:
    """
    過濾純引用格式：
    - {atm9.quest.create.desc.belts.1}
    - {atm9.quest.create.desc.belts.1}\n{atm9.quest.create.desc.belts.2}
    """
    if not isinstance(s, str):
        return False
    t = s.strip()
    if not t:
        return False
    return bool(re.fullmatch(r"\{[^{}]+\}(?:\n\{[^{}]+\})*", t))


def clean_text(s: str) -> str:
    """
    清理文本中的雜質，主要針對 Minecraft 的特殊格式。
    
    1. 移除 Minecraft 內建的顏色碼與格式碼（如 §a, §l, §r 等）。
    2. 移除字串前後的贅餘空白。
    """
    if not s:
        return ""

    # 使用正則替換掉 § 及其後方緊跟的一個字元
    original = s
    cleaned = re.sub(r"§.", "", s)
    cleaned = cleaned.strip()

    # 只有在真的有變動時才紀錄 debug log，避免 Log 檔案過於混亂
    if original != cleaned:
        log_debug(f"文字清理完成: '{original}' -> '{cleaned}'")
        
    return cleaned


_RE_SKIP_KUBEJS_TOOLTIP_EXPR = re.compile(
    r"^\s*(Text\.translate|Text\.of|Component\.translatable|Component\.translate|Component\.literal)\s*\(",
    re.S,
)

def should_skip_kubejs_tooltip_expr(expr: str) -> bool:
    """第二參數如果是 Text.translate(...) 這種，代表語言 key 引用，不要抽去翻譯。"""
    return bool(_RE_SKIP_KUBEJS_TOOLTIP_EXPR.match((expr or "").strip()))



def extract_js_string_call(text: str, start: int) -> str | None:
    """
    從指定的起始位置開始，解析並提取第一個出現的 JavaScript 字串內容。
    支援處理單引號、雙引號以及轉義字元（如 \\' 或 \\"）。
    
    通常用於解析：Text.of( '內容' ) 或 Text.red( "內容" )
    
    :param text: 原始腳本文字內容。
    :param start: 開始搜尋的索引位置（通常是左括號 '(' 的下一個位置）。
    :return: 提取到的字串內容（不含兩側引號）；若找不到完整字串則回傳 None。
    """
    log_debug(f"開始提取 JS 字串參數，起始索引: {start}")
    
    i = start
    quote = None
    escaped = False
    buf = ""

    text_len = len(text)
    
    # 檢查起始位置是否合法
    if start >= text_len:
        log_warning(f"提取位置超出範圍: start={start}, text_length={text_len}")
        return None

    while i < text_len:
        ch = text[i]

        # 狀態 A: 已經進入引號範圍內
        if quote:
            buf += ch
            
            if escaped:
                # 前一個字元是 \，所以無論這個字元是什麼都當作一般文字處理
                escaped = False
                log_debug(f"處理轉義字元: \\{ch}")
            elif ch == "\\":
                # 偵測到轉義字元開頭
                escaped = True
            elif ch == quote:
                # 偵測到與開頭相匹配的結束引號
                result = buf[:-1]  # 去掉最後一個被加入 buf 的結尾引號
                log_debug(f"成功提取字串參數: '{result}'")
                return result
        
        # 狀態 B: 還在尋找字串的開頭引號
        else:
            if ch in ("'", '"', "`"):
                quote = ch
                log_debug(f"偵測到字串起始引號: {quote}")
        
        i += 1

    # 如果跑完迴圈都沒 return，代表字串沒閉合
    log_warning(f"字串解析未完成（可能缺少結尾引號）。目前緩存: '{buf}'，起始位置: {start}")
    return None


def should_skip_text(text: str) -> bool:
    """
    判斷該段文字是否應該跳過翻譯流程。
    
    此函式整合了多種過濾機制，包含：
    1. 空白/無內容過濾。
    2. Patchouli 指令過濾。
    3. Minecraft 翻譯鍵（Lang Key）過濾。
    4. 已翻譯（包含中文字元）內容過濾。
    
    :param text: 待檢查的原始字串。
    :return: bool, True 表示應跳過（不翻譯），False 表示需要翻譯。
    """
    # 進行初步清理
    t = clean_text(text)
    
    # 情況 1：清理後為空
    if not t:
        # 這裡不特別紀錄 Log，因為空行很常見
        return True
    
    log_debug(f"正在評估文字是否跳過: '{t}'")
    
    # ✅ 情況 2：跳過純 Patchouli 指令 (如 $(br), $(img:...) )
    if is_patchouli_command_only(t):
        log_debug(f"跳過判定: 純指令段落 -> '{t}'")
        return True
    
    # ✅ 情況 3：跳過看起來像翻譯 Key 的內容 (如 item.minecraft.dirt)
    if is_lang_key_like(t):
        log_debug(f"跳過判定: 翻譯鍵格式 (Key-like) -> '{t}'")
        return True
    
    # ✅ 情況 4：跳過純引用格式（如 {xxx}\n{yyy}）
    if is_lang_key_ref_like(t):
        log_debug(f"跳過判定: 純引用格式 -> '{t}'")
        return True

    # ✅ 情況 5：跳過已包含中文字元的文字（視為已翻譯完成）
    # 使用 Unicode 範圍 \u4e00-\u9fff 判定常用漢字
    if re.search(r"[\u4e00-\u9fff]", t):
        log_debug(f"跳過判定: 偵測到中文字元（已翻譯） -> '{t}'")
        return True

    # 情況 6：通過所有檢查，代表這是一段需要翻譯的英文/原始文字
    log_debug(f"確定需要翻譯: '{t}'")
    return False



def extract_call_args(text: str, start: int) -> str | None:
    """
    從指定的起始位置開始，提取括號 '()' 內的所有內容。
    支援嵌套括號處理（例如：.add(item, Text.of(Text.red('...')))）。
    
    :param text: 原始文字內容。
    :param start: 左括號 '(' 之後的第一個字元索引。
    :return: 括號內的完整字串；若括號未閉合則回傳 None。
    """
    depth = 1
    i = start
    buf = ""
    text_len = len(text)

    while i < text_len:
        ch = text[i]

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                # 成功找到匹配的閉合括號
                return buf

        buf += ch
        i += 1

    log_warning(f"括號解析失敗：未找到匹配的閉合括號。起始位置: {start}")
    return None


def extract_itemevents_tooltips(content: str, file_name: str, extracted: dict, auto_id: int) -> int:
    """
    解析 KubeJS 的 ItemEvents.tooltip 腳本，提取其中的文字內容。
    
    支援格式範例：
    - event.add('minecraft:dirt', Text.of('Hello'))
    - event.add(['item1', 'item2'], Text.red('Warning'))
    
    :param content: 腳本檔案的全文內容。
    :param file_name: 目前處理的檔案名稱（用於生成 Key）。
    :param extracted: 存放提取結果的字典（Key-Value）。
    :param auto_id: 目前的自動編號計數器。
    :return: 更新後的 auto_id。
    """
    log_info(f"正在處理檔案: {file_name}，開始掃描 .add() 調用...")
    
    match_count = 0

    # 搜尋所有 .add( 的位置
    for m in re.finditer(r"\.add\s*\(", content):
        arg_str = extract_call_args(content, m.end())
        if not arg_str:
            continue

        # 使用之前定義的 split_js_args 拆分參數 (Item ID, Tooltip Text, ...)
        args = split_js_args(arg_str)
        if len(args) < 2:
            log_debug(f"跳過不完整的 .add() 調用: {arg_str[:50]}...")
            continue

        # 1. 處理 Item ID (第一個參數)
        raw_id = args[0].strip()
        # 使用 strip_quotes 邏輯簡化提取
        if (raw_id.startswith("'") and raw_id.endswith("'")) or \
           (raw_id.startswith('"') and raw_id.endswith('"')):
            item_id = raw_id[1:-1]
        else:
            # 如果是陣列或正則表達式，保留原樣作為 Key 的一部分
            item_id = raw_id

        # 2. 處理 Tooltip 區塊 (第二個參數以後)
        tooltip_block = args[1]
        idx = 0

        # 尋找區塊內所有的 Text.xxx( 調用
        for tm in re.finditer(r"Text\.\w+\s*\(", tooltip_block):
            start_pos = tm.end()
            text_content = extract_js_string_call(tooltip_block, start_pos)
            
            if text_content is None:
                continue
            
            # ✅ 檢查是否符合跳過條件（空值、指令、Key、已翻譯等）
            if should_skip_text(text_content):
                idx += 1
                continue

            # 產生唯一的 Key 格式: 檔案名|物品ID.tooltip.序號
            key = f"{file_name}|{item_id}.tooltip.{idx}"
            
            # 存入結果並清理 Minecraft 顏色代碼
            cleaned_val = clean_text(text_content)
            extracted[key] = cleaned_val
            
            log_debug(f"成功提取內容 [{key}]: {cleaned_val}")
            
            auto_id += 1
            idx += 1
            match_count += 1

    log_info(f"檔案 {file_name} 處理完畢，共提取 {match_count} 條文本。")
    return auto_id



# ---------- 主流程 ----------
def extract(
    source_dir: str | None = None,
    output_dir: str | None = None,
    *,
    session=None,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
) -> dict:
    """
    執行全文提取流程：將 KubeJS 腳本與 Lang JSON 中的待翻譯文字提取出來。
    
    :param source_dir: 模組包根目錄或 kubejs 目錄。
    :param output_dir: 翻譯 JSON 的輸出目錄。
    :param session: UI 工作對話實體，用於更新進度條與日誌。
    :return: 包含提取統計資訊的字典。
    """
    # 1. 初始化路徑與環境
    src_input = source_dir or "SOURCE_DIR_NOT_SET"
    src_root = Path(resolve_kubejs_root(src_input)).resolve()
    out_root = Path(output_dir or "OUTPUT_DIR_NOT_SET").resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    log_info(f"🚀 開始提取流程 | 來源: {src_root} | 輸出: {out_root}")

    if not src_root.exists() or not src_root.is_dir():
        msg = f"❌ 找不到 kubejs 資料夾：{src_root}"
        log_error(msg)
        if session:
            session.set_error()
        raise FileNotFoundError(msg)


    if session:
        log_info(f"🔎 識別到 KubeJS 目錄: {src_root}")

    # 2. 預掃描檔案總數（用於精確計算進度條）
    all_files_path = []
    for root, _, files in os.walk(src_root):
        for f in files:
            all_files_path.append(os.path.join(root, f))
    
    total_files = max(1, len(all_files_path))
    processed_count = 0
    extracted_files_count = 0
    extracted_keys_total = 0
    errors_count = 0

    log_debug(f"掃描完畢，共發現 {total_files} 個檔案。")

    # 3. 開始遍歷檔案處理
    for file_path in all_files_path:
        file_name = os.path.basename(file_path)
        rel_dir = os.path.relpath(os.path.dirname(file_path), src_root)
        
        extracted = {} # 存放當前檔案提取出的 Key-Value
        id_counters = defaultdict(int)
        auto_id = 1

        try:
            # --- A) 處理 KubeJS 腳本 (.js) ---
            if file_name.endswith(".js") and "client_scripts" in file_path.replace("\\", "/"):
                log_debug(f"正在分析 JS 腳本: {file_name}")
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # 1. 處理 event.add(...)  ✅改用括號掃描，避免 regex 截斷造成括號/引號警告
                for m in re.finditer(r"event\.add\s*\(", content):
                    arg_str = extract_call_args(content, m.end())
                    if not arg_str:
                        continue
                    
                    args = split_js_args(arg_str)

                    # 單一參數情況：可能是字串直接添加
                    if len(args) == 1:
                        text = strip_quotes(args[0])
                        # 過濾掉 Resource Location (mod:id)
                        if re.match(r"^[a-z0-9_.-]+:[a-z0-9_/.-]+$", text):
                            continue
                        if should_skip_text(text):
                            continue
                        
                        text = clean_text(text)
                        if len(text) > 1:
                            key = f"{file_name}|auto.{auto_id}"
                            extracted[key] = text
                            auto_id += 1

                    # 兩個參數情況：(ItemID, Content)
                    elif len(args) == 2:
                        item_id = strip_quotes(args[0])
                        n = id_counters[item_id]
                        id_counters[item_id] += 1

                        # ✅ Text.translate(...) / Text.of(...) 等屬於語言 key 引用，不抽取
                        if should_skip_kubejs_tooltip_expr(args[1]):
                            continue
                        
                        # 若內容包含 Text. 元件
                        if "Text." in args[1]:
                            # 嘗試簡單提取第一個引號內容
                            m2 = re.search(r"['\"](.+?)['\"]", args[1])
                            if m2:
                                raw = m2.group(1)
                                if not should_skip_text(raw):
                                    extracted[f"{file_name}|{item_id}.{n}"] = clean_text(raw)

                        # 若內容是陣列 [...]
                        elif args[1].startswith("["):
                            if "Text." in args[1]:
                                idx = 0
                                for tm in re.finditer(r"Text\.\w+\s*\(", args[1]):
                                    t = extract_js_string_call(args[1], tm.end())
                                    if t and not should_skip_text(t):
                                        extracted[f"{file_name}|{item_id}.{n}.{idx}"] = clean_text(t)
                                    idx += 1
                            else:
                                # 純字串陣列
                                for i, txt in enumerate(extract_array_strings(args[1])):
                                    if not should_skip_text(txt):
                                        extracted[f"{file_name}|{item_id}.{n}.{i}"] = clean_text(txt)



                # 2. 處理 Ponder 劇情文字 (scene.text)
                for m in re.finditer(r"scene\.text\s*\((.+?)\)", content, re.S):
                    args = split_js_args(m.group(1))

                    if len(args) >= 2:
                        # ✅ 新增：scene.text 第二參數若是 Text.translate(...)，也不要抽
                        if should_skip_kubejs_tooltip_expr(args[1]):
                            continue

                        text = strip_quotes(args[1])
                        if not should_skip_text(text):
                            key = f"{file_name}|scene.{auto_id}"
                            if key not in extracted:
                                extracted[key] = clean_text(text)
                                auto_id += 1

                # 3. 處理 ItemEvents Tooltips (模組化調用)
                auto_id = extract_itemevents_tooltips(content, file_name, extracted, auto_id)

            # --- B) 處理語言檔 (.json) ---
            elif file_name.endswith(".json") and "/lang/" in file_path.replace("\\", "/"):
                log_debug(f"正在讀取 Lang JSON: {file_name}")
                with open(file_path, "r", encoding="utf-8") as f:
                    raw = f.read().lstrip("\ufeff") # 處理可能的 BOM
                    # 清除 JSON 中常見的結尾逗號錯誤
                    raw = re.sub(r",\s*([}\]])", r"\1", raw)
                    data = json.loads(raw)
                    for k, v in data.items():
                        if isinstance(v, str) and v.strip() and not is_lang_key_ref_like(v):
                            extracted[k] = v

        except Exception as e:
            errors_count += 1  # ✅ 累加
            msg = f"❌ 處理檔案失敗: {file_path} | 錯誤: {str(e)}"
            log_error(msg)
            # ✅ 出錯就跳過該檔案，繼續下一個
            processed_count += 1
            if session:
                p = progress_base + (processed_count / total_files) * progress_span
                session.set_progress(min(max(p, 0.0), 0.999))
            continue

        # --- C) 輸出與進度更新 ---
        if extracted:
            out_dir = out_root / rel_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / to_json_name(file_name)

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(extracted, f, ensure_ascii=False, indent=2)

            extracted_files_count += 1
            extracted_keys_total += len(extracted)
            log_info(f"✔ 已提取: {file_name} ({len(extracted)} 條)")

        # 進度條計算
        processed_count += 1
        if session:
            p = progress_base + (processed_count / total_files) * progress_span
            session.set_progress(min(max(p, 0.0), 0.999))

    # 4. 完成報告
    summary = {
        "kubejs_dir": str(src_root),
        "output_dir": str(out_root),
        "extracted_files": extracted_files_count,
        "extracted_keys_total": extracted_keys_total,
        "errors_count": errors_count,  # ✅ 建議加上
    }

    if errors_count:
        log_info(f"⚠ 提取完成（含 {errors_count} 筆錯誤）！共輸出 {extracted_files_count} 個檔案，提取 {extracted_keys_total} 條文本。")
    else:
        log_info(f"🎊 提取完成！共輸出 {extracted_files_count} 個檔案，提取 {extracted_keys_total} 條文本。")

    return summary


if __name__ == "__main__":
    extract()
