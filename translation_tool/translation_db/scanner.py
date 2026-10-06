"""scanner.py

掃描 mods 資料夾中的 jar（或翻譯 ZIP），讀出語言檔（lang）與 Patchouli 書籍，寫入資料庫。

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
import re
import sqlite3
import zipfile
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.core.lm_config_rules import is_translatable_field
from translation_tool.core.translatable_extractor import extract_translatables
from translation_tool.translation_db.identity import (
    FileIdentity,
    classify_member,
    get_by_path,
    patchouli_dir_names,
)
from translation_tool.translation_db.models import IngestStats, ScanItem
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.schema import SRC_CUSTOM
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.text_processor import (
    apply_replace_rules,
    load_replace_rules,
    recursive_translate_dict,
)
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor
from translation_tool.utils.zip_safety import (
    MAX_FILE_BYTES,
    ArchiveBudgetError,
    ZipReadBudget,
    ZipSizeError,
    read_limited,
)

NESTED_DIRS = ("META-INF/jarjar/", "META-INF/jars/")
# 書籍檔的「命名空間資源 ID」（如 patchouli:basics）是引用不是文字；直接匯入時只跳過這一種結構值
_RESOURCE_ID = re.compile(r"^#?[a-z0-9_.-]+:[a-z0-9_./-]+$")
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
    clean_english: bool = True  # 判斷並清理英文內容；關閉則所有英文字串逐字匯入
    translated: bool = False  # 來源是已翻譯的內容（翻譯 ZIP）：zh_tw 不判讀、直接匯入
    translation_source: int = SRC_CUSTOM  # translated 時，zh_tw 的來源代碼


@dataclass
class JarResult:
    """單一 jar 的讀取結果（尚未寫入資料庫）。"""

    name: str
    items: list[ScanItem] = field(default_factory=list)
    nested_jars: int = 0
    skipped_nested: int = 0  # 損毀或過大而略過的內嵌 jar（不影響其餘內容）
    error: str = ""  # 有值＝這個 archive 讀取失敗：items 會被清空，整包不寫入資料庫
    cancelled: bool = False
    notes: list[str] = field(default_factory=list)  # 略過內嵌 jar 的原因（供日誌顯示）

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
    skipped_nested: int = 0
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
            "skipped_nested": self.skipped_nested,
            "items_found": self.items_found,
            "dry_run": self.dry_run,
            "new_entries": self.stats.new_entries,
            "existing": self.stats.existing,
            "added_translations": self.stats.added_translations,
            "en_changed": self.stats.en_changed,
            "adopted": self.stats.adopted,
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
        log_warning(f"載入替換規則失敗，略過替換：{exc!r}")
        return []


def make_converter(rules: list | None = None) -> Callable[[str], str]:
    """簡轉繁（OpenCC s2twp）後套用替換規則；與語系合併的 ``recursive_translate_dict`` 相同。"""
    active = list(rules or [])
    return lambda text: recursive_translate_dict(text, active)


class ScanCancelled(Exception):
    """使用者取消掃描：在讀取解壓的下一個區塊就中止（不必等整個 jar 讀完）。"""


class _DualBudget:
    """同時計入「單一 archive 預算」與「整棵遞迴樹預算」，並在每次讀取時檢查取消。

    ``read_limited`` 只需要 ``begin_member`` / ``charge``，所以直接以相同介面包裝。
    樹預算讓巢狀 jar 重新取得的 archive 預算仍受總量約束（壓縮率高的內嵌 jar 不能放大總解壓量）。
    """

    def __init__(
        self,
        archive: ZipReadBudget,
        tree: ZipReadBudget,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        self._archive, self._tree, self._cancel = archive, tree, should_cancel

    def _check(self) -> None:
        if self._cancel is not None and self._cancel():
            raise ScanCancelled

    def begin_member(self, name: str, declared_size: int) -> None:
        self._check()
        self._tree.begin_member(name, declared_size)
        self._archive.begin_member(name, declared_size)

    def charge(self, nbytes: int, name: str) -> None:
        self._check()
        self._tree.charge(nbytes, name)
        self._archive.charge(nbytes, name)


def _new_budget(options: ScanOptions, label: str) -> ZipReadBudget:
    """翻譯 ZIP 可能含較多檔案，使用較寬鬆的預算。"""
    return (
        ZipReadBudget.for_pack(label)
        if options.translated
        else ZipReadBudget(label=label)
    )


def _new_tree_budget(options: ScanOptions, label: str) -> ZipReadBudget:
    """整棵遞迴樹（外層 + 所有內嵌 jar）共用的預算。"""
    return _new_budget(options, label)


def _load_json(data: bytes) -> dict | None:
    try:
        obj = json.loads(data.decode("utf-8-sig"), strict=False)
    except (UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_all_strings(data: Any, *, is_lang: bool) -> dict[str, str]:
    """不判讀內容，取出全部字串值：``{JSON 路徑: 文字}``。

    lang 檔取頂層所有字串；書籍檔沿用欄位名稱規則（``is_translatable_field``，只看欄位名、不看內容），
    避免把 ``type``、``id`` 之類的結構欄位也當成文字；書籍檔另外略過 ``namespace:id`` 形式的資源引用。
    """
    found: dict[str, str] = {}

    def walk(node: Any, base: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{base}.{key}" if base else str(key)
                if isinstance(value, str):
                    if value.strip() and (
                        (is_lang and not base)
                        or (
                            not is_lang
                            and is_translatable_field(str(key))
                            and not _RESOURCE_ID.match(value.strip())
                        )
                    ):
                        found[path] = value
                else:
                    walk(value, path)
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                path = f"{base}[{idx}]"
                if isinstance(value, str):
                    if value.strip() and not is_lang:
                        found[path] = value
                else:
                    walk(value, path)

    walk(data, "")
    return found


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


def _read_json_member(
    zf: zipfile.ZipFile, budget: ZipReadBudget, member: str | None
) -> dict | None:
    if member is None:
        return None
    return _load_json(read_limited(zf, member, budget=budget))


def _items_for_group(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    ident_key: tuple[str, str, str],
    members: dict[str, str],
    options: ScanOptions,
) -> list[ScanItem]:
    kind, mod_id, file_key = ident_key
    is_lang = kind == "lang"
    en_member = members.get("en_us")
    en = _read_json_member(zf, budget, en_member)
    if not options.translated and en is None:
        return []  # 掃描 jar 以原文為準；沒有 en_us 就沒有可匯入的條目

    # 英文：預設與翻譯流程使用同一套抽取規則（之後查詢才會對得上）；關閉「清理」則全部字串逐字匯入
    en_items: dict[str, str] = {}
    if en is not None and en_member is not None:
        if options.clean_english:
            for it in extract_translatables(en, PurePosixPath(en_member)):
                en_items[it["path"]] = it["source_text"]
        else:
            en_items = extract_all_strings(en, is_lang=is_lang)

    tw_obj = cn_obj = None
    if options.read_jar_translations or options.translated:
        tw_obj = _read_json_member(zf, budget, members.get("zh_tw"))
        cn_obj = _read_json_member(zf, budget, members.get("zh_cn"))

    paths = list(en_items)
    tw_all: dict[str, str] = {}
    if options.translated and tw_obj is not None:
        # 已翻譯來源：zh_tw 的每個字串都匯入，連 en_us 沒有的鍵值也收（原文先留空）
        tw_all = extract_all_strings(tw_obj, is_lang=is_lang)
        paths += [p for p in tw_all if p not in en_items]
    if not paths:
        return []

    ident = FileIdentity(kind, mod_id, file_key, "en_us")
    out: list[ScanItem] = []
    for path in paths:
        tw = cn = ""
        source = None
        if options.translated:
            tw = tw_all.get(path, "")  # 不判讀、不套規則，逐字匯入
            source = options.translation_source
        elif tw_obj is not None:
            raw = get_by_path(tw_obj, path)
            # 與語系合併相同：只有含中文（CJK）的值才算譯文；繁中先套替換規則
            if isinstance(raw, str) and contains_cjk(raw):
                tw = apply_replace_rules(raw, options.rules)
        if cn_obj is not None:
            raw_cn = get_by_path(cn_obj, path)
            if isinstance(raw_cn, str) and contains_cjk(raw_cn):
                cn = raw_cn
        out.append(
            ScanItem(
                kind=kind,
                mod_id=mod_id,
                key=ident.item_key(path),
                en_us=en_items.get(path, ""),
                zh_tw=tw,
                zh_cn=cn,
                source=source,
            )
        )
    return out


def _reason(exc: BaseException) -> str:
    """略過原因：損毀的 jar 的例外訊息常是空字串或英文，補上可讀的說明。"""
    if isinstance(exc, zipfile.BadZipFile):
        return f"不是有效的 zip／jar（{exc}）"
    return str(exc) or exc.__class__.__name__


def _scan_archive(
    zf: zipfile.ZipFile,
    label: str,
    options: ScanOptions,
    dir_names: Sequence[str],
    result: JarResult,
    depth: int,
    tree: ZipReadBudget,
    should_cancel: Callable[[], bool] | None,
) -> None:
    """讀取一個 archive（含內嵌 jar）。安全上限（單一成員、archive、整棵樹）超過時設定 ``result.error``。"""
    budget = _DualBudget(_new_budget(options, label), tree, should_cancel)
    names = zf.namelist()
    for ident_key, members in _group_members(names, options, dir_names).items():
        try:
            result.items.extend(
                _items_for_group(zf, budget, ident_key, members, options)
            )
        except ZipSizeError as exc:
            result.error = str(exc)
            return
    if not options.scan_nested or depth >= MAX_NESTED_DEPTH:
        return
    for name in names:
        if name.endswith("/") or not name.lower().endswith(".jar"):
            continue
        # 翻譯 ZIP 內任何位置的 jar 都掃；一般 jar 只看 jarjar／jars 內嵌目錄
        if not (options.translated or name.startswith(NESTED_DIRS)):
            continue
        try:
            data = read_limited(zf, name, MAX_FILE_BYTES, budget=budget)
            with zipfile.ZipFile(io.BytesIO(data)) as nested:
                result.nested_jars += 1
                _scan_archive(
                    nested,
                    f"{label}!{name}",
                    options,
                    dir_names,
                    result,
                    depth + 1,
                    tree,
                    should_cancel,
                )
        except ArchiveBudgetError as exc:
            result.error = str(exc)  # 累計上限：整包視為失敗，不再讀其餘內嵌 jar
            return
        except (zipfile.BadZipFile, ZipSizeError, OSError) as exc:
            result.skipped_nested += 1  # 單一內嵌 jar 損毀或過大：略過它，記錄在結果中
            result.notes.append(f"略過內嵌 jar {label}!{name}：{_reason(exc)}")
        if result.error:  # 內層已回報安全上限
            return


def scan_jar(
    path: str | Path,
    options: ScanOptions,
    dir_names: Sequence[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> JarResult:
    """讀取單一 jar／zip（含內嵌 jar）的語言與書籍項目。同一個鍵值只保留第一筆。

    失敗契約：``result.error`` 有值（無法讀取、超過安全上限）時 ``items`` 一律清空，
    呼叫端不得寫入部分資料。取消（``result.cancelled``）同理。
    """
    p = Path(path)
    result = JarResult(name=p.name)
    dirs = tuple(dir_names) if dir_names is not None else patchouli_dir_names()
    try:
        with zipfile.ZipFile(p) as zf:
            _scan_archive(
                zf,
                p.name,
                options,
                dirs,
                result,
                0,
                _new_tree_budget(options, p.name),
                should_cancel,
            )
    except ScanCancelled:
        result.cancelled, result.items = True, []
        return result
    except (zipfile.BadZipFile, OSError) as exc:
        result.error = f"無法讀取：{exc}"
        result.items = []
        return result
    if result.error:
        result.items = []
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


def find_jars(folder: str | Path, *, translated: bool = False) -> list[Path]:
    """要掃描的檔案（排序，順序固定）。

    傳入單一檔案就只掃它；資料夾則遞迴找 jar（``translated`` 時也找 zip）。
    """
    path = Path(folder)
    if path.is_file():
        return [path]
    patterns = ("*.jar", "*.zip") if translated else ("*.jar",)
    found = {p for pattern in patterns for p in path.rglob(pattern)}
    return sorted(found, key=lambda x: x.as_posix().lower())


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
    jars = find_jars(folder, translated=options.translated)
    report.jars_total = len(jars)
    if not jars:
        yield {
            "progress": 1.0,
            "log": f"⚠️ 在 {folder} 找不到任何 {'jar／zip' if options.translated else 'jar'}"
            "（請確認路徑，以及資料夾內是否有檔案）",
            "level": "warning",
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
    with ContextThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(scan_jar, j, options, dirs, should_cancel): j for j in jars
        }
        try:
            for fut in concurrent.futures.as_completed(futures):
                if should_cancel is not None and should_cancel():
                    report.cancelled = True
                    break
                result: JarResult = fut.result()
                if result.cancelled:
                    report.cancelled = True
                    break
                done += 1
                level = "info"
                report.nested_jars += result.nested_jars
                report.skipped_nested += result.skipped_nested
                if (
                    result.error
                ):  # 失敗的 archive 不寫入任何資料（避免只匯入一部分卻顯示成功）
                    report.jars_failed.append(result.name)
                    log = f"❌ {result.name}　未寫入：{result.error}"
                    level = "error"
                elif not result.has_lang:
                    report.jars_without_lang += 1
                    log = f"{result.name}　沒有語言檔"
                else:
                    report.jars_with_lang += 1
                    report.items_found += len(result.items)
                    if options.dry_run or db is None:
                        log = f"{result.name}　可匯入 {len(result.items)} 項"
                    else:
                        try:
                            stats = db.ingest(options.version, result.items, convert)
                        except sqlite3.Error as exc:
                            raise RuntimeError(
                                f"寫入資料庫失敗（{result.name}，版本 {options.version}，"
                                f"資料庫 {db.path}）：{exc}"
                            ) from exc
                        report.stats.add(stats)
                        log = (
                            f"{result.name}　新增 {stats.new_entries}"
                            f"　補入 {stats.added_translations}　略過 {stats.existing}"
                            + (f"　補上原文 {stats.adopted}" if stats.adopted else "")
                            + (
                                f"　原文已變動 {stats.en_changed}"
                                if stats.en_changed
                                else ""
                            )
                        )
                if result.skipped_nested and not result.error:
                    log += f"（略過 {result.skipped_nested} 個損毀或過大的內嵌 jar）"
                    level = "warning"
                    log += "".join(f"\n　⚠️ {note}" for note in result.notes)
                yield {"progress": done / len(jars), "log": log, "level": level}
        finally:
            if report.cancelled:
                for f in futures:
                    f.cancel()

    state = "已取消（已處理的檔案已寫入，其餘未處理）" if report.cancelled else "完成"
    if options.dry_run or db is None:
        summary = f"🔎 預覽{state}：{report.jars_with_lang} 個 jar 含語言檔，共 {report.items_found} 項（未寫入）"
    else:
        db.record_scan(options.version, str(folder), report.as_dict())
        summary = (
            f"🎉 掃描{state}：新增 {report.stats.new_entries}　補入 {report.stats.added_translations}"
            f"　略過 {report.stats.existing}　原文已變動 {report.stats.en_changed}"
        )
    if report.jars_failed:
        summary += (
            f"　⚠️ 失敗 {len(report.jars_failed)} 個（{'、'.join(report.jars_failed[:5])}"
            f"{'…' if len(report.jars_failed) > 5 else ''}）"
        )
    yield {
        "progress": 1.0,
        "log": summary,
        "level": "warning" if report.jars_failed or report.cancelled else "info",
        "report": report,
    }
