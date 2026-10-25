"""Patchouli JSON 的共用語意分類器。

Patchouli 的 JSON 同時包含玩家看得到的文字、翻譯鍵、資源 ID、版面資料與
自訂 template 參數。只以欄位名稱抽取會把結構值送進翻譯或清理流程，也會讓
掃描器、輸出器與清理器對同一個 JSON 路徑產生不同結論。

本模組只負責「這個 JSON 路徑是什麼」的判斷，不負責寫檔或資料庫交易。所有
不具備足夠上下文的值都會標為 ``unknown``，只有既有相容性入口在未提供語意
上下文時才使用舊分類器。這讓新流程可以保守運作，也不破壞舊呼叫端。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from translation_tool.core.book_dirs import DEFAULT_BOOK_DIRS, find_book_dir_index
from translation_tool.core.text_sniffer import (
    BOOK_DISPLAY_FIELDS,
    BOOK_ID_FIELDS,
    book_field,
    classify_book_string,
    value_shape_reason,
)

PATCHOULI_CLASSIFIER_VERSION = "patchouli-semantic-v1"

LITERAL_TEXT = "literal_text"
TRANSLATION_REFERENCE = "translation_reference"
DISPLAY_NONTRANSLATABLE = "display_nontranslatable"
STRUCTURAL = "structural"
UNKNOWN = "unknown"

_LOCALE_NAMES = frozenset({"en_us", "zh_tw", "zh_cn"})
_LANG_KEY = re.compile(r"^[a-z0-9_.-]+(?:\.[a-z0-9_.-]+)+$", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]")

# Official page fields and component fields that carry a reference or layout
# rather than player-visible prose. The list intentionally errs on the side of
# structural classification; a new custom field must provide template context
# before it can enter the translation pipeline.
_STRUCTURAL_FIELDS = frozenset(
    {
        *BOOK_ID_FIELDS,
        "pattern",
        "mapping",
        "multiblock",
        "multiblock_id",
        "entries",
        "entry",
        "item",
        "items",
        "recipe",
        "recipe2",
        "recipes",
        "entity",
        "entity_id",
        "entity_data",
        "images",
        "image",
        "texture",
        "textures",
        "model",
        "font",
        "sound",
        "sounds",
        "trigger",
        "triggers",
        "condition",
        "conditions",
        "requirements",
        "effects",
        "components",
        "include",
        "includes",
        "using",
        "as",
        "processor",
        "processors",
        "guard",
        "guards",
        "class",
        "type",
        "id",
        "uuid",
        "key",
        "path",
        "url",
        "link",
        "x",
        "y",
        "z",
        "scale",
        "offset",
        "rotate",
        "rotation",
        "width",
        "height",
        "size",
        "color",
        "colors",
        "flag",
        "flags",
        "advancement",
        "negate_advancement",
        "parent",
        "sortnum",
        "draw_filler",
        "text_overrides",
    }
)

_DISPLAY_FIELDS = frozenset(
    {
        *BOOK_DISPLAY_FIELDS,
        "heading",
        "header",
        "flavor",
        "label",
        "link_text",
        "page_title",
        "landing_text",
        "description",
    }
)

_KNOWN_PAGE_TYPES = frozenset(
    {
        "text",
        "crafting",
        "furnace",
        "smelting",
        "smithing",
        "stonecutting",
        "spotlight",
        "multiblock",
        "entity",
        "image",
        "link",
        "relations",
        "quest",
        "empty",
        "well",
        "patchouli:text",
        "patchouli:crafting",
        "patchouli:furnace",
        "patchouli:smelting",
        "patchouli:smithing",
        "patchouli:stonecutting",
        "patchouli:spotlight",
        "patchouli:multiblock",
        "patchouli:entity",
        "patchouli:image",
        "patchouli:link",
        "patchouli:relations",
        "patchouli:quest",
        "patchouli:empty",
        "patchouli:well",
    }
)

# 這些不是頁型，但其欄位定義本身就是 Patchouli 文件的顯示內容。
_KNOWN_DOCUMENT_TYPES = frozenset({"book", "template"})

_DISPLAY_COMPONENT_TYPES = frozenset(
    {"patchouli:text", "patchouli:header", "patchouli:tooltip"}
)


def _has_known_display_schema(page_type: str) -> bool:
    normalized_page_type = _normal_type(page_type)
    return (
        not normalized_page_type
        or normalized_page_type in _KNOWN_PAGE_TYPES
        or normalized_page_type in _KNOWN_DOCUMENT_TYPES
    )


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normal_type(value: Any) -> str:
    return str(value or "").strip().lower()


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^.\[\]]+", path.rsplit("#", 1)[-1]))


def _field(path: str) -> str:
    return book_field(path).lower()


def _ancestor_fields(path: str) -> tuple[str, ...]:
    parts = _path_parts(path)
    return tuple(part.lower() for part in parts[:-1])


def _is_structural_path(path: str, field: str) -> bool:
    if field in _STRUCTURAL_FIELDS:
        return True
    ancestors = set(_ancestor_fields(path))
    return bool(
        ancestors
        & {
            "pattern",
            "mapping",
            "multiblock",
            "images",
            "item",
            "items",
            "recipe",
            "recipes",
            "entity_data",
            "include",
            "includes",
            "processor",
            "processors",
            "guard",
            "guards",
            "conditions",
            "requirements",
        }
    )


def _is_display_path(path: str, field: str) -> bool:
    if field in _DISPLAY_FIELDS:
        return True
    ancestors = set(_ancestor_fields(path))
    return bool(ancestors & {"text", "title", "description", "tooltip", "header"})


def _is_translation_reference(value: str, *, i18n: bool | None) -> bool:
    if i18n is not True:
        return False
    return bool(_LANG_KEY.fullmatch(value.strip()))


@dataclass(frozen=True)
class PatchouliSemantic:
    """一個 JSON 字串的可追蹤語意判斷。"""

    path: str
    value: str
    kind: str
    reason: str
    evidence: tuple[str, ...] = ()
    source_available: bool = False
    classifier_version: str = PATCHOULI_CLASSIFIER_VERSION
    source_fingerprint: str = ""
    context_fingerprint: str = ""

    @property
    def ai_allowed(self) -> bool:
        return self.kind == LITERAL_TEXT

    @property
    def writer_allowed(self) -> bool:
        return self.kind == LITERAL_TEXT

    @property
    def cleanup_delete_allowed(self) -> bool:
        # Reference values and display-only non-text must remain visible to the
        # user. Only explicit, high-confidence structural values may be removed.
        return self.kind == STRUCTURAL and bool(self.evidence)


@dataclass(frozen=True)
class PatchouliContext:
    """分類所需的 book/template 上下文。

    ``source_available`` 表示上下文確實來自原始檔案；它不代表每個 custom
    page 都能解析。解析失敗時仍保留 fingerprint 和 failure reason，讓報表
    可以區分「沒有來源」與「來源存在但未能解析」。
    """

    file_path: str = ""
    book_i18n: bool | None = None
    page_type: str = ""
    template_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    source_available: bool = False
    source_fingerprint: str = ""
    context_fingerprint: str = ""
    resolution_error: str = ""

    @property
    def has_template_context(self) -> bool:
        return bool(self.template_fields)


def context_from_metadata(
    file_path: str | Path,
    *,
    book_i18n: bool | None = None,
    template_fields: Mapping[str, Iterable[str]] | None = None,
    source_available: bool = False,
    source_fingerprint: str = "",
    resolution_error: str = "",
) -> PatchouliContext:
    """建立可在測試、ZIP scanner 或檔案抽取器間共用的 context。"""

    normalized = {
        str(key).lower(): tuple(sorted({str(item).lower() for item in values}))
        for key, values in (template_fields or {}).items()
    }
    context_payload = {
        "book_i18n": book_i18n,
        "template_fields": normalized,
        "source_available": source_available,
        "source_fingerprint": source_fingerprint,
        "resolution_error": resolution_error,
    }
    return PatchouliContext(
        file_path=str(file_path),
        book_i18n=book_i18n,
        template_fields=normalized,
        source_available=source_available,
        source_fingerprint=source_fingerprint,
        context_fingerprint=_canonical_hash(context_payload),
        resolution_error=resolution_error,
    )


def context_from_archive_documents(
    file_path: str | Path,
    *,
    book_json: Mapping[str, Any] | None = None,
    templates: Mapping[str, Any] | None = None,
    resolution_error: str = "",
) -> PatchouliContext:
    """由 ZIP/JAR 已讀取的 book/template 文件建立 context。

    scanner 以此入口把同一 archive 內的 book.json/template 結果共享給所有
    entry；不把 ZipFile 或讀取預算帶進核心分類器，避免分類器反向依賴 I/O。
    """

    template_fields: dict[str, tuple[str, ...]] = {}
    for name, value in (templates or {}).items():
        normalized = str(name).replace("\\", "/").lower()
        fields = _template_fields(value)
        aliases = {normalized, PurePosixPath(normalized).stem}
        if "templates/" in normalized:
            aliases.add(normalized.split("templates/", 1)[1].rsplit(".", 1)[0])
        for alias in aliases:
            template_fields[alias] = fields
    payload = {
        "book_json": book_json or {},
        "templates": template_fields,
    }
    return context_from_metadata(
        file_path,
        book_i18n=(
            book_json.get("i18n")
            if isinstance(book_json, Mapping)
            and isinstance(book_json.get("i18n"), bool)
            else None
        ),
        template_fields=template_fields,
        source_available=bool(book_json is not None or templates),
        source_fingerprint=_canonical_hash(payload),
        resolution_error=resolution_error,
    )


def _configured_book_dirs() -> tuple[str, ...]:
    try:
        from translation_tool.utils.config_manager import load_config

        configured = (
            load_config().get("lm_translator", {}).get("patchouli", {}).get("dir_names")
        )
        if isinstance(configured, list):
            names = tuple(str(name).strip() for name in configured if str(name).strip())
            if names:
                return names
    except Exception:  # noqa: BLE001 - 上下文解析失敗時仍採用預設目錄
        return DEFAULT_BOOK_DIRS
    return DEFAULT_BOOK_DIRS


def _book_root_parts(
    parts: tuple[str, ...], dir_names: Sequence[str]
) -> tuple[tuple[str, ...], str] | None:
    index = find_book_dir_index(parts, dir_names)
    if index is None:
        return None
    locale_index = next(
        (i for i in range(index + 1, len(parts)) if parts[i].lower() in _LOCALE_NAMES),
        None,
    )
    if locale_index is None or locale_index <= index + 1:
        return None
    return parts[:locale_index], parts[locale_index].lower()


def _template_fields(template: Any) -> tuple[str, ...]:
    fields: set[str] = set()
    if not isinstance(template, dict):
        return ()
    components = template.get("components")
    if not isinstance(components, list):
        return ()
    for component in components:
        if not isinstance(component, dict):
            continue
        component_type = _normal_type(component.get("type"))
        if component_type in _DISPLAY_COMPONENT_TYPES:
            display_field = "tooltip" if component_type.endswith("tooltip") else "text"
            fields.add(display_field)
            component_value = component.get(display_field)
            if isinstance(component_value, str) and component_value.startswith("#"):
                variable = component_value.strip("# ").lower()
                if variable:
                    fields.add(f"variable:{variable}")
        elif component_type:
            # A custom component may expose display fields only after a Java
            # processor runs. Do not guess those fields here.
            fields.add(f"unknown:{component_type}")
    return tuple(sorted(fields))


def _alternate_resource_root(root: Path) -> Path | None:
    """取得同一本書的另一個 Minecraft resource root。"""

    parts = list(root.parts)
    for index, part in enumerate(parts):
        if part.lower() not in {"assets", "data"}:
            continue
        parts[index] = "data" if part.lower() == "assets" else "assets"
        return Path(*parts)
    return None


@lru_cache(maxsize=256)
def _load_book_context(book_root: str, locale: str) -> PatchouliContext:
    root = Path(book_root)
    roots = [root]
    alternate = _alternate_resource_root(root)
    if alternate is not None and alternate != root:
        roots.append(alternate)

    payload: dict[str, Any] = {}
    book_path: Path | None = None
    book_invalid = False
    for candidate_root in roots:
        candidate = candidate_root / "book.json"
        if not candidate.is_file():
            continue
        book_path = candidate
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeError):
            book_invalid = True
            continue
        if isinstance(loaded, dict):
            payload = loaded
        break
    template_fields: dict[str, tuple[str, ...]] = {}
    for candidate_root in roots:
        template_root = candidate_root / locale / "templates"
        if not template_root.is_dir():
            continue
        try:
            template_paths = sorted(template_root.rglob("*.json"))[:256]
        except OSError:
            template_paths = []
        for template_path in template_paths:
            try:
                template_data = json.loads(
                    template_path.read_text(encoding="utf-8-sig")
                )
            except (OSError, ValueError, UnicodeError):
                continue
            relative = template_path.relative_to(template_root).with_suffix("")
            template_fields.setdefault(
                "/".join(relative.parts).lower(), _template_fields(template_data)
            )
        # Prefer the primary resource root when both sides contain a template
        # with the same name; the first value is the one used by the archive
        # scanner's setdefault semantics as well.
        if len(template_fields) >= 256:
            break

    source_payload = {
        "book": payload,
        "templates": template_fields,
    }
    source_available = book_path is not None or bool(template_fields)
    return context_from_metadata(
        root,
        book_i18n=(
            payload.get("i18n") if isinstance(payload.get("i18n"), bool) else None
        ),
        template_fields=template_fields,
        source_available=source_available,
        source_fingerprint=(
            _canonical_hash(source_payload) if source_available else ""
        ),
        resolution_error=(
            "book.json_invalid"
            if book_invalid and not payload
            else ("book.json_missing" if book_path is None else "")
        ),
    )


def context_for_path(
    file_path: str | Path, *, dir_names: Sequence[str] | None = None
) -> PatchouliContext:
    """從已解壓的 Patchouli 檔案推導 book.json/template context。

    讀取結果以 book root + locale 快取，避免數萬個 entry 逐筆掃描同一批
    template。找不到原始 book.json 時仍回傳可辨識的 ``source_available`` 狀態。
    """

    path = Path(file_path)
    if not path.is_file():
        # In-memory document callers (including the long-standing extractor
        # unit tests) do not have a book root to resolve. Keep their legacy
        # sentence behaviour; ZIP scanner supplies an explicit archive context
        # and therefore never falls through to this compatibility path.
        return context_from_metadata(
            path, resolution_error="document_only_book_missing"
        )
    parts = tuple(path.parts)
    book = _book_root_parts(parts, tuple(dir_names or _configured_book_dirs()))
    if book is None:
        return context_from_metadata(path, resolution_error="book_root_unresolved")
    root_parts, locale = book
    root = Path(*root_parts)
    base = _load_book_context(str(root), locale)
    return PatchouliContext(
        file_path=str(path),
        book_i18n=base.book_i18n,
        template_fields=base.template_fields,
        source_available=base.source_available,
        source_fingerprint=base.source_fingerprint,
        context_fingerprint=base.context_fingerprint,
        resolution_error=base.resolution_error,
    )


def _semantic(
    path: str,
    value: str,
    kind: str,
    reason: str,
    context: PatchouliContext,
    source_fingerprint: str,
    *evidence: str,
) -> PatchouliSemantic:
    return PatchouliSemantic(
        path=path,
        value=value,
        kind=kind,
        reason=reason,
        evidence=tuple(evidence),
        source_available=context.source_available,
        source_fingerprint=source_fingerprint or context.source_fingerprint,
        context_fingerprint=context.context_fingerprint,
    )


def _legacy_document_semantic(
    value: str,
    path: str,
    field: str,
    context: PatchouliContext,
    source_fingerprint: str,
    structural: bool,
) -> PatchouliSemantic | None:
    if context.resolution_error not in {
        "book_root_unresolved",
        "document_only_book_missing",
    }:
        return None
    if "text_overrides" in path:
        structural = "[1]" in path
    elif "entity_data" in _ancestor_fields(path):
        structural = False
    if structural:
        return None
    verdict = classify_book_string(value, field)
    if verdict == "text":
        return _semantic(
            path,
            value,
            LITERAL_TEXT,
            "legacy_document_only_sentence",
            context,
            source_fingerprint,
            f"field:{field}",
        )
    if verdict == "lang_key":
        return _semantic(
            path,
            value,
            TRANSLATION_REFERENCE,
            "legacy_document_only_translation_key",
            context,
            source_fingerprint,
            f"field:{field}",
        )
    return None


def _display_semantic(
    value: str,
    path: str,
    field: str,
    page_type: str,
    context: PatchouliContext,
    source_fingerprint: str,
) -> PatchouliSemantic:
    verdict = classify_book_string(value, field)
    if verdict == "text":
        return _semantic(
            path,
            value,
            LITERAL_TEXT,
            "known_display_field",
            context,
            source_fingerprint,
            f"field:{field}",
            f"page_type:{page_type or 'unknown'}",
        )
    if verdict == "lang_key" and context.book_i18n is not False:
        return _semantic(
            path,
            value,
            TRANSLATION_REFERENCE,
            "display_value_matches_translation_key",
            context,
            source_fingerprint,
            f"field:{field}",
        )
    if verdict == "lang_key":
        return _semantic(
            path,
            value,
            LITERAL_TEXT,
            "display_field_key_with_i18n_false",
            context,
            source_fingerprint,
            f"field:{field}",
        )
    return _semantic(
        path,
        value,
        DISPLAY_NONTRANSLATABLE,
        "display_field_shape_not_translatable",
        context,
        source_fingerprint,
    )


def classify_patchouli_value(
    value: str,
    path: str,
    *,
    context: PatchouliContext | None = None,
    source_fingerprint: str = "",
    known_page_type: str = "",
    display_context: bool | None = None,
    structural_context: bool = False,
) -> PatchouliSemantic:
    """對單一字串做分類；所有掃描、輸出與清理邏輯應使用此入口。"""

    context = context or context_from_metadata(
        "", source_fingerprint=source_fingerprint
    )
    field = _field(path)
    page_type = _normal_type(known_page_type or context.page_type)
    structural = structural_context or _is_structural_path(path, field)
    if display_context is None:
        display = _is_display_path(path, field)
        if not _has_known_display_schema(page_type):
            # 直接呼叫分類器時也要維持自訂頁型的保守規則，不能只依賴 walker
            # 先把 display_context 設成 False。
            display = False
    else:
        display = display_context
    legacy = _legacy_document_semantic(
        value, path, field, context, source_fingerprint, structural
    )
    if legacy is not None:
        return legacy
    if structural:
        return _semantic(
            path,
            value,
            STRUCTURAL,
            "explicit_structural_field",
            context,
            source_fingerprint,
            f"field:{field}",
        )
    if _is_translation_reference(value, i18n=context.book_i18n) and display:
        return _semantic(
            path,
            value,
            TRANSLATION_REFERENCE,
            "book_i18n_translation_key",
            context,
            source_fingerprint,
            "book_i18n=true",
            f"field:{field}",
        )
    if not value.strip() or _CJK.search(value):
        return _semantic(
            path,
            value,
            DISPLAY_NONTRANSLATABLE,
            "empty_or_already_localized",
            context,
            source_fingerprint,
        )
    shape = value_shape_reason(value)
    if shape is not None:
        return _semantic(
            path,
            value,
            DISPLAY_NONTRANSLATABLE,
            f"value_shape:{shape}",
            context,
            source_fingerprint,
        )
    if display:
        return _display_semantic(
            value, path, field, page_type, context, source_fingerprint
        )
    # 已知頁型不代表所有自訂欄位都是玩家可見文字。沒有欄位或 template
    # 證據時，保留 UNKNOWN，避免把內部 metadata 送進翻譯或寫回流程。
    reason = (
        "custom_page_requires_template_context"
        if page_type and page_type not in _KNOWN_PAGE_TYPES
        else "field_context_missing"
    )
    return _semantic(
        path,
        value,
        UNKNOWN,
        reason,
        context,
        source_fingerprint,
        f"field:{field}",
    )


def _page_context(
    node: dict[str, Any], parent_page_type: str, context: PatchouliContext
) -> tuple[str, tuple[str, ...]]:
    page_type = _normal_type(node.get("type")) or parent_page_type
    template = _normal_type(node.get("template"))
    fields = context.template_fields.get(template or page_type, ())
    return (template if template and fields else page_type), fields


def _child_flags(
    child_path: str,
    field: str,
    value: Any,
    page_type: str,
    display_context: bool | None,
    structural_context: bool,
    template_fields: tuple[str, ...],
) -> tuple[bool | None, bool]:
    structural = structural_context or _is_structural_path(child_path, field)
    if field in {"entries", "components", "text_overrides"} and not isinstance(
        value, str
    ):
        structural = structural_context
    display = display_context
    normalized_page_type = _normal_type(page_type)
    has_known_display_schema = _has_known_display_schema(normalized_page_type)
    if field in _DISPLAY_FIELDS and has_known_display_schema:
        display = True
    elif (
        normalized_page_type
        and not has_known_display_schema
        and display_context is None
    ):
        # 自訂頁型沒有 template 證據時，禁止分類器退回全域欄位名單。
        display = False
    elif field in _STRUCTURAL_FIELDS:
        display = False
    if f"variable:{field}" in template_fields:
        display = True
    if (
        isinstance(value, str)
        and "text_overrides" in child_path
        and child_path.endswith("]")
    ):
        display = "[1]" not in child_path
    return display, structural


def _walk_patchouli_node(
    node: Any,
    path: str,
    *,
    context: PatchouliContext,
    source_fingerprint: str,
    output: list[PatchouliSemantic],
    page_type: str = "",
    display_context: bool | None = None,
    structural_context: bool = False,
) -> None:
    if isinstance(node, dict):
        _walk_patchouli_mapping(
            node,
            path,
            context=context,
            source_fingerprint=source_fingerprint,
            output=output,
            page_type=page_type,
            display_context=display_context,
            structural_context=structural_context,
        )
    elif isinstance(node, list):
        _walk_patchouli_sequence(
            node,
            path,
            context=context,
            source_fingerprint=source_fingerprint,
            output=output,
            page_type=page_type,
            display_context=display_context,
            structural_context=structural_context,
        )


def _walk_patchouli_mapping(
    node: dict[str, Any],
    path: str,
    *,
    context: PatchouliContext,
    source_fingerprint: str,
    output: list[PatchouliSemantic],
    page_type: str,
    display_context: bool | None,
    structural_context: bool,
) -> None:
    current_page_type, template_fields = _page_context(node, page_type, context)
    for key, value in node.items():
        child_path = f"{path}.{key}" if path else str(key)
        display, structural = _child_flags(
            child_path,
            str(key).lower(),
            value,
            current_page_type,
            display_context,
            structural_context,
            template_fields,
        )
        if isinstance(value, str):
            output.append(
                classify_patchouli_value(
                    value,
                    child_path,
                    context=context,
                    source_fingerprint=source_fingerprint,
                    known_page_type=current_page_type,
                    display_context=display,
                    structural_context=structural,
                )
            )
            continue
        _walk_patchouli_node(
            value,
            child_path,
            context=context,
            source_fingerprint=source_fingerprint,
            output=output,
            page_type=current_page_type,
            display_context=display,
            structural_context=structural,
        )


def _walk_patchouli_sequence(
    node: list[Any],
    path: str,
    *,
    context: PatchouliContext,
    source_fingerprint: str,
    output: list[PatchouliSemantic],
    page_type: str,
    display_context: bool | None,
    structural_context: bool,
) -> None:
    for index, value in enumerate(node):
        child_path = f"{path}[{index}]"
        if isinstance(value, str):
            output.append(
                classify_patchouli_value(
                    value,
                    child_path,
                    context=context,
                    source_fingerprint=source_fingerprint,
                    known_page_type=page_type,
                    display_context=display_context,
                    structural_context=structural_context,
                )
            )
            continue
        _walk_patchouli_node(
            value,
            child_path,
            context=context,
            source_fingerprint=source_fingerprint,
            output=output,
            page_type=page_type,
            display_context=display_context,
            structural_context=structural_context,
        )


def iter_patchouli_semantics(
    document: Any,
    file_path: str | Path,
    *,
    context: PatchouliContext | None = None,
) -> tuple[PatchouliSemantic, ...]:
    """走完整份文件，回傳所有字串的可追蹤分類。"""

    context = context or context_for_path(file_path)
    output: list[PatchouliSemantic] = []
    source_fingerprint = _canonical_hash(document)
    normalized_path = str(file_path).replace("\\", "/").lower()
    file_name = PurePosixPath(normalized_path).name
    page_type = "template" if "/templates/" in f"/{normalized_path}" else ""
    if file_name == "book.json":
        page_type = "book"
    _walk_patchouli_node(
        document,
        "",
        context=context,
        source_fingerprint=source_fingerprint,
        output=output,
        page_type=page_type,
    )
    return tuple(output)


def patchouli_translatable_items(
    document: Any,
    file_path: str | Path,
    *,
    context: PatchouliContext | None = None,
) -> list[dict[str, Any]]:
    """只回傳可送進翻譯與 writer 的 literal text。"""

    items: list[dict[str, Any]] = []
    for semantic in iter_patchouli_semantics(document, file_path, context=context):
        if not semantic.ai_allowed:
            continue
        items.append(
            {
                "file": str(file_path),
                "path": semantic.path,
                "text": semantic.value,
                "source_text": semantic.value,
                "semantic_kind": semantic.kind,
                "semantic_reason": semantic.reason,
                "semantic_evidence": list(semantic.evidence),
                "source_available": semantic.source_available,
                "classifier_version": semantic.classifier_version,
                "source_fingerprint": semantic.source_fingerprint,
                "context_fingerprint": semantic.context_fingerprint,
            }
        )
    return items
