"""translation_tool/plugins/md/md_inject_qa.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# md_inject_qa.py
# ------------------------------------------------------------
# 方案 B（實際使用）：以「原始 .md 行結構」為骨架，保留翻譯段落的多行寫回
#
# 做法：
#   - 讀取原始 .md（輸入資料夾）
#   - 讀取翻譯後 JSON（例如：輸出資料夾/LM翻譯後/**/*.json）
#   - 對每個 item（start_line ~ end_line 範圍）：
#       1) 找出該範圍內所有「可翻譯的文字行」
#          - 跳過 §token 行（§align / §rule / §recipe ...）
#          - 空行與排版行一律保留不動
#       2) 使用翻譯後 JSON 的 items[].text
#          - 保留其中的換行（\n）
#          - 將其視為「要寫回的文字行序列」
#       3) 逐行一對一寫回原 md 的文字行位置
#          - 保留原本的縮排
#          - 不插入、不刪除行，避免破壞原始行結構
#       4) 若翻譯後行數少於原文字行數：
#          - 清空多餘的原文字行內容（保留行本身）
#          - 避免殘留未翻譯的英文
#       5) 若翻譯後行數多於原文字行數：
#          - 目前採保守策略：多餘行不插入
#          - 避免破壞 token / 排版結構
#
# 輸出：
#   - 寫到：<輸出根目錄>/完成/<相對路徑>
#   - 相對路徑中的語言資料夾會自動由 en_us 映射為 zh_tw
#     （支援 _en_us / 大小寫變形）
#
# 設計取向：
#   - 本腳本假設翻譯後 JSON 的 items[].text 代表「完整段落內容」
#   - 翻譯結果可為多行（例如 Side note、說明段落）
#   - 不對段落內容做 flatten，避免多行段落被壓成一行
#   - 以「不破壞原 md 骨架」與「不殘留英文」為最高優先
# ------------------------------------------------------------


from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Optional


# ======== 跟抽取器一致：哪些行視為 token 行（不可翻、不可改） ========
RE_HARD_SPLIT_LINE = re.compile(r"^\s*§(rule\{|recipe\[|entity\[)", re.I)
RE_SOFT_SKIP_LINE = re.compile(r"^\s*§(align:|stack\[)", re.I)


# ======== 語言資料夾段落映射：en_us -> zh_tw（支援 _en_us、大小寫） ========
RE_LANG_SEG = re.compile(r"^(_?)([a-z]{2}_[a-z]{2})$", re.IGNORECASE)

def map_lang_in_rel_path(rel_path: str, src_lang: str = "en_us", dst_lang: str = "zh_tw") -> str:
    """
    只替換「路徑 segment」剛好等於語言碼的那一段，避免誤傷其他字串。
    - 支援: en_us / EN_US / _en_us / _EN_US
    - 保留底線前綴: _en_us -> _zh_tw
    """
    # source_md 在 extract 端是 rel_md.replace("\\", "/")，因此這裡用 "/" 分割最穩 :contentReference[oaicite:1]{index=1}
    parts = rel_path.replace("\\", "/").split("/")
    src_norm = src_lang.lower()
    dst_norm = dst_lang.lower()

    for i, seg in enumerate(parts):
        m = RE_LANG_SEG.match(seg)
        if not m:
            continue
        prefix_us, lang = m.group(1), m.group(2).lower()
        if lang == src_norm:
            parts[i] = f"{prefix_us}{dst_norm}"

    return "/".join(parts)


# ======== 語言資料夾段落映射：允許 en_us -> zh_tw，也允許來源是 zh_tw ========
RE_LANG_SEG = re.compile(r"^(_?)([a-z]{2}_[a-z]{2})$", re.IGNORECASE)

def map_lang_in_rel_path_allow_zh(rel_path: str,
                                 src_lang: str = "en_us",
                                 dst_lang: str = "zh_tw") -> tuple[str, str]:
    """
    回傳 (mapped_rel_path, status)

    status:
      - "SRC_EN": 找到 en_us/_en_us（大小寫不拘），已轉成 zh_tw/_zh_tw
      - "SRC_ZH": 找到 zh_tw/_zh_tw（大小寫不拘），保持 zh_tw（可用於重寫/覆蓋）
      - "NO_LANG": 找不到語言段
      - "OTHER_LANG": 找到語言段但不是 en_us/zh_tw（例如 ru_ru/ja_jp）
    """
    parts = rel_path.replace("\\", "/").split("/")
    src_norm = src_lang.lower()
    dst_norm = dst_lang.lower()

    found_any_lang = False
    found_src_en = False
    found_src_zh = False
    found_other = False

    for i, seg in enumerate(parts):
        m = RE_LANG_SEG.match(seg)
        if not m:
            continue
        prefix_us, lang = m.group(1), m.group(2).lower()
        found_any_lang = True

        if lang == src_norm:
            parts[i] = f"{prefix_us}{dst_norm}"
            found_src_en = True
        elif lang == dst_norm:
            found_src_zh = True
        else:
            found_other = True

    mapped = "/".join(parts)

    if not found_any_lang:
        return mapped, "NO_LANG"
    if found_src_en:
        return mapped, "SRC_EN"
    if found_src_zh and not found_other:
        return mapped, "SRC_ZH"
    return mapped, "OTHER_LANG"


# 若一整行幾乎都是 §token，也視為 token 行（避免誤判）
RE_MOSTLY_TOKEN_LINE = re.compile(r"^\s*(§[0-9a-zA-Z]+\S*)\s*(§[0-9a-zA-Z]+\S*)*\s*$")

def is_token_line(line: str) -> bool:
    """判斷此函式的工作（細節以程式碼為準）。
    
    - 主要包裝：`strip`
    
    回傳：依函式內 return path。
    """
    s = line.strip()
    if not s:
        return False  # 空行不是 token 行（要保留）
    if RE_HARD_SPLIT_LINE.match(s):
        return True
    if RE_SOFT_SKIP_LINE.match(s):
        return True
    if RE_MOSTLY_TOKEN_LINE.match(s):
        return True
    return False


def is_text_line_old(line: str) -> bool:
    """
    判斷「原始 md」中的某一行是否視為可翻文字行：
      - 非空行
      - 非 token 行
    """
    return bool(line.strip()) and (not is_token_line(line))

def is_text_line(line: str) -> bool:
    """
    判斷「原始 md」中的某一行是否視為可回寫的文字行：
      - 非空行
      - 非 token 行（§...）
      - 非 Markdown 圖片行：![alt](url)
      - 非純 component/tag 行：<ItemImage ...>、<ImportStructure ...> 等
    """
    s = line.strip()
    if not s:
        return False  # 空行不是文字行（要保留）
    if is_token_line(line):
        return False  # token 行不可改

    # Markdown 圖片行：整行不應被替換
    if re.match(r"^\s*!\[.*?\]\(.*?\)\s*$", line):
        return False

    # 純 component/tag 行：整行不應被替換
    if re.match(r"^\s*<[^>]+>\s*$", line):
        return False

    return True


def flatten_for_md(text: str) -> str:
    """
    把 JSON 內「為了可讀性」可能新增的一堆換行，還原成段落級結構：
      - 段落之間保留空行（空行代表段落分隔）
      - 段落內如果有多行，合併成一行（用空格接）
    這樣回寫時比較不會把原 md 洗成怪格式。
    """
    lines = [l.rstrip() for l in text.splitlines()]
    out: List[str] = []
    buf: List[str] = []

    def flush_buf():
        """處理此函式的工作（細節以程式碼為準）。
        
        回傳：None
        """
        nonlocal buf
        if buf:
            out.append(" ".join([x.strip() for x in buf if x.strip()]))
            buf = []

    for l in lines:
        if not l.strip():
            flush_buf()
            # 保留段落空行（不要連續堆很多空行）
            if out and out[-1] != "":
                out.append("")
            elif not out:
                out.append("")
        else:
            buf.append(l)

    flush_buf()

    # 收斂：移除頭尾空行
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()

    return "\n".join(out)


@dataclass
class Item:
    """Item 類別。

    用途：封裝與 Item 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    source_md: str
    start_line: int
    end_line: int
    text: str


def load_items_from_json(json_path: Path) -> Tuple[str, List[Item]]:
    """
    讀一個 md_pending_blocks_v1 JSON，回傳 source_md 與 items
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    source_md = data["source_md"]
    items: List[Item] = []
    for it in data.get("items", []):
        items.append(Item(
            source_md=source_md,
            start_line=int(it["start_line"]),
            end_line=int(it["end_line"]),
            text=str(it["text"]),
        ))
    return source_md, items


def apply_item_to_md_lines_old(md_lines: List[str], item: Item) -> None:
    """
    在 md_lines 上原地套用一個 item（只替換文字行，token/空行不動）
    """
    # Python list index：0-based
    a = max(item.start_line - 1, 0)
    b = min(item.end_line - 1, len(md_lines) - 1)
    if a > b:
        return

    # 1) 找範圍內所有「文字行」的位置
    text_line_indices: List[int] = []
    for idx in range(a, b + 1):
        if is_text_line(md_lines[idx]):
            text_line_indices.append(idx)

    if not text_line_indices:
        return

    # 2) 把翻譯文本 flatten 成段落級，再拆成「段落行」
    #    注意：這裡的「段落行」是指我們準備拿來填回原 md 的文字行集合
    flat = flatten_for_md(item.text)

    # 3) 將 flat 轉成要寫回的「文字行序列」
    #    - 原 md 的文字行可能本來是一行一段，也可能多行
    #    - 我們採保守策略：用 flat 的每一行去對應原本的文字行
    new_text_lines = flat.splitlines()
    # 去掉空白行（空白行代表段落分隔；原 md 的空白行我們不動）
    new_text_lines = [x for x in new_text_lines if x.strip()]

    # 4) 一對一替換：只替換既有文字行數量範圍
    n = min(len(text_line_indices), len(new_text_lines))
    for i in range(n):
        idx = text_line_indices[i]
        # 保留原本縮排（如果有的話）
        lead = re.match(r"^\s*", md_lines[idx]).group(0)
        md_lines[idx] = lead + new_text_lines[i]

    # 5) 若翻譯後行數少於原文字行：剩下的文字行維持原樣（最保守，避免刪內容）
    #    若翻譯後行數多於原文字行：多的先不插入（避免破壞結構）
    #    之後你若想更進階：可把多的插到第一個空行前，但要小心 diff。
    #    目前先穩定為主。


def apply_item_to_md_lines(md_lines: List[str], item: Item) -> None:
    """
    在 md_lines 上原地套用一個 item（只替換文字行，token/空行不動）

    ✅ 方案 B（推薦給你的 pipeline）：
      - 抽取是 block（start_line~end_line），翻譯 text 也保留原本的 \n 多行
      - 因此寫回時也應「保留多行」逐行對齊回填
      - 另外補一個保護：若翻譯行數少於原文字行數，清空剩餘原文行，避免殘留英文

    為什麼不再 flatten_for_md？
      - flatten_for_md 會把段落內多行合併成一行（用空格接）
      - 一旦原文是多行、翻譯被 flatten 成一行，就只會替換第一行，其餘行會殘留（你遇到的狀況）
    """
    # Python list index：0-based
    a = max(item.start_line - 1, 0)
    b = min(item.end_line - 1, len(md_lines) - 1)
    if a > b:
        return

    # 1) 找範圍內所有「可翻文字行」的位置（空行與 token 行不動）
    text_line_indices: List[int] = []
    for idx in range(a, b + 1):
        if is_text_line(md_lines[idx]):
            text_line_indices.append(idx)

    if not text_line_indices:
        return

    # 2) 直接使用翻譯後文本的換行（保留多行結構）
    #    - item.text 可能包含 \n（例如 Side note 三行）
    #    - 我們將其視為「要寫回的文字行序列」
    new_text_lines = item.text.splitlines()

    # 3) 去掉空白行（空白行代表段落分隔；原 md 的空白行我們不動）
    #    這裡只處理要填回「文字行」的內容
    new_text_lines = [x for x in new_text_lines if x.strip()]

    # 4) 一對一替換：只替換既有文字行數量範圍（不插入、不刪除，保持行結構穩定）
    n = min(len(text_line_indices), len(new_text_lines))
    for i in range(n):
        idx = text_line_indices[i]
        # 保留原本縮排（如果有的話）
        lead = re.match(r"^\s*", md_lines[idx]).group(0)
        md_lines[idx] = lead + new_text_lines[i]

    # 5) ✅ 關鍵修正：若翻譯行數少於原文字行數，清空剩餘原文字行，避免殘留英文
    #    - 不刪除行（避免行號漂移與破壞原始骨架）
    #    - 只把內容清成空（保留縮排），讓原文不會「漏在下面」
    for j in range(n, len(text_line_indices)):
        idx = text_line_indices[j]
        lead = re.match(r"^\s*", md_lines[idx]).group(0)
        md_lines[idx] = lead

    # 6) 若翻譯行數多於原文字行數：多的先不插入（避免破壞原 md 結構）
    #    之後若要更進階，可考慮「在該區塊最後一個文字行後插入」，但要非常小心 token/排版。


def iter_json_files(root: Path):
    """處理此 generator 並逐步回報進度（yield update dict）。
    
    - 主要包裝：`rglob`
    """
    for p in root.rglob("*.json"):
        if p.is_file():
            yield p


def main():
    """處理此函式的工作（細節以程式碼為準）。
    
    - 主要包裝：`strip`
    
    回傳：None
    """
    print("=== md 寫回（方案 A：保留原檔骨架，只替換文字行）===")

    src_md_root = input("原始 .md 輸入資料夾（原檔所在根目錄）: ").strip().strip('"').strip("'")
    json_root = input("翻譯後 JSON 資料夾（例如：輸出/LM翻譯後）: ").strip().strip('"').strip("'")
    out_root = input("輸出資料夾（會輸出到：<輸出>/完成/.../*.md）: ").strip().strip('"').strip("'")

    src_root = Path(src_md_root).expanduser().resolve()
    jroot = Path(json_root).expanduser().resolve()
    oroot = Path(out_root).expanduser().resolve()
    out_done = oroot / "完成"
    out_done.mkdir(parents=True, exist_ok=True)

    if not src_root.exists() or not src_root.is_dir():
        print(f"[錯誤] 原始 .md 資料夾不存在：{src_root}")
        return
    if not jroot.exists() or not jroot.is_dir():
        print(f"[錯誤] JSON 資料夾不存在：{jroot}")
        return

    json_files = list(iter_json_files(jroot))
    if not json_files:
        print("[提示] 找不到任何 .json")
        return

    wrote = 0
    skipped = 0

    for jp in json_files:
        try:
            source_md, items = load_items_from_json(jp)
        except Exception as e:
            print(f"[略過] JSON 讀取失敗：{jp} ({e})")
            skipped += 1
            continue

        src_md_path = src_root / source_md
        if not src_md_path.exists():
            print(f"[略過] 找不到對應原 md：{src_md_path}")
            skipped += 1
            continue

        # 讀原 md（保留原本每一行）
        try:
            original_text = src_md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            original_text = src_md_path.read_text(encoding="utf-8", errors="replace")

        ends_with_nl = original_text.endswith("\n")
        md_lines = original_text.splitlines(keepends=False)

        # 套用所有 items（依行號範圍回填）
        for it in items:
            apply_item_to_md_lines(md_lines, it)

        # 寫出到 <輸出>/完成/<相對路徑>
        #out_path = out_done / source_md
        # 寫出到 <輸出>/完成/<相對路徑>（但語言段落 en_us -> zh_tw）
        #out_rel = map_lang_in_rel_path(source_md, src_lang="en_us", dst_lang="zh_tw")

        # 寫出到 <輸出>/完成/<相對路徑>
        # - 來源是 en_us/_en_us：輸出轉成 zh_tw/_zh_tw
        # - 來源是 zh_tw/_zh_tw：保持 zh_tw（可用於重寫/覆蓋）
        # - 其他語言或找不到語言段：跳過，避免污染輸出樹
        out_rel, status = map_lang_in_rel_path_allow_zh(source_md, src_lang="en_us", dst_lang="zh_tw")
        if status not in ("SRC_EN", "SRC_ZH"):
            print(f"[SKIP:{status}] {source_md}")
            continue

        out_path = out_done / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_text = "\n".join(md_lines) + ("\n" if ends_with_nl else "")
        out_path.write_text(out_text, encoding="utf-8")

        wrote += 1

    print("\n=== 完成 ===")
    print(f"原始根目錄：{src_root}")
    print(f"JSON 根目錄：{jroot}")
    print(f"輸出根目錄：{out_done}")
    print(f"成功寫出：{wrote}")
    print(f"略過：{skipped}")


if __name__ == "__main__":
    main()
