"""sniff_groups.py

不靠資料夾設定，從 jar 內容找出「手冊／指南」檔案並分組（供 scanner 匯入）。

做法（效率與安全）：

- 候選只看路徑：文字類副檔名、不在規格固定的非文字資料夾（``textures``、``models``…）、
  不是其他語系。
- 以「單元」（模組底下前兩層資料夾）為單位均勻抽樣讀取，一個單元最多讀
  ``SAMPLES_PER_UNIT`` 個檔案；抽樣通過，整個單元的同副檔名原文檔都收進來。
  一個有上千個檔案或上千個子資料夾的單元（例如動畫、世界生成資料）只花幾次讀取。
- 使用獨立且較小的讀取預算，用盡時停止判斷、保留已找到的結果，**不會**讓整個 jar 掃描失敗。
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence

from translation_tool.core.text_sniffer import (
    VANILLA_NON_TEXT_DIRS,
    is_project_doc_name,
    looks_like_book_json,
    sniff_text_file,
    split_locale,
)
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_PATCHOULI
from translation_tool.utils.zip_safety import (
    ArchiveBudgetError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)

SAMPLES_PER_UNIT = 6
SNIFF_MAX_MEMBERS = 1500
SNIFF_MAX_BYTES = 48 * 1024 * 1024

Groups = dict[tuple[str, str, str], dict[str, str]]


def _candidates(names: Sequence[str], claimed: set[str]) -> dict[tuple[str, str], dict]:
    """``{(模組, 中性路徑): {語系: 成員}}``；只留文字類副檔名的來源語系檔。"""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for name in names:
        low = name.lower()
        if name in claimed or not low.endswith((".json", ".md", ".txt")):
            continue
        parts = name.split("/")
        if len(parts) < 4 or parts[0].lower() not in ("assets", "data"):
            continue
        if parts[2].lower() in VANILLA_NON_TEXT_DIRS:
            continue
        if low.endswith(".json") and len(parts) == 4 and parts[2] == "lang":
            continue
        loc = split_locale(parts[2:])
        if loc.other or loc.lang is None:
            continue
        found.setdefault((parts[1], loc.neutral), {})[loc.lang] = name
    return found


def _passes(zf: zipfile.ZipFile, budget: ZipReadBudget, member: str) -> bool:
    """抽樣檔案是否像手冊／指南。"""
    raw = read_limited(zf, member, budget=budget)
    if member.lower().endswith(".json"):
        import json

        try:
            data = json.loads(raw.decode("utf-8-sig"), strict=False)
        except (UnicodeDecodeError, ValueError):
            return False
        return looks_like_book_json(data, member.split("/")[2:])
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return sniff_text_file(text, member[member.rfind(".") :], member) is not None


def sniffed_groups(
    zf: zipfile.ZipFile, names: Sequence[str], claimed: set[str]
) -> Groups:
    """未被資料夾設定認領的手冊／指南檔，依 ``(類型, 模組, 中性路徑)`` 分組。"""
    by_dir: dict[tuple[str, str, str], list[tuple[str, dict[str, str]]]] = {}
    for (mod_id, neutral), members in _candidates(names, claimed).items():
        source = members.get("en_us")
        if source is None:
            continue
        kind_ext = source[source.rfind(".") :].lower()
        directory = "/".join(neutral.split("/")[:-1][:2])
        by_dir.setdefault((mod_id, directory, kind_ext), []).append((neutral, members))

    budget = ZipReadBudget(SNIFF_MAX_BYTES, SNIFF_MAX_MEMBERS, label="sniff")
    groups: Groups = {}
    try:
        for (mod_id, _directory, ext), files in by_dir.items():
            files.sort()
            step = max(1, len(files) // SAMPLES_PER_UNIT)
            samples = files[::step][:SAMPLES_PER_UNIT]
            if not any(_passes(zf, budget, m["en_us"]) for _n, m in samples):
                continue
            kind = KIND_PATCHOULI if ext == ".json" else KIND_GUIDE
            for neutral, members in files:
                if kind == KIND_GUIDE and is_project_doc_name(neutral):
                    continue
                groups[(kind, mod_id, neutral)] = members
    except (ZipSizeError, ArchiveBudgetError):
        pass  # 預算用盡：保留已判斷的結果，不影響其他匯入
    return groups
