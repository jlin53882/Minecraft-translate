"""Render bounded, source-aware batch replacement preview pages."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import flet as ft

from app.services_impl.moddb_service import (
    SRC_MANUAL,
    BatchReplaceChange,
    token_category,
)
from app.services_impl.moddb_source_service import (
    MANUAL_REVIEW_LABELS,
    SourceCatalog,
    source_label,
)
from app.ui import kit
from app.ui.design import C

_QUALITY_LABELS = {
    "worsened": "品質變差",
    "improved": "品質改善",
    "mixed": "品質混合變化（仍需確認）",
    "unchanged": "品質無變化",
}
_CATEGORY_LABELS = {
    "placeholder": "佔位符",
    "minecraft": " Minecraft 色碼",
    "patchouli": " Patchouli 巨集",
    "newline": "換行",
    "other": "特殊 token",
}


def _token_label(token: str) -> str:
    if token == "\n":
        return "實際換行 LF"
    if token == "\\n":
        return "字面 \\n"
    return token if token.isprintable() else token.encode("unicode_escape").decode()


def _quality_details(row: BatchReplaceChange) -> list[str]:
    details = [
        f"{'缺少' if delta.direction == 'missing' else '多出'}"
        f"{_CATEGORY_LABELS.get(token_category(delta.token), '特殊 token')}"
        f" `{_token_label(delta.token)}`：{delta.before} → {delta.after}"
        for delta in row.quality_deltas
    ]
    if row.old_whitespace_note or row.new_whitespace_note:
        before = row.old_whitespace_note or "無空白風險"
        after = row.new_whitespace_note or "無空白風險"
        details.append(f"前後空白風險：{before} → {after}")
    return details or ["特殊字元與前後空白無變化"]


def _effective_source_detail(row: BatchReplaceChange, catalog: SourceCatalog) -> str:
    source_name = source_label(row.effective_source, catalog)
    source_code = "未知" if row.effective_source is None else row.effective_source
    if row.effective_source == SRC_MANUAL:
        state = row.effective_review_status
        if state not in MANUAL_REVIEW_LABELS:
            state = "legacy_unknown"
        review = source_label(SRC_MANUAL, catalog, state)
    else:
        review = "人工審核：不適用"
    checker = row.effective_checker or "未知"
    return f"生效來源：{source_name}〔{source_code}〕 · checker {checker} · {review}"


def _row_selector(
    row: BatchReplaceChange,
    *,
    excluded_roots: set[int],
    selection_active: bool,
    busy: bool,
    on_toggle: Callable[[int, bool], None],
) -> ft.Control:
    if row.is_extra_version:
        return ft.Text("跨版本額外項目", size=10.5, color=C.DIM)
    if not selection_active:
        return ft.Text("根條目", size=10.5, color=C.DIM)
    checked = row.entry_id not in excluded_roots
    return ft.Checkbox(
        value=checked,
        label="處理此根條目" if checked else "個別排除",
        on_change=lambda e: on_toggle(row.entry_id, bool(e.control.value)),
        disabled=busy,
    )


def render_batch_preview_rows(
    rows: Iterable[BatchReplaceChange],
    *,
    catalog: SourceCatalog,
    excluded_roots: set[int],
    selection_active: bool,
    show_skipped: bool,
    busy: bool,
    on_toggle: Callable[[int, bool], None],
) -> list[ft.Control]:
    """Build controls only for the supplied page, never the complete plan."""
    controls: list[ft.Control] = []
    for row in rows:
        if show_skipped:
            controls.append(
                ft.Text(
                    f"略過・{row.mc_version}・{row.key}：{row.reason}",
                    size=11.5,
                    color=C.GOLD,
                    selectable=True,
                )
            )
            continue
        change_label = _QUALITY_LABELS[row.quality_change_kind]
        controls.append(
            ft.Column(
                [
                    ft.Row(
                        [
                            _row_selector(
                                row,
                                excluded_roots=excluded_roots,
                                selection_active=selection_active,
                                busy=busy,
                                on_toggle=on_toggle,
                            ),
                            ft.Text(change_label, size=10.5, color=C.GOLD),
                        ],
                        spacing=4,
                        wrap=True,
                    ),
                    ft.Text(
                        f"原文：{row.en_us or '（原文未知）'}",
                        size=11,
                        color=C.MUTED,
                        selectable=True,
                    ),
                    ft.Text(
                        f"{row.mc_version} · {row.mod_id} · {row.kind} · {row.key}",
                        size=10,
                        color=C.DIM,
                        selectable=True,
                    ),
                    ft.Text(
                        _effective_source_detail(row, catalog), size=10, color=C.DIM
                    ),
                    ft.Text(
                        f"繁中譯文：{row.old_zh_tw} → {row.new_zh_tw}",
                        size=11.5,
                        color=C.TEXT,
                        selectable=True,
                    ),
                    *[
                        ft.Text(
                            f"品質差異：{detail}",
                            size=10.5,
                            color=C.GOLD,
                            selectable=True,
                        )
                        for detail in _quality_details(row)
                    ],
                ],
                spacing=2,
                tight=True,
            )
        )
    return controls or [kit.hint_text("此頁沒有條目")]
