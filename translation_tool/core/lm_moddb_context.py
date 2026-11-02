"""Attach module-scoped LLM context for Mod DB translation routes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import PurePath
from typing import Any

from translation_tool.core.lm_batch_budget import BudgetConfig
from translation_tool.core.lm_session import TranslationSessionState
from translation_tool.translation_db.schema import (
    KIND_FTB_QUESTS,
    KIND_GUIDE,
    KIND_KUBEJS_SCRIPT,
    KIND_LANG,
    KIND_PATCHOULI,
)
from translation_tool.utils.config_manager import load_config

_KIND_BY_CACHE_TYPE = {
    "lang": KIND_LANG,
    "md": KIND_GUIDE,
    "patchouli": KIND_PATCHOULI,
    "kubejs": KIND_KUBEJS_SCRIPT,
    "ftbquests": KIND_FTB_QUESTS,
}
_MOD_MARKERS = {"lang", "patchouli_books", "advancements", "ftbquests", "kubejs"}


def _infer_kind(item: dict[str, Any]) -> str:
    kind = item.get("_kind")
    if isinstance(kind, str) and kind:
        return kind
    ident = item.get("db_ident")
    if isinstance(ident, tuple) and ident and isinstance(ident[0], str):
        return ident[0]
    return _KIND_BY_CACHE_TYPE.get(str(item.get("cache_type") or "lang"), KIND_LANG)


def _infer_module_id(item: dict[str, Any]) -> str:
    value = item.get("_mod_id")
    if isinstance(value, str) and value:
        return value
    ident = item.get("db_ident")
    if isinstance(ident, tuple) and len(ident) >= 2 and ident[1]:
        return str(ident[1])
    raw_file = str(item.get("file") or "").replace("\\", "/")
    parts = list(PurePath(raw_file).parts)
    lowered = [part.casefold() for part in parts]
    for marker in _MOD_MARKERS:
        if marker in lowered:
            index = lowered.index(marker)
            if index > 0:
                return parts[index - 1]
    return ""


def _item_context(
    item: dict[str, Any], mc_version: str | None
) -> tuple[str, str | None, str]:
    """回傳批次項目的模組、版本與資料種類。"""
    kind = _infer_kind(item)
    mod_id = _infer_module_id(item)
    version = (
        item.get("_expected_version")
        or item.get("mc_version")
        or item.get("version")
        or mc_version
    )
    return mod_id, str(version) if version else None, kind


def _source_text_contexts(
    items: list[dict[str, Any]], mc_version: str | None
) -> tuple[
    dict[tuple[str, str | None], dict[str, None]],
    dict[tuple[str, str | None, str], dict[str, None]],
]:
    """一次收集術語與完整句查詢所需的模組／版本／種類來源集合。"""
    by_context: dict[tuple[str, str | None], dict[str, None]] = {}
    by_kind_context: dict[tuple[str, str | None, str], dict[str, None]] = {}
    for item in items:
        mod_id, version, kind = _item_context(item, mc_version)
        source = item.get("source_text") or item.get("text")
        if not isinstance(source, str) or not source.strip():
            continue
        context_key = (mod_id, version)
        by_context.setdefault(context_key, {}).setdefault(source, None)
        by_kind_context.setdefault((*context_key, kind), {}).setdefault(source, None)
    return by_context, by_kind_context


def _attach_reviewed_context(
    item: dict[str, Any],
    *,
    mod_id: str,
    version: str | None,
    kind: str,
    reviewed_glossary_loader: Callable[..., list[dict[str, str]]] | None,
    reviewed_exact_loader: Callable[..., list[dict[str, str]]] | None,
    source_texts_by_context: dict[tuple[str, str | None], dict[str, None]],
    source_texts_by_kind_context: dict[
        tuple[str, str | None, str], dict[str, None]
    ],
    glossaries: dict[tuple[str, str | None], list[dict[str, str]]],
    exact_glossaries: dict[tuple[str, str | None, str], list[dict[str, str]]],
) -> None:
    """將兩種已審核參考以共用的模組／版本／種類範圍注入項目。"""
    if not mod_id:
        return
    if reviewed_glossary_loader is not None:
        glossary_key = (mod_id, version)
        if glossary_key not in glossaries:
            source_texts: Sequence[str] = tuple(
                source_texts_by_context.get(glossary_key, {})
            )
            glossaries[glossary_key] = reviewed_glossary_loader(
                mod_id, version, source_texts
            )
        item["_reviewed_glossary"] = glossaries[glossary_key]
    if reviewed_exact_loader is not None:
        exact_key = (mod_id, version, kind)
        if exact_key not in exact_glossaries:
            source_texts = tuple(source_texts_by_kind_context.get(exact_key, {}))
            exact_glossaries[exact_key] = reviewed_exact_loader(
                mod_id, version, kind, source_texts
            )
        item["_reviewed_exact_glossary"] = exact_glossaries[exact_key]


def attach_moddb_context(
    items: list[dict[str, Any]],
    *,
    operation_id: str,
    mc_version: str | None = None,
    reviewed_glossary_loader: Callable[..., list[dict[str, str]]] | None = None,
    reviewed_exact_loader: Callable[..., list[dict[str, str]]] | None = None,
) -> None:
    """Attach module-wide session/glossary while preserving kind batch boundaries.

    A module's language and Patchouli entries share conversation history, but their
    ``_batch_group`` remains ``(module, kind)`` so prompts and cache buckets never
    mix in one request.
    """
    lm_config = load_config().get("lm_translator", {})
    estimator = BudgetConfig.from_config(
        lm_config if isinstance(lm_config, dict) else {}
    ).token_estimator
    if (
        isinstance(lm_config, dict)
        and not lm_config.get("moddb_reviewed_glossary", True)
    ):
        reviewed_glossary_loader = None
        reviewed_exact_loader = None
    sessions: dict[str, TranslationSessionState] = {}
    glossaries: dict[tuple[str, str | None], list[dict[str, str]]] = {}
    exact_glossaries: dict[tuple[str, str | None, str], list[dict[str, str]]] = {}
    source_texts_by_context, source_texts_by_kind_context = _source_text_contexts(
        items, mc_version
    )
    for item in items:
        mod_id, item_version, kind = _item_context(item, mc_version)
        item.setdefault("_kind", kind)
        item.setdefault("_mod_id", mod_id)
        item.setdefault("_batch_group", (mod_id, kind))
        session_key = mod_id or str(item.get("file") or item.get("path") or "")
        sessions.setdefault(
            session_key,
            TranslationSessionState(
                f"moddb:{operation_id}:{session_key}", estimator=estimator
            ),
        )
        item["_translation_session"] = sessions[session_key]
        _attach_reviewed_context(
            item,
            mod_id=mod_id,
            version=item_version,
            kind=kind,
            reviewed_glossary_loader=reviewed_glossary_loader,
            reviewed_exact_loader=reviewed_exact_loader,
            source_texts_by_context=source_texts_by_context,
            source_texts_by_kind_context=source_texts_by_kind_context,
            glossaries=glossaries,
            exact_glossaries=exact_glossaries,
        )


# 只存在於記憶體的執行期欄位（物件無法 JSON 序列化）：翻譯 session、已審核 glossary、
# 指南／Markdown／任務段落的行內保護（``ShieldedText``）。
_RUNTIME_ONLY_KEYS = frozenset(
    {
        "_translation_session",
        "_reviewed_glossary",
        "_reviewed_exact_glossary",
        "_shielded",
    }
)


def strip_runtime_context(item: dict[str, Any]) -> dict[str, Any]:
    """Remove live session/glossary/shield objects before checkpoint or preview JSON."""
    return {key: value for key, value in item.items() if key not in _RUNTIME_ONLY_KEYS}


__all__ = ["attach_moddb_context", "strip_runtime_context"]
