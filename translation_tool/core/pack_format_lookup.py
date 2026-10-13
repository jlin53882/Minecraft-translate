"""把使用者輸入的 Minecraft 版本（如 ``1.20.4``）或 pack_format 換算成整數 pack_format。

資料來自 ``pack_format_table.json``（正式版；不含快照）。版本以數字元組比較，
不做字串比對，所以 ``1.9`` 不會被當成 ``1.19``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from translation_tool.core.pack_mcmeta import (
    DEFAULT_PACK_FORMAT,
    describe_pack_format,
    has_max_format,
    parse_pack_format,
)
from translation_tool.utils.log_unit import log_warning

TABLE_FILE = Path(__file__).with_name("pack_format_table.json")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")

# 版本輸入框旁的快速選擇（需存在於資料表）
QUICK_VERSIONS = ("1.21.1", "1.21.4", "1.20.1", "1.19.2")


@dataclass(frozen=True)
class FormatRange:
    low: tuple[int, ...]
    high: tuple[int, ...]
    pack_format: int
    label: str


@dataclass(frozen=True)
class ResolvedFormat:
    """換算結果：``pack_format`` 與一行給使用者看的來源說明。"""

    pack_format: int
    note: str


def parse_version(text: str) -> tuple[int, ...]:
    """``"1.20.4"`` → ``(1, 20, 4)``；格式不合拋出 ValueError。"""
    raw = (text or "").strip()
    if not _VERSION_RE.match(raw):
        raise ValueError(
            f"「{raw}」不是版本格式，請輸入如 1.21.1，或直接輸入 pack_format 整數"
        )
    return tuple(int(part) for part in raw.split("."))


def _range_label(low: str, high: str) -> str:
    return low if low == high else f"{low}～{high}"


@lru_cache(maxsize=1)
def load_table() -> tuple[FormatRange, ...]:
    """讀取資料表；檔案不存在或損毀時回傳空表（輸入版本會提示改填 pack_format）。"""
    try:
        rows = json.loads(TABLE_FILE.read_text(encoding="utf-8"))
        return tuple(
            FormatRange(
                parse_version(row["from"]),
                parse_version(row["to"]),
                int(row["pack_format"]),
                _range_label(row["from"], row["to"]),
            )
            for row in rows
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log_warning(f"讀取 pack_format 對照表失敗，改用空表：{TABLE_FILE}: {exc!r}")
        return ()


def lookup_version(text: str) -> ResolvedFormat:
    """找出涵蓋該版本的區間；比表內最舊更舊、比最新更新或落在空隙時拋出 ValueError。"""
    version = parse_version(text)
    table = load_table()
    for row in table:
        if row.low <= version <= row.high:
            return ResolvedFormat(
                row.pack_format,
                f"{text.strip()} 屬於 {row.label}，pack_format {row.pack_format}",
            )
    if not table:
        raise ValueError("找不到版本對照表，請直接輸入 pack_format 整數")
    if version > table[-1].high:
        raise ValueError(
            f"對照表只到 {table[-1].label.split('～')[-1]}，"
            "更新的版本請直接輸入 pack_format 整數"
        )
    raise ValueError(f"對照表沒有 {text.strip()}，請直接輸入 pack_format 整數")


def resolve_version_input(text: object) -> ResolvedFormat:
    """空白 → 預設；純整數 → 當 pack_format；其餘當版本換算。無效時拋出 ValueError。"""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return ResolvedFormat(
            DEFAULT_PACK_FORMAT,
            f"未指定，使用預設 1.21.1（pack_format {DEFAULT_PACK_FORMAT}）",
        )
    if "." not in raw:
        value = parse_pack_format(raw)
        return ResolvedFormat(value, f"直接使用 pack_format {value}")
    return lookup_version(raw)


def resolve_max_input(text: object) -> int | None:
    """最大使用版本：空白 → None（用預設）；純整數 → 該 pack_format；含 ``.`` → 版本換算。"""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    return resolve_version_input(raw).pack_format


def explain_version_input(
    text: object, max_text: object = ""
) -> tuple[int | None, str, bool]:
    """給輸入框下方的說明：回傳 (pack_format 或 None, 說明文字, 是否有效)。"""
    try:
        resolved = resolve_version_input(text)
        max_pack_format = resolve_max_input(max_text)
        written = describe_pack_format(resolved.pack_format, max_pack_format)
    except ValueError as exc:
        return None, str(exc), False
    note = resolved.note
    if max_pack_format is not None and not has_max_format(resolved.pack_format):
        note += "（此版本沒有最大版本欄位，最大版本不會寫入）"
    return resolved.pack_format, f"{note}；將寫入 {written}", True
