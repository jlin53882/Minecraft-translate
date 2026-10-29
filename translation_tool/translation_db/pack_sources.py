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
from translation_tool.plugins.kubejs.kubejs_tooltip_extract import (
    extract_js_string_call,
    split_js_args,
)
from translation_tool.translation_db.models import ScanItem, SourceReference
from translation_tool.translation_db.schema import (
    KIND_FTB_QUEST_LANG,
    KIND_FTB_QUESTS,
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


def discover_pack_root(folder: str | Path) -> Path | None:
    """由 mods、kubejs 或模組包根目錄找出同一個 instance 根目錄。"""
    try:
        current = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if current.is_file():
        current = current.parent
    candidates = [current]
    if current.name.lower() in {"mods", "kubejs"}:
        candidates.append(current.parent)
    candidates.extend(current.parents[:3])
    for candidate in candidates:
        if (candidate / "kubejs").is_dir() or (
            candidate / "config" / "ftbquests" / "quests"
        ).is_dir():
            return candidate
    return current if _has_pack_sources(current) else None


def scan_pack_sources(pack_root: str | Path, version: str) -> PackScanResult:
    """掃描 KubeJS 與 FTB Quests，保守地只輸出可定位的靜態文字。"""
    root = Path(pack_root).expanduser().resolve()
    result = PackScanResult(pack_scope=_pack_scope(root))
    if not root.is_dir():
        result.issues.append(_issue(root, "source_missing", "source_not_directory"))
        return result
    scope = result.pack_scope
    kubejs = root / "kubejs"
    if kubejs.is_dir():
        _scan_kubejs(kubejs, version, scope, result)
    quests = root / "config" / "ftbquests" / "quests"
    if quests.is_dir():
        _scan_ftb_quests(quests, version, scope, result)
    _resolve_reference_targets(result)
    return result


def pack_scope_for_root(pack_root: str | Path) -> str:
    """回傳與掃描器相同的模組包身分，不重新掃描內容。"""
    root = Path(pack_root).expanduser().resolve()
    return _pack_scope(root)


def _has_pack_sources(root: Path) -> bool:
    return (root / "kubejs").is_dir() or (
        root / "config" / "ftbquests" / "quests"
    ).is_dir()


def _pack_scope(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"{root.name or 'pack'}@{digest}"


def _issue(path: Path, reason: str, detail: str, *, status: str = "pending") -> dict[str, str]:
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
            result.issues.append(_issue(current, "source_read_failed", str(exc), status="parse_failed"))
            continue
        for entry in children:
            path = Path(entry.path)
            try:
                if entry.is_symlink() or _is_reparse_point(path):
                    result.issues.append(_issue(path, "source_link_skipped", "symbolic link 或 junction 不會被追蹤"))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in suffixes:
                    yield path
            except OSError as exc:
                result.issues.append(_issue(path, "source_read_failed", str(exc), status="parse_failed"))


def _read_text(path: Path, result: PackScanResult) -> tuple[str, str] | None:
    try:
        raw = path.read_bytes()
        return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError) as exc:
        result.issues.append(_issue(path, "source_read_failed", str(exc), status="parse_failed"))
        return None


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _scan_kubejs(root: Path, version: str, scope: str, result: PackScanResult) -> None:
    for path in _iter_files(root, (".js",), result):
        result.files_seen += 1
        rel = _relative(path, root)
        result.sources_found.append(rel)
        loaded = _read_text(path, result)
        if loaded is None:
            continue
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
    args_code = _mask_js_non_code(args)
    for match in _EVENT_ADD.finditer(args_code):
        call_args = _extract_js_call_args(args, match.end())
        if not call_args:
            continue
        parts = split_js_args(call_args)
        if len(parts) < 2:
            continue
        if event_name == "ClientEvents.lang":
            lang_occurrence += 1
            _append_kubejs_lang_item(
                parts, rel, digest, scope, version, result, lang_occurrence
            )
        else:
            _append_kubejs_tooltip_item(
                parts, rel, digest, scope, version, result, tooltip_occurrences
            )
    return lang_occurrence


def _append_kubejs_lang_item(
    parts: list[str],
    rel: str,
    digest: str,
    scope: str,
    version: str,
    result: PackScanResult,
    occurrence: int,
) -> None:
    key = _literal(parts[0])
    value = _literal(parts[1])
    if not key or value is None:
        return
    result.items.append(
        _make_item(
            kind=KIND_LANG,
            # ClientEvents.lang may use a default namespace.  The exact key is
            # stable; pack scope supplies the source identity.
            mod_id=f"kubejs@{scope}",
            key=key,
            text=value,
            rel=rel,
            selector=f"ClientEvents.lang.event.add[{occurrence}]|{key}",
            digest=digest,
            scope=scope,
            version=version,
            source_format="kubejs-js",
        )
    )


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


def _scan_kubejs_lang(root: Path, version: str, scope: str, result: PackScanResult) -> None:
    assets = root / "assets"
    for path in _iter_files(assets, (".json",), result) if assets.is_dir() else ():
        parts = path.relative_to(root).parts
        if len(parts) < 4 or parts[-2] != "lang":
            continue
        locale = path.stem.lower()
        if locale not in {"en_us", "zh_tw", "zh_cn"}:
            continue
        loaded = _read_text(path, result)
        if loaded is None:
            continue
        result.files_seen += 1
        result.sources_found.append(path.relative_to(root).as_posix())
        text, digest = loaded
        try:
            values = json.loads(text)
        except json.JSONDecodeError as exc:
            result.issues.append(_issue(path, "json_parse_failed", str(exc), status="parse_failed"))
            continue
        if not isinstance(values, dict):
            result.issues.append(_issue(path, "json_root_not_object", "lang JSON 必須是 object", status="parse_failed"))
            continue
        parts = path.relative_to(root).parts
        namespace = parts[1] if len(parts) > 1 else "kubejs"
        for key, value in values.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            result.items.append(
                _make_item(
                    kind=KIND_LANG,
                    # pack_scope 必須進入 entry 的模組身分；entry 的既有唯一鍵
                    # 沒有另加 pack_scope 欄位，不能讓不同模組包共用同一個 namespace。
                    mod_id=f"{namespace}@{scope}",
                    key=key,
                    text=value,
                    rel=path.relative_to(root).as_posix(),
                    selector=f"lang:{locale}:{key}",
                    digest=digest,
                    scope=scope,
                    version=version,
                    translated_locale=locale,
                )
            )


def _scan_ftb_quests(root: Path, version: str, scope: str, result: PackScanResult) -> None:
    locale_values: dict[tuple[str, str], dict[str, tuple[str, str, str]]] = defaultdict(dict)
    lang_root = root / "lang"
    if lang_root.is_dir():
        for path in _iter_files(lang_root, (".snbt",), result):
            result.files_seen += 1
            loaded = _read_text(path, result)
            if loaded is None:
                continue
            _, digest = loaded
            data = walk_snbt_file(str(path))
            if data is None:
                result.issues.append(_issue(path, "snbt_parse_failed", "語系 SNBT 無法解析", status="parse_failed"))
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
            result.sources_found.append(path.relative_to(root).as_posix())
    for (rel, lang_key), locales in sorted(locale_values.items()):
        source_entry = locales.get("en_us") or locales.get("zh_cn") or locales.get("zh_tw")
        if source_entry is None:
            continue
        source, source_digest, source_path = source_entry
        if not source:
            continue
        digest = hashlib.sha256(
            "|".join(f"{locale}:{value[1]}" for locale, value in sorted(locales.items())).encode()
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
                translated_locale="zh_tw" if "zh_tw" in locales else "",
                zh_cn=locales.get("zh_cn", ("", ""))[0],
                zh_tw=locales.get("zh_tw", ("", ""))[0],
                source_format="ftb-snbt-lang",
            )
        )
    for path in _iter_files(root, (".snbt",), result):
        if lang_root in path.parents:
            continue
        result.files_seen += 1
        loaded = _read_text(path, result)
        if loaded is None:
            continue
        _, digest = loaded
        data = walk_snbt_file(str(path))
        if data is None:
            result.issues.append(_issue(path, "snbt_parse_failed", "任務 SNBT 無法解析", status="parse_failed"))
            continue
        rel = path.relative_to(root).as_posix()
        _scan_quest_value(data, rel, version, scope, digest, result, "root")
        result.sources_found.append(rel)


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
                    str(field_value), rel, selector, field_name, object_id,
                    version, scope, digest, result,
                )
            elif isinstance(field_value, List):
                for index, slot in enumerate(field_value):
                    if isinstance(slot, snbt.String):
                        _append_quest_item(
                            str(slot), rel, selector, f"{field_name}[{index}]", object_id,
                            version, scope, digest, result,
                        )
        for name, child in value.items():
            if isinstance(child, (Compound, List)):
                _scan_quest_value(child, rel, version, scope, digest, result, f"{selector}.{name}")
    elif isinstance(value, List):
        for index, child in enumerate(value):
            if isinstance(child, (Compound, List)):
                _scan_quest_value(child, rel, version, scope, digest, result, f"{selector}[{index}]")


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
            key=f"{rel}|{selector}.{field}|id:{object_id}" if object_id else f"{rel}|{selector}.{field}",
            text=text,
            rel=rel,
            selector=f"{selector}.{field}",
            digest=digest,
            scope=scope,
            version=version,
        )
    )


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
            slots = [str(item) for item in value if isinstance(item, snbt.String) and is_extractable_line(str(item))]
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
        values = [str(value)] if isinstance(value, snbt.String) else (
            [str(item) for item in value if isinstance(item, snbt.String)]
            if isinstance(value, List)
            else []
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
        relative[0].lower() == locale
        or Path(relative[0]).stem.lower() == locale
    ):
        relative = relative[1:]
    return "/".join(relative) or path.name


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
        source_format=source_format or (
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
        if item.kind == KIND_LANG
        and item.source_format in {"lang-json", "kubejs-js"}
    ]
    resolved: list[SourceReference] = []
    for reference in result.references:
        candidates = sorted(
            {
                item.mod_id
                for item in language_items
                if item.key == reference.target_key
            }
        )
        target_mod_id = candidates[0] if len(candidates) == 1 else ""
        resolved.append(replace(reference, target_mod_id=target_mod_id))
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
