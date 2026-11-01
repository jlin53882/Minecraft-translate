"""Related Lang retrieval prototype (isolated experiment; NOT wired to product paths).

Scope: same mod_id + mc_version, one kind (default 'lang'). Read-only SQLite.
Tier A  structural_suffix: X <-> X.desc/.description/.tooltip/.title (indexed key lookup).
Tier B  lexical: shared identifying words (bounded LIKE per word, low confidence only).
        Same-family siblings that differ only by a size/index suffix are reported as
        sibling_variant, never as the same entity.
Returns at most MAX_NEW evidence items for one target entry, deterministic order.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

MAX_NEW = 5
SUFFIXES = (".desc", ".description", ".tooltip", ".title")
GENERIC = {
    "advancement",
    "and",
    "block",
    "button",
    "config",
    "connect",
    "connected",
    "connecting",
    "desc",
    "description",
    "entity",
    "for",
    "gui",
    "inv",
    "item",
    "items",
    "key",
    "kubejs",
    "message",
    "mode",
    "name",
    "off",
    "on",
    "sat",
    "screen",
    "tab",
    "text",
    "the",
    "title",
    "tooltip",
    "var",
    "variant",
    "with",
}
_FORMAT_RE = re.compile(
    r"%(?:\d+\$)?[sdf]|\{\d+\}|§[0-9a-fk-orA-FK-OR]|\$\([^)]*\)|\\n|\n|\[#\]\([^)]*\)"
)
_VARIANT_SUFFIX = re.compile(r"(?:_\d+x?|_\d+)$")
_RANK_CONF = {"high": 0, "medium": 1, "low": 2}
_RANK_REL = {"structural_suffix": 0, "lexical": 1, "sibling_variant": 2}


@dataclass(frozen=True)
class Evidence:
    entry_id: int
    key: str
    en_us: str
    zh_tw: str | None
    review_status: str | None
    relation_type: str
    relevance_reason: str
    confidence: str
    limitations: tuple[str, ...]


def open_readonly(path: str) -> sqlite3.Connection:
    """Open strictly read-only (mode=ro); never migrates, never writes."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def format_signature(text: str) -> tuple[str, ...]:
    return tuple(sorted(m.group(0) for m in _FORMAT_RE.finditer(text or "")))


def sibling_family(key: str) -> str:
    """Key with a trailing size/index suffix removed (e.g. _5x, _3)."""
    return _VARIANT_SUFFIX.sub("", key)


def key_tokens(key: str) -> list[str]:
    """Identifying words: drop the type and namespace segments of 'type.namespace.name...'."""
    segments = key.lower().split(".")
    identifying = segments[2:] if len(segments) >= 3 else segments[-1:]
    words = re.split(r"[._\-:/]+", ".".join(identifying))
    return sorted({w for w in words if len(w) >= 3 and w not in GENERIC})


def _escape_like(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _untranslated(en: str, zh: str | None) -> bool:
    """A 'translation' that repeats the English (or is still plain ASCII) is not a reference."""
    if zh is None or not zh.strip():
        return False
    if zh.strip() == en.strip():
        return True
    return zh.isascii() and en.isascii()


def _to_evidence(row, source_en, relation, reason, confidence, extra=()):
    limits = list(extra)
    review = row["review_status"]
    if review != "reviewed":
        limits.append("unreviewed or unknown review status: not authoritative")
        if confidence == "high":
            confidence = "medium"
    if format_signature(row["en_us"]) != format_signature(source_en):
        limits.append("format tokens differ: not used as same-text evidence")
        confidence = "low"
    if _untranslated(row["en_us"], row["zh_tw"]):
        limits.append(
            "untranslated_reference: zh_tw repeats the English, not a translation"
        )
    return Evidence(
        entry_id=int(row["id"]),
        key=row["key"],
        en_us=row["en_us"],
        zh_tw=row["zh_tw"],
        review_status=review,
        relation_type=relation,
        relevance_reason=reason,
        confidence=confidence,
        limitations=tuple(limits),
    )


_BASE_SELECT = (
    "SELECT e.id, e.key, e.en_us, f.zh_tw, f.review_status FROM entry e "
    "LEFT JOIN effective f ON f.entry_id = e.id "
)


def retrieve(
    conn, *, entry_id: int, kind: str = "lang", exclude_ids: frozenset = frozenset()
) -> list[Evidence]:
    """Bounded retrieval for one target entry (at most MAX_NEW new evidence items)."""
    src = conn.execute(
        "SELECT id, key, mod_id, mc_version, en_us FROM entry WHERE id = ?", (entry_id,)
    ).fetchone()
    if src is None:
        return []
    key, en = src["key"], src["en_us"]
    found: dict[int, Evidence] = {}

    # Tier A: exact structural suffix pairs (uses idx_entry_kind_mod_key).
    suffix = next((x for x in SUFFIXES if key.endswith(x)), None)
    if suffix:
        base = key[: -len(suffix)]
        candidates = [base] + [base + x for x in SUFFIXES]
        reason = f"structural: same identifier '{base}' with a different suffix"
    else:
        candidates = [key + x for x in SUFFIXES]
        reason = (
            "structural: target is the identifier of a companion '.desc/.title/...' key"
        )
    candidates = [c for c in dict.fromkeys(candidates) if c != key]
    if candidates:
        marks = ",".join("?" * len(candidates))
        rows = conn.execute(
            _BASE_SELECT
            + f"WHERE e.kind = ? AND e.mc_version = ? AND e.mod_id = ? AND e.key IN ({marks})",
            (kind, src["mc_version"], src["mod_id"], *candidates),
        ).fetchall()
        for row in rows:
            rid = int(row["id"])
            if rid == entry_id or rid in exclude_ids:
                continue
            found[rid] = _to_evidence(row, en, "structural_suffix", reason, "high")

    # Tier B: lexical overlap (bounded LIKE per word, LIMIT per word).
    token_hits: dict[int, set[str]] = {}
    for token in key_tokens(key)[:4]:
        rows = conn.execute(
            _BASE_SELECT
            + "WHERE e.kind = ? AND e.mc_version = ? AND e.mod_id = ? AND e.key LIKE ? ESCAPE '\\' LIMIT 200",
            (kind, src["mc_version"], src["mod_id"], f"%{_escape_like(token)}%"),
        ).fetchall()
        for row in rows:
            rid = int(row["id"])
            if rid == entry_id or rid in exclude_ids or rid in found:
                continue
            token_hits.setdefault(rid, set()).add(token)
    for rid, shared in token_hits.items():
        row = conn.execute(_BASE_SELECT + "WHERE e.id = ?", (rid,)).fetchone()
        if sibling_family(row["key"]) == sibling_family(key):
            found[rid] = _to_evidence(
                row,
                en,
                "sibling_variant",
                "same family, different size/index suffix",
                "low",
                ("sibling variant: a different entity, not a translation reference",),
            )
        else:
            found[rid] = _to_evidence(
                row,
                en,
                "lexical",
                f"lexical overlap on {sorted(shared)}",
                "low",
                ("lexical only: a shared word is not proof of the same entity",),
            )

    ordered = sorted(
        found.values(),
        key=lambda e: (
            _RANK_CONF[e.confidence],
            _RANK_REL[e.relation_type],
            e.entry_id,
        ),
    )
    return ordered[:MAX_NEW]
