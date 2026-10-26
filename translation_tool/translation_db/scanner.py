"""scanner.py

掃描 mods 資料夾中的 jar（或翻譯 ZIP），讀出語言檔（lang）與 Patchouli 書籍，寫入資料庫。

- 以 ``zipfile`` 直接讀 jar，沿用 ``zip_safety`` 的大小與成員數預算（防 ZIP bomb）。
- 內嵌 jar（``META-INF/jarjar``、``META-INF/jars``）會遞迴掃描，最多 3 層。
- 只處理 JSON 格式（1.13 以後）；``.lang`` 舊格式不處理。
- 沒有繁中、只有簡中時，以 OpenCC（s2twp）轉換後標記為「簡中轉繁」。
- 大量 jar 以行程池並行讀取（見 ``scan_pool``），寫入資料庫在呼叫端執行緒進行（SQLite 單一寫入者），
  每累積一批才提交一次交易。
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from translation_tool.core.book_dirs import find_book_dir_index
from translation_tool.core.guide_blocks import (
    extract_guide_blocks,
    has_translatable_text,
)
from translation_tool.core.lang_codec import parse_lang_text
from translation_tool.core.lang_merge_dict import contains_cjk
from translation_tool.core.lm_config_rules import is_translatable_field
from translation_tool.core.patchouli_compat import (
    find_patchouli_marker_issue,
    find_patchouli_mismatch,
)
from translation_tool.core.patchouli_semantics import (
    PatchouliContext,
    PatchouliSemantic,
    context_from_archive_documents,
    context_from_metadata,
    iter_patchouli_semantics,
)
from translation_tool.core.text_sniffer import (
    advancement_texts,
    raw_english_fallback_allowed,
)
from translation_tool.core.translatable_extractor import extract_translatables
from translation_tool.translation_db.cleanup import entry_cleanup_category
from translation_tool.translation_db.identity import (
    FileIdentity,
    classify_member,
    get_by_path,
    patchouli_dir_names,
)
from translation_tool.translation_db.models import IngestStats, ScanItem
from translation_tool.translation_db.repository import TranslationDB
from translation_tool.translation_db.scan_ingest import (
    PreviewIngestTarget,
    preview_ingest_many,
)
from translation_tool.translation_db.scan_pool import replay_logs, scan_executor
from translation_tool.translation_db.schema import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    SRC_CUSTOM,
)
from translation_tool.translation_db.sniff_groups import sniffed_groups
from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.nested_archive import (
    is_nested_archive_name,
    walk_nested_archives,
)
from translation_tool.utils.text_processor import (
    ReplaceRules,
    apply_replace_rules,
    load_replace_rules,
    recursive_translate_dict,
)
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
_PATCHOULI_LOCALES = frozenset({"en_us", "zh_tw", "zh_cn"})
_MAX_PATCHOULI_CONTEXT_FILES = 256


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
    clean_english: bool = True  # 關閉＝放寬文字篩選，但仍排除結構值
    translated: bool = False  # 來源是已翻譯的內容（翻譯 ZIP）：zh_tw 不判讀、直接匯入
    translation_source: int = SRC_CUSTOM  # translated 時，zh_tw 的來源代碼


@dataclass
class JarResult:
    """單一 jar 的讀取結果（尚未寫入資料庫）。"""

    name: str
    items: list[ScanItem] = field(default_factory=list)
    nested_jars: int = 0
    skipped_nested: int = 0  # 損毀或過大而略過的內嵌 jar（不影響其餘內容）
    truncated_nested: int = 0  # 到達安全深度上限而未再走訪的內嵌 jar
    error: str = ""  # 有值＝這個 archive 讀取失敗：items 會被清空，整包不寫入資料庫
    cancelled: bool = False
    notes: list[str] = field(default_factory=list)  # 略過內嵌 jar 的原因（供日誌顯示）
    invalid_books: list[str] = field(default_factory=list)
    logs: list[tuple[int, str]] = field(
        default_factory=list
    )  # 行程池 worker 收集的日誌

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
    truncated_nested: int = 0
    items_found: int = 0  # 讀到的項目數（預覽與實際掃描都會統計）
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
            "truncated_nested": self.truncated_nested,
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
    active = rules.snapshot() if isinstance(rules, ReplaceRules) else list(rules or [])
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

    lang 檔取頂層所有字串，加上巢狀的 ``text`` 與陣列裡的字串（和預設抽取規則收的結構相同，只是不篩選內容）；書籍檔沿用欄位名稱規則（``is_translatable_field``，只看欄位名、不看內容），
    避免把 ``type``、``id`` 之類的結構欄位也當成文字；書籍檔另外略過 ``namespace:id`` 形式的資源引用。
    """
    found: dict[str, str] = {}

    def walk(node: Any, base: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{base}.{key}" if base else str(key)
                if isinstance(value, str):
                    if value.strip() and (
                        (is_lang and (not base or str(key) == "text"))
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
                    if value.strip():
                        found[path] = value
                else:
                    walk(value, path)

    walk(data, "")
    return found


def _lang_rank(member: str) -> int:
    """同一個語系有多個檔案時的取捨：新式 JSON 優先於舊式 ``.lang``，``assets`` 優先於 ``data``。"""
    return (
        (4 if member.lower().endswith(".lang") else 0)
        + (2 if member.startswith("data/") else 0)
        + (0 if member.rsplit("/", 1)[-1].lower() == member.rsplit("/", 1)[-1] else 1)
    )


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
        if (
            ident.kind in ("patchouli", KIND_GUIDE, KIND_ADVANCEMENT)
            and not options.include_patchouli
        ):
            continue
        members = groups.setdefault((ident.kind, ident.mod_id, ident.file_key), {})
        previous = members.get(ident.lang)
        if previous is None or _lang_rank(name) < _lang_rank(previous):
            members[ident.lang] = name
    return groups


def _read_json_member(
    zf: zipfile.ZipFile, budget: ZipReadBudget, member: str | None
) -> dict | None:
    if member is None:
        return None
    raw = read_limited(zf, member, budget=budget)
    if member.lower().endswith(".lang"):
        return _load_legacy_lang(raw)
    return _load_json(raw)


def _patchouli_member_info(
    member: str, dir_names: Sequence[str]
) -> tuple[str, str, str] | None:
    """回傳 canonical book root、locale 與書本內相對路徑。

    Patchouli 的實際資源可以把 ``book.json`` 放在 ``data/<namespace>``，把
    entry/template 放在 ``assets/<namespace>``。``assets``／``data`` 是資源
    類型，不是 book identity 的一部分；canonical root 只保留 namespace、
    book directory 與 book name，讓兩邊共用 context。
    """

    parts = tuple(member.replace("\\", "/").split("/"))
    index = find_book_dir_index(parts, dir_names)
    if index is None or index == 0 or len(parts) <= index + 1:
        return None
    book_root = "/".join(parts[index - 1 : index + 2])
    tail = list(parts[index + 2 :])
    locale = "en_us"
    if tail and tail[0].lower() in _PATCHOULI_LOCALES:
        locale = tail.pop(0).lower()
    if not tail:
        return None
    return book_root, locale, "/".join(tail)


def _patchouli_contexts_for_archive(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    names: Sequence[str],
    dir_names: Sequence[str],
) -> dict[str, PatchouliContext]:
    """一次讀取 archive 內的 book.json/template，供所有 entry 共用。

    這裡只讀最多 256 個 template JSON，並沿用 archive 的 ZipReadBudget；不會
    為每一筆候選重新打開或掃描同一本 book。讀不到 context 時不阻斷掃描，
    分類器會把自訂欄位標成 unknown。
    """

    book_members: dict[str, str] = {}
    template_members: dict[str, dict[str, str]] = {}
    for name in names:
        if name.endswith("/"):
            continue
        member_info = _patchouli_member_info(name, dir_names)
        if member_info is None:
            continue
        root, _locale, relative = member_info
        lower = relative.lower()
        if lower == "book.json":
            book_members.setdefault(root, name)
            continue
        if lower.startswith("templates/") and lower.endswith(".json"):
            template_members.setdefault(root, {})[relative] = name

    contexts: dict[str, PatchouliContext] = {}
    roots = set(book_members) | set(template_members)
    for root in roots:
        book = None
        book_member = book_members.get(root)
        if book_member is not None:
            book = _read_json_member(zf, budget, book_member)
        templates: dict[str, dict] = {}
        for relative, member in list(template_members.get(root, {}).items())[
            :_MAX_PATCHOULI_CONTEXT_FILES
        ]:
            data = _read_json_member(zf, budget, member)
            if data is not None:
                templates[relative] = data
        contexts[root] = context_from_archive_documents(
            root,
            book_json=book,
            templates=templates,
            resolution_error=(
                "book.json_missing"
                if book_member is None
                else ("book.json_invalid" if book is None else "")
            ),
        )
    return contexts


def _patchouli_context_for_member(
    member: str | None,
    contexts: Mapping[str, PatchouliContext] | None,
    dir_names: Sequence[str],
) -> PatchouliContext | None:
    if member is None or not contexts:
        return None
    info = _patchouli_member_info(member, dir_names)
    if info is None:
        return None
    return contexts.get(info[0])


def _load_legacy_lang(data: bytes) -> dict | None:
    """舊版 ``key=value`` 語言檔；與語系合併共用 ``parse_lang_text``（續行、註解、空白規則一致）。"""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return parse_lang_text(text) or None


def _drop_stale_book(zh: dict | None, en: dict, member: str | None) -> dict | None:
    """譯文檔和 en_us 格式不同時回傳 None（不採用），否則原樣回傳。"""
    if zh is None:
        return None
    reason = find_patchouli_mismatch(en, zh)
    if reason:
        log_info(f"[ModDB] 手冊譯文與 en_us 格式不同（{reason}），不匯入譯文：{member}")
        return None
    marker_issue = find_patchouli_marker_issue(en, zh)
    if marker_issue:
        log_warning(
            f"[ModDB] 手冊譯文的格式標記與 en_us 不同（{marker_issue}），仍匯入譯文：{member}"
        )
    return zh


def _read_blocks(
    zf: zipfile.ZipFile, budget: ZipReadBudget, member: str | None
) -> list[str] | None:
    """指南文字檔依段落切成的區塊文字（與 Markdown 翻譯流程同一套切法）；讀不出來回傳 None。"""
    if member is None:
        return None
    try:
        text = read_limited(zf, member, budget=budget).decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return [block.text for block in extract_guide_blocks(text, member)]


def _aligned_blocks(
    en: list[str] | None, other: list[str] | None, member: str | None
) -> list[str] | None:
    """譯文區塊數和原文相同才逐段對應；不同代表指南改版後譯文沒跟上，不匯入以免錯位。"""
    if other is None or en is None:
        return other
    if len(en) == 1 and len(other) > 1:
        # 原文整頁只有一個區塊（沒有空行分段）、譯文有分段：整頁對整頁
        return ["\n\n".join(other)]
    if len(other) != len(en):
        log_info(
            f"[ModDB] 指南譯文的段落數與原文不同（{len(en)} / {len(other)}），不匯入譯文：{member}"
        )
        return None
    return other


def _guide_items(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    ident_key: tuple[str, str, str],
    members: dict[str, str],
    options: ScanOptions,
) -> list[ScanItem]:
    """指南（.md／.txt）：每個可翻譯段落一個條目，鍵值是 ``檔案鍵#段落序號``。"""
    kind, mod_id, file_key = ident_key
    en = _read_blocks(zf, budget, members.get("en_us"))
    if en is None and not options.translated:
        return []  # 掃描 jar 以原文為準；沒有原文就沒有可匯入的條目
    tw = cn = None
    if options.read_jar_translations or options.translated:
        tw = _aligned_blocks(
            en, _read_blocks(zf, budget, members.get("zh_tw")), members.get("zh_tw")
        )
        cn = _aligned_blocks(
            en, _read_blocks(zf, budget, members.get("zh_cn")), members.get("zh_cn")
        )
    if en is None and tw is not None and cn is not None and len(tw) != len(cn):
        # 沒有原文可對齊，繁中與簡中段落數不同就無法逐段對應：只匯入繁中
        log_info(
            f"[ModDB] 指南繁中與簡中的段落數不同（{len(tw)} / {len(cn)}），不匯入簡中：{members.get('zh_cn')}"
        )
        cn = None
    count = len(en) if en is not None else max(len(tw or []), len(cn or []))
    ident = FileIdentity(kind, mod_id, file_key, "en_us")
    out: list[ScanItem] = []
    for index in range(count):
        source_text = en[index] if en is not None else ""
        if source_text and contains_cjk(source_text):
            continue  # 原文已是中文，不是要翻譯的內容
        if source_text and not has_translatable_text(source_text):
            continue  # 只有元件標籤，沒有敘述文字
        zh_tw = zh_cn = ""
        source = options.translation_source if options.translated else None
        if tw is not None:
            raw = tw[index]
            if options.translated:
                zh_tw = raw
            elif contains_cjk(raw):
                zh_tw = apply_replace_rules(raw, options.rules)
        if cn is not None and contains_cjk(cn[index]):
            zh_cn = cn[index]
        out.append(
            ScanItem(
                kind=kind,
                mod_id=mod_id,
                key=ident.item_key(str(index)),
                en_us=source_text,
                zh_tw=zh_tw,
                zh_cn=zh_cn,
                source=source,
            )
        )
    return out


def _advancement_items(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    ident_key: tuple[str, str, str],
    members: dict[str, str],
) -> list[ScanItem]:
    """進度：``display.title``／``display.description`` 的字面文字（進度檔沒有語系變體）。"""
    kind, mod_id, file_key = ident_key
    data = _read_json_member(zf, budget, members.get("en_us"))
    ident = FileIdentity(kind, mod_id, file_key, "en_us")
    return [
        ScanItem(
            kind=kind,
            mod_id=mod_id,
            key=ident.item_key(path),
            en_us=text,
            zh_tw="",
            zh_cn="",
            source=None,
        )
        for path, text in advancement_texts(data).items()
    ]


def _patchouli_value_map(
    document: dict,
    member: str,
    context: PatchouliContext,
    *,
    include_localized: bool = False,
) -> tuple[dict[str, str], dict[str, PatchouliSemantic]]:
    semantics = iter_patchouli_semantics(
        document, PurePosixPath(member), context=context
    )
    values = {
        item.path: item.value
        for item in semantics
        if item.writer_allowed
        or (
            include_localized
            and item.kind == "display_nontranslatable"
            and item.reason == "empty_or_already_localized"
        )
    }
    return values, {item.path: item for item in semantics}


def _english_value_map(
    kind: str,
    document: dict | None,
    member: str | None,
    options: ScanOptions,
    context: PatchouliContext | None,
) -> tuple[dict[str, str], dict[str, PatchouliSemantic]]:
    if document is None or member is None:
        return {}, {}
    if kind == "patchouli":
        if context is None:
            return {}, {}
        return _patchouli_value_map(document, member, context)
    if options.clean_english:
        return (
            {
                item["path"]: item["source_text"]
                for item in extract_translatables(document, PurePosixPath(member))
            },
            {},
        )
    return extract_all_strings(document, is_lang=kind == "lang"), {}


def _translated_value_map(
    kind: str,
    document: dict | None,
    member: str | None,
    context: PatchouliContext | None,
) -> dict[str, str]:
    if document is None or member is None:
        return {}
    if kind == "patchouli":
        if context is None:
            return {}
        values, _ = _patchouli_value_map(
            document, member, context, include_localized=True
        )
        return values
    return extract_all_strings(document, is_lang=kind == "lang")


def _build_scan_items(
    *,
    ident: FileIdentity,
    paths: Sequence[str],
    kind: str,
    options: ScanOptions,
    en_items: Mapping[str, str],
    en_raw: Mapping[str, str],
    semantic_by_path: Mapping[str, PatchouliSemantic],
    tw_all: Mapping[str, str],
    tw_obj: dict | None,
    cn_obj: dict | None,
) -> list[ScanItem]:
    out: list[ScanItem] = []
    for path in paths:
        key = ident.item_key(path)
        tw = cn = ""
        source = None
        if options.translated:
            tw = tw_all.get(path, "")
            source = options.translation_source
        elif tw_obj is not None:
            raw = get_by_path(tw_obj, path)
            if isinstance(raw, str) and contains_cjk(raw):
                tw = apply_replace_rules(raw, options.rules)
        if cn_obj is not None:
            raw_cn = get_by_path(cn_obj, path)
            if isinstance(raw_cn, str) and contains_cjk(raw_cn):
                cn = raw_cn
        if (
            entry_cleanup_category(
                kind,
                key,
                en_items.get(path) or en_raw.get(path, ""),
                tw or cn,
                semantic=semantic_by_path.get(path),
            )
            is not None
        ):
            continue
        source_text = en_items.get(path, "")
        if not source_text:
            raw_source = en_raw.get(path, "")
            if kind == "patchouli":
                semantic = semantic_by_path.get(path)
                if semantic is not None and semantic.ai_allowed:
                    source_text = raw_source
                elif raw_source:
                    continue
            elif raw_english_fallback_allowed(kind, path, raw_source):
                source_text = raw_source
            elif raw_source:
                continue
        out.append(
            ScanItem(
                kind=kind,
                mod_id=ident.mod_id,
                key=key,
                en_us=source_text,
                zh_tw=tw,
                zh_cn=cn,
                source=source,
            )
        )
    return out


def _items_for_group(
    zf: zipfile.ZipFile,
    budget: ZipReadBudget,
    ident_key: tuple[str, str, str],
    members: dict[str, str],
    options: ScanOptions,
    patchouli_contexts: Mapping[str, PatchouliContext] | None = None,
    dir_names: Sequence[str] = (),
) -> list[ScanItem]:
    kind, mod_id, file_key = ident_key
    if kind == KIND_GUIDE:
        return _guide_items(zf, budget, ident_key, members, options)
    if kind == KIND_ADVANCEMENT:
        return _advancement_items(zf, budget, ident_key, members)
    en_member = members.get("en_us")
    en = _read_json_member(zf, budget, en_member)
    if not options.translated and en is None:
        return []  # 掃描 jar 以原文為準；沒有 en_us 就沒有可匯入的條目

    patchouli_context = _patchouli_context_for_member(
        en_member or members.get("zh_tw") or members.get("zh_cn"),
        patchouli_contexts,
        dir_names,
    )
    if kind == "patchouli" and patchouli_context is None:
        patchouli_context = context_from_metadata(
            en_member or members.get("zh_tw") or members.get("zh_cn") or file_key,
            resolution_error="archive_context_missing",
        )

    # 英文與 Patchouli 語意判斷共用同一個 value map；關閉清理只放寬
    # 文字形狀篩選，不能繞過結構性保護。
    en_items, semantic_by_path = _english_value_map(
        kind, en, en_member, options, patchouli_context
    )

    tw_obj = cn_obj = None
    if options.read_jar_translations or options.translated:
        tw_obj = _read_json_member(zf, budget, members.get("zh_tw"))
        cn_obj = _read_json_member(zf, budget, members.get("zh_cn"))
        if kind == "patchouli" and en is not None:
            # 手冊譯文以 JSON 路徑和原文對應；格式和 en_us 不同（頁數、欄位、舊版物品 NBT）
            # 時路徑會對到別頁，匯入只會把錯位的譯文寫進資料庫，所以不採用。
            tw_obj = _drop_stale_book(tw_obj, en, members.get("zh_tw"))
            cn_obj = _drop_stale_book(cn_obj, en, members.get("zh_cn"))

    paths = list(en_items)
    tw_all: dict[str, str] = {}
    if options.translated and tw_obj is not None:
        # 已翻譯來源：zh_tw 的每個字串都匯入，連 en_us 沒有的鍵值也收（原文先留空）
        tw_all = _translated_value_map(
            kind, tw_obj, members.get("zh_tw"), patchouli_context
        )
        paths += [p for p in tw_all if p not in en_items]
    if options.translated and tw_obj is None and cn_obj is not None:
        # 翻譯來源只有簡中：條目照樣建立（沒有 en_us 的鍵值也收），簡中交給匯入轉繁
        cn_all = _translated_value_map(
            kind, cn_obj, members.get("zh_cn"), patchouli_context
        )
        paths += [p for p in cn_all if p not in en_items]
    if not paths:
        return []

    ident = FileIdentity(kind, mod_id, file_key, "en_us")
    en_raw = (
        {path: item.value for path, item in semantic_by_path.items()}
        if options.translated and en is not None and kind == "patchouli"
        else (
            extract_all_strings(en, is_lang=kind == "lang")
            if options.translated and en is not None
            else {}
        )
    )
    return _build_scan_items(
        ident=ident,
        paths=paths,
        kind=kind,
        options=options,
        en_items=en_items,
        en_raw=en_raw,
        semantic_by_path=semantic_by_path,
        tw_all=tw_all,
        tw_obj=tw_obj,
        cn_obj=cn_obj,
    )


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

    def visit_archive(
        current: zipfile.ZipFile,
        current_label: str,
        current_depth: int,
        _archive_chain: tuple[str, ...],
        current_budget: _DualBudget,
    ) -> bool:
        if current_depth:
            result.nested_jars += 1
        names = current.namelist()
        groups = _group_members(names, options, dir_names)
        patchouli_contexts: dict[str, PatchouliContext] = {}
        if options.include_patchouli:
            claimed = {n for members in groups.values() for n in members.values()}
            sniff_diagnostics: list[str] = []
            for key, members in sniffed_groups(
                current,
                names,
                claimed,
                translated=options.translated,
                diagnostics=sniff_diagnostics,
            ).items():
                groups.setdefault(key, members)
            for member in dict.fromkeys(sniff_diagnostics):
                result.invalid_books.append(f"{current_label}:{member}")
                result.notes.append(
                    f"略過無法解析的書籍 JSON：{current_label}:{member}"
                )
            patchouli_contexts = (
                _patchouli_contexts_for_archive(
                    current, current_budget, names, dir_names
                )
                if any(kind == "patchouli" for kind, _mod, _file in groups)
                else {}
            )
        for ident_key, members in groups.items():
            try:
                result.items.extend(
                    _items_for_group(
                        current,
                        current_budget,
                        ident_key,
                        members,
                        options,
                        patchouli_contexts,
                        dir_names,
                    )
                )
            except ZipSizeError as exc:
                result.error = str(exc)
                return False
        return not result.error

    def make_child_budget(child_label: str) -> _DualBudget:
        return _DualBudget(_new_budget(options, child_label), tree, should_cancel)

    def on_nested_error(child_label: str, name: str, exc: BaseException) -> None:
        result.skipped_nested += 1
        result.notes.append(f"略過內嵌 jar {child_label}：{_reason(exc)}")

    def on_nested_truncated(child_label: str, depth: int) -> None:
        result.truncated_nested += 1
        result.notes.append(f"內嵌 jar 深度超過上限（depth={depth}）：{child_label}")

    root_budget = _DualBudget(_new_budget(options, label), tree, should_cancel)
    try:
        walk_nested_archives(
            zf,
            label,
            root_budget,
            visit_archive,
            budget_factory=make_child_budget,
            should_cancel=should_cancel,
            max_depth=MAX_NESTED_DEPTH,
            on_nested_error=on_nested_error,
            on_nested_truncated=on_nested_truncated,
            archive_max_bytes=MAX_FILE_BYTES,
            scan_nested=options.scan_nested,
            nested_name_predicate=(
                (lambda name: name.lower().endswith(".jar"))
                if options.translated
                else is_nested_archive_name
            ),
        )
    except ArchiveBudgetError as exc:
        result.error = str(exc)


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


def _progress(
    done: int, jars: Sequence[Path], result: JarResult, log: str, level: str = "info"
) -> dict[str, Any]:
    """一個 jar 處理完的進度更新；略過內嵌 jar 時附上說明並升為警告。"""
    if (result.skipped_nested or result.truncated_nested) and not result.error:
        details = []
        if result.skipped_nested:
            details.append(f"略過 {result.skipped_nested} 個損毀或過大的內嵌 jar")
        if result.truncated_nested:
            details.append(f"截斷 {result.truncated_nested} 個超過深度上限的內嵌 jar")
        log += "（" + "；".join(details) + "）"
        level = "warning"
        log += "".join(f"\n　⚠️ {note}" for note in result.notes)
    return {"progress": done / len(jars), "log": log, "level": level}


def ingest_counts_text(stats: IngestStats, *, always_changed: bool = False) -> str:
    """「新增 N　補入 N　略過 N　補上原文 N　原文已變動 N」：預覽與實際掃描共用同一組欄位與用語。"""
    return (
        f"新增 {stats.new_entries:,}"
        f"　補入 {stats.added_translations:,}　略過 {stats.existing:,}"
        + (f"　補上原文 {stats.adopted:,}" if stats.adopted else "")
        + (
            f"　原文已變動 {stats.en_changed:,}"
            if stats.en_changed or always_changed
            else ""
        )
    )


def _ingest_log(result: JarResult, stats: IngestStats) -> str:
    return f"{result.name}　{ingest_counts_text(stats)}"


class _IngestBuffer:
    """累積讀好的 jar，滿一批才以單一交易寫入資料庫（每次提交的固定成本不隨 jar 數成長）。"""

    MAX_JARS = 32
    MAX_ITEMS = 15_000

    def __init__(self, db, version: str, convert) -> None:
        self.db, self.version, self.convert = db, version, convert
        self.pending: list[tuple[int, JarResult]] = []
        self.items = 0

    def add(self, done: int, result: JarResult) -> bool:
        """加入一個 jar；回傳是否該寫入了。"""
        self.pending.append((done, result))
        self.items += len(result.items)
        return len(self.pending) >= self.MAX_JARS or self.items >= self.MAX_ITEMS

    def flush(self) -> list[tuple[int, JarResult, IngestStats]]:
        pending, self.pending, self.items = self.pending, [], 0
        if not pending:
            return []
        try:
            stats = self.db.ingest_many(
                self.version, [r.items for _, r in pending], self.convert
            )
        except sqlite3.Error as exc:
            names = "、".join(r.name for _, r in pending[:3])
            raise RuntimeError(
                f"寫入資料庫失敗（{names}{'…' if len(pending) > 3 else ''}，"
                f"版本 {self.version}，資料庫 {self.db.path}）：{exc}"
            ) from exc
        return [(d, r, st) for (d, r), st in zip(pending, stats, strict=True)]


def _flush(
    buffer: _IngestBuffer, report: ScanReport, jars: Sequence[Path]
) -> Generator[dict[str, Any], None, None]:
    for done, result, stats in buffer.flush():
        report.stats.add(stats)
        yield _progress(done, jars, result, _ingest_log(result, stats))


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
        "log": (
            f"🔍 找到 {len(jars)} 個翻譯檔（ZIP／jar），版本 {options.version}"
            if options.translated
            else f"🔍 找到 {len(jars)} 個 jar，版本 {options.version}"
        ),
    }

    done = 0
    buffer = _IngestBuffer(db, options.version, convert)
    # 預覽用與實際寫入相同的分類規則，只是不寫入（見 scan_ingest）；沒有資料庫時一定是預覽
    preview = (
        (
            db.scan_preview_target(options.version)
            if db is not None
            else PreviewIngestTarget(None, options.version)
        )
        if options.dry_run
        else None
    )

    with (
        scan_executor(
            options,
            dirs,
            workers=workers,
            jar_count=len(jars),
            should_cancel=should_cancel,
        ) as (pool, submit),
        bounded_as_completed(
            pool,
            jars,
            submit,
            max_in_flight=max(1, workers) * 2,
        ) as completed,
    ):
        for fut, _jar_path in completed:
            if should_cancel is not None and should_cancel():
                report.cancelled = True
                break
            result: JarResult = fut.result()
            replay_logs(result.logs)
            if result.cancelled:
                report.cancelled = True
                break
            done += 1
            report.nested_jars += result.nested_jars
            report.skipped_nested += result.skipped_nested
            report.truncated_nested += result.truncated_nested
            if (
                result.error
            ):  # 失敗的 archive 不寫入任何資料（避免只匯入一部分卻顯示成功）
                report.jars_failed.append(result.name)
                yield _progress(
                    done,
                    jars,
                    result,
                    f"❌ {result.name}　未寫入：{result.error}",
                    "error",
                )
            elif not result.has_lang:
                report.jars_without_lang += 1
                yield _progress(done, jars, result, f"{result.name}　沒有語言檔")
            else:
                report.jars_with_lang += 1
                report.items_found += len(result.items)
                if preview is not None:
                    stats = preview_ingest_many(preview, [result.items], convert)[0]
                    report.stats.add(stats)
                    yield _progress(
                        done, jars, result, f"🔎 {_ingest_log(result, stats)}（預覽）"
                    )
                elif buffer.add(done, result):
                    yield from _flush(buffer, report, jars)
        # 取消或結束時，已讀取的 jar 也要寫入
        yield from _flush(buffer, report, jars)

    unit = "個檔案" if options.translated else "個 jar"
    found = f"{report.jars_with_lang:,} {unit} 含語言檔，共 {report.items_found:,} 項"
    counts = ingest_counts_text(report.stats, always_changed=True)
    if preview is not None:
        state = "已取消（只統計已讀取的檔案）" if report.cancelled else "完成"
        summary = f"🔎 預覽{state}：{found}；{counts}（未寫入）"
    else:
        state = (
            "已取消（已處理的檔案已寫入，其餘未處理）" if report.cancelled else "完成"
        )
        db.record_scan(options.version, str(folder), report.as_dict())
        summary = f"🎉 掃描{state}：{found}；{counts}"
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
