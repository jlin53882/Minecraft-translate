"""translation_tool/plugins/kubejs/kubejs_tooltip_inject.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import os
import json
import re
from collections import defaultdict

from pathlib import Path
from translation_tool.utils.log_unit import log_info, log_error

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

# ---------------- 工具 ----------------

def split_js_args(s):
    """解析 JavaScript 函式參數"""
    args = []
    buf = ""
    depth = 0
    quote = None

    for c in s:
        if quote:
            buf += c
            if c == quote:
                quote = None
            continue

        if c in ("'", '"'):
            quote = c
            buf += c
            continue

        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1

        if c == "," and depth == 0:
            args.append(buf.strip())
            buf = ""
        else:
            buf += c

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

def replace_text_in_text_obj(expr, new_text):
    """

    """
    return re.sub(
        r'(Text\.\w+\(\s*[\'"])(.+?)([\'"]\s*\))',
        lambda m: m.group(1) + new_text + m.group(3),
        expr,
        count=1,
    )

def extract_array_strings(expr):
    """從表達式中擷取陣列字串"""
    return re.findall(r"[\"']([^\"']+)[\"']", expr)

def replace_array(expr, new_values):
    """

    """
    parts = split_js_args(expr[1:-1])
    out = []

    for i, part in enumerate(parts):
        part = part.strip()
        if part.startswith(("'", '"')) and i < len(new_values):
            out.append(f'"{new_values[i]}"')
        else:
            out.append(part)

    return "[" + ", ".join(out) + "]"

def to_js_name(json_name):
    """

    """
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
    """
    ✅ 新版：由 services/UI 呼叫
    - original_dir: UI 傳入的模組包 root 或 kubejs/ 目錄（會自動 resolve）
    - translated_dir: 抽取/翻譯後的 JSON 目錄（例如 .../Output/kubejs/待翻譯）
    - final_output_dir: 插回後輸出的目錄（例如 .../Output/kubejs/完成）
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

        id_counters = defaultdict(int)
        auto_id = 1  # ✅ 必須跟 extractor 一樣從 1 開始，先跑 event.add，再跑 scene.text，最後跑 ItemEvents

        # ----------------------------
        # 1) Patch event.add(...)
        # ----------------------------
        def repl_event_add(m: re.Match) -> str:
            """

        
            """
            nonlocal auto_id
            arg_str = m.group(1)
            args = split_js_args(arg_str)
            new_args = list(args)

            # 單參數 -> auto.N
            if len(args) == 1:
                key = f"{original_js}|auto.{auto_id}"
                if key in translations:
                    # 保留雙引號輸出，避免單引號/跳脫亂掉
                    new_args[0] = json.dumps(translations[key], ensure_ascii=False)
                auto_id += 1

            # ID + tooltip（你原本的格式：file|item_id.n 或 file|item_id.n.idx）
            elif len(args) == 2:
                item_id = strip_quotes(args[0])
                n = id_counters[item_id]
                id_counters[item_id] += 1

                if args[1].strip().startswith("Text."):
                    key = f"{original_js}|{item_id}.{n}"
                    if key in translations:
                        new_args[1] = replace_text_in_text_obj(
                            args[1], translations[key]
                        )

                elif args[1].strip().startswith("["):
                    if "Text." in args[1]:
                        idx = 0

                        def repl_text(mm: re.Match) -> str:
                            """

                            - 主要包裝：`group`

                        
                            """
                            nonlocal idx
                            key = f"{original_js}|{item_id}.{n}.{idx}"
                            idx += 1
                            if key in translations:
                                return replace_text_in_text_obj(
                                    mm.group(0), translations[key]
                                )
                            return mm.group(0)

                        new_args[1] = re.sub(
                            r"Text\.\w+\s*\(\s*['\"].*?['\"]\s*\)",
                            repl_text,
                            args[1],
                            flags=re.S,
                        )
                    else:
                        old = extract_array_strings(args[1])
                        new_vals = []
                        for i, o in enumerate(old):
                            key = f"{original_js}|{item_id}.{n}.{i}"
                            new_vals.append(translations.get(key, o))
                        new_args[1] = replace_array(args[1], new_vals)

            return "event.add(" + ", ".join(new_args) + ")"

        content = re.sub(r"event\.add\s*\((.+?)\)", repl_event_add, content, flags=re.S)

        # ----------------------------
        # 2) Patch Ponder: scene.text(...)
        #    key: file|scene.{auto_id}  (✅ 接續 event.add 用掉的 auto_id)
        # ----------------------------
        def repl_scene_text(m: re.Match) -> str:
            """

        
            """
            nonlocal auto_id
            arg_str = m.group(1)
            args = split_js_args(arg_str)
            if len(args) < 2:
                return m.group(0)

            # extractor：text = strip_quotes(args[1]) -> clean_text -> if text: key = file|scene.auto_id ; auto_id++
            raw_text_expr = args[1].strip()
            text_candidate = ""

            # A) 'string' / "string"
            if (raw_text_expr.startswith("'") and raw_text_expr.endswith("'")) or (
                raw_text_expr.startswith('"') and raw_text_expr.endswith('"')
            ):
                text_candidate = strip_quotes(raw_text_expr)

            # B) Text.xxx("string")（有些 Ponder 也會這樣寫）
            elif raw_text_expr.startswith("Text."):
                m2 = re.search(r"['\"](.+?)['\"]", raw_text_expr, flags=re.S)
                if m2:
                    text_candidate = m2.group(1)

            # 只有真的有字才算一筆，並且 auto_id 要++（跟 extractor 對齊）
            if clean_text(text_candidate):
                key = f"{original_js}|scene.{auto_id}"
                if key in translations:
                    new_text = translations[key]

                    # 依照原本表達式型態替換
                    if raw_text_expr.startswith("Text."):
                        args[1] = replace_text_in_text_obj(args[1], new_text)
                    else:
                        # 一律輸出成 JSON 字串（雙引號 + 正確跳脫）
                        args[1] = json.dumps(new_text, ensure_ascii=False)

                auto_id += 1

            return "scene.text(" + ", ".join(args) + ")"

        content = re.sub(
            r"scene\.text\s*\((.+?)\)", repl_scene_text, content, flags=re.S
        )

        # ----------------------------
        # 3) Patch ItemEvents Tooltips: .add(...)
        #    key: file|{item_id}.tooltip.{idx}
        # ----------------------------
        def extract_call_args_with_end(
            text: str, start: int
        ) -> tuple[str | None, int | None]:
            # start 指向 '(' 後面的位置
            """

        
            """
            depth = 1
            i = start
            buf = ""
            while i < len(text):
                ch = text[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return buf, i  # buf = args, i = ')' 的 index
                buf += ch
                i += 1
            return None, None

        def patch_itemevents_tooltips(full: str) -> str:
            """

        
            """
            out = []
            last = 0

            for m in re.finditer(r"\.add\s*\(", full):
                args_str, end_idx = extract_call_args_with_end(full, m.end())
                if args_str is None or end_idx is None:
                    continue

                # 把 .add( 之前的內容先塞進去
                out.append(full[last : m.start()])

                args = split_js_args(args_str)
                if len(args) < 2:
                    # 還原原樣
                    out.append(full[m.start() : end_idx + 1])
                    last = end_idx + 1
                    continue

                raw_id = args[0].strip()
                if (raw_id.startswith("'") and raw_id.endswith("'")) or (
                    raw_id.startswith('"') and raw_id.endswith('"')
                ):
                    item_id = raw_id[1:-1]
                else:
                    item_id = raw_id  # array / regex 原樣保留（跟 extractor 一樣）

                tooltip_block = args[1]
                idx = 0

                def repl_text_call(mm: re.Match) -> str:
                    """

                
                    """
                    nonlocal idx
                    key = f"{original_js}|{item_id}.tooltip.{idx}"
                    idx += 1
                    if key in translations:
                        return replace_text_in_text_obj(mm.group(0), translations[key])
                    return mm.group(0)

                # 只替換 Text.xxx("...") 這種
                new_tooltip_block = re.sub(
                    r"Text\.\w+\s*\(\s*['\"].*?['\"]\s*\)",
                    repl_text_call,
                    tooltip_block,
                    flags=re.S,
                )

                args[1] = new_tooltip_block

                # 重組 .add(...)
                rebuilt = ".add(" + ", ".join(args) + ")"
                out.append(rebuilt)

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

        patched_js_files += 1
        msg = f"✔ Patched {out_path}"
        if session:
            log_info(msg)

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
