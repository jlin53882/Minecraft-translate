"""共用的 LM 格式契約、payload 建構與結構種類判斷。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from translation_tool.core.guide_format import format_spec

# These are stable DB wire values.  Keep this low-level policy module free of
# ``translation_db`` imports: that package eagerly imports its repository and
# quality modules, which would create a cycle through the shared LM budget.
KIND_GUIDE = "guide"
KIND_PATCHOULI = "patchouli"
STRUCTURED_KINDS = frozenset({KIND_GUIDE, KIND_PATCHOULI})

# These are the exact legacy lines emitted by the previous config schema.  The
# normalizer is intentionally line-scoped so a user's larger custom paragraph
# is never rewritten just because it contains similar wording.
_LEGACY_FORMAT_RULES = frozenset(
    {
        "保留每筆 value 原有的實際換行數量；可依譯文語意調整換行位置，但不可新增或刪除實際換行",
        "字面上的反斜線+n（\\n）必須原樣保留，不可轉成實際換行；實際換行也不可改成字面上的反斜線+n。",
    }
)
_LEGACY_RULE_PREFIX = re.compile(r"^(?:(?:[-*+]\s+)|(?:\d+[.)]\s+))?(.*)$")

FORMAT_POLICY_PROMPT = r"""以下格式契約優先於前文任何互相衝突的換行或格式規則。
【格式契約（程式驗證的硬性規則）】
- 空白行、段落分隔、<br>、文字形式的 \n、Markdown 標題／清單／引用必須保留。
- 同一段落內、沒有空白行或顯式硬換行的英文排版折行，可以依繁體中文語意自然重排。
- 不可自行新增大量換行，也不可刪除或合併硬結構。
- 不可把實際換行轉成文字形式的 \n，也不可把文字形式的 \n 轉成實際換行。
- 只回傳結構化 JSON，不要輸出說明文字。
"""

STRUCTURED_FORMAT_POLICY_PROMPT = r"""【Guide/Patchouli 結構補充契約】
- 空白行、段落分隔、<br>、文字形式的 \n、Markdown 標題／清單／引用與 Patchouli 元件結構必須保留。
- <ItemLink ... /> 的標籤名稱、屬性名稱、id、冒號、括號與斜線必須原樣保留。
- 只翻譯 Patchouli 標籤以外的人類語言文字。
"""


def normalize_legacy_prompt(prompt: str) -> str:
    """Remove only exact legacy newline-rule lines from a configured prompt."""
    if not isinstance(prompt, str) or not prompt:
        return prompt

    kept: list[str] = []
    changed = False
    for line in prompt.splitlines(keepends=True):
        content = line.rstrip("\r\n").strip()
        match = _LEGACY_RULE_PREFIX.fullmatch(content)
        if match and match.group(1).strip() in _LEGACY_FORMAT_RULES:
            changed = True
            continue
        kept.append(line)
    if not changed:
        return prompt
    return "".join(kept).strip()


def structured_kind(kind: object) -> bool:
    """Return whether the kind has Markdown/Patchouli hard structure."""
    return str(kind or "") in STRUCTURED_KINDS


def format_policy_for_items(items: Iterable[dict[str, Any]]) -> str:
    """Return common policy plus the structure section only for Guide/Patchouli."""
    values = list(items)
    if any(structured_kind(kind_for_item(item)) for item in values):
        return f"{FORMAT_POLICY_PROMPT}\n{STRUCTURED_FORMAT_POLICY_PROMPT}"
    return FORMAT_POLICY_PROMPT


def kind_for_item(item: dict[str, Any]) -> str | None:
    """Resolve the DB kind carried by an item, including standalone Markdown items."""
    kind = item.get("_kind")
    if not kind:
        ident = item.get("db_ident")
        kind = ident[0] if isinstance(ident, (tuple, list)) and ident else None
    if kind:
        return str(kind)
    cache_type = str(item.get("cache_type") or "")
    if cache_type == "md":
        return KIND_GUIDE
    if cache_type == "patchouli":
        return KIND_PATCHOULI
    return None


def format_spec_for_item(item: dict[str, Any]) -> dict[str, object] | None:
    """Return a payload format specification only for structured translation kinds."""
    kind = kind_for_item(item)
    if not structured_kind(kind):
        return None
    source = str(item.get("source_text") or item.get("text") or "")
    return format_spec(source)


def build_payload_item(
    item: dict[str, Any],
    item_id: str,
    *,
    retry_reasons: Iterable[str] = (),
    format_issues: Iterable[str] = (),
) -> dict[str, object]:
    """Build the only request-item shape used by normal and retry requests."""
    payload: dict[str, object] = {
        "id": str(item_id),
        "value": str(item.get("text") or ""),
    }
    spec = format_spec_for_item(item)
    if spec is not None:
        payload["format_spec"] = spec
    reasons = list(retry_reasons)
    issues = list(format_issues)
    if reasons:
        payload["retry_reasons"] = reasons
    if issues:
        payload["format_issues"] = issues
    return payload


def build_translation_payload(
    items: Iterable[dict[str, Any]],
    *,
    item_ids: Iterable[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Build the normal request payload with the same item builder as retry."""
    values = list(items)
    ids = (
        list(item_ids) if item_ids is not None else [str(i) for i in range(len(values))]
    )
    if len(ids) != len(values):
        raise ValueError("payload item IDs and items must have the same length")
    return {
        "items": [
            build_payload_item(item, item_id)
            for item, item_id in zip(values, ids, strict=True)
        ]
    }


def estimate_format_spec_tokens(item: dict[str, Any], estimate_text_tokens) -> float:
    """Estimate only the additional input tokens introduced by ``format_spec``."""
    spec = format_spec_for_item(item)
    if spec is None:
        return 0.0
    return estimate_text_tokens(json.dumps({"format_spec": spec}, ensure_ascii=False))


__all__ = [
    "FORMAT_POLICY_PROMPT",
    "STRUCTURED_FORMAT_POLICY_PROMPT",
    "STRUCTURED_KINDS",
    "build_payload_item",
    "build_translation_payload",
    "estimate_format_spec_tokens",
    "format_policy_for_items",
    "format_spec_for_item",
    "kind_for_item",
    "normalize_legacy_prompt",
    "structured_kind",
]
