"""Markdown 管線：以 zh_cn 為底稿補譯 zh_tw（沒有 zh_tw 時）。

用途：
  - 判斷 zh_cn 檔是不是「完整、跟得上英文原文的譯本」（結構對齊）
  - 對齊時以 zh_cn 為底稿、整檔簡轉繁；只有與英文原文完全相同的殘留區塊才送 LM
  - 轉換時保護程式碼區塊，且絕不增減行數（Step 3 依行號回填）

維護注意：對齊只回答「這份 zh_cn 能不能整份採用」，不做逐段或依位置的部分合併。
任何一項不符就整份退回英文送 LM。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from translation_tool.plugins.md.md_extract_qa import (
    RE_CODE_FENCE,
    extract_blocks,
)

CONVERT_NAME = "opencc_s2twp"

_RE_URL = re.compile(r"\]\(([^)\s]*)")
_RE_INLINE_CODE = re.compile(r"`[^`\n]+`")
_RE_SECTION = re.compile(r"§.")
_RE_TAG_NAME = re.compile(r"</?([A-Za-z][\w:-]*)")
_RE_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_RE_IMAGE_LINE = re.compile(r"^\s*!\[.*?\]\((.*?)\)\s*$")
_RE_TAG_LINE = re.compile(r"^\s*<[^>]+>\s*$")


def file_sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def build_base(rel_en: str, en_path: Path, cn_path: Path) -> dict[str, Any]:
    """pending 頂層的 base：記錄這份 pending 以哪份 zh_cn 為底稿，Step 3 據此驗證來源沒變。"""
    return {
        "lang": "zh_cn",
        "convert": CONVERT_NAME,
        "source_sha1": file_sha1(cn_path),
        "paired_en": rel_en.replace("\\", "/"),
        "paired_en_sha1": file_sha1(en_path),
    }


def base_is_stale(base: dict[str, Any], src_root: Path, source_md: str) -> bool:
    """zh_cn 底稿或對應英文檔在抽取後被改過（或消失）。"""
    try:
        cn_now = file_sha1(src_root / source_md)
        en_now = file_sha1(src_root / str(base.get("paired_en", "")))
    except OSError:
        return True
    return cn_now != base.get("source_sha1") or en_now != base.get("paired_en_sha1")


def _block_kind(text: str) -> tuple[Any, ...]:
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    stripped = first.lstrip()
    lines = text.splitlines()
    if stripped.startswith("#"):
        return ("h", len(stripped) - len(stripped.lstrip("#")))
    if stripped.startswith(">"):
        return ("q", sum(1 for ln in lines if ln.lstrip().startswith(">")))
    if stripped.startswith("|"):
        return ("t", sum(1 for ln in lines if ln.lstrip().startswith("|")))
    if _RE_LIST_ITEM.match(first):
        return ("l", sum(1 for ln in lines if _RE_LIST_ITEM.match(ln)))
    return ("p",)


def _block_invariants(text: str) -> tuple[Any, ...]:
    """區塊內翻譯後也必須原樣出現的部分，依出現順序比較。"""
    return (
        tuple(_RE_URL.findall(text)),
        tuple(_RE_INLINE_CODE.findall(text)),
        tuple(_RE_SECTION.findall(text)),
        tuple(_RE_TAG_NAME.findall(text)),
    )


def _normalize_other_line(line: str) -> tuple[Any, ...]:
    """不屬於任何區塊的行：圖片只比目標 URL、元件行只比標籤名，其餘（token、程式碼）原樣比。"""
    image = _RE_IMAGE_LINE.match(line)
    if image:
        return ("img", image.group(1))
    if _RE_TAG_LINE.match(line):
        return ("tag", tuple(_RE_TAG_NAME.findall(line)))
    return ("raw", line.strip())


def _frontmatter_range(lines: list[str]) -> tuple[int, int] | None:
    """YAML frontmatter 的行索引範圍（含兩條 ---）；規則與 extract_blocks 相同。"""
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or lines[first].strip() != "---":
        return None
    for j in range(first + 1, len(lines)):
        if lines[j].strip() == "---":
            return first, j
    return None


def document_skeleton(md_text: str) -> list[tuple[Any, ...]]:
    """整份文件的結構骨架：翻譯過的文字被遮掉，剩下的結構與不可翻譯內容必須對得上。"""
    md_text = md_text.lstrip("\ufeff")  # BOM 不算內容；只有一邊有時不能因此判為不對齊
    lines = md_text.splitlines()
    blocks = {b.start_line - 1: b for b in extract_blocks(md_text, "x", "all")}
    frontmatter = _frontmatter_range(lines)
    skeleton: list[tuple[Any, ...]] = []
    i = 0
    code_fence: str | None = None
    while i < len(lines):
        line = lines[i]
        if code_fence is not None or RE_CODE_FENCE.match(line):
            fence = RE_CODE_FENCE.match(line)
            skeleton.append(("code", line))
            if code_fence is None:
                code_fence = fence.group(1)
            elif (
                fence
                and fence.group(1)[0] == code_fence[0]
                and len(fence.group(1)) >= len(code_fence)
            ):
                code_fence = None
            i += 1
        elif frontmatter and frontmatter[0] <= i <= frontmatter[1]:
            # frontmatter 的值（title、分類名稱）會被翻譯，只比對 key 的順序與縮排
            key = re.split(r"[:：]", line, maxsplit=1)[0]
            skeleton.append(("fm", key if ":" in line or "：" in line else line[:1]))
            i += 1
        elif i in blocks:
            block = blocks[i]
            skeleton.append(
                ("block", _block_kind(block.text), _block_invariants(block.text))
            )
            i = block.end_line
        elif not line.strip():
            i += 1
        else:
            skeleton.append(_normalize_other_line(line))
            i += 1
    return skeleton


def check_zh_cn_alignment(en_text: str, cn_text: str) -> str | None:
    """zh_cn 能整份採用就回傳 None，否則回傳原因（供 log／manifest）。"""
    en_skeleton = document_skeleton(en_text)
    cn_skeleton = document_skeleton(cn_text)
    if len(en_skeleton) != len(cn_skeleton):
        return f"結構項目數不同（en={len(en_skeleton)} zh_cn={len(cn_skeleton)}）"
    for index, (en_part, cn_part) in enumerate(zip(en_skeleton, cn_skeleton)):
        if en_part != cn_part:
            return f"第 {index + 1} 個結構項目不同（{en_part[0]}）"
    return None


def convert_markdown_lines(
    lines: list[str], convert: Callable[[str], str]
) -> list[str]:
    """逐行簡轉繁；程式碼區塊（含圍欄行）原樣保留。行數與順序不變。"""
    out: list[str] = []
    code_fence: str | None = None
    for line in lines:
        fence = RE_CODE_FENCE.match(line)
        if code_fence is not None:
            out.append(line)
            if (
                fence
                and fence.group(1)[0] == code_fence[0]
                and len(fence.group(1)) >= len(code_fence)
            ):
                code_fence = None
        elif fence:
            out.append(line)
            code_fence = fence.group(1)
        else:
            out.append(convert(line))
    return out
