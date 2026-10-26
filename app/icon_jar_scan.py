"""單一 JAR 的圖示預覽掃描：開一次 ZIP，同時讀出 ``en_us.json`` 項目並解析每個 key 的圖示。

單一責任：給定一個 JAR 與（可選的）``ModpackAssetCatalog``，回傳該 JAR 的所有 lang 項目與圖示
URI。不碰執行緒／行程、不碰 UI；執行器在 ``app.icon_scan_pool``，進度與取消由呼叫端處理。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.icon_catalog import ModpackAssetCatalog
from app.icon_index import loads_lang_json
from app.icon_resolve import hit_to_uri, key_needs_icon, resolve_icon
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.zip_safety import (
    ArchiveBudgetError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)

_LANG_PATTERN = re.compile(r"assets/([^/]+)/lang/en_us\.json")

# (key, en, icon_uri | None)
LangRow = tuple[str, object, str | None]


@dataclass
class JarScan:
    """一個 JAR 的掃描結果；``mods`` 為 ``modid -> [(key, en, icon_uri | None)]``。"""

    jar_name: str
    mods: dict[str, list[LangRow]] = field(default_factory=dict)
    failed: bool = False  # 無法讀取（壞 ZIP、累計讀取超限）：沒有任何項目
    logs: list[tuple[int, str]] = field(default_factory=list)


def _read_lang_files(
    zf: zipfile.ZipFile, jar_name: str, result: JarScan
) -> list[tuple[str, dict]]:
    """讀出並解析 JAR 內所有 ``assets/<modid>/lang/en_us.json``，回傳 ``[(modid, data)]``。"""
    budget = ZipReadBudget(label=jar_name)
    parsed: list[tuple[str, dict]] = []
    for name in zf.namelist():
        raise_if_cancelled()
        if not _LANG_PATTERN.search(name):
            continue
        try:
            text = read_limited(zf, name, budget=budget).decode("utf-8")
        except ArchiveBudgetError:
            # 只讀到一部分會讓後續處理誤以為內容完整：整個 JAR 視為沒有內容
            result.failed = True
            return []
        except ZipSizeError as exc:
            log_warning(f"[IconPreview] 略過過大檔案 {jar_name}!{name}: {exc!r}")
            continue
        except UnicodeDecodeError:
            continue
        data = _parse_lang(jar_name, name, text)
        if data is not None:
            parsed.append((name.split("/")[1], data))
    return parsed


def _parse_lang(jar_name: str, name: str, text: str) -> dict | None:
    try:
        data = loads_lang_json(text.removeprefix("﻿"))
    except ValueError:
        log_warning(f"[IconPreview] JAR 解析 JSON 失敗: {jar_name} / {name}")
        return None
    return data if isinstance(data, dict) else None


def _resolve_rows(
    zf: zipfile.ZipFile,
    jar_path: Path,
    parsed: list[tuple[str, dict]],
    catalog: ModpackAssetCatalog | None,
    mods: dict[str, list[LangRow]],
) -> None:
    """把每個 lang 項目與其圖示寫進 ``mods``（沒有 catalog 時不解析，圖示一律為 None）。

    就地填入：中途失敗時，已處理的項目仍保留。
    """
    budget = ZipReadBudget.for_icon_scan(jar_path.name) if catalog else None
    for modid, data in parsed:
        rows = mods.setdefault(modid, [])
        for key, en in data.items():
            raise_if_cancelled()
            icon = None
            if budget is not None and key_needs_icon(key):
                icon, budget = _resolve_one(zf, jar_path, modid, key, catalog, budget)
            rows.append((key, en, icon))


def _resolve_one(zf, jar_path, modid, key, catalog, budget):
    """解析單一 key；累計讀取超限後本 JAR 不再解析（回傳 ``budget=None``）。"""
    try:
        hit = resolve_icon(jar_path, modid, key, catalog, current_zf=zf, budget=budget)
    except ArchiveBudgetError:
        log_warning(
            f"[IconPreview] 略過 {jar_path.name} 剩餘項目的圖示解析（累計讀取超限）"
        )
        return None, None
    return (hit_to_uri(hit) if hit else None), budget


def scan_jar(jar_path: Path, catalog: ModpackAssetCatalog | None) -> JarScan:
    """掃描單一 JAR：lang 項目＋圖示（``catalog`` 為 None 時只讀 lang）。"""
    result = JarScan(jar_path.name)
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            parsed = _read_lang_files(zf, jar_path.name, result)
            _resolve_rows(zf, jar_path, parsed, catalog, result.mods)
    except zipfile.BadZipFile:
        log_warning(f"[IconPreview] 不是有效的 ZIP/JAR: {jar_path.name}")
        result.failed = True
    except OSError as exc:
        log_warning(f"[IconPreview] 讀取失敗: {jar_path.name} - {exc!r}")
        result.failed = True
    except Exception as exc:  # noqa: BLE001 - 單一 JAR 失敗不中止整批，但要留下是哪個 JAR
        log_warning(
            f"[IconPreview] {jar_path.name} 圖示解析中斷：{exc!r}", exc_info=True
        )
        result.failed = not result.mods
    return result
