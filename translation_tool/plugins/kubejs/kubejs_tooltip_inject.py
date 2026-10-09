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
) -> str:
    """以平衡括號邊界替換具名 JavaScript 呼叫的引數。

    Args:
        content: JavaScript 原始內容。
        function_name: 不含呼叫括號的函式名稱。
        replace_args: 接收完整引數並回傳替換後內容的函式。

    Returns:
        僅替換指定呼叫引數後的完整 JavaScript 內容。
    """
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    output: list[str] = []
    cursor = 0
    search_from = 0
    while True:
        match = pattern.search(content, search_from)
        if match is None:
            break
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


# ---------------- 主流程 ----------------


def inject(
    original_dir: str,
    translated_dir: str,
    final_output_dir: str,
    *,
    session=None,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
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

    total = max(1, len(json_files))
    done = 0

    patched_js_files = 0
    wrote_lang_files = 0

    for json_path in json_files:
        file = os.path.basename(json_path)
        rel = os.path.relpath(os.path.dirname(json_path), trans_root)

        # ---------------- Lang JSON ----------------
        if "/lang/" in json_path.replace("\\", "/"):
            # ✅ 把 LM翻譯後的 lang 結果輸出到 完成（保留相對路徑）
            rel_file = Path(os.path.relpath(json_path, trans_root))
            out_path = out_root / rel_file
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(Path(json_path).read_bytes())

            if session:
                log_info(f"✔ Lang → {out_path}")
            wrote_lang_files += 1

            done += 1
            if session:
                p = progress_base + (done / total) * progress_span
                session.set_progress(min(max(p, 0.0), 0.999))
            continue

        # ---------------- KubeJS Tooltips ----------------
        original_js = to_js_name(file)
        js_path = orig_root / rel / original_js

        if not js_path.exists():
            # 找不到原始 js 就跳過（但也算進 progress）
            if session:
                log_info(f"⏭️ 找不到原始 JS，略過：{js_path}")
            done += 1
            if session:
                p = progress_base + (done / total) * progress_span
                session.set_progress(min(max(p, 0.0), 0.999))
            continue

        with open(json_path, "r", encoding="utf-8") as f:
            translations = json.load(f)

        # ✅ 改成整檔處理（避免跨行 / chain call / scene.text 插不回）
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
        original_content = content

        id_counters = defaultdict(int)
        auto_id = 1  # ✅ 必須跟 extractor 一樣從 1 開始，先跑 event.add，再跑 scene.text，最後跑 ItemEvents

        # ----------------------------
        # 1) Patch event.add(...)
        # ----------------------------
        def repl_event_add(
            arg_str: str,
            *,
            source_file: str = original_js,
            translation_map: dict = translations,
            counters: defaultdict[str, int] = id_counters,
        ) -> str:
            """只在 event.add 引數實際改變時回傳重建後的引數。"""
            nonlocal auto_id
            args = split_js_args(arg_str)
            new_args = list(args)
            changed = False

            # 單參數 -> auto.N
            if len(args) == 1:
                key = f"{source_file}|auto.{auto_id}"
                if key in translation_map:
                    translated = translation_map[key]
                    if _decode_js_string_literal(args[0]) != translated:
                        replacement = json.dumps(translated, ensure_ascii=False)
                        new_args[0] = replacement
                        changed = True
                auto_id += 1

            # ID + tooltip（檔案|item_id.n 或檔案|item_id.n.idx）
            elif len(args) == 2:
                item_id = strip_quotes(args[0])
                number = counters[item_id]
                counters[item_id] += 1

                if args[1].strip().startswith("Text."):
                    key = f"{source_file}|{item_id}.{number}"
                    if key in translation_map:
                        replacement = replace_text_in_text_obj(
                            args[1], translation_map[key]
                        )
                        if replacement != args[1]:
                            new_args[1] = replacement
                            changed = True

                elif args[1].strip().startswith("["):
                    if "Text." in args[1]:
                        index = 0

                        def repl_text(match: re.Match[str]) -> str:
                            """依序替換 tooltip 陣列中的 Text 字串。"""
                            nonlocal index
                            key = f"{source_file}|{item_id}.{number}.{index}"
                            index += 1
                            if key in translation_map:
                                return replace_text_in_text_obj(
                                    match.group(0), translation_map[key]
                                )
                            return match.group(0)

                        quote_class = "['" + chr(34) + "]"
                        text_call_pattern = (
                            rf"Text\.\w+\s*\(\s*{quote_class}.*?{quote_class}\s*\)"
                        )
                        replacement = re.sub(
                            text_call_pattern,
                            repl_text,
                            args[1],
                            flags=re.DOTALL,
                        )
                    else:
                        old_values = extract_array_strings(args[1])
                        new_values = [
                            translation_map.get(
                                f"{source_file}|{item_id}.{number}.{index}",
                                old_value,
                            )
                            for index, old_value in enumerate(old_values)
                        ]
                        replacement = replace_array(args[1], new_values)

                    if replacement != args[1]:
                        new_args[1] = replacement
                        changed = True

            return ", ".join(new_args) if changed else arg_str

        content = _replace_js_call_arguments(content, "event.add", repl_event_add)

        # ----------------------------
        # 2) Patch Ponder: scene.text(...)
        #    key: file|scene.{auto_id}  (✅ 接續 event.add 用掉的 auto_id)
        # ----------------------------
        def repl_scene_text(
            arg_str: str,
            *,
            source_file: str = original_js,
            translation_map: dict = translations,
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
                key = f"{source_file}|scene.{auto_id}"
                if key in translation_map:
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
        ) -> str:
            """修補 ItemEvents Tooltip，無變更時保留呼叫原始文字。"""
            out = []
            last = 0

            for match in re.finditer(r"\.add\s*\(", full):
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

                def repl_text_call(
                    text_match: re.Match[str],
                    *,
                    current_file: str = source_file,
                    current_item_id: str = item_id,
                    current_translations: dict = translation_map,
                ) -> str:
                    """替換一個 ItemEvents Tooltip 字串並維持原索引順序。"""
                    nonlocal index
                    key = f"{current_file}|{current_item_id}.tooltip.{index}"
                    index += 1
                    if key in current_translations:
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
        out_path = out_root / rel / original_js
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        if content != original_content:
            patched_js_files += 1
            if session:
                log_info(f"✔ Patched {out_path}")

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
    }


if __name__ == "__main__":
    inject()
