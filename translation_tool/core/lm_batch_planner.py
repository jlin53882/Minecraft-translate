"""Canonical batch grouping and planning for every LLM translation route."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from inspect import Parameter, signature
from typing import Any

from translation_tool.core.lm_batch_budget import (
    profile_for_cache_type,
    select_batch_size,
)
from translation_tool.core.source_locale import normalize_source_locale


def _locale_group_part(item: dict[str, Any]) -> str:
    """批次邊界中的來源語系：正規化後的語系、沒有語系回傳空字串。

    不合法的語系 token 不能被丟掉（那會讓它混進別的批次）；以獨立的值保留，
    讓只有它自己的批次在送出時依 ``batch_source_locale`` fail closed。
    """
    raw = item.get("source_locale")
    if raw in (None, ""):
        return ""
    return normalize_source_locale(raw) or f"invalid:{raw}"


def batch_group_key(item: dict[str, Any]) -> tuple[str, ...]:
    """Return the stable boundary key used by both preview and execution.

    批次邊界永遠包含來源語系：不同語系（包含 ``en_us``、``zh_cn`` 這類預設語系與
    沒有語系的項目）不會放進同一批，因為 ``batch_source_locale`` 遇到同批混有多種語系
    會直接拒絕（見 ``translation_tool.core.source_locale``）。Mod DB 路線已在
    ``_batch_group`` 的第三個元素帶入非預設語系，這裡不重複加入。
    """
    group = item.get("_batch_group")
    if isinstance(group, tuple) and len(group) == 3:
        return tuple(str(part) for part in group)
    if isinstance(group, tuple) and len(group) == 2:
        return (*(str(part) for part in group), _locale_group_part(item))
    # 一般目錄翻譯沒有 Mod DB 的模組邊界；同 cache type 且同來源語系的項目放同一批。
    return "", str(item.get("cache_type") or "lang"), _locale_group_part(item)


def group_items_by_batch_key(
    items: Sequence[dict[str, Any]],
    *,
    check: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Stable-group items by ``cache_type + batch_group`` without losing order."""
    groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    order: list[tuple[str, tuple[str, ...]]] = []
    for item in items:
        if check is not None:
            check()
        key = (str(item.get("cache_type") or "lang"), batch_group_key(item))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    return [item for key in order for item in groups[key]]


def next_batch(
    items: Sequence[dict[str, Any]],
    *,
    start: int = 0,
    lm_cfg: dict[str, Any] | None,
    batch_size_for_type: Callable[[str], int],
    fixed_input_tokens_for_batch: Callable[[list[dict[str, Any]]], float] | None = None,
    select_size: Callable[..., int] = select_batch_size,
    check: Callable[[], None] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Select the exact next prefix used by preview and the live translation loop."""
    if start < 0:
        raise ValueError("start must be non-negative")
    if start >= len(items):
        return [], "lang"
    if check is not None:
        check()
    cache_type = str(items[start].get("cache_type") or "lang")
    preferred = int(batch_size_for_type(cache_type) or 0)
    if preferred <= 0:
        preferred = 50
    group = batch_group_key(items[start])
    homogeneous = 1
    # 只探查本批可能使用的窗口；下一批會從新的 start 重新確認邊界。
    while start + homogeneous < len(items) and homogeneous < preferred:
        if check is not None and homogeneous % 128 == 0:
            check()
        candidate = items[start + homogeneous]
        if str(candidate.get("cache_type") or "lang") != cache_type:
            break
        if batch_group_key(candidate) != group:
            break
        homogeneous += 1
    window = list(items[start : start + min(homogeneous, preferred)])
    fixed_input_tokens = (
        fixed_input_tokens_for_batch(window)
        if fixed_input_tokens_for_batch is not None
        else 0.0
    )
    select_args = (window, profile_for_cache_type(cache_type), preferred, lm_cfg)
    if fixed_input_tokens_for_batch is None:
        fit_count = select_size(*select_args)
    elif _accepts_fixed_input_tokens(select_size):
        fit_count = select_size(*select_args, fixed_input_tokens=fixed_input_tokens)
    else:
        # Keep old test/plugin selector seams callable while production
        # selectors still receive the unified fixed-prompt budget.
        fit_count = select_size(*select_args)
    return window[: max(1, min(fit_count, len(window)))], cache_type


def _accepts_fixed_input_tokens(selector: Callable[..., int]) -> bool:
    """Detect legacy selector mocks without catching their internal errors."""
    try:
        params = signature(selector).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == "fixed_input_tokens"
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in params
    )


def count_planned_batches(
    items: Sequence[dict[str, Any]],
    *,
    lm_cfg: dict[str, Any] | None,
    batch_size_for_type: Callable[[str], int],
    fixed_input_tokens_for_batch: Callable[[list[dict[str, Any]]], float] | None = None,
    select_size: Callable[..., int] = select_batch_size,
    check: Callable[[], None] | None = None,
) -> int:
    """Count the same windows that ``next_batch`` would submit."""
    batches = 0
    offset = 0
    while offset < len(items):
        batch, _cache_type = next_batch(
            items,
            start=offset,
            lm_cfg=lm_cfg,
            batch_size_for_type=batch_size_for_type,
            fixed_input_tokens_for_batch=fixed_input_tokens_for_batch,
            select_size=select_size,
            check=check,
        )
        if not batch:
            break
        offset += len(batch)
        batches += 1
    return batches


__all__ = [
    "batch_group_key",
    "count_planned_batches",
    "group_items_by_batch_key",
    "next_batch",
]
