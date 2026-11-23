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

from translation_tool.plugins.md.md_extract_qa import (
    is_translatable_text_line,
    map_rel_lang_path,
    neutral_guide_index,
)
from translation_tool.plugins.shared.rich_text_shield import (
    ShieldPiece,
    unshield_text,
)

# ======== 跟抽取器一致：哪些行視為 token 行（不可翻、不可改） ========
RE_HARD_SPLIT_LINE = re.compile(r"^\s*§(rule\{|recipe\[|entity\[)", re.IGNORECASE)
RE_SOFT_SKIP_LINE = re.compile(r"^\s*§(align:|stack\[)", re.IGNORECASE)

# ======== 語言資料夾段落映射：en_us -> zh_tw（支援 _en_us、大小寫） ========
RE_LANG_SEG = re.compile(r"^(_?)([a-z]{2}_[a-z]{2})$", re.IGNORECASE)


def map_lang_in_rel_path(
    rel_path: str, src_lang: str = "en_us", dst_lang: str = "zh_tw"
) -> str:
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


def map_lang_in_rel_path_allow_zh(
    rel_path: str, src_lang: str = "en_us", dst_lang: str = "zh_tw"
) -> tuple[str, str]:
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

    if src_norm == "en_us" and neutral_guide_index(parts) is not None:
        # 無語言資料夾的指南：英文沒有語言段，輸出放進指南資料夾下的 _zh_tw
        return map_rel_lang_path(rel_path, src_norm, dst_norm), "SRC_EN"

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
    """判斷行是否為 Minecraft 格式 token 行"""
    s = line.strip()
    if not s:
        return False  # 空行不是 token 行（要保留）
    if RE_HARD_SPLIT_LINE.match(s):
        return True
    if RE_SOFT_SKIP_LINE.match(s):
        return True
    return bool(RE_MOSTLY_TOKEN_LINE.match(s))


def is_text_line(line: str) -> bool:
    """
    判斷「原始 md」中的某一行是否視為可回寫的文字行。

    與抽取器共用 is_translatable_text_line：抽取器認定的文字行（含 §lNote:§r 這種夾著文字的
    單字格式行）才會進區塊，注入端必須用同一把尺，否則區塊內的文字行數與譯文行數對不上，
    逐行回填會整個錯位。
    """
    return bool(line.strip()) and is_translatable_text_line(line)


def flatten_for_md(text: str) -> str:
    """
    把 JSON 內「為了可讀性」可能新增的一堆換行，還原成段落級結構：
      - 段落之間保留空行（空行代表段落分隔）
      - 段落內如果有多行，合併成一行（用空格接）
    這樣回寫時比較不會把原 md 洗成怪格式。
    """
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    buf: list[str] = []

    def flush_buf():
        """將緩衝區內容刷新到輸出。"""
        nonlocal buf
        if buf:
            out.append(" ".join([x.strip() for x in buf if x.strip()]))
            buf = []

    for line in lines:
        if not line.strip():
            flush_buf()
            # 保留段落空行（不要連續堆很多空行）
            if not out or out[-1] != "":
                out.append("")
        else:
            buf.append(line)

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
    _shields: list = None

    def __post_init__(self):
        if self._shields is None:
            self._shields = []


def load_items_from_json(json_path: Path) -> tuple[str, list[Item]]:
    """
    讀一個 md_pending_blocks_v1 JSON，回傳 source_md 與 items
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    source_md = data["source_md"]
    items: list[Item] = []
    for it in data.get("items", []):
        items.append(
            Item(
                source_md=source_md,
                start_line=int(it["start_line"]),
                end_line=int(it["end_line"]),
                text=str(it["text"]),
                _shields=[ShieldPiece(**p) for p in it.get("_shields", [])],
            )
        )
    return source_md, items

    # 5) 若翻譯後行數少於原文字行：剩下的文字行維持原樣（最保守，避免刪內容）
    #    若翻譯後行數多於原文字行：多的先不插入（避免破壞結構）
    #    之後你若想更進階：可把多的插到第一個空行前，但要小心 diff。
    #    目前先穩定為主。


def apply_item_to_md_lines(md_lines: list[str], item: Item) -> str:
    """
    在 md_lines 上原地套用一個 item（只改區塊內的文字行，token/空行不動）。

    回傳：
      - "applied"：譯文行數與原文字行數相同，逐行替換
      - "line_count_changed"：行數不同；多出的譯文插在區塊最後一個文字行後，
        不足時刪掉多餘的原文字行（不留空白行：空白行會把一個段落切成兩個）
      - "skipped_empty"：譯文沒有任何內容，保留原文（寧可留英文也不刪內容）
      - "skipped_range"：行號範圍內沒有可回寫的文字行

    插入／刪行會讓後面的行號位移，所以多個 item 必須由後往前套用（見
    apply_items_to_md_lines）。
    """
    a = max(item.start_line - 1, 0)
    b = min(item.end_line - 1, len(md_lines) - 1)
    if a > b:
        return "skipped_range"
    text_line_indices = [idx for idx in range(a, b + 1) if is_text_line(md_lines[idx])]
    if not text_line_indices:
        return "skipped_range"

    # 譯文可能含換行（多行區塊）；空白行代表段落分隔，原 md 的空白行不動，所以不寫回
    new_text_lines = [
        x for x in unshield_text(item.text, item._shields).splitlines() if x.strip()
    ]
    if not new_text_lines:
        return "skipped_empty"

    n = min(len(text_line_indices), len(new_text_lines))
    for i in range(n):
        idx = text_line_indices[i]
        lead = re.match(r"^\s*", md_lines[idx]).group(0)  # 保留原本縮排
        md_lines[idx] = lead + new_text_lines[i]

    if len(new_text_lines) > n:
        last = text_line_indices[-1]
        lead = re.match(r"^\s*", md_lines[last]).group(0)
        md_lines[last + 1 : last + 1] = [lead + x for x in new_text_lines[n:]]
        return "line_count_changed"
    if len(text_line_indices) > n:
        for idx in reversed(text_line_indices[n:]):
            del md_lines[idx]
        return "line_count_changed"
    return "applied"


def apply_items_to_md_lines(md_lines: list[str], items: list[Item]) -> dict[str, int]:
    """由後往前套用所有 items，回傳各結果代碼的次數。"""
    counts: dict[str, int] = {}
    for it in sorted(items, key=lambda x: x.start_line, reverse=True):
        status = apply_item_to_md_lines(md_lines, it)
        counts[status] = counts.get(status, 0) + 1
    return counts


_NON_BLOCK_JSON_PREFIXES = ("translation_map_md", "_md_dry_run")


def iter_json_files(root: Path):
    """處理此 generator 並逐步回報進度（yield update dict）。"""
    for p in root.rglob("*.json"):
        # 翻譯記錄（translation_map_md*）與 dry-run 預覽（_md_dry_run*）寫在同一個資料夾，
        # 不是逐檔的 md_pending_blocks；混進來會被當成注入錯誤。
        if p.is_file() and not p.name.startswith(_NON_BLOCK_JSON_PREFIXES):
            yield p


def main():
    """Markdown 寫回工具主入口。"""
    print("=== md 寫回（方案 A：保留原檔骨架，只替換文字行）===")

    src_md_root = (
        input("原始 .md 輸入資料夾（原檔所在根目錄）: ").strip().strip('"').strip("'")
    )
    json_root = (
        input("翻譯後 JSON 資料夾（例如：輸出/LM翻譯後）: ")
        .strip()
        .strip('"')
        .strip("'")
    )
    out_root = (
        input("輸出資料夾（會輸出到：<輸出>/完成/.../*.md）: ")
        .strip()
        .strip('"')
        .strip("'")
    )

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
        except Exception as e:  # noqa: BLE001 - 單一 JSON 損壞時略過該檔，繼續處理其餘檔案
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
        apply_items_to_md_lines(md_lines, items)

        # 寫出到 <輸出>/完成/<相對路徑>
        # out_path = out_done / source_md
        # 寫出到 <輸出>/完成/<相對路徑>（但語言段落 en_us -> zh_tw）
        # out_rel = map_lang_in_rel_path(source_md, src_lang="en_us", dst_lang="zh_tw")

        # 寫出到 <輸出>/完成/<相對路徑>
        # - 來源是 en_us/_en_us：輸出轉成 zh_tw/_zh_tw
        # - 來源是 zh_tw/_zh_tw：保持 zh_tw（可用於重寫/覆蓋）
        # - 其他語言或找不到語言段：跳過，避免污染輸出樹
        out_rel, status = map_lang_in_rel_path_allow_zh(
            source_md, src_lang="en_us", dst_lang="zh_tw"
        )
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
