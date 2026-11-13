"""app/views/moddb/suggestions.py：條目校對右側的「同鍵值・其他版本／相同原文」建議清單。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import content_mod_value, pack_label
from app.services_impl.moddb_source_service import source_label
from app.ui import kit
from app.ui.design import C
from app.views.moddb.formatting import shorten


def build_suggestions(
    detail,
    tab: str,
    on_apply,
    *,
    catalog=None,
    on_review_same_text=None,
) -> list[ft.Control]:
    """「同鍵值・其他版本／相同原文」建議清單；``on_apply(text)`` 在按「套用」時呼叫。

    ``on_review_same_text(en_us)`` 讓「相同原文」頁籤底部出現「檢視全部不一致的譯文…」，
    開啟同文一致性審核並只看目前條目的原文。
    """
    entry = detail.entry
    controls: list[ft.Control] = []
    if tab == "key":
        rows = detail.same_key
        for r in rows:
            controls.append(
                _sug_item(
                    r.zh_tw,
                    [
                        r.mc_version,
                        source_label(
                            r.source, catalog, getattr(r, "review_status", None)
                        )
                        if r.zh_tw
                        else "",
                        _source_tag(r),
                    ],
                    apply=r.zh_tw if (r.zh_tw and r.zh_tw != entry.zh_tw) else None,
                    same=r.zh_tw == entry.zh_tw,
                    on_apply=on_apply,
                    original=_original_line(r),
                )
            )
        hint = "可按「套用」把該版本的譯文帶入輸入框（不會自動儲存）。原文相同的版本，手動儲存時會一併被取代；標示「原文不同」或「原文未知」者不會被動到（沒有原文就無法確認是同一句），套用前請先對照上方顯示的原文。"
    else:
        groups: dict[str, list] = {}
        for r in detail.same_text:
            groups.setdefault(r.zh_tw, []).append(r)
        rows = detail.same_text
        for tw, items in groups.items():
            tags = [_same_text_tag(x) for x in items[:6]]
            if len(items) > 6:
                tags.append(f"…共 {len(items)} 筆")
            controls.append(
                _sug_item(
                    tw,
                    tags,
                    apply=tw if (tw and tw != entry.zh_tw) else None,
                    same=tw == entry.zh_tw,
                    count=len(items),
                    on_apply=on_apply,
                )
            )
        hint = f"原文「{shorten(entry.en_us, 24)}」在其他鍵值／模組的譯法，用來檢查用詞一致性；只供參考。"
    if not rows:
        controls.append(kit.hint_text("沒有符合的資料"))
    else:
        controls.append(kit.hint_text(hint))
    if tab != "key" and on_review_same_text is not None and entry.en_us:
        controls.append(
            ft.Row(
                [
                    kit.button(
                        "檢視全部不一致的譯文…",
                        "ghost",
                        icon=ft.Icons.RULE,
                        size="sm",
                        on_click=lambda _e, t=entry.en_us: on_review_same_text(t),
                        tooltip="開啟同文一致性審核，只看這段原文在目前版本的所有譯法",
                    )
                ]
            )
        )
    return controls


def _same_text_tag(r) -> str:
    """相同原文建議的來源標籤。整合包 KubeJS 的模組識別（命名空間@資料夾@雜湊）
    只顯示命名空間與整合包資料夾名稱，不顯示路徑雜湊。"""
    pack = pack_label(r.kind, r.mod_id)
    mod = content_mod_value(r.kind, r.mod_id)
    where = f"{mod}（{pack}）" if pack else mod
    return f"{where}・{r.key.split('.')[-1]}・{r.mc_version}"


def _source_tag(r) -> str:
    """其他版本原文與目前條目的關係標籤：原文相同不標、沒有原文標「原文未知」、有但不同標「原文不同」。"""
    if r.same_text:
        return ""
    return "原文不同" if r.en_us else "原文未知"


def _original_line(r) -> str:
    """其他版本條目的原文（讓使用者直接對照）；ZIP 匯入的條目沒有原文時標明未知。"""
    return (
        f"原文：{shorten(r.en_us, 90)}"
        if r.en_us
        else "原文：（未知，例如由翻譯 ZIP 匯入）"
    )


def _sug_item(
    text: str,
    tags: list[str],
    *,
    apply: str | None,
    same: bool,
    on_apply,
    count: int = 0,
    original: str = "",
) -> ft.Control:
    title = text if text else "（未翻譯）"
    head = ft.Text(
        title + (f"  × {count}" if count else ""),
        size=14,
        color=C.MUTED if same or not text else C.TEXT,
        selectable=True,
        expand=True,
    )
    row: list[ft.Control] = [head]
    if apply:
        row.append(
            kit.button(
                "套用",
                "secondary",
                size="sm",
                on_click=lambda _e, t=apply: on_apply(t),
            )
        )
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=8),
        border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
        content=ft.Column(
            [
                ft.Row(row, vertical_alignment=ft.CrossAxisAlignment.START),
                *(
                    [ft.Text(original, size=12, color=C.DIM, selectable=True)]
                    if original
                    else []
                ),
                ft.Row(
                    [kit.chip(t, "neutral") for t in tags if t]
                    + ([kit.chip("與目前相同", "em")] if same and text else []),
                    spacing=6,
                    wrap=True,
                ),
            ],
            spacing=4,
        ),
    )
