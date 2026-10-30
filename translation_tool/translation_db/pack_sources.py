"""模組包中的 KubeJS／FTB Quests 靜態文字掃描器。

這個模組只做安全的「文字候選擷取」：不執行 JavaScript、不改寫來源檔案，
並把每個候選的來源檔案與 selector 一起帶入資料庫。真正的回寫仍由各格式
既有的 pipeline writer 負責，避免把不可直接放進 Resource Pack 的內容混入 lang。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import ftb_snbt_lib as snbt
from ftb_snbt_lib.tag import Compound, List

from translation_tool.plugins.ftbquests.ftbquests_snbt_extractor import (
    is_extractable_line,
    is_lang_key_ref,
    is_lang_key_ref_like,
    is_tag_condition_text,
    walk_snbt_file,
)
from translation_tool.plugins.kubejs.kubejs_lang_selection import (
    count_language_values,
    normalize_locale,
    select_source_locale,
    selected_locales,
)
from translation_tool.plugins.kubejs.kubejs_tooltip_extract import (
    KUBEJS_DEFAULT_LANG_NAMESPACE,
    extract_js_string_call,
    is_client_lang_add_shape,
    parse_client_lang_add,
    split_js_args,
)
from translation_tool.translation_db.models import ScanItem, SourceReference
from translation_tool.translation_db.schema import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
    KIND_KUBEJS_LANG,
    KIND_KUBEJS_SCRIPT,
    KIND_LANG,
    SRC_CUSTOM,
)

_LANG_SUFFIXES = (".title", ".quest_desc", ".quest_subtitle", ".chapter_subtitle")
_CJK = re.compile(r"[\u3400-\u9fff]")
_KUBEJS_CALL = re.compile(r"(ClientEvents\.lang|ItemEvents\.tooltip)\s*\(")
_EVENT_ADD = re.compile(r"\bevent\.add\s*\(")
_SCENE_TEXT = re.compile(r"\bscene\.text\s*\(")
_REFERENCE = re.compile(r"\{([^{}]+)\}")
_TRANSLATE_EXPR = re.compile(
    r"^(?:Text|Component)\.(?:translate|translatable)\s*\(", re.IGNORECASE
)
_KUBEJS_LANG_LOCALES = {"en_us", "zh_cn", "zh_tw"}


def _mask_js_non_code(text: str) -> str:
    """以等長空白遮蔽字串與註解，讓 API regex 不會命中資料內容。"""
    out = list(text)
    quote = ""
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote:
            out[i] = " "
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out[i] = " "
            i += 1
            continue
        if ch == "/" and nxt == "/":
            out[i] = out[i + 1] = " "
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < len(text):
                if text[i] == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    i += 2
                    break
                out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def _extract_js_call_args(text: str, start: int) -> str | None:
    """擷取括號內容，忽略字串／註解中的括號。"""
    depth = 1
    quote = ""
    escaped = False
    i = start
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "/" and nxt == "/":
            newline = text.find("\n", i + 2)
            i = len(text) if newline < 0 else newline
            continue
        elif ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return None


_LITERAL_EXPR = re.compile(
    r"^(?:Text|Component)\.(?:of|literal|raw|red|green|blue|gold|yellow|gray|grey|white|black|aqua|dark[A-Za-z]+)\s*\(",
    re.IGNORECASE,
)


@dataclass
class PackScanResult:
    """一個模組包的掃描結果；不包含任何資料庫寫入副作用。"""

    pack_scope: str = ""
    items: list[ScanItem] = field(default_factory=list)
    references: list[SourceReference] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)
    files_seen: int = 0
    sources_found: list[str] = field(default_factory=list)
    locale_fallbacks: dict[str, str] = field(default_factory=dict)
    source_directories: dict[str, bool] = field(default_factory=dict)
    files_seen_by_source: dict[str, int] = field(default_factory=dict)
    files_read_by_source: dict[str, int] = field(default_factory=dict)
    sources_found_by_source: dict[str, int] = field(default_factory=dict)

    def mark_file(self, source: str) -> None:
        """記錄一個符合該格式的來源檔案（即使之後讀取或解析失敗）。"""
        self.files_seen += 1
        self.files_seen_by_source[source] = self.files_seen_by_source.get(source, 0) + 1

    def mark_file_read(self, source: str) -> None:
        """記錄原始檔案內容成功讀取。"""
        self.files_read_by_source[source] = self.files_read_by_source.get(source, 0) + 1

    def mark_source(self, source: str, relative_path: str) -> None:
        """記錄可供後續處理的來源路徑並保留既有總清單。"""
        self.sources_found.append(relative_path)
        self.sources_found_by_source[source] = (
            self.sources_found_by_source.get(source, 0) + 1
        )

    def source_breakdown(self) -> dict[str, dict[str, int]]:
        """回傳 UI 與報告共用的 KubeJS／FTB Quests 分組統計。"""
        groups = {
            "kubejs": {KIND_KUBEJS_LANG, KIND_KUBEJS_SCRIPT},
            "ftb_quests": {KIND_FTB_QUEST_LANG, KIND_FTB_QUESTS},
        }
        breakdown: dict[str, dict[str, int]] = {}
        for source, kinds in groups.items():
            breakdown[source] = {
                "files_seen": self.files_seen_by_source.get(source, 0),
                "files_read": self.files_read_by_source.get(source, 0),
                "sources_found": self.sources_found_by_source.get(source, 0),
                "items_found": sum(item.kind in kinds for item in self.items),
                "references_found": sum(
                    reference.source_kind in kinds for reference in self.references
                ),
            }
        return breakdown


def discover_pack_roots(
    folder: str | Path, *, max_depth: int = 4, max_candidates: int = 32
) -> list[Path]:
    """找出指定路徑下可用的模組包根目錄。

    ``kubejs``、``config/ftbquests/quests`` 可能位於解壓包的 wrapper、
    ``overrides`` 或直接根目錄。這裡只做有限深度、拒絕 reparse point 的
    結構探索；多個結果不會自動合併，呼叫端必須讓使用者明確選擇。
    """
    current = _discovery_start(folder)
    if current is None:
        return []
    roots: dict[Path, int] = {}
    _add_discovery_root(roots, current, 0)
    if current.name.lower() in {"mods", "kubejs", "config", "quests"}:
        _add_discovery_root(roots, current.parent, 0)
    for parent in current.parents[:4]:
        _add_discovery_root(roots, parent, 1)
    _walk_discovery_tree(current, max_depth, roots)
    return _rank_discovery_roots(roots, max_candidates)


def _discovery_start(folder: str | Path) -> Path | None:
    try:
        current = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return current.parent if current.is_file() else current


def _add_discovery_root(roots: dict[Path, int], root: Path, rank: int) -> None:
    try:
        resolved = root.resolve()
    except (OSError, RuntimeError):
        return
    if _has_pack_sources(resolved):
        roots[resolved] = min(rank, roots.get(resolved, rank))


def _walk_discovery_tree(start: Path, max_depth: int, roots: dict[Path, int]) -> None:
    pending: list[tuple[Path, int]] = [(start, 0)]
    visited: set[Path] = set()
    while pending and len(visited) < 4096:
        directory, depth = pending.pop()
        if depth > max_depth:
            continue
        try:
            directory = directory.resolve()
        except (OSError, RuntimeError):
            continue
        if directory in visited or _is_reparse_point(directory):
            continue
        visited.add(directory)
        _add_discovery_root(roots, directory, 2 if depth else 0)
        _visit_discovery_children(directory, depth, max_depth, roots, pending)


def _visit_discovery_children(
    directory: Path,
    depth: int,
    max_depth: int,
    roots: dict[Path, int],
    pending: list[tuple[Path, int]],
) -> None:
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
    except OSError:
        return
    for entry in entries:
        _queue_discovery_child(entry, depth, max_depth, roots, pending)


def _queue_discovery_child(
    entry: os.DirEntry,
    depth: int,
    max_depth: int,
    roots: dict[Path, int],
    pending: list[tuple[Path, int]],
) -> None:
    child = Path(entry.path)
    try:
        if entry.is_symlink() or _is_reparse_point(child):
            return
        if not entry.is_dir(follow_symlinks=False):
            return
        name = entry.name.casefold()
        if name == "kubejs":
            _add_discovery_root(roots, child.parent, 2)
        elif _is_ftb_quests_directory(child, name):
            _add_discovery_root(roots, child.parents[2], 2)
        if depth < max_depth:
            pending.append((child, depth + 1))
    except OSError:
        return


def _is_ftb_quests_directory(child: Path, name: str) -> bool:
    return (
        name == "quests"
        and child.parent.name.casefold() == "ftbquests"
        and len(child.parents) > 2
        and child.parents[1].name.casefold() == "config"
    )


def _rank_discovery_roots(roots: dict[Path, int], max_candidates: int) -> list[Path]:
    return [
        root
        for root, _rank in sorted(
            roots.items(), key=lambda pair: (pair[1], str(pair[0]).casefold())
        )[:max_candidates]
    ]


def discover_pack_root(folder: str | Path) -> Path | None:
    """相容舊呼叫者：回傳排序後第一個根目錄。

    新的流水線應使用 :func:`discover_pack_roots`，遇到多個候選時要求明確選擇。
    """
    roots = discover_pack_roots(folder)
    return roots[0] if roots else None


def scan_pack_sources(
    pack_root: str | Path,
    version: str,
    *,
    include_kubejs: bool = True,
    include_ftb: bool = True,
) -> PackScanResult:
    """掃描選定的 KubeJS／FTB Quests 來源，保守地只輸出可定位文字。"""
    root = Path(pack_root).expanduser().resolve()
    result = PackScanResult(pack_scope=_pack_scope(root))
    if not root.is_dir():
        result.issues.append(_issue(root, "source_missing", "source_not_directory"))
        return result
    scope = result.pack_scope
    kubejs = root / "kubejs"
    quests = root / "config" / "ftbquests" / "quests"
    result.source_directories = {
        "kubejs": kubejs.is_dir(),
        "ftb_quests": quests.is_dir(),
    }
    if include_kubejs and kubejs.is_dir():
        _scan_kubejs(kubejs, version, scope, result)
    if include_ftb and quests.is_dir():
        _scan_ftb_quests(quests, version, scope, result)
    _resolve_reference_targets(result)
    return result


def pack_scope_for_root(pack_root: str | Path) -> str:
    """回傳與掃描器相同的模組包身分，不重新掃描內容。"""
    root = Path(pack_root).expanduser().resolve()
    return _pack_scope(root)


def _has_pack_sources(root: Path) -> bool:
    return (
        _has_kubejs_source(root) or (root / "config" / "ftbquests" / "quests").is_dir()
    )


def _has_kubejs_source(root: Path) -> bool:
    """確認 ``root/kubejs`` 是 KubeJS 來源，而不是 namespace 內容資料夾。

    KubeJS 的 ``assets/<namespace>`` 內可能再次出現名為 ``kubejs`` 的
    namespace。單純檢查 ``root/kubejs`` 是否存在，會把
    ``<pack>/kubejs/assets`` 或 ``dynamic_data`` 誤判成第二個模組包根目錄。
    只接受 KubeJS 已知的來源子目錄或直接存在的腳本檔案，讓候選清單
    保持「可實際掃描的 pack root」語意。
    """
    kubejs = root / "kubejs"
    if not kubejs.is_dir() or _is_reparse_point(kubejs):
        return False
    known_source_dirs = {
        "assets",
        "client_scripts",
        "config",
        "data",
        "server_scripts",
        "startup_scripts",
    }
    try:
        for entry in os.scandir(kubejs):
            if entry.is_symlink() or _is_reparse_point(Path(entry.path)):
                continue
            if entry.is_dir(follow_symlinks=False) and entry.name.casefold() in {
                name.casefold() for name in known_source_dirs
            }:
                return True
            if (
                entry.is_file(follow_symlinks=False)
                and Path(entry.name).suffix.lower() == ".js"
            ):
                return True
    except OSError:
        return False
    return False


def _pack_scope(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"{root.name or 'pack'}@{digest}"


def _issue(
    path: Path, reason: str, detail: str, *, status: str = "pending"
) -> dict[str, str]:
    return {
        "source_archive": str(path),
        "source_member": path.name,
        "kind": "pack_source",
        "status": status,
        "reason": reason,
        "detail": detail,
    }


def _is_reparse_point(path: Path) -> bool:
    """判斷 symbolic link 或 Windows junction，避免掃描逃出模組包根目錄。"""
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _iter_files(root: Path, suffixes: tuple[str, ...], result: PackScanResult):
    """以不追蹤 reparse point 的方式遞迴列出一般檔案。"""
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                children = sorted(entries, key=lambda entry: entry.name.lower())
        except OSError as exc:
            result.issues.append(
                _issue(current, "source_read_failed", str(exc), status="parse_failed")
            )
            continue
        for entry in children:
            path = Path(entry.path)
            try:
                if entry.is_symlink() or _is_reparse_point(path):
                    result.issues.append(
                        _issue(
                            path,
                            "source_link_skipped",
                            "symbolic link 或 junction 不會被追蹤",
                        )
                    )
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif (
                    entry.is_file(follow_symlinks=False)
                    and path.suffix.lower() in suffixes
                ):
                    yield path
            except OSError as exc:
                result.issues.append(
                    _issue(path, "source_read_failed", str(exc), status="parse_failed")
                )


def _read_text(path: Path, result: PackScanResult) -> tuple[str, str] | None:
    try:
        raw = path.read_bytes()
        return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError) as exc:
        result.issues.append(
            _issue(path, "source_read_failed", str(exc), status="parse_failed")
        )
        return None


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _scan_kubejs(root: Path, version: str, scope: str, result: PackScanResult) -> None:
    for path in _iter_files(root, (".js",), result):
        result.mark_file("kubejs")
        rel = _relative(path, root)
        result.mark_source("kubejs", rel)
        loaded = _read_text(path, result)
        if loaded is None:
            continue
        result.mark_file_read("kubejs")
        text, digest = loaded
        _scan_kubejs_script(text, rel, digest, scope, version, result)
    _scan_kubejs_lang(root, version, scope, result)


def _scan_kubejs_script(
    text: str,
    rel: str,
    digest: str,
    scope: str,
    version: str,
    result: PackScanResult,
) -> None:
    lang_occurrence = 0
    tooltip_occurrences: defaultdict[str, int] = defaultdict(int)
    code = _mask_js_non_code(text)
    for call in _KUBEJS_CALL.finditer(code):
        args = _extract_js_call_args(text, call.end())
        if not args:
            continue
        lang_occurrence = _scan_kubejs_event(
            call.group(1),
            args,
            rel,
            digest,
            scope,
            version,
            result,
            lang_occurrence,
            tooltip_occurrences,
        )
    _scan_kubejs_scenes(text, code, rel, digest, scope, version, result)


def _scan_kubejs_event(
    event_name: str,
    args: str,
    rel: str,
    digest: str,
    scope: str,
    version: str,
    result: PackScanResult,
    lang_occurrence: int,
    tooltip_occurrences: defaultdict[str, int],
) -> int:
    """掃描一個 KubeJS event callback，回傳語系事件的累計序號。"""
    lang_locale = _kubejs_lang_locale(event_name, args, rel, result)
    if event_name == "ClientEvents.lang" and lang_locale is None:
        return lang_occurrence
    args_code = _mask_js_non_code(args)
    for match in _EVENT_ADD.finditer(args_code):
        call_args = _extract_js_call_args(args, match.end())
        if not call_args:
            continue
        parts = split_js_args(call_args)
        if not is_client_lang_add_shape(parts):
            continue
        if event_name == "ClientEvents.lang":
            lang_occurrence += 1
            _append_kubejs_lang_item(
                parts,
                lang_locale or "en_us",
                rel,
                digest,
                scope,
                version,
                result,
                lang_occurrence,
            )
        else:
            _append_kubejs_tooltip_item(
                parts, rel, digest, scope, version, result, tooltip_occurrences
            )
    return lang_occurrence


def _append_kubejs_lang_item(
    parts: list[str],
    locale: str,
    rel: str,
    digest: str,
    scope: str,
    version: str,
    result: PackScanResult,
    occurrence: int,
) -> None:
    parsed = parse_client_lang_add(parts)
    if parsed is None:
        return
    namespace_expr, key_expr, value_expr = parsed
    namespace = KUBEJS_DEFAULT_LANG_NAMESPACE
    if namespace_expr is not None:
        namespace = _literal(namespace_expr)
        if not namespace:
            result.issues.append(
                _issue(
                    Path(rel),
                    "kubejs_lang_namespace_dynamic",
                    "ClientEvents.lang 的 namespace 不是靜態字串",
                )
            )
            return
    key = _literal(key_expr)
    value = _literal(value_expr)
    if not key or value is None:
        return
    result.items.append(
        _make_item(
            kind=KIND_KUBEJS_LANG,
            # namespace 是 KubeJS LangEventJS 的資料身分；不能只用 key，
            # 否則同一檔案的不同 namespace 會在 ModDB 合併成同一筆。
            mod_id=f"{namespace}@{scope}",
            key=key,
            text=value,
            rel=rel,
            selector=(
                f"ClientEvents.lang[{locale}][{namespace}]"
                f".event.add[{occurrence}]|{key}"
            ),
            digest=digest,
            scope=scope,
            version=version,
            source_format="kubejs-js",
            translated_locale=locale,
        )
    )


def _kubejs_lang_locale(
    event_name: str,
    args: str,
    rel: str,
    result: PackScanResult,
) -> str | None:
    """讀取 ``ClientEvents.lang`` 的語系；未知語系不猜測成英文。"""
    if event_name != "ClientEvents.lang":
        return "en_us"
    outer_parts = split_js_args(args)
    if not outer_parts:
        result.issues.append(
            _issue(Path(rel), "kubejs_lang_locale_missing", "缺少 lang locale")
        )
        return None
    first = _literal(outer_parts[0])
    if first is None:
        # 舊版 API 可省略 locale，第一個參數直接是 callback。
        if "=>" in outer_parts[0] or outer_parts[0].strip().startswith("function"):
            return "en_us"
        result.issues.append(
            _issue(
                Path(rel),
                "kubejs_lang_locale_dynamic",
                "ClientEvents.lang 的 locale 不是靜態字串",
            )
        )
        return None
    locale = first.lower()
    if locale not in _KUBEJS_LANG_LOCALES:
        result.issues.append(
            _issue(
                Path(rel),
                "kubejs_lang_locale_unsupported",
                f"locale={locale}",
            )
        )
        return None
    return locale


def _append_kubejs_tooltip_item(
    parts: list[str],
    rel: str,
    digest: str,
    scope: str,
    version: str,
    result: PackScanResult,
    tooltip_occurrences: defaultdict[str, int],
) -> None:
    raw_item_id = _literal(parts[0]) or parts[0].strip()
    tooltip_occurrences[raw_item_id] += 1
    occurrence = tooltip_occurrences[raw_item_id]
    selector = f"ItemEvents.tooltip.event.add[{raw_item_id}|{occurrence}]"
    value, reason = _tooltip_literal(parts[1])
    _record_kubejs_reason(parts[1], reason, rel, digest, scope, selector, result)
    if value is None:
        return
    result.items.append(
        _make_item(
            kind=KIND_KUBEJS_SCRIPT,
            mod_id=f"kubejs@{scope}",
            key=f"{rel}|tooltip:{raw_item_id}:{occurrence}",
            text=value,
            rel=rel,
            selector=selector,
            digest=digest,
            scope=scope,
            version=version,
        )
    )


def _scan_kubejs_scenes(
    text: str,
    code: str,
    rel: str,
    digest: str,
    scope: str,
    version: str,
    result: PackScanResult,
) -> None:
    occurrence = 0
    for match in _SCENE_TEXT.finditer(code):
        args = _extract_js_call_args(text, match.end())
        if not args:
            continue
        parts = split_js_args(args)
        if len(parts) < 2:
            continue
        text_expr = parts[1]
        value, reason = _tooltip_literal(text_expr)
        if value is None and reason != "kubejs_lang_reference":
            _record_kubejs_reason(
                text_expr, reason, rel, digest, scope, "scene.text", result
            )
            continue
        occurrence += 1
        selector = f"scene.text[{occurrence}]"
        _record_kubejs_reason(text_expr, reason, rel, digest, scope, selector, result)
        if value is None:
            continue
        result.items.append(
            _make_item(
                kind=KIND_KUBEJS_SCRIPT,
                mod_id=f"kubejs@{scope}",
                key=f"{rel}|scene:{occurrence}",
                text=value,
                rel=rel,
                selector=selector,
                digest=digest,
                scope=scope,
                version=version,
            )
        )


def _record_kubejs_reason(
    expression: str,
    reason: str | None,
    rel: str,
    digest: str,
    scope: str,
    selector: str,
    result: PackScanResult,
) -> None:
    if reason == "kubejs_lang_reference":
        target_key = _translate_key(expression)
        if target_key:
            result.references.append(
                SourceReference(
                    pack_scope=scope,
                    source_kind=KIND_KUBEJS_SCRIPT,
                    source_path=rel,
                    selector=selector,
                    target_key=target_key,
                    source_hash=digest,
                )
            )
        return
    if reason:
        result.issues.append(_issue(Path(rel), reason, f"selector={selector}"))


_KubeJSLangData = tuple[str, str, dict[str, object]]


def _collect_kubejs_lang_paths(
    root: Path, result: PackScanResult
) -> defaultdict[str, defaultdict[str, list[Path]]]:
    files_by_namespace: defaultdict[str, defaultdict[str, list[Path]]] = defaultdict(
        lambda: defaultdict(list)
    )
    assets = root / "assets"
    paths = _iter_files(assets, (".json",), result) if assets.is_dir() else ()
    for path in paths:
        parts = path.relative_to(root).parts
        if len(parts) < 4 or parts[-2] != "lang":
            continue
        namespace = parts[1] if len(parts) > 1 else "kubejs"
        files_by_namespace[namespace][normalize_locale(path.stem)].append(path)
    return files_by_namespace


def _load_kubejs_lang_file(
    root: Path,
    path: Path,
    result: PackScanResult,
    parsed: dict[Path, _KubeJSLangData | None],
) -> _KubeJSLangData | None:
    if path in parsed:
        return parsed[path]
    result.mark_file("kubejs")
    loaded = _read_text(path, result)
    if loaded is None:
        parsed[path] = None
        return None
    result.mark_file_read("kubejs")
    result.mark_source("kubejs", path.relative_to(root).as_posix())
    text, digest = loaded
    try:
        values = json.loads(text)
    except json.JSONDecodeError as exc:
        result.issues.append(
            _issue(path, "json_parse_failed", str(exc), status="parse_failed")
        )
        parsed[path] = None
        return None
    if not isinstance(values, dict):
        result.issues.append(
            _issue(
                path,
                "json_root_not_object",
                "lang JSON 必須是 object",
                status="parse_failed",
            )
        )
        parsed[path] = None
        return None
    parsed[path] = (text, digest, values)
    return parsed[path]


def _kubejs_lang_counts(
    root: Path,
    locale_paths: Mapping[str, list[Path]],
    result: PackScanResult,
    parsed: dict[Path, _KubeJSLangData | None],
) -> dict[str, int]:
    return {
        locale: sum(
            count_language_values(loaded[2])
            for path in paths
            if (loaded := _load_kubejs_lang_file(root, path, result, parsed))
            is not None
        )
        for locale, paths in locale_paths.items()
    }


def _select_kubejs_lang_source(
    root: Path,
    locale_paths: Mapping[str, list[Path]],
    result: PackScanResult,
    parsed: dict[Path, _KubeJSLangData | None],
) -> str | None:
    # Parse only en_us first.  Other locales are loaded only for a fallback.
    source_locale = select_source_locale(
        {
            "en_us": _kubejs_lang_counts(
                root,
                {"en_us": locale_paths.get("en_us", [])},
                result,
                parsed,
            ).get("en_us", 0)
        }
    )
    if source_locale == "en_us":
        return source_locale
    return select_source_locale(_kubejs_lang_counts(root, locale_paths, result, parsed))


def _append_kubejs_lang_items(
    root: Path,
    namespace: str,
    locale_paths: defaultdict[str, list[Path]],
    source_locale: str | None,
    version: str,
    scope: str,
    result: PackScanResult,
    parsed: dict[Path, _KubeJSLangData | None],
) -> None:
    locales = selected_locales(set(locale_paths), source_locale)
    for locale in sorted(locales):
        for path in locale_paths.get(locale, []):
            loaded = _load_kubejs_lang_file(root, path, result, parsed)
            if loaded is None:
                continue
            _, digest, values = loaded
            rel = path.relative_to(root).as_posix()
            for key, value in values.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                result.items.append(
                    _make_item(
                        kind=KIND_KUBEJS_LANG,
                        # pack_scope 必須進入 entry 的模組身分；entry 的既有唯一鍵
                        # 沒有另加 pack_scope 欄位，不能讓不同模組包共用同一個 namespace。
                        mod_id=f"{namespace}@{scope}",
                        key=key,
                        text=value,
                        rel=rel,
                        selector=f"lang:{locale}:{key}",
                        digest=digest,
                        scope=scope,
                        version=version,
                        # A zh_cn/zh_tw file selected as the fallback source is
                        # the source snapshot, not an already translated value.
                        translated_locale=locale if locale != source_locale else "",
                    )
                )


def _scan_kubejs_lang(
    root: Path, version: str, scope: str, result: PackScanResult
) -> None:
    files_by_namespace = _collect_kubejs_lang_paths(root, result)
    parsed: dict[Path, _KubeJSLangData | None] = {}
    for namespace, locale_paths in sorted(files_by_namespace.items()):
        source_locale = _select_kubejs_lang_source(root, locale_paths, result, parsed)
        if source_locale and source_locale != "en_us":
            result.locale_fallbacks[namespace] = source_locale
        _append_kubejs_lang_items(
            root,
            namespace,
            locale_paths,
            source_locale,
            version,
            scope,
            result,
            parsed,
        )


def _scan_ftb_quests(
    root: Path, version: str, scope: str, result: PackScanResult
) -> None:
    locale_values: dict[tuple[str, str], dict[str, tuple[str, str, str]]] = defaultdict(
        dict
    )
    lang_root = root / "lang"
    if lang_root.is_dir():
        for path in _iter_files(lang_root, (".snbt",), result):
            result.mark_file("ftb_quests")
            loaded = _read_text(path, result)
            if loaded is None:
                continue
            result.mark_file_read("ftb_quests")
            _, digest = loaded
            data = walk_snbt_file(str(path))
            if data is None:
                result.issues.append(
                    _issue(
                        path,
                        "snbt_parse_failed",
                        "語系 SNBT 無法解析",
                        status="parse_failed",
                    )
                )
                continue
            locale = _locale_for_lang_file(path, lang_root)
            rel = _lang_relative(path, lang_root, locale)
            source_path = path.relative_to(root).as_posix()
            _record_lang_references(
                data,
                source_path=source_path,
                scope=scope,
                digest=digest,
                result=result,
            )
            for key, value in _top_level_lang(data):
                locale_values[(rel, key)][locale] = (value, digest, source_path)
            result.mark_source("ftb_quests", path.relative_to(root).as_posix())
    for (rel, lang_key), locales in sorted(locale_values.items()):
        source_entry = (
            locales.get("en_us") or locales.get("zh_cn") or locales.get("zh_tw")
        )
        if source_entry is None:
            continue
        source, source_digest, source_path = source_entry
        if not source:
            continue
        digest = hashlib.sha256(
            "|".join(
                f"{locale}:{value[1]}" for locale, value in sorted(locales.items())
            ).encode()
        ).hexdigest()
        if is_lang_key_ref_like(source):
            _append_references(
                result,
                scope=scope,
                source_kind=KIND_FTB_QUEST_LANG,
                source_path=source_path,
                selector=f"lang:{lang_key}",
                text=source,
                digest=source_digest,
            )
            continue
        result.items.append(
            _make_item(
                kind=KIND_FTB_QUEST_LANG,
                mod_id=f"ftbquests@{scope}",
                key=f"{rel}|lang:{lang_key}",
                text=source,
                rel=source_path,
                selector=f"lang:{lang_key}",
                digest=source_digest,
                scope=scope,
                version=version,
                # When en_us is present it is the source text and zh_tw/zh_cn
                # are companion translations.  Mark a translated locale only
                # for a locale-only file; otherwise _make_item would clear the
                # English source while keeping the existing translation.
                translated_locale=(
                    ""
                    if "en_us" in locales
                    else "zh_tw"
                    if "zh_tw" in locales
                    else "zh_cn"
                    if "zh_cn" in locales
                    else ""
                ),
                zh_cn=locales.get("zh_cn", ("", ""))[0],
                zh_tw=locales.get("zh_tw", ("", ""))[0],
                source_format="ftb-snbt-lang",
            )
        )
    for path in _iter_files(root, (".snbt",), result):
        if lang_root in path.parents:
            continue
        result.mark_file("ftb_quests")
        loaded = _read_text(path, result)
        if loaded is None:
            continue
        result.mark_file_read("ftb_quests")
        _, digest = loaded
        data = walk_snbt_file(str(path))
        if data is None:
            result.issues.append(
                _issue(
                    path,
                    "snbt_parse_failed",
                    "任務 SNBT 無法解析",
                    status="parse_failed",
                )
            )
            continue
        rel = path.relative_to(root).as_posix()
        _scan_quest_value(data, rel, version, scope, digest, result, "root")
        result.mark_source("ftb_quests", rel)


def _scan_quest_value(
    value: Any,
    rel: str,
    version: str,
    scope: str,
    digest: str,
    result: PackScanResult,
    selector: str,
) -> None:
    if isinstance(value, Compound):
        object_id = str(value.get("id", ""))
        for field_name in ("title", "subtitle", "description"):
            field_value = value.get(field_name)
            if isinstance(field_value, snbt.String):
                _append_quest_item(
                    str(field_value),
                    rel,
                    selector,
                    field_name,
                    object_id,
                    version,
                    scope,
                    digest,
                    result,
                )
            elif isinstance(field_value, List):
                for index, slot in enumerate(field_value):
                    if isinstance(slot, snbt.String):
                        _append_quest_item(
                            str(slot),
                            rel,
                            selector,
                            f"{field_name}[{index}]",
                            object_id,
                            version,
                            scope,
                            digest,
                            result,
                        )
        for name, child in value.items():
            if isinstance(child, (Compound, List)):
                _scan_quest_value(
                    child, rel, version, scope, digest, result, f"{selector}.{name}"
                )
    elif isinstance(value, List):
        for index, child in enumerate(value):
            if isinstance(child, (Compound, List)):
                _scan_quest_value(
                    child, rel, version, scope, digest, result, f"{selector}[{index}]"
                )


def _append_quest_item(
    text: str,
    rel: str,
    selector: str,
    field: str,
    object_id: str,
    version: str,
    scope: str,
    digest: str,
    result: PackScanResult,
) -> None:
    if _looks_like_component_json(text):
        result.issues.append(
            _issue(
                Path(rel),
                "ftb_component_json_unsupported",
                f"selector={selector}.{field}",
            )
        )
        return
    if is_lang_key_ref_like(text):
        _append_references(
            result,
            scope=scope,
            source_kind=KIND_FTB_QUESTS,
            source_path=rel,
            selector=f"{selector}.{field}",
            text=text,
            digest=digest,
        )
        return
    if not is_extractable_line(text) or is_tag_condition_text(text):
        return
    result.items.append(
        _make_item(
            kind=KIND_FTB_QUESTS,
            mod_id=f"ftbquests@{scope}",
            key=f"{rel}|{selector}.{field}|id:{object_id}"
            if object_id
            else f"{rel}|{selector}.{field}",
            text=text,
            rel=rel,
            selector=f"{selector}.{field}",
            digest=digest,
            scope=scope,
            version=version,
        )
    )


def _looks_like_component_json(text: str) -> bool:
    """判斷 FTB literal 是否為 Minecraft Component JSON。

    Component 需要保留 JSON 結構才能在遊戲中正確套用；目前 writer 只支援
    SNBT literal 字串，因此先明確列為 unsupported，避免把整段 JSON 送進 LM
    後破壞格式。普通的 JSON-like 說明文字不會因為解析失敗而被排除。
    """
    candidate = text.strip()
    if not candidate.startswith(("{", "[")):
        return False
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return False
    if isinstance(value, dict):
        return bool(
            set(value).intersection(
                {
                    "text",
                    "translate",
                    "with",
                    "extra",
                    "score",
                    "selector",
                    "keybind",
                    "nbt",
                    "interpret",
                    "color",
                    "bold",
                    "italic",
                    "underlined",
                    "strikethrough",
                    "obfuscated",
                    "clickEvent",
                    "hoverEvent",
                }
            )
        )
    if isinstance(value, list):
        return any(isinstance(item, (dict, list)) for item in value)
    return False


def _top_level_lang(root: Compound) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for key, value in root.items():
        if not any(str(key).endswith(suffix) for suffix in _LANG_SUFFIXES):
            continue
        if isinstance(value, snbt.String):
            text = str(value)
            if text and is_extractable_line(text) and not is_lang_key_ref(text):
                values.append((str(key), text))
        elif isinstance(value, List):
            slots = [
                str(item)
                for item in value
                if isinstance(item, snbt.String) and is_extractable_line(str(item))
            ]
            if slots:
                values.append((str(key), "\n".join(slots)))
    return values


def _record_lang_references(
    root: Compound,
    *,
    source_path: str,
    scope: str,
    digest: str,
    result: PackScanResult,
) -> None:
    """把 FTB 語系檔中的引用記為 edge，不把 ``{...}`` 當成譯文。"""
    for key, value in root.items():
        if not any(str(key).endswith(suffix) for suffix in _LANG_SUFFIXES):
            continue
        values = (
            [str(value)]
            if isinstance(value, snbt.String)
            else (
                [str(item) for item in value if isinstance(item, snbt.String)]
                if isinstance(value, List)
                else []
            )
        )
        for index, text in enumerate(values):
            _append_references(
                result,
                scope=scope,
                source_kind=KIND_FTB_QUEST_LANG,
                source_path=source_path,
                selector=f"lang:{key}[{index}]" if len(values) > 1 else f"lang:{key}",
                text=text,
                digest=digest,
            )


def _append_references(
    result: PackScanResult,
    *,
    scope: str,
    source_kind: str,
    source_path: str,
    selector: str,
    text: str,
    digest: str,
) -> None:
    """只接受可明確辨識的語言 key；圖片／換頁／格式標記不是引用。"""
    for match in _REFERENCE.finditer(text):
        target_key = match.group(1).strip()
        if (
            not target_key
            or target_key.startswith(("@", "image:", "pagebreak", "br:"))
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", target_key)
        ):
            continue
        result.references.append(
            SourceReference(
                pack_scope=scope,
                source_kind=source_kind,
                source_path=source_path,
                selector=selector,
                target_key=target_key,
                source_hash=digest,
            )
        )


def _locale_for_lang_file(path: Path, root: Path) -> str:
    relative = path.relative_to(root).parts
    if len(relative) > 1 and relative[0].lower() in {"en_us", "zh_cn", "zh_tw"}:
        return relative[0].lower()
    return path.stem.lower()


def _lang_relative(path: Path, root: Path, locale: str) -> str:
    relative = path.relative_to(root).parts
    if relative and (
        relative[0].lower() == locale or Path(relative[0]).stem.lower() == locale
    ):
        relative = relative[1:]
    # FTB Quests 也支援 lang/en_us.snbt 這種單檔語系；各 locale 必須共用
    # 同一個 canonical identity，否則 en_us 與 zh_cn/zh_tw 無法合併。
    if not relative or (
        len(relative) == 1 and Path(relative[0]).stem.lower() == locale
    ):
        return "__root__.snbt"
    if Path(relative[-1]).stem.lower() == locale:
        return "/".join((*relative[:-1], "__root__.snbt"))
    return "/".join(relative)


def _make_item(
    *,
    kind: str,
    mod_id: str,
    key: str,
    text: str,
    rel: str,
    selector: str,
    digest: str,
    scope: str,
    version: str,
    translated_locale: str = "",
    zh_tw: str = "",
    zh_cn: str = "",
    source_format: str = "",
) -> ScanItem:
    if not zh_tw and translated_locale == "zh_tw":
        zh_tw = text
    if not zh_cn and translated_locale == "zh_cn":
        zh_cn = text
    en_us = "" if translated_locale in {"zh_tw", "zh_cn"} else text
    source = SRC_CUSTOM if zh_tw or zh_cn else None
    return ScanItem(
        kind=kind,
        mod_id=mod_id,
        key=key,
        en_us=en_us,
        zh_tw=zh_tw,
        zh_cn=zh_cn,
        source=source,
        source_path=rel,
        source_selector=selector,
        source_hash=digest,
        source_format=source_format
        or (
            "kubejs-js"
            if kind == KIND_KUBEJS_SCRIPT
            else "ftb-snbt"
            if kind.startswith("ftb_")
            else "lang-json"
        ),
        pack_scope=scope,
    )


def _translate_key(expr: str) -> str | None:
    """從 Text.translate／Component.translatable 取出靜態 key。"""
    value = expr.strip()
    match = _TRANSLATE_EXPR.match(value)
    if not match:
        return None
    args = _extract_js_call_args(value, match.end())
    parts = split_js_args(args) if args else []
    return _literal(parts[0]) if parts else None


def _resolve_reference_targets(result: PackScanResult) -> None:
    """以同一模組包的語言項目解析引用，無法唯一判定就保留 ambiguous。"""
    language_items = [
        item
        for item in result.items
        if item.kind in {KIND_LANG, KIND_KUBEJS_LANG}
        and item.source_format in {"lang-json", "kubejs-js"}
    ]
    targets_by_key: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    for item in language_items:
        targets_by_key[item.key].add((item.kind, item.mod_id))
    resolved: list[SourceReference] = []
    for reference in result.references:
        pairs = sorted(targets_by_key.get(reference.target_key, ()))
        kinds = {kind for kind, _mod_id in pairs}
        mod_ids = {mod_id for _kind, mod_id in pairs}
        target_kind = reference.target_kind
        if len(pairs) == 1:
            target_kind = pairs[0][0]
        elif len(kinds) == 1:
            target_kind = next(iter(kinds))
        resolved.append(
            replace(
                reference,
                target_kind=target_kind,
                target_mod_id=next(iter(mod_ids)) if len(mod_ids) == 1 else "",
            )
        )
    result.references = resolved


def _literal(expr: str) -> str | None:
    value = expr.strip()
    if not value or value.startswith(("{", "[")):
        return None
    if value[0] in "'\"`":
        return _quoted(value)
    return None


def _tooltip_literal(expr: str) -> tuple[str | None, str | None]:
    value = expr.strip()
    if _TRANSLATE_EXPR.match(value):
        return None, "kubejs_lang_reference"
    if _LITERAL_EXPR.match(value):
        start = value.find("(") + 1
        text = extract_js_string_call(value, start)
        return (text, None) if text else (None, "kubejs_dynamic_text")
    text = _literal(value)
    if text is not None:
        return text, None
    return None, "kubejs_dynamic_text"


def _quoted(value: str) -> str | None:
    try:
        if value[0] == "`" and value.endswith("`"):
            return value[1:-1] if "${" not in value else None
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, str) else None
