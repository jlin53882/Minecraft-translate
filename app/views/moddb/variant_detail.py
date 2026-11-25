"""「其他原文」頁籤的右側檢視：主原文與這份其他原文並排，長文字可捲動、可選取。

只負責把一筆 ``VariantListRow`` 畫成控制項；資料讀取與寫入都在 ``VariantsPanel``。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_source_service import source_label
from app.ui import design, kit
from app.ui.design import C
from app.views.moddb.formatting import kind_label
from app.views.moddb.text_compare import text_control

# 文字區的固定高度：長段落（指南、手冊）在區塊內捲動，不會把另一欄推出畫面
SOURCE_BOX_HEIGHT = 280
TRANSLATION_BOX_HEIGHT = 200


def _text_box(title: str, body: ft.Control, height: int) -> ft.Container:
    return ft.Container(
        expand=True,
        padding=12,
        bgcolor=C.PANEL2,
        border_radius=design.RADIUS_CONTROL,
        border=ft.Border.all(1, C.LINE),
        content=ft.Column(
            [
                kit.section_label(title),
                ft.Column([body], scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            spacing=6,
            tight=True,
        ),
        height=height,
    )


def _empty_hint(text: str) -> ft.Text:
    return ft.Text(text, size=12.5, color=C.DIM, italic=True)


def build_variant_detail(
    row,
    catalog,
    *,
    version: str,
    on_adopt: Callable[[], None] | None,
) -> ft.Control:
    """一份其他原文的完整內容。

    ``on_adopt`` 只在這份原文沒有譯文、條目有目前譯文時提供（按下後沿用目前譯文）；
    差異以紅底標在這份其他原文上（與主原文相比）。
    """
    main_body: ft.Control = text_control(row.main_en, show_diff=False)
    other_body: ft.Control = text_control(row.en_us, row.main_en)
    main_zh: ft.Control = (
        text_control(row.main_zh, show_diff=False)
        if row.main_zh
        else _empty_hint("（條目尚無譯文）")
    )
    if row.zh_tw:
        label = source_label(row.source, catalog, row.review_status)
        other_zh: ft.Control = text_control(row.zh_tw, show_diff=False)
        other_zh_title = f"這份原文的譯文（{label}）"
    else:
        other_zh = _empty_hint("（尚無譯文）")
        other_zh_title = "這份原文的譯文"

    header: list[ft.Control] = [
        ft.Column(
            [
                ft.Text(
                    row.key,
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=C.TEXT,
                    selectable=True,
                ),
                kit.hint_text(f"{row.mod_id} · {kind_label(row.kind)} · {version}"),
            ],
            spacing=2,
            expand=True,
        )
    ]
    if on_adopt is not None:  # 放在標題列：不必捲動就能看到
        header.append(
            kit.button(
                "沿用目前譯文",
                "secondary",
                icon=ft.Icons.CONTENT_COPY_OUTLINED,
                on_click=lambda _e: on_adopt(),
                tooltip="把條目目前的譯文存成這份原文的譯文（未審核、只供流水線使用）",
            )
        )
    controls: list[ft.Control] = [
        ft.Row(header, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Row(
            [
                _text_box("條目主原文", main_body, SOURCE_BOX_HEIGHT),
                _text_box("這份其他原文", other_body, SOURCE_BOX_HEIGHT),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        ft.Row(
            [
                _text_box("條目目前譯文", main_zh, TRANSLATION_BOX_HEIGHT),
                _text_box(other_zh_title, other_zh, TRANSLATION_BOX_HEIGHT),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        kit.hint_text(
            "其他原文的譯文只供一鍵流水線與機器翻譯查詢，不是條目本身的譯文，"
            "不影響條目的生效譯文。紅底標出與主原文不同的字元。"
        ),
    ]
    return ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
