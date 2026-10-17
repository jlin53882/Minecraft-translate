"""Attach module-scoped LLM context for Mod DB translation routes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import PurePath
from typing import Any

from translation_tool.core.lm_session import TranslationSessionState
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_LANG, KIND_PATCHOULI
from translation_tool.utils.config_manager import load_config

_KIND_BY_CACHE_TYPE = {
    "lang": KIND_LANG,
    "md": KIND_GUIDE,
    "patchouli": KIND_PATCHOULI,
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


def attach_moddb_context(
    items: list[dict[str, Any]],
    *,
    operation_id: str,
    mc_version: str | None = None,
    reviewed_glossary_loader: Callable[..., list[dict[str, str]]] | None = None,
) -> None:
    """Attach module-wide session/glossary while preserving kind batch boundaries.

    A module's language and Patchouli entries share conversation history, but their
    ``_batch_group`` remains ``(module, kind)`` so prompts and cache buckets never
    mix in one request.
    """
    if reviewed_glossary_loader is not None:
        config = load_config()
        lm_config = config.get("lm_translator", {})
        if isinstance(lm_config, dict) and not lm_config.get(
            "moddb_reviewed_glossary", True
        ):
            reviewed_glossary_loader = None
    sessions: dict[str, TranslationSessionState] = {}
    glossaries: dict[tuple[str, str | None], list[dict[str, str]]] = {}
    source_texts_by_context: dict[tuple[str, str | None], tuple[str, ...]] = {}
    for candidate in items:
        candidate_mod = _infer_module_id(candidate)
        candidate_version = (
            candidate.get("_expected_version")
            or candidate.get("mc_version")
            or candidate.get("version")
            or mc_version
        )
        context_key = (
            candidate_mod,
            str(candidate_version) if candidate_version else None,
        )
        source = candidate.get("source_text") or candidate.get("text")
        if isinstance(source, str) and source.strip():
            source_texts_by_context[context_key] = tuple(
                dict.fromkeys((*source_texts_by_context.get(context_key, ()), source))
            )
    for item in items:
        kind = _infer_kind(item)
        mod_id = _infer_module_id(item)
        item_version = (
            item.get("_expected_version")
            or item.get("mc_version")
            or item.get("version")
            or mc_version
        )
        item_version = str(item_version) if item_version else None
        item.setdefault("_kind", kind)
        item.setdefault("_mod_id", mod_id)
        item.setdefault("_batch_group", (mod_id, kind))
        session_key = mod_id or str(item.get("file") or item.get("path") or "")
        sessions.setdefault(
            session_key,
            TranslationSessionState(f"moddb:{operation_id}:{session_key}"),
        )
        item["_translation_session"] = sessions[session_key]
        if reviewed_glossary_loader is not None and mod_id:
            glossary_key = (mod_id, item_version)
            if glossary_key not in glossaries:
                source_texts: Sequence[str] = source_texts_by_context.get(
                    glossary_key, ()
                )
                glossaries[glossary_key] = reviewed_glossary_loader(
                    mod_id, item_version, source_texts
                )
            item["_reviewed_glossary"] = glossaries[glossary_key]


def strip_runtime_context(item: dict[str, Any]) -> dict[str, Any]:
    """Remove live session/glossary objects before checkpoint or preview JSON."""
    return {
        key: value
        for key, value in item.items()
        if key not in {"_translation_session", "_reviewed_glossary"}
    }


__all__ = ["attach_moddb_context", "strip_runtime_context"]
