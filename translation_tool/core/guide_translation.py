"""guide_translation.py

指南（``.md``／``.txt``）的 LM 翻譯：找檔案、切段落、翻完組回文字、決定輸出位置。

流程與 Patchouli JSON 共用目錄翻譯：每個檔案載入成一份「文件」（一個 dict），段落是
``blocks[<序號>]``，翻譯結果用 ``set_by_path`` 寫回 ``blocks``，最後由 ``render_guide`` 依段落
行號組回原檔，只替換文字行；標題以外的結構（front matter、程式碼區塊、圖片、元件標籤）不動。

段落切法、行替換規則與 Markdown 外掛相同（``md_extract_qa.extract_blocks``、
``md_inject_qa.apply_item_to_md_lines``），另外翻譯 front matter 的 ``navigation.title``（``guide_blocks``），行內保護沿用 ``rich_text_shield``。
翻完的段落會檢查元件標籤與連結目標沒被改動，不一致就保留原文。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from translation_tool.core.guide_blocks import (
    extract_guide_blocks,
    front_title_line,
    has_translatable_text,
)
from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.core.text_sniffer import (
    VANILLA_NON_TEXT_DIRS,
    is_project_doc_name,
    sniff_text_file,
    split_locale,
)
from translation_tool.plugins.md.md_inject_qa import (
    Item,
    apply_item_to_md_lines,
    is_text_line,
)
from translation_tool.plugins.shared.rich_text_shield import shield_text, unshield_text
from translation_tool.translation_db.quality import guide_structure_issues
from translation_tool.translation_db.schema import KIND_GUIDE
from translation_tool.utils.log_unit import log_warning

GUIDE_SUFFIXES = (".md", ".mdx", ".txt")
GUIDE_MARKER = "__guide__"
_LOCALE_DIR = re.compile(r"^_?[a-z]{2}_[a-z]{2}$", re.IGNORECASE)
_MAX_GUIDE_BYTES = 1024 * 1024


class GuidePath(type(Path())):
    """指南檔路徑；``guide_root`` 是掃描時的根目錄（算模組與輸出位置用）。"""

    guide_root: Path


def is_guide_document(data: Any) -> bool:
    """``file_cache`` 裡的內容是不是指南文件（而不是 JSON 資料）。"""
    return isinstance(data, dict) and data.get(GUIDE_MARKER) is True


def _strip_extracted(parts: Sequence[str]) -> Sequence[str]:
    return parts[1:] if parts and parts[0].endswith("_extracted") else parts


def _guide_parts(path: Path, root: Path) -> Sequence[str] | None:
    """``assets|data/<模組>/…`` 形式的路徑片段；不在其中回傳 None。"""
    try:
        parts = _strip_extracted(path.relative_to(root).parts)
    except ValueError:
        return None
    if len(parts) < 4 or parts[0] not in ("assets", "data"):
        return None
    return parts


def find_guide_files(root: Path) -> list[Path]:
    """依內容找出英文原文的指南檔（不看資料夾名稱）。"""
    found: list[Path] = []
    for suffix in GUIDE_SUFFIXES:
        for path in root.rglob(f"*{suffix}"):
            parts = _guide_parts(path, root)
            if parts is None or parts[2].lower() in VANILLA_NON_TEXT_DIRS:
                continue
            locale = split_locale(parts[2:])
            if locale.other or locale.lang != "en_us":
                continue
            if is_project_doc_name(path.name):
                continue
            try:
                if path.stat().st_size > _MAX_GUIDE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            if sniff_text_file(text, suffix, "/".join(parts)) is not None:
                guide = GuidePath(path)
                guide.guide_root = root
                found.append(guide)
    return sorted(found)


def load_guide(path: GuidePath) -> tuple[dict, list[dict[str, Any]]]:
    """讀一個指南檔：回傳 ``(文件, 待翻譯項目)``。"""
    root = path.guide_root
    text = path.read_text(encoding="utf-8-sig")
    parts = _guide_parts(path, root) or ()
    blocks = extract_guide_blocks(text, path.name)
    doc = {
        GUIDE_MARKER: True,
        "source": text,
        "ranges": [[b.start_line, b.end_line] for b in blocks],
        "prefixes": [b.prefix for b in blocks],
        "originals": [b.text for b in blocks],
        "blocks": [b.text for b in blocks],
    }
    mod_id = parts[1] if len(parts) > 1 else ""
    file_key = split_locale(parts[2:]).neutral if parts else path.name
    items: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if contains_cjk(block.text):
            continue  # 原文已是中文
        if not has_translatable_text(block.text):
            continue  # 只有元件標籤，沒有敘述文字
        shielded = shield_text(block.text)
        if shielded.skip_reason is not None:
            continue
        items.append(
            {
                "file": str(path),
                "path": f"blocks[{index}]",
                "text": shielded.clean,
                "source_text": block.text,
                # Guide (.md/.txt) still uses the Patchouli prompt, but its
                # cache is stored under md; .txt is part of the md bucket too.
                "cache_type": "md",
                "_shielded": shielded,
                "db_ident": (KIND_GUIDE, mod_id, f"{file_key}#{index}"),
            }
        )
    return doc, items


def fit_to_source_lines(source: str, translated: str) -> str:
    """譯文行數超過原文的文字行數時，把多出來的行併進最後一行。

    寫回原檔時是一行對一行，多出的行會被丟掉（``apply_item_to_md_lines`` 不插入新行）；
    LM 自行換行時，併行比默默遺失後半句安全。
    """
    slots = sum(1 for line in source.splitlines() if is_text_line(line))
    lines = [line for line in translated.splitlines() if line.strip()]
    if slots < 1 or len(lines) <= slots:
        return translated
    tail = " ".join(line.strip() for line in lines[slots - 1 :])
    return "\n".join([*lines[: slots - 1], tail])


def finish_guide_item(item: dict[str, Any]) -> None:
    """LM 回傳後：還原行內保護；標籤、標題／清單前綴、粗體、程式碼標記或連結目標被改動時保留原文。"""
    shielded = item.pop("_shielded", None)
    if shielded is None:
        return
    translated = unshield_text(str(item.get("text", "")), shielded.shields)
    source = str(item.get("source_text", ""))
    translated = fit_to_source_lines(source, translated)
    issues = guide_structure_issues(source, translated, kind=KIND_GUIDE)
    if issues:
        log_warning(
            f"⚠️ 指南段落的標記被改動，保留原文：{item.get('file')}（{'、'.join(issues[:3])}）"
        )
        item["text"] = source
        item["_untranslated"] = True
        return
    item["text"] = translated


def render_guide(doc: dict) -> str:
    """把翻譯過的段落依行號組回原檔。"""
    lines = str(doc["source"]).splitlines()
    for (start, end), prefix, original, current in zip(
        doc["ranges"], doc["prefixes"], doc["originals"], doc["blocks"], strict=True
    ):
        if current == original:
            continue
        if prefix:
            lines[start - 1] = front_title_line(prefix, current)
        else:
            apply_item_to_md_lines(lines, Item("", start, end, current))
    return "\n".join(lines) + ("\n" if str(doc["source"]).endswith("\n") else "")


def guide_output_rel(rel: Path, root: Path) -> Path:
    """翻譯輸出的相對路徑。

    有 ``en_us``／``_en_us`` 資料夾就換成 ``zh_tw``／``_zh_tw``；英文放在指南根目錄的
    （AE2、GuideME）則在指南根目錄下加 ``_zh_tw``——指南根目錄是模組資料夾之下、
    旁邊已經有 ``_xx_xx`` 語系資料夾的那一層，找不到就用模組資料夾下的第一層。
    """
    parts = list(rel.parts)
    offset = 1 if parts and parts[0].endswith("_extracted") else 0
    for index in range(offset + 2, len(parts) - 1):
        segment = parts[index].lower()
        if segment in ("en_us", "_en_us"):
            parts[index] = "_zh_tw" if segment.startswith("_") else "zh_tw"
            return Path(*parts)
    insert_at = offset + 3
    for depth in range(len(parts) - 1, offset + 2, -1):
        directory = root.joinpath(*parts[:depth])
        try:
            if any(
                child.is_dir() and _LOCALE_DIR.match(child.name)
                for child in directory.iterdir()
            ):
                insert_at = depth
                break
        except OSError:
            continue
    insert_at = min(insert_at, len(parts) - 1)
    return Path(*parts[:insert_at], "_zh_tw", *parts[insert_at:])
