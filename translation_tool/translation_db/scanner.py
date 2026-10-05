"""scanner.py

掃描 mods 資料夾中的 jar，讀出語言檔（lang）與 Patchouli 書籍，寫入資料庫。

- 以 ``zipfile`` 直接讀 jar，沿用 ``zip_safety`` 的大小與成員數預算（防 ZIP bomb）。
- 內嵌 jar（``META-INF/jarjar``、``META-INF/jars``）會遞迴掃描，最多 3 層。
- 只處理 JSON 格式（1.13 以後）；``.lang`` 舊格式不處理。
- 沒有繁中、只有簡中時，以 OpenCC（s2twp）轉換後標記為「簡中轉繁」。
- 多個 jar 並行讀取，寫入資料庫在呼叫端執行緒進行（SQLite 單一寫入者）。
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import zipfile
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.core.translatable_extractor import extract_translatables
from translation_tool.translation_db.identity import (
    FileIdentity,
    classify_member,
    get_by_path,
    patchouli_dir_names,
)
from translation_tool.translation_db.models import IngestStats, ScanItem
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.text_processor import (
    apply_replace_rules,
    load_replace_rules,
    recursive_translate_dict,
)
from translation_tool.utils.zip_safety import (
    MAX_FILE_BYTES,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)

NESTED_DIRS = ("META-INF/jarjar/", "META-INF/jars/")
MAX_NESTED_DEPTH = 3
CONVERT_MODE = "s2twp"


@dataclass(frozen=True)
class ScanOptions:
    """掃描選項。"""

    version: str
    read_jar_translations: bool = True
    convert_cn: bool = True
    scan_nested: bool = True
    include_patchouli: bool = True
    dry_run: bool = False  # 只讀 jar 並統計，不寫入資料庫
    apply_rules: bool = True  # 與語系合併相同：對 jar 自帶譯文套用「替換規則」
    rules: Any = field(default=(), compare=False, repr=False)  # 載入後的替換規則


@dataclass
class JarResult:
    """單一 jar 的讀取結果（尚未寫入資料庫）。"""

    name: str
    items: list[ScanItem] = field(default_factory=list)
    nested_jars: int = 0
    error: str = ""

    @property
    def has_lang(self) -> bool:
        return bool(self.items)


@dataclass
class ScanReport:
    """整次掃描的彙總。"""

    version: str
    jars_total: int = 0
    jars_with_lang: int = 0
    jars_without_lang: int = 0
    jars_failed: list[str] = field(default_factory=list)
    nested_jars: int = 0
    items_found: int = 0  # 讀到的項目數（預覽時用）
    stats: IngestStats = field(default_factory=IngestStats)
    cancelled: bool = False
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "jars_total": self.jars_total,
            "jars_with_lang": self.jars_with_lang,
            "jars_without_lang": self.jars_without_lang,
            "jars_failed": len(self.jars_failed),
            "nested_jars": self.nested_jars,
            "items_found": self.items_found,
            "dry_run": self.dry_run,
            "new_entries": self.stats.new_entries,
            "existing": self.stats.existing,
            "added_translations": self.stats.added_translations,
            "en_changed": self.stats.en_changed,
            "cancelled": self.cancelled,
        }


def load_rules() -> list:
    """載入替換規則（與語系合併讀同一個檔案）；失敗回傳空清單。"""
    try:
        return load_replace_rules(
            load_config()
            .get("translator", {})
            .get("replace_rules_path", "replace_rules.json")
        )
    except Exception as exc:  # noqa: BLE001 - 規則檔問題不應讓掃描失敗，只是略過替換
        log_warning(f"載入替換規則失敗，略過替換：{exc}")
        return []


def make_converter(rules: list | None = None) -> Callable[[str], str]:
    """簡轉繁（OpenCC s2twp）後套用替換規則；與語系合併的 ``recursive_translate_dict`` 相同。"""
    active = list(rules or [])
    return lambda text: recursive_translate_dict(text, active)


def _load_json(data: bytes) -> dict | None:
    try:
        obj = json.loads(data.decode("utf-8-sig"), strict=False)
    except (UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _group_members(
    names: Sequence[str], options: ScanOptions, dir_names: Sequence[str]
) -> dict[tuple[str, str, str], dict[str, str]]:
    """把成員依 ``(類型, 模組, 檔案鍵)`` 分組：``{lang: 成員路徑}``。"""
    groups: dict[tuple[str, str, str], dict[str, str]] = {}
    for name in names:
        if name.endswith("/"):
            continue
        ident: FileIdentity | None = classify_member(name, dir_names)
        if ident is None:
            continue
        if ident.kind == "patchouli" and not options.include_patchouli:
            continue
        groups.setdefault((ident.kind, ident.mod_id, ident.file_key), {})[
            ident.lang
        ] = name
    return groups


def _items_for_group(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    ident_key: tuple[str, str, str],
    members: dict[str, str],
    options: ScanOptions,
) -> list[ScanItem]:
    kind, mod_id, file_key = ident_key
    en_member = members.get("en_us")
    if en_member is None:
        return []
    en = _load_json(read_limited(zf, en_member, budget=budget))
    if en is None:
        return []

    # 與翻譯流程使用同一套抽取規則，之後查詢才會對得上
    extracted = extract_translatables(en, PurePosixPath(en_member))
    if not extracted:
        return []

    translations: dict[str, dict] = {}
    if options.read_jar_translations:
        for lang in ("zh_tw", "zh_cn"):
            member = members.get(lang)
            if member is None:
                continue
            obj = _load_json(read_limited(zf, member, budget=budget))
            if obj is not None:
                translations[lang] = obj

    ident = FileIdentity(kind, mod_id, file_key, "en_us")
    out: list[ScanItem] = []
    for item in extracted:
        path = item["path"]
        text = item["source_text"]
        tw = get_by_path(translations["zh_tw"], path) if "zh_tw" in translations else ""
        cn = get_by_path(translations["zh_cn"], path) if "zh_cn" in translations else ""
        # 與語系合併相同：只有含中文（CJK）的值才算譯文；繁中先套替換規則
        tw = (
            apply_replace_rules(tw, options.rules)
            if isinstance(tw, str) and contains_cjk(tw)
            else ""
        )
        cn = cn if isinstance(cn, str) and contains_cjk(cn) else ""
        out.append(
            ScanItem(
                kind=kind,
                mod_id=mod_id,
                key=ident.item_key(path),
                en_us=text,
                zh_tw=tw,
                zh_cn=cn,
            )
        )
    return out


def _scan_archive(
    zf: zipfile.ZipFile,
    label: str,
    options: ScanOptions,
    dir_names: Sequence[str],
    result: JarResult,
    depth: int,
) -> None:
    budget = ZipReadBudget(label=label)
    names = zf.namelist()
    for ident_key, members in _group_members(names, options, dir_names).items():
        try:
            result.items.extend(
                _items_for_group(zf, budget, ident_key, members, options)
            )
        except ZipSizeError as exc:
            result.error = str(exc)
            break
    if not options.scan_nested or depth >= MAX_NESTED_DEPTH:
        return
    for name in names:
        if name.endswith("/") or not name.lower().endswith(".jar"):
            continue
        if not name.startswith(NESTED_DIRS):
            continue
        try:
            data = read_limited(zf, name, MAX_FILE_BYTES)
            with zipfile.ZipFile(io.BytesIO(data)) as nested:
                result.nested_jars += 1
                _scan_archive(
                    nested, f"{label}!{name}", options, dir_names, result, depth + 1
                )
        except (zipfile.BadZipFile, ZipSizeError, OSError):
            continue


def scan_jar(
    path: str | Path, options: ScanOptions, dir_names: Sequence[str] | None = None
) -> JarResult:
    """讀取單一 jar（含內嵌 jar）的語言與書籍項目。同一個鍵值只保留第一筆。"""
    p = Path(path)
    result = JarResult(name=p.name)
    dirs = tuple(dir_names) if dir_names is not None else patchouli_dir_names()
    try:
        with zipfile.ZipFile(p) as zf:
            _scan_archive(zf, p.name, options, dirs, result, 0)
    except (zipfile.BadZipFile, OSError) as exc:
        result.error = f"無法讀取：{exc}"
        return result
    seen: set[tuple[str, str, str]] = set()
    unique: list[ScanItem] = []
    for item in result.items:
        marker = (item.kind, item.mod_id, item.key)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    result.items = unique
    return result


def find_jars(folder: str | Path) -> list[Path]:
    """遞迴找出資料夾內所有 jar（排序，掃描順序固定）。"""
    return sorted(Path(folder).rglob("*.jar"), key=lambda x: x.as_posix().lower())


def scan_folder_generator(
    db: TranslationDB | None,
    folder: str | Path,
    options: ScanOptions,
    *,
    should_cancel: Callable[[], bool] | None = None,
    workers: int = 4,
) -> Generator[dict[str, Any], None, None]:
    """掃描資料夾並寫入資料庫；每個 jar 完成就 yield 一次進度（與其他服務相同的格式）。

    最後一次 yield 帶 ``report``（``ScanReport``）。
    """
    report = ScanReport(version=options.version, dry_run=options.dry_run)
    if db is None and not options.dry_run:
        raise ValueError("需要資料庫才能寫入掃描結果")
    jars = find_jars(folder)
    report.jars_total = len(jars)
    if not jars:
        yield {
            "progress": 1.0,
            "log": f"⚠️ 在 {folder} 找不到任何 jar",
            "report": report,
        }
        return

    options = replace(options, rules=load_rules() if options.apply_rules else [])
    convert = make_converter(options.rules) if options.convert_cn else None
    dirs = patchouli_dir_names()
    yield {
        "progress": 0.0,
        "log": f"🔍 找到 {len(jars)} 個 jar，版本 {options.version}",
    }

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(scan_jar, j, options, dirs): j for j in jars}
        try:
            for fut in concurrent.futures.as_completed(futures):
                if should_cancel is not None and should_cancel():
                    report.cancelled = True
                    break
                result: JarResult = fut.result()
                done += 1
                report.nested_jars += result.nested_jars
                if result.error and not result.items:
                    report.jars_failed.append(result.name)
                    log = f"❌ {result.name}　{result.error}"
                elif not result.has_lang:
                    report.jars_without_lang += 1
                    log = f"{result.name}　沒有語言檔"
                else:
                    report.jars_with_lang += 1
                    report.items_found += len(result.items)
                    if options.dry_run or db is None:
                        log = f"{result.name}　可匯入 {len(result.items)} 項"
                    else:
                        stats = db.ingest(options.version, result.items, convert)
                        report.stats.add(stats)
                        log = (
                            f"{result.name}　新增 {stats.new_entries}"
                            f"　補入 {stats.added_translations}　略過 {stats.existing}"
                            + (
                                f"　原文已變動 {stats.en_changed}"
                                if stats.en_changed
                                else ""
                            )
                        )
                yield {"progress": done / len(jars), "log": log}
        finally:
            if report.cancelled:
                for f in futures:
                    f.cancel()

    state = "已取消" if report.cancelled else "完成"
    if options.dry_run or db is None:
        summary = f"🔎 預覽{state}：{report.jars_with_lang} 個 jar 含語言檔，共 {report.items_found} 項（未寫入）"
    else:
        db.record_scan(options.version, str(folder), report.as_dict())
        summary = (
            f"🎉 掃描{state}：新增 {report.stats.new_entries}　補入 {report.stats.added_translations}"
            f"　略過 {report.stats.existing}　原文已變動 {report.stats.en_changed}"
        )
    yield {"progress": 1.0, "log": summary, "report": report}
