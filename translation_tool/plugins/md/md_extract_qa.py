# md_extract_qa.py
# ------------------------------------------------------------
# 問答式 Markdown (.md) 抽取器（段落/區塊為單位）
#
# 你要的重點：
#   - 不要一行一行抽取（會很碎）
#   - 改成「段落」抽取：連續的文字行合併成一個 item
#   - 遇到空行 or 特定 § 指令行（align/stack/rule/recipe/entity...）就切段
#   - README.md 排除（不分大小寫、任何層級）
#   - 支援語言過濾模式（含中文 / 不含中文 / 全部）
#
# 輸出：
#   輸出資料夾/待翻譯/<原相對路徑>.json
# ------------------------------------------------------------

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional
import hashlib


# ========= 你那套 § 指令行（遇到就切段，且本行不納入段落翻譯） =========
# 你貼的內容常見：§align, §stack, §rule, §recipe, §entity
RE_TOKEN_LINE = re.compile(r"^\s*§(align:|stack\[|rule\{|recipe\[|entity\[)", re.I)

# 另外：有些人會把多個 §stack 接在同一行，這行也視為 token 行（避免被當文字）
RE_MOSTLY_TOKEN_LINE = re.compile(r"^\s*(§[0-9a-zA-Z]+\S*)\s*(§[0-9a-zA-Z]+\S*)*\s*$")

# 標題：##### §nTITLE§n（這行是「文字」，要納入段落）
# 我們保留整行進段落（讓翻譯有上下文），但也可以選擇只翻 TITLE（之後翻譯階段再做保護）
RE_HEADING_N = re.compile(r"^(?P<prefix>\s*#{1,6}\s+)(?P<pre>§n)(?P<title>.*?)(?P<post>§n)\s*$")

# 格式碼開頭行：§bStats 或 §4Warning...
# 這行也是「文字」，要納入段落（但 § 前綴要保留）
RE_FORMAT_PREFIX = re.compile(r"^(?P<prefix>\s*§[0-9a-zA-Z]+)(?P<text>.+)$")

# 語言過濾（漢字）
RE_CJK = re.compile(r"[\u4e00-\u9fff]")


def contains_cjk(s: str) -> bool:
    return bool(RE_CJK.search(s))


def pass_lang_filter(block_text: str, mode: str) -> bool:
    """
    mode:
      cjk_only     -> 只保留含中文
      non_cjk_only -> 只保留不含中文（通常是英文要翻）
      all          -> 全部保留
    """
    has_cjk = contains_cjk(block_text)
    if mode == "cjk_only":
        return has_cjk
    if mode == "non_cjk_only":
        return not has_cjk
    return True



def normalize_for_dedupe(s: str) -> str:
    """
    去重用的正規化（保守版）：
    - 保留 token，不做移除（最安全，避免不同 token 的段落被誤判相同）
    - 統一行尾空白
    - 多個空白壓成單一空白
    - 3+ 空行壓成 2 空行
    """
    s = "\n".join([ln.rstrip() for ln in s.splitlines()])
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def make_content_hash(text: str) -> str:
    n = normalize_for_dedupe(text)
    return hashlib.sha1(n.encode("utf-8")).hexdigest()



@dataclass
class BlockItem:
    """
    段落/區塊 item（你要的：不要碎成一行一行）
    """
    id: str                 # 唯一 ID：<relpath>:<start>-<end>
    text: str               # 段落文字（多行，以 \n 串起來）
    content_hash: str       # 資料重複標記
    start_line: int         # 起始行（1-based）
    end_line: int           # 結束行（1-based）





def is_splitter_line_old(line: str) -> bool:
    """
    只在「強分隔 token 行」才切段：
      - §align/§stack/§rule/§recipe/§entity
      - 幾乎整行都是 §token
    空行不再切段（空行會被保留在 block 內，維持段落感）。
    """
    if RE_TOKEN_LINE.match(line):
        return True
    if RE_MOSTLY_TOKEN_LINE.match(line.strip()):
        return True
    return False



def is_splitter_line(line: str) -> bool:
    # 原有的強分隔符
    if RE_TOKEN_LINE.match(line):
        return True
    if RE_MOSTLY_TOKEN_LINE.match(line.strip()):
        return True
    
    # 新增：標題行也是一種切分點 (但標題行本身稍後要被納入翻譯)
    # 這裡我們只判斷「是否觸發切段」
    if line.strip().startswith("#"):
        return True
        
    # 新增：YAML Frontmatter 標記
    if line.strip() == "---":
        return True
        
    return False


def is_translatable_text_line(line: str) -> bool:
    """
    判斷「這一行」是否應該送進翻譯。
    這是行級（line-level）的過濾器，由 extract_blocks() 使用。
    """
    s = line.strip()
    if not s:
        return False

    # § 指令行或純 token 行：不翻譯
    if RE_TOKEN_LINE.match(line):
        return False
    if RE_MOSTLY_TOKEN_LINE.match(s):
        return False

    # Markdown 圖片行：![alt](url)
    # 為避免破壞路徑與括號，整行不翻譯
    if re.match(r"^\s*!\[.*?\]\(.*?\)\s*$", line):
        return False

    # 純 component / tag 行（例如 ItemImage / ItemLink / ImportStructure 等）
    # 若整行只是一個 <...> 標籤，則不翻譯
    if re.match(r"^\s*<[^>]+>\s*$", line):
        return False

    # 類 YAML 的 key-only 行（保險用）
    # 例如：navigation:、categories:、item_ids:
    # frontmatter 已在 extract_blocks() 處理，這裡只是防呆
    if re.match(r"^[A-Za-z0-9_\-]+:\s*$", s):
        return False

    return True



def normalize_blank_lines(text: str) -> str:
    """
    將 3 個以上連續空行壓縮成最多 2 個，
    並移除開頭/結尾多餘的空行。
    """
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")

def extract_blocks(md_text: str, rel_file: str, lang_mode: str) -> List[BlockItem]:
    """
    從 Markdown 中抽取「可翻譯的文字區塊」

    規則：
    - 空行視為段落邊界
    - 標題（# 開頭）獨立成一個 block
    - YAML frontmatter（--- ... ---）整段不翻譯
    - Markdown 圖片行不翻譯
    - 純 component / tag 行（<ItemImage ...> 等）不翻譯
    - § 指令 / token 行不翻譯
    """
    lines = md_text.splitlines(keepends=False)
    items: List[BlockItem] = []
    buf: List[str] = []
    start_ln: Optional[int] = None
    in_frontmatter = False

    def flush(end_ln: int):
        """將目前 buffer 內容輸出成一個翻譯 block"""
        nonlocal buf, start_ln
        if not buf:
            start_ln = None
            return

        block_text = "\n".join(buf).strip()
        if block_text and pass_lang_filter(block_text, lang_mode):
            h = make_content_hash(block_text)
            items.append(BlockItem(
                id=f"{rel_file}:{start_ln}-{end_ln}",
                text=block_text,
                content_hash=h,
                start_line=start_ln,
                end_line=end_ln
            ))
        buf = []
        start_ln = None

    for i, line in enumerate(lines):
        ln = i + 1
        stripped = line.strip()

        # 1) YAML frontmatter 處理（--- ... ---）
        # 進入或離開 frontmatter 區段，整段不翻譯
        if stripped == "---":
            flush(end_ln=ln - 1)
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue

        # 2) 空行：結束目前段落
        if not stripped:
            flush(end_ln=ln - 1)
            continue

        # 3) § 指令 / token 行：切段但不翻譯該行
        if RE_TOKEN_LINE.match(line) or RE_MOSTLY_TOKEN_LINE.match(stripped):
            flush(end_ln=ln - 1)
            continue

        # 4) 標題行（# 開頭）：獨立成一個可翻譯 block
        if stripped.startswith("#"):
            flush(end_ln=ln - 1)
            start_ln = ln
            buf = [line]
            flush(end_ln=ln)
            continue

        # 5) Markdown 圖片行：整行跳過
        if re.match(r"^\s*!\[.*?\]\(.*?\)\s*$", line):
            flush(end_ln=ln - 1)
            continue

        # 6) 純 component / tag 行（<ItemImage ...>、<ImportStructure ...>）
        if re.match(r"^\s*<[^>]+>\s*$", line):
            flush(end_ln=ln - 1)
            continue

        # 7) 行級過濾：不應翻譯的行直接跳過
        if not is_translatable_text_line(line):
            flush(end_ln=ln - 1)
            continue

        # 8) 一般文字行：加入目前段落
        if start_ln is None:
            start_ln = ln
        buf.append(line)

    flush(end_ln=len(lines))
    return items


def build_pending_json(rel_md: str, abs_md: Path, items: List[BlockItem], lang_mode: str) -> dict:
    return {
        "schema": "md_pending_blocks_v1",
        "source_md": rel_md.replace("\\", "/"),
        "source_abs": str(abs_md),
        "lang_filter_mode": lang_mode,
        "items": [asdict(it) for it in items],
        "stats": {
            "blocks": len(items),
        },
        "notes": [
            "本版本以「段落/區塊」為單位抽取（連續文字行合併）。",
            "遇到空行或 §align/§stack/§rule/§recipe/§entity 等指令行會切段，且指令行不納入抽取。",
            "語言過濾是以整個段落判斷（不是逐行）。"
        ]
    }

# 語言資料夾段落（en_us / zh_tw，允許 _en_us / _zh_tw，大小寫不拘）
RE_LANG_SEG = re.compile(r"^_?(en_us|zh_cn|zh_tw)$", re.IGNORECASE)

def has_allowed_lang_segment(path: Path) -> bool:
    # 用 parts 掃描每個 segment，支援 structure/en_us 這種深層結構
    return any(RE_LANG_SEG.match(seg) for seg in path.parts)


def detect_lang_segment(parts: List[str]) -> Optional[str]:
    """
    從路徑 segments 判斷語言資料夾（支援 _en_us/_zh_tw、大小寫）
    回傳: "en_us" / "zh_tw" / "zh_cn" / None
    """
    for seg in parts:
        m = RE_LANG_SEG.match(seg)
        if m:
            # m.group(0) 可能是 _EN_US 這種，把前綴 _ 去掉再 lower
            return seg.lstrip("_").lower()
    return None


def map_rel_lang_path(rel_path: str, src_lang: str, dst_lang: str) -> str:
    """
    只替換「剛好是語言資料夾」的 segment：
      - en_us -> zh_tw
      - _en_us -> _zh_tw
      - 大小寫不拘
    """
    parts = rel_path.replace("\\", "/").split("/")
    src_norm = src_lang.lower()
    dst_norm = dst_lang.lower()

    for i, seg in enumerate(parts):
        m = RE_LANG_SEG.match(seg)
        if not m:
            continue
        # seg 可能是 _EN_US 之類
        prefix = "_" if seg.startswith("_") else ""
        lang = seg.lstrip("_").lower()
        if lang == src_norm:
            parts[i] = f"{prefix}{dst_norm}"

    return "/".join(parts)



def iter_md_files(root: Path):
    """
    遞迴列出所有 .md（大小寫不敏感），排除 README.md（不分大小寫、任何層級）
    ✅ 額外限制：只處理路徑中包含 en_us / zh_tw（含 _ 前綴、大小寫不拘）的檔案
    """
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".md":
            continue
        if p.name.lower() == "readme.md":
            continue

        # ✅ 只允許 en_us / zh_tw（例如：.../en_us/... 或 .../structure/en_us/...）
        if not has_allowed_lang_segment(p):
            continue

        yield p


def safe_relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def main():
    print("=== Markdown .md 抽取（段落/區塊）問答式 ===")

    in_dir = input("輸入資料夾（會遞迴往下找 .md）: ").strip().strip('"').strip("'")
    out_dir = input("輸出資料夾（會輸出到：<輸出>/待翻譯/.../*.json）: ").strip().strip('"').strip("'")

    in_root = Path(in_dir).expanduser().resolve()
    out_root = Path(out_dir).expanduser().resolve()

    if not in_root.exists() or not in_root.is_dir():
        print(f"[錯誤] 輸入資料夾不存在或不是資料夾：{in_root}")
        return

    # 問答：過濾模式
    print("\n過濾模式：")
    print("  1) 只保留含中文（CJK only）")
    print("  2) 只保留不含中文（Non-CJK only，通常是英文要翻）")
    print("  3) 全部保留（All）")
    choice = input("請輸入 1/2/3（預設 2）: ").strip() or "2"

    if choice == "1":
        lang_mode = "cjk_only"
    elif choice == "3":
        lang_mode = "all"
    else:
        lang_mode = "non_cjk_only"

    pending_root = out_root / "待翻譯"
    pending_root.mkdir(parents=True, exist_ok=True)

    md_files = list(iter_md_files(in_root))
    if not md_files:
        print("[提示] 找不到任何 .md 檔案（或都被 README.md 過濾掉了）。")
        return

    total_blocks = 0
    json_written = 0
    skipped_empty = 0

    seen_hashes = set()
    dup_blocks = 0
    unique_blocks = 0

    for md_path in md_files:
        rel_md = safe_relpath(md_path, in_root)

        # 讀檔
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            md_text = md_path.read_text(encoding="utf-8", errors="replace")

        ## 段落抽取
        #items = extract_blocks(md_text, rel_md, lang_mode=lang_mode)
        #total_blocks += len(items)
#
        #for it in items:
        #    if it.content_hash in seen_hashes:
        #        dup_blocks += 1
        #    else:
        #        seen_hashes.add(it.content_hash)
        #        unique_blocks += 1

        # 段落抽取（先抽 en_us 自己）
        items = extract_blocks(md_text, rel_md, lang_mode=lang_mode)

        # ✅ MVP：若正在抽「非中文 only」（通常是英文待翻），且本檔在 en_us，
        #         且存在對應 zh_tw 檔，且 blocks 數量一致，
        #         就把已翻（zh_tw 含 CJK）的 block 從 pending 排除。
        if lang_mode == "non_cjk_only":
            rel_parts = rel_md.replace("\\", "/").split("/")
            lang = detect_lang_segment(rel_parts)

            if lang == "en_us":
                rel_zh = map_rel_lang_path(rel_md, src_lang="en_us", dst_lang="zh_tw")
                zh_path = in_root / rel_zh  # 同一個輸入根目錄下找對應 zh_tw

                if zh_path.exists() and zh_path.is_file():
                    try:
                        zh_text = zh_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        zh_text = zh_path.read_text(encoding="utf-8", errors="replace")

                    zh_items = extract_blocks(zh_text, rel_zh, lang_mode="all")

                    # 只有在切分數量一致時才做 index 對齊（保守避免錯位）
                    if len(items) == len(zh_items):
                        filtered: List[BlockItem] = []
                        for en_it, zh_it in zip(items, zh_items):
                            # zh_tw block 內有 CJK → 視為已翻，跳過 en_us block
                            if contains_cjk(zh_it.text):
                                continue
                            filtered.append(en_it)
                        items = filtered
                    else:
                        # 可選：印 log 方便你抓到需要人工處理的檔案
                        print(f"[WARN] block misaligned, keep all: {rel_md} (en={len(items)} zh={len(zh_items)})")

        total_blocks += len(items)

        for it in items:
            if it.content_hash in seen_hashes:
                dup_blocks += 1
            else:
                seen_hashes.add(it.content_hash)
                unique_blocks += 1



        if not items:
            skipped_empty += 1
            continue

        out_json_path = pending_root / (rel_md + ".json")
        out_json_path.parent.mkdir(parents=True, exist_ok=True)

        payload = build_pending_json(rel_md, md_path, items, lang_mode)
        out_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        json_written += 1

    # manifest
    manifest_path = pending_root / "_manifest.json"
    manifest = {
        "schema": "md_pending_manifest_blocks_v1",
        "input_root": str(in_root),
        "output_root": str(out_root),
        "pending_root": str(pending_root),
        "lang_filter_mode": lang_mode,
        "md_files_found": len(md_files),
        "json_written": json_written,
        "md_skipped_empty": skipped_empty,
        "total_blocks": total_blocks,
        "unique_blocks": unique_blocks,
        "duplicate_blocks": dup_blocks,
        "notes": [
            "README.md 已排除（不分大小寫、任何層級）。",
            "以段落/區塊抽取（連續文字行合併）。",
            "空行或 §align/§stack/§rule/§recipe/§entity 行會切段，且該行不納入抽取。"
        ]
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 完成 ===")
    print(f"輸入根目錄：{in_root}")
    print(f"輸出根目錄：{out_root}")
    print(f"過濾模式：{lang_mode}")
    print(f"找到 .md：{len(md_files)}（README.md 已排除）")
    print(f"輸出 JSON：{json_written}")
    print(f"無可翻譯內容而跳過：{skipped_empty}")
    print(f"總抽取 blocks：{total_blocks}")
    print(f"去重後 blocks（實際只需翻一次）：{unique_blocks}")
    print(f"重複 blocks（若不 cache 會重送）：{dup_blocks}")

    print(f"manifest：{manifest_path}")


if __name__ == "__main__":
    main()
