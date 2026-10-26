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

import json
import zipfile
from collections.abc import Sequence

from translation_tool.core.book_formats import (
    BOOK_EXCLUDED_DIRS,
    FORMAT_NEUTRAL_PAGES,
    FORMAT_STANDARD_PATCHOULI,
    UNSUPPORTED_BOOK_FORMATS,
    resolve_book_format,
)
from translation_tool.core.text_sniffer import (
    is_project_doc_name,
    looks_like_advancement,
    looks_like_book_json,
    sniff_text_file,
    split_locale,
)
from translation_tool.translation_db.schema import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_PATCHOULI,
)
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
        if name in claimed or not low.endswith((".json", ".md", ".mdx", ".txt")):
            continue
        if low.rsplit("/", 1)[-1] == "book.json":
            continue  # 書本根設定：資源包能否覆寫尚未確認，不處理
        parts = name.split("/")
        if len(parts) < 4 or parts[0].lower() not in ("assets", "data"):
            continue
        if parts[2].lower() in BOOK_EXCLUDED_DIRS:
            continue
        if low.endswith(".json") and len(parts) == 4 and parts[2] == "lang":
            continue
        loc = split_locale(parts[2:])
        if loc.other or loc.lang is None:
            continue
        found.setdefault((parts[1], loc.neutral), {})[loc.lang] = name
    return found


def _kind_of(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    member: str,
    *,
    invalid_members: list[str] | None = None,
) -> str | None:
    """檔案像什麼：手冊／進度（JSON）或指南（文字）；都不是回傳 None。

    文字檔依「這個檔案自己的語系」判斷：英文看停用字，繁中／簡中看中文字數，
    所以翻譯 ZIP 同時有英文與中文指南時，選到哪一份都能辨識。
    單一檔案超過大小上限時視為不是（略過該檔）；整體預算用盡仍拋出 ``ArchiveBudgetError``。
    """
    path_format = resolve_book_format(member)
    if path_format.name in UNSUPPORTED_BOOK_FORMATS:
        return None
    try:
        raw = read_limited(zf, member, budget=budget)
    except ArchiveBudgetError:
        raise
    except ZipSizeError:
        return None
    if member.lower().endswith(".json"):
        try:
            data = json.loads(raw.decode("utf-8-sig"), strict=False)
        except (UnicodeDecodeError, ValueError):
            if invalid_members is not None and path_format.name != "unknown":
                invalid_members.append(member)
            return None
        if looks_like_advancement(data):
            return KIND_ADVANCEMENT
        format_info = resolve_book_format(member, data)
        if format_info.name in {FORMAT_STANDARD_PATCHOULI, FORMAT_NEUTRAL_PAGES}:
            return KIND_PATCHOULI
        if format_info.name != "unknown":
            return None
        if looks_like_book_json(data, member.split("/")[2:]):
            return KIND_PATCHOULI
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    suffix = member[member.rfind(".") :]
    own_lang = split_locale(member.split("/")[2:]).lang
    if (
        sniff_text_file(text, suffix, member, translated=own_lang != "en_us")
        is not None
    ):
        return KIND_GUIDE
    return None


def _drop_advancement_root(neutral: str) -> str:
    """進度資料夾在 1.21 由 ``advancements`` 改名 ``advancement``；去掉它，跨版本才是同一個鍵。"""
    head, _, rest = neutral.partition("/")
    return rest if rest and head.lower() in ("advancement", "advancements") else neutral


def _primary(members: dict[str, str], translated: bool) -> str | None:
    """辨識與分類用的檔案：英文原文；翻譯來源（只有譯文也要能匯入）才退回繁中／簡中。"""
    found = members.get("en_us")
    if found is None and translated:
        found = members.get("zh_tw") or members.get("zh_cn")
    return found


def sniffed_groups(
    zf: zipfile.ZipFile,
    names: Sequence[str],
    claimed: set[str],
    *,
    translated: bool = False,
    diagnostics: list[str] | None = None,
) -> Groups:
    """未被資料夾設定認領的手冊／指南檔，依 ``(類型, 模組, 中性路徑)`` 分組。"""
    by_dir: dict[tuple[str, str, str], list[tuple[str, dict[str, str]]]] = {}
    for (mod_id, neutral), members in _candidates(names, claimed).items():
        source = _primary(members, translated)
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
            kind = next(
                (
                    k
                    for _n, m in samples
                    if (k := _kind_of(
                        zf,
                        budget,
                        _primary(m, translated),
                        invalid_members=diagnostics,
                    ))
                ),
                None,
            )
            if kind is None:
                continue
            for neutral, members in files:
                if kind == KIND_GUIDE and is_project_doc_name(neutral):
                    continue
                # 抽樣只決定這個資料夾像什麼；同資料夾的設定檔、中繼資料不能沿用結論
                try:
                    own = _kind_of(
                        zf,
                        budget,
                        _primary(members, translated),
                        invalid_members=diagnostics,
                    )
                except ArchiveBudgetError:
                    break  # 預算用盡：沒驗證過的檔案不收，保留已驗證的
                if own is None:
                    continue  # 設定檔、中繼資料不收
                kind_of_file = own  # 同一資料夾可以同時有手冊與進度，各自分類
                if kind_of_file == KIND_ADVANCEMENT:
                    neutral = _drop_advancement_root(neutral)
                groups[(kind_of_file, mod_id, neutral)] = members
    except (ZipSizeError, ArchiveBudgetError):
        pass  # 預算用盡：保留已判斷的結果，不影響其他匯入
    return groups
