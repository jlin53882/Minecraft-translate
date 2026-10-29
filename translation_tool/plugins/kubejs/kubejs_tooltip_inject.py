"""translation_tool/plugins/kubejs/kubejs_tooltip_inject.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import ast
import json
import os
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from translation_tool.core.kubejs_translator_state import atomic_write_bytes
from translation_tool.plugins.kubejs.kubejs_tooltip_extract import (
    KUBEJS_DEFAULT_LANG_NAMESPACE,
    parse_client_lang_add,
)
from translation_tool.utils.log_unit import log_error, log_info


def resolve_kubejs_root(input_dir: str, *, max_depth: int = 4) -> str:
    """
    UI 可能傳：
      A) 模組包根目錄
      B) 直接傳 kubejs/ 目錄
    會自動往下找名為 kubejs 的資料夾（不分大小寫），最多找 max_depth 層。
    找到就回傳 kubejs 目錄；找不到就回傳原路徑（後面會報錯更直覺）。
    """
    base = Path(input_dir).resolve()

    # 1) 使用者已經選到 kubejs
    if base.is_dir() and base.name.lower() == "kubejs":
        return str(base)

    # 2) 第一層最常見：<root>/kubejs
    direct = base / "kubejs"
    if direct.is_dir():
        return str(direct)

    # 3) 往下找：限制深度，避免整包掃爆
    # 深度算法：base 本身 depth=0；base/* depth=1 ...
    base_parts = len(base.parts)
    best = None

    # 用 rglob 搜，但用 depth 來截斷
    for p in base.rglob("*"):
        if not p.is_dir():
            continue
        depth = len(p.parts) - base_parts
        if depth > max_depth:
            continue
        if p.name.lower() == "kubejs":
            best = p
            break

    return str(best) if best else str(base)


def _mask_js_non_code(text: str) -> str:
    """以等長空白遮蔽字串與註解，避免 writer 命中假呼叫。"""
    out = list(text)
    state: str | None = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state in {"'", chr(34), "`"}:
            out[index] = "\n" if char == "\n" else " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == state:
                state = None
            index += 1
            continue
        if state == "line":
            out[index] = "\n" if char == "\n" else " "
            if char == "\n":
                state = None
            index += 1
            continue
        if state == "block":
            out[index] = "\n" if char == "\n" else " "
            if char == "*" and next_char == "/":
                out[index + 1] = " "
                index += 2
                state = None
            else:
                index += 1
            continue
        if char in ("'", chr(34), "`"):
            state = char
            out[index] = " "
            index += 1
            continue
        if char == "/" and next_char == "/":
            out[index] = out[index + 1] = " "
            index += 2
            state = "line"
            continue
        if char == "/" and next_char == "*":
            out[index] = out[index + 1] = " "
            index += 2
            state = "block"
            continue
        index += 1
    return "".join(out)


def _extract_call_args_with_end(
    text: str,
    start: int,
) -> tuple[str | None, int | None]:
    """擷取平衡括號呼叫的引數，忽略字串中的括號與跳脫引號。

    Args:
        text: 含有 JavaScript 呼叫的完整來源。
        start: 外層左括號後的第一個字元索引。

    Returns:
        完整引數內容與外層右括號索引；未閉合時回傳 (None, None)。
    """
    depth = 1
    quote: str | None = None
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ("'", chr(34), "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index], index
        index += 1
    return None, None


def _replace_js_call_arguments(
    content: str,
    function_name: str,
    replace_args: Callable[[str], str],
    *,
    excluded_ranges: list[tuple[int, int]] | None = None,
) -> str:
    """以平衡括號邊界替換具名 JavaScript 呼叫的引數。

    Args:
        content: JavaScript 原始內容。
        function_name: 不含呼叫括號的函式名稱。
        replace_args: 接收完整引數並回傳替換後內容的函式。

    Returns:
        僅替換指定呼叫引數後的完整 JavaScript 內容。
    """
    # 只在程式碼區搜尋；否則註解或字串中的 ``event.add(...)`` 也會被
    # 當成可回寫的語法節點。
    code = _mask_js_non_code(content)
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    output: list[str] = []
    cursor = 0
    search_from = 0
    while True:
        match = pattern.search(code, search_from)
        if match is None:
            break
        if excluded_ranges and any(
            start <= match.start() < end for start, end in excluded_ranges
        ):
            search_from = match.end()
            continue
        arguments, closing_index = _extract_call_args_with_end(content, match.end())
        if arguments is None or closing_index is None:
            output.append(content[cursor:])
            return "".join(output)

        output.append(content[cursor : match.start()])
        replacement = replace_args(arguments)
        if replacement == arguments:
            output.append(content[match.start() : closing_index + 1])
        else:
            output.append(content[match.start() : match.end()])
            output.append(replacement)
            output.append(")")
        cursor = closing_index + 1
        search_from = cursor

    output.append(content[cursor:])
    return "".join(output)


def _itemevents_tooltip_ranges(content: str) -> list[tuple[int, int]]:
    """回傳 ``ItemEvents.tooltip(...)`` 的完整來源範圍。

    新版掃描只把 Tooltip callback 內的 ``event.add`` 視為候選；回寫也
    必須使用相同的 API 邊界。若舊版翻譯輸出沒有任何 Tooltip wrapper，
    呼叫端仍可使用 legacy fallback，維持既有腳本格式相容性。
    """
    ranges: list[tuple[int, int]] = []
    code = _mask_js_non_code(content)
    for match in re.finditer(r"\bItemEvents\.tooltip\s*\(", code):
        _arguments, closing_index = _extract_call_args_with_end(content, match.end())
        if closing_index is not None:
            ranges.append((match.start(), closing_index + 1))
    return ranges


def _clientevents_lang_ranges(
    content: str,
) -> list[tuple[int, int, str]]:
    """回傳可安全辨識的 ``ClientEvents.lang`` 呼叫範圍與 locale。"""
    ranges: list[tuple[int, int, str]] = []
    code = _mask_js_non_code(content)
    for match in re.finditer(r"\bClientEvents\.lang\s*\(", code):
        arguments, closing_index = _extract_call_args_with_end(content, match.end())
        if arguments is None or closing_index is None:
            continue
        parts = split_js_args(arguments)
        if not parts:
            continue
        locale = _decode_js_string_literal(parts[0])
        if locale is None and (
            "=>" in parts[0] or parts[0].strip().startswith("function")
        ):
            locale = "en_us"
        if locale is None or locale.lower() not in {"en_us", "zh_cn", "zh_tw"}:
            continue
        ranges.append((match.start(), closing_index + 1, locale.lower()))
    return ranges


def _has_clientevents_lang_call(content: str) -> bool:
    """判斷檔案是否含有語系 API，包含目前不支援的動態 locale。"""
    code = _mask_js_non_code(content)
    return re.search(r"\bClientEvents\.lang\s*\(", code) is not None


def _clientevents_lang_locale_at(
    ranges: list[tuple[int, int, str]], position: int
) -> str | None:
    """回傳位置所在的語系事件；位置不在支援範圍時回傳 ``None``。"""
    for start, end, locale in ranges:
        if start <= position < end:
            return locale
    return None


def _parse_kubejs_lang_call(
    content: str, match: re.Match[str], locale: str
) -> tuple[str, str, str, list[str], int, bool, str] | None:
    """解析一個已位於 ``ClientEvents.lang`` 範圍內的 ``event.add``。"""
    arguments, closing_index = _extract_call_args_with_end(content, match.end())
    if arguments is None or closing_index is None:
        return None
    parts = split_js_args(arguments)
    parsed = parse_client_lang_add(parts)
    if parsed is None:
        return None
    namespace_expr, key_expr, value_expr = parsed
    namespace = KUBEJS_DEFAULT_LANG_NAMESPACE
    if namespace_expr is not None:
        namespace = _decode_js_string_literal(namespace_expr)
        if not namespace:
            return None
    key = _decode_js_string_literal(key_expr)
    current_value = _decode_js_string_literal(value_expr)
    if not key or current_value is None:
        return None
    return (
        locale,
        namespace,
        key,
        parts,
        closing_index,
        namespace_expr is not None,
        current_value,
    )


def _select_kubejs_lang_translation(
    source_file: str,
    locale: str,
    namespace: str,
    key: str,
    occurrence: int,
    translation_map: dict[str, str],
) -> tuple[str, str] | None:
    """以新 selector 優先、legacy selector 相容地選一筆翻譯。"""
    stable_key = (
        f"{source_file}|ClientEvents.lang[{locale}][{namespace}]"
        f".event.add[{occurrence}]|{key}"
    )
    legacy_selector = (
        f"{source_file}|ClientEvents.lang[{locale}].event.add[{occurrence}]|{key}"
    )
    legacy_key = f"{source_file}|lang:{key}"
    selected_key = next(
        (
            candidate
            for candidate in (stable_key, legacy_selector, legacy_key)
            if candidate in translation_map
        ),
        None,
    )
    if selected_key is None:
        return None
    return selected_key, str(translation_map[selected_key])


def _append_kubejs_lang_translations(
    content: str,
    *,
    source_file: str,
    translation_map: dict[str, str],
    used_keys: set[str],
) -> str:
    """以 locale-aware 方式輸出 KubeJS LangEventJS 翻譯。

    英文來源事件永遠保持原樣；翻譯會追加成 ``zh_tw`` 事件。若來源本身
    已是 ``zh_tw``，只在同一個精確 selector 上更新 value。這避免把英文
    腳本誤當成繁中輸出，也避免對檔案中其他 API 的 ``event.add`` 做全文替換。
    """
    ranges = _clientevents_lang_ranges(content)
    if not ranges:
        return content

    code = _mask_js_non_code(content)
    pattern = re.compile(r"\bevent\.add\s*\(")
    output: list[str] = []
    cursor = 0
    occurrence = 0
    additions: list[tuple[str, str, str, bool]] = []

    for match in pattern.finditer(code):
        locale = _clientevents_lang_locale_at(ranges, match.start())
        if locale is None:
            continue
        candidate = _parse_kubejs_lang_call(content, match, locale)
        if candidate is None:
            continue
        (
            locale,
            namespace,
            key,
            parts,
            closing_index,
            explicit_namespace,
            current_value,
        ) = candidate

        occurrence += 1
        selected = _select_kubejs_lang_translation(
            source_file,
            locale,
            namespace,
            key,
            occurrence,
            translation_map,
        )
        if selected is None:
            continue

        selected_key, translated = selected
        used_keys.add(selected_key)
        if locale == "en_us":
            additions.append((namespace, key, translated, explicit_namespace))
            continue
        if locale != "zh_tw" or translated == current_value:
            continue

        new_parts = list(parts)
        value_index = 2 if explicit_namespace else 1
        new_parts[value_index] = json.dumps(translated, ensure_ascii=False)
        output.append(content[cursor : match.start()])
        output.append(content[match.start() : match.end()])
        output.append(", ".join(new_parts))
        output.append(")")
        cursor = closing_index + 1

    output.append(content[cursor:])
    result = "".join(output)
    if not additions:
        return result

    lines = [
        "",
        "// Minecraft-translate generated zh_tw language overlay",
        "ClientEvents.lang('zh_tw', event => {",
    ]
    for namespace, key, value, explicit_namespace in additions:
        encoded_key = json.dumps(key, ensure_ascii=False)
        encoded_value = json.dumps(value, ensure_ascii=False)
        if explicit_namespace:
            encoded_namespace = json.dumps(namespace, ensure_ascii=False)
            lines.append(
                f"  event.add({encoded_namespace}, {encoded_key}, {encoded_value});"
            )
        else:
            lines.append(f"  event.add({encoded_key}, {encoded_value});")
    lines.extend(["});", ""])
    separator = "" if result.endswith("\n") else "\n"
    return result + separator + "\n".join(lines)


# ---------------- 工具 ----------------


def split_js_args(s: str) -> list[str]:
    """依巢狀括號切分 JavaScript 引數，保留字串逗號與跳脫引號。"""
    args: list[str] = []
    buf = ""
    depth = 0
    quote: str | None = None
    escaped = False

    for char in s:
        if quote is not None:
            buf += char
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in ("'", chr(34), "`"):
            quote = char
            buf += char
            continue

        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1

        if char == "," and depth == 0:
            args.append(buf.strip())
            buf = ""
        else:
            buf += char

    if buf.strip():
        args.append(buf.strip())

    return args


def strip_quotes(s):
    """移除字串首尾的引號"""
    s = s.strip()
    if (s.startswith("'") and s.endswith("'")) or (
        s.startswith('"') and s.endswith('"')
    ):
        return s[1:-1]
    return s


def _decode_js_string_literal(literal: str) -> str | None:
    """解碼單、雙引號字串 literal，供 no-op 判斷保留原格式。

    Args:
        literal: 含首尾引號的 JavaScript 字串 literal。

    Returns:
        可安全解碼的字串值；格式不支援或內容不是字串時回傳 None。
    """
    if (
        len(literal) < 2
        or literal[0] not in ("'", chr(34))
        or literal[-1] != literal[0]
    ):
        return None
    try:
        value = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def replace_text_in_text_obj(expr: str, new_text: str) -> str:
    """替換 Text 呼叫的第一個字串引數，並保留其餘引數與 JS 跳脫。

    Args:
        expr: Text 方法呼叫表達式，可能包含巢狀 Text 呼叫。
        new_text: 要寫回的翻譯文字。

    Returns:
        字串引數以安全 JavaScript literal 替換後的表達式；找不到完整字串時
        回傳原表達式。
    """
    call_pattern = re.compile(r"Text\.\w+\s*\(")
    literal_start = None
    for match in call_pattern.finditer(expr):
        candidate = match.end()
        while candidate < len(expr) and expr[candidate].isspace():
            candidate += 1
        if candidate < len(expr) and expr[candidate] in ("'", chr(34)):
            literal_start = candidate
            break
    if literal_start is None:
        return expr

    quote = expr[literal_start]
    closing_index = literal_start + 1
    escaped = False
    while closing_index < len(expr):
        char = expr[closing_index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            break
        closing_index += 1
    if closing_index >= len(expr):
        return expr

    original_text = _decode_js_string_literal(expr[literal_start : closing_index + 1])
    if original_text == new_text:
        return expr

    replacement = json.dumps(new_text, ensure_ascii=False)
    return expr[:literal_start] + replacement + expr[closing_index + 1 :]


def extract_array_strings(expr):
    """從表達式中擷取陣列字串"""
    return re.findall(r"[\"']([^\"']+)[\"']", expr)


def replace_array(expr: str, new_values: list[str]) -> str:
    """以安全 JSON/JavaScript 字串 literal 替換陣列元素。

    Args:
        expr: 原始 JavaScript 陣列表達式。
        new_values: 依序對應陣列字串的替換值。

    Returns:
        保留未替換元素並安全跳脫新字串後的陣列表達式。
    """
    parts = split_js_args(expr[1:-1])
    output = []

    for index, part in enumerate(parts):
        part = part.strip()
        if part.startswith(("'", chr(34))) and index < len(new_values):
            original_value = _decode_js_string_literal(part)
            if original_value == new_values[index]:
                output.append(part)
            else:
                output.append(json.dumps(new_values[index], ensure_ascii=False))
        else:
            output.append(part)

    return "[" + ", ".join(output) + "]"


def to_js_name(json_name):
    """將檔案名稱的副檔名從 .json 轉換為 .js。"""
    if json_name.endswith(".json"):
        return json_name[:-5] + ".js"
    return json_name


def clean_text(s: str) -> str:
    """
    與 extractor 相同：用來判斷字串是否「有有效內容」。
    - 去頭尾空白
    - 移除 \n
    """
    if s is None:
        return ""
    return str(s).replace("\\n", "\n").strip()


def _replace_tooltip_text_calls(
    expression: str,
    *,
    source_file: str,
    item_id: str,
    occurrence: int,
    translation_map: dict[str, str],
    used_keys: set[str],
) -> str:
    """替換 tooltip 陣列中的 Text 元件，並回報實際命中的 selector。"""
    index = 0

    def replace_call(match: re.Match[str]) -> str:
        nonlocal index
        key = f"{source_file}|{item_id}.{occurrence}.{index}"
        index += 1
        if key not in translation_map:
            return match.group(0)
        used_keys.add(key)
        return replace_text_in_text_obj(match.group(0), translation_map[key])

    quote_class = "['" + chr(34) + "]"
    pattern = rf"Text\.\w+\s*\(\s*{quote_class}.*?{quote_class}\s*\)"
    return re.sub(pattern, replace_call, expression, flags=re.DOTALL)


def _patch_event_add_two_args(
    args: list[str],
    *,
    source_file: str,
    translation_map: dict[str, str],
    counters: defaultdict[str, int],
    used_keys: set[str],
) -> tuple[list[str], bool]:
    """處理兩參數 tooltip／ClientEvents.lang 呼叫。"""
    item_id = strip_quotes(args[0])
    occurrence = counters[item_id]
    counters[item_id] += 1
    new_args = list(args)
    lang_key = _decode_js_string_literal(args[0])
    lang_value = _decode_js_string_literal(args[1])
    stable_lang_key = f"{source_file}|lang:{lang_key}" if lang_key else ""
    if stable_lang_key in translation_map and lang_value is not None:
        used_keys.add(stable_lang_key)
        translated = translation_map[stable_lang_key]
        if lang_value != translated:
            new_args[1] = json.dumps(translated, ensure_ascii=False)
            return new_args, True
        return new_args, False

    key = f"{source_file}|{item_id}.{occurrence}"
    if args[1].strip().startswith("Text.") and key in translation_map:
        used_keys.add(key)
        replacement = replace_text_in_text_obj(args[1], translation_map[key])
        if replacement != args[1]:
            new_args[1] = replacement
            return new_args, True
        return new_args, False

    if not args[1].strip().startswith("["):
        return new_args, False
    if "Text." in args[1]:
        replacement = _replace_tooltip_text_calls(
            args[1],
            source_file=source_file,
            item_id=item_id,
            occurrence=occurrence,
            translation_map=translation_map,
            used_keys=used_keys,
        )
    else:
        old_values = extract_array_strings(args[1])
        new_values = list(old_values)
        for index, old_value in enumerate(old_values):
            key = f"{source_file}|{item_id}.{occurrence}.{index}"
            if key in translation_map:
                used_keys.add(key)
                new_values[index] = translation_map[key]
        replacement = replace_array(args[1], new_values)
    if replacement == args[1]:
        return new_args, False
    new_args[1] = replacement
    return new_args, True


def _patch_event_add_three_args(
    args: list[str],
    *,
    source_file: str,
    translation_map: dict[str, str],
    used_keys: set[str],
) -> tuple[list[str], bool]:
    """處理 ``event.add(namespace, key, value)`` 語系呼叫。"""
    parsed = parse_client_lang_add(args)
    if parsed is None:
        return args, False
    _namespace, key_expr, value_expr = parsed
    lang_key = _decode_js_string_literal(key_expr)
    lang_value = _decode_js_string_literal(value_expr)
    stable_key = f"{source_file}|lang:{lang_key}" if lang_key else ""
    if stable_key not in translation_map or lang_value is None:
        return args, False
    used_keys.add(stable_key)
    translated = translation_map[stable_key]
    if translated == lang_value:
        return args, False
    new_args = list(args)
    new_args[2] = json.dumps(translated, ensure_ascii=False)
    return new_args, True


def _patch_event_add_args(
    arg_str: str,
    *,
    source_file: str,
    translation_map: dict[str, str],
    counters: defaultdict[str, int],
    used_keys: set[str],
    auto_id: int,
) -> tuple[str, int]:
    """依參數數量分派 event.add，回傳新引數與下一個 auto ordinal。"""
    args = split_js_args(arg_str)
    if len(args) == 1:
        key = f"{source_file}|auto.{auto_id}"
        new_args = list(args)
        if key in translation_map:
            used_keys.add(key)
            translated = translation_map[key]
            if _decode_js_string_literal(args[0]) != translated:
                new_args[0] = json.dumps(translated, ensure_ascii=False)
        return (", ".join(new_args) if new_args != args else arg_str), auto_id + 1
    if len(args) == 2:
        new_args, changed = _patch_event_add_two_args(
            args,
            source_file=source_file,
            translation_map=translation_map,
            counters=counters,
            used_keys=used_keys,
        )
    elif len(args) == 3:
        new_args, changed = _patch_event_add_three_args(
            args,
            source_file=source_file,
            translation_map=translation_map,
            used_keys=used_keys,
        )
    else:
        return arg_str, auto_id
    return (", ".join(new_args) if changed else arg_str), auto_id


# ---------------- 主流程 ----------------


def inject(
    original_dir: str,
    translated_dir: str,
    final_output_dir: str,
    *,
    session=None,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
    source_roots: dict[str, str] | None = None,
    source_order: list[str] | None = None,
    pending_root: str | None = None,
) -> dict:
    """將翻譯內容注入 KubeJS 輸出，保留來源腳本並只計數真正變更的 JS。

    Args:
        original_dir: 原始模組包根目錄或 kubejs/ 目錄。
        translated_dir: 抽取或翻譯後的 JSON 目錄。
        final_output_dir: 注入結果的輸出目錄。
        session: 可選的進度與日誌工作階段。
        progress_base: 此步驟進度區間的起點。
        progress_span: 此步驟進度區間的寬度。

    Returns:
        輸出目錄與檔案統計；patched_js_files 只計算輸出內容不同於來源的 JS 檔。

    Raises:
        FileNotFoundError: translated_dir 不存在時引發。
    """
    orig_root = Path(resolve_kubejs_root(original_dir)).resolve()
    trans_root = Path(translated_dir).resolve()
    out_root = Path(final_output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if session:
        log_info(f"🧩 [KubeJS-INJECT] kubejs dir: {orig_root}")
        log_info(f"🧩 [KubeJS-INJECT] translated dir: {trans_root}")
        log_info(f"🧩 [KubeJS-INJECT] final output dir: {out_root}")

    if not trans_root.exists():
        msg = f"❌ translated_dir 不存在：{trans_root}"
        if session:
            log_error(msg)
            session.set_error()
        raise FileNotFoundError(msg)

    # progress：先收集要處理的檔案（只算 json）
    json_files = []
    for root, _, files in os.walk(trans_root):
        for file in files:
            if file.endswith(".json"):
                json_files.append(os.path.join(root, file))

    source_roots = source_roots or {}
    order_index = {
        source_id: index for index, source_id in enumerate(source_order or [])
    }

    def _ordered_json_key(path: str) -> tuple[int, str]:
        relative = Path(path).relative_to(trans_root)
        if len(relative.parts) >= 3 and relative.parts[0] == "_sources":
            return (
                order_index.get(relative.parts[1], 10**9),
                relative.as_posix().casefold(),
            )
        return (-1, relative.as_posix().casefold())

    json_files.sort(key=_ordered_json_key)
    claimed_js_outputs: set[str] = set()

    total = max(1, len(json_files))
    done = 0

    patched_js_files = 0
    wrote_lang_files = 0
    applied_translation_keys: set[str] = set()
    missing_translation_keys: set[str] = set()

    for json_path in json_files:
        file = os.path.basename(json_path)
        relative_path = Path(json_path).relative_to(trans_root)
        relative_parts = relative_path.parts
        source_id: str | None = None
        script_relative = relative_path
        if len(relative_parts) >= 3 and relative_parts[0] == "_sources":
            source_id = relative_parts[1]
            script_relative = Path(*relative_parts[2:])
        rel = script_relative.parent.as_posix()
        if rel == ".":
            rel = ""

        # ---------------- Lang JSON ----------------
        if "lang" in {part.lower() for part in relative_path.parts}:
            # ✅ 把 LM翻譯後的 lang 結果輸出到 完成（保留相對路徑）
            rel_file = Path(os.path.relpath(json_path, trans_root))
            out_path = out_root / rel_file
            try:
                incoming = json.loads(Path(json_path).read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"無法讀取翻譯語言檔：{json_path}: {exc}") from exc
            if not isinstance(incoming, dict):
                raise ValueError(f"翻譯語言檔不是 JSON object：{json_path}")
            existing: dict = {}
            if out_path.is_file():
                try:
                    loaded = json.loads(out_path.read_text(encoding="utf-8-sig"))
                    if isinstance(loaded, dict):
                        existing = loaded
                except (OSError, json.JSONDecodeError):
                    existing = {}
            pending_keys: set[str] = set()
            if pending_root:
                pending_relative = (
                    rel_file.with_name("en_us.json")
                    if rel_file.name.lower() == "zh_tw.json"
                    else rel_file
                )
                pending_path = Path(pending_root) / pending_relative
                if pending_path.is_file():
                    try:
                        pending_value = json.loads(
                            pending_path.read_text(encoding="utf-8-sig")
                        )
                        if isinstance(pending_value, dict):
                            pending_keys = set(pending_value)
                    except (OSError, json.JSONDecodeError):
                        pending_keys = set()
            merged = dict(existing)
            for key, value in incoming.items():
                if key not in merged or key in pending_keys:
                    merged[key] = value
            applied_translation_keys.update(str(key) for key in incoming)
            payload = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            atomic_write_bytes(out_path, payload)

            if session:
                log_info(f"✔ Lang → {out_path}")
            wrote_lang_files += 1

            done += 1
            if session:
                p = progress_base + (done / total) * progress_span
                session.set_progress(min(max(p, 0.0), 0.999))
            continue

        # ---------------- KubeJS Tooltips ----------------
        # 翻譯鍵以 KubeJS 根目錄下的相對路徑建立；只用 basename 會讓
        # client_scripts/a/main.js 與 client_scripts/b/main.js 互相覆蓋。
        original_js = script_relative.with_suffix(".js").as_posix()
        if source_id is not None:
            source_root_value = source_roots.get(source_id)
            if source_root_value is None:
                if session:
                    log_info(f"⏭️ 找不到來源對應，略過：{relative_path}")
                done += 1
                continue
            # source_roots 已是流水線快照出來的確切 kubejs 根目錄；不能再往下搜尋名為 kubejs 的
            # 資料夾，否則含 assets/kubejs/ 的快照會被誤判成 assets/kubejs 而找不到任何腳本。
            script_root = Path(source_root_value).resolve()
        else:
            script_root = orig_root
        script_path = script_relative.with_suffix(".js")
        js_path = script_root / script_path

        if not js_path.exists():
            # 找不到原始 js 就跳過（但也算進 progress）
            try:
                missing_values = json.loads(
                    Path(json_path).read_text(encoding="utf-8-sig")
                )
            except (OSError, json.JSONDecodeError):
                missing_values = {}
            if isinstance(missing_values, dict):
                missing_translation_keys.update(str(key) for key in missing_values)
            if session:
                log_info(f"⏭️ 找不到原始 JS，略過：{js_path}")
            done += 1
            if session:
                p = progress_base + (done / total) * progress_span
                session.set_progress(min(max(p, 0.0), 0.999))
            continue

        output_relative = script_path.as_posix().casefold()
        if output_relative in claimed_js_outputs:
            if session:
                log_info(f"⏭️ 多來源同一路徑，保留較早匯入的來源：{script_path}")
            done += 1
            continue
        claimed_js_outputs.add(output_relative)

        with open(json_path, "r", encoding="utf-8") as f:
            translations = json.load(f)
        if not isinstance(translations, dict):
            raise TypeError(f"翻譯腳本不是 JSON object：{json_path}")

        # ✅ 改成整檔處理（避免跨行 / chain call / scene.text 插不回）
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
        original_content = content

        id_counters = defaultdict(int)
        auto_id = 1  # ✅ 必須跟 extractor 一樣從 1 開始，先跑 event.add，再跑 scene.text，最後跑 ItemEvents
        source_translation_keys = {str(key) for key in translations}
        used_translation_keys: set[str] = set()

        # ----------------------------
        # 1) Patch event.add(...)
        # ----------------------------
        def repl_event_add(
            arg_str: str,
            *,
            source_file: str = original_js,
            translation_map: dict = translations,
            counters: defaultdict[str, int] = id_counters,
            used_keys: set[str] = used_translation_keys,
        ) -> str:
            """只在 event.add 引數實際改變時回傳重建後的引數。"""
            nonlocal auto_id
            replacement, auto_id = _patch_event_add_args(
                arg_str,
                source_file=source_file,
                translation_map=translation_map,
                counters=counters,
                used_keys=used_keys,
                auto_id=auto_id,
            )
            return replacement

        lang_ranges = _clientevents_lang_ranges(content)
        if lang_ranges:
            # ClientEvents.lang 的英文定義不可直接覆蓋；helper 會依 locale、
            # namespace 與 occurrence 產生精確的 zh_tw overlay。
            content = _append_kubejs_lang_translations(
                content,
                source_file=original_js,
                translation_map=translations,
                used_keys=used_translation_keys,
            )
        elif not _has_clientevents_lang_call(content):
            # 保留舊版 event.add fixture 的相容性；同檔的新式 Tooltip
            # 仍由下方專用 writer 處理，不能讓 legacy fallback 越界改寫它。
            content = _replace_js_call_arguments(
                content,
                "event.add",
                repl_event_add,
                excluded_ranges=_itemevents_tooltip_ranges(content),
            )

        # ----------------------------
        # 2) Patch Ponder: scene.text(...)
        #    key: file|scene.{auto_id}  (✅ 接續 event.add 用掉的 auto_id)
        # ----------------------------
        scene_counter = [0]

        def repl_scene_text(
            arg_str: str,
            *,
            source_file: str = original_js,
            translation_map: dict = translations,
            counter: list[int] = scene_counter,
            used_keys: set[str] = used_translation_keys,
        ) -> str:
            """只在 scene.text 文字實際改變時回傳重建後的引數。"""
            nonlocal auto_id
            args = split_js_args(arg_str)
            if len(args) < 2:
                return arg_str

            raw_text_expr = args[1].strip()
            text_candidate = ""
            if (raw_text_expr.startswith("'") and raw_text_expr.endswith("'")) or (
                raw_text_expr.startswith(chr(34)) and raw_text_expr.endswith(chr(34))
            ):
                text_candidate = strip_quotes(raw_text_expr)
            elif raw_text_expr.startswith("Text."):
                quote_class = "['" + chr(34) + "]"
                match = re.search(
                    rf"{quote_class}(.+?){quote_class}",
                    raw_text_expr,
                    flags=re.DOTALL,
                )
                if match:
                    text_candidate = match.group(1)

            changed = False
            if clean_text(text_candidate):
                counter[0] += 1
                stable_key = f"{source_file}|scene:{counter[0]}"
                legacy_key = f"{source_file}|scene.{auto_id}"
                key = stable_key if stable_key in translation_map else legacy_key
                if key in translation_map:
                    used_keys.add(key)
                    new_text = translation_map[key]
                    if raw_text_expr.startswith("Text."):
                        replacement = replace_text_in_text_obj(args[1], new_text)
                    else:
                        replacement = json.dumps(new_text, ensure_ascii=False)
                    if replacement != args[1]:
                        args[1] = replacement
                        changed = True
                auto_id += 1

            return ", ".join(args) if changed else arg_str

        content = _replace_js_call_arguments(content, "scene.text", repl_scene_text)

        # ----------------------------
        # 3) Patch ItemEvents Tooltips: .add(...)
        #    key: file|{item_id}.tooltip.{idx}
        # ----------------------------
        def patch_itemevents_tooltips(
            full: str,
            *,
            source_file: str = original_js,
            translation_map: dict = translations,
            used_keys: set[str] = used_translation_keys,
        ) -> str:
            """修補 ItemEvents Tooltip，無變更時保留呼叫原始文字。"""
            out = []
            last = 0
            tooltip_ranges = _itemevents_tooltip_ranges(full)
            # 舊版 extractor 允許未包在 ItemEvents.tooltip callback 的
            # event.add；只有在來源確實有新式 wrapper 時才套用嚴格邊界。
            strict_context = bool(tooltip_ranges)
            tooltip_call_indices: defaultdict[str, int] = defaultdict(int)

            code = _mask_js_non_code(full)
            for match in re.finditer(r"\.add\s*\(", code):
                if strict_context and not any(
                    start <= match.start() < end for start, end in tooltip_ranges
                ):
                    continue
                args_str, end_idx = _extract_call_args_with_end(full, match.end())
                if args_str is None or end_idx is None:
                    continue

                out.append(full[last : match.start()])
                args = split_js_args(args_str)
                if len(args) < 2:
                    out.append(full[match.start() : end_idx + 1])
                    last = end_idx + 1
                    continue

                raw_id = args[0].strip()
                if (raw_id.startswith("'") and raw_id.endswith("'")) or (
                    raw_id.startswith(chr(34)) and raw_id.endswith(chr(34))
                ):
                    item_id = raw_id[1:-1]
                else:
                    item_id = raw_id

                tooltip_block = args[1]
                index = 0
                tooltip_call_indices[item_id] += 1
                tooltip_call_index = tooltip_call_indices[item_id]

                def repl_text_call(
                    text_match: re.Match[str],
                    *,
                    current_file: str = source_file,
                    current_item_id: str = item_id,
                    current_tooltip_index: int = tooltip_call_index,
                    current_translations: dict = translation_map,
                    current_used_keys: set[str] = used_keys,
                ) -> str:
                    """替換一個 ItemEvents Tooltip 字串並維持原索引順序。"""
                    nonlocal index
                    legacy_key = f"{current_file}|{current_item_id}.tooltip.{index}"
                    stable_key = (
                        f"{current_file}|tooltip:{current_item_id}:"
                        f"{current_tooltip_index}"
                    )
                    index += 1
                    key = (
                        stable_key if stable_key in current_translations else legacy_key
                    )
                    if key in current_translations:
                        current_used_keys.add(key)
                        return replace_text_in_text_obj(
                            text_match.group(0), current_translations[key]
                        )
                    return text_match.group(0)

                quote_class = "['" + chr(34) + "]"
                text_call_pattern = (
                    rf"Text\.\w+\s*\(\s*{quote_class}.*?{quote_class}\s*\)"
                )
                new_tooltip_block = re.sub(
                    text_call_pattern,
                    repl_text_call,
                    tooltip_block,
                    flags=re.DOTALL,
                )

                if new_tooltip_block == tooltip_block:
                    out.append(full[match.start() : end_idx + 1])
                else:
                    args[1] = new_tooltip_block
                    out.append(".add(" + ", ".join(args) + ")")
                last = end_idx + 1

            out.append(full[last:])
            return "".join(out)

        content = patch_itemevents_tooltips(content)

        # ----------------------------
        # ✅ 寫出 patched js
        # ----------------------------
        out_path = out_root / script_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        if content != original_content:
            patched_js_files += 1
            if session:
                log_info(f"✔ Patched {out_path}")

        applied_translation_keys.update(used_translation_keys)
        missing_translation_keys.update(source_translation_keys - used_translation_keys)

        done += 1
        if session:
            p = progress_base + (done / total) * progress_span
            session.set_progress(min(max(p, 0.0), 0.999))

    msg = "🎉 所有翻譯已成功插回！"
    if session:
        log_info(msg)
        session.set_progress(min(progress_base + progress_span, 0.999))

    # ----------------------------
    # 📦 注入結果統計摘要（關鍵）
    # ----------------------------
    summary = (
        f"📦 [KubeJS-INJECT] 完成注入統計："
        f"lang輸出={wrote_lang_files} | "
        f"patch_js={patched_js_files}"
    )

    # logger / UI 兩邊都顯示
    log_info(summary)

    return {
        "kubejs_dir": str(orig_root),
        "translated_dir": str(trans_root),
        "final_output_dir": str(out_root),
        "patched_js_files": patched_js_files,
        "wrote_lang_files": wrote_lang_files,
        "applied_translation_keys": sorted(applied_translation_keys),
        "missing_translation_keys": sorted(missing_translation_keys),
    }


if __name__ == "__main__":
    inject()
