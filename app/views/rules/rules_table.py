from __future__ import annotations

import flet as ft

from app.ui import kit
from app.ui.design import C


def create_rule_row(view, from_text, to_text, rid: int, display_no: int):
    """建立規則表格列（兩個等寬字可多行輸入框 + 序號 + 刪除）。"""
    from_field = kit.text_field(
        value=from_text,
        multiline=True,
        mono=True,
        expand=True,
        on_change=view.on_text_change,
    )
    from_field.data = {"rid": rid, "field": "from"}
    to_field = kit.text_field(
        value=to_text,
        multiline=True,
        mono=True,
        expand=True,
        on_change=view.on_text_change,
    )
    to_field.data = {"rid": rid, "field": "to"}
    delete_button = ft.IconButton(
        icon=ft.Icons.DELETE_OUTLINE,
        icon_color=C.RED,
        tooltip="刪除此列",
        on_click=view.delete_row_clicked,
        data=rid,
    )
    return ft.DataRow(
        data=rid,
        cells=[
            ft.DataCell(ft.Text(str(display_no), color=C.DIM, size=12)),
            ft.DataCell(from_field),
            ft.DataCell(to_field),
            ft.DataCell(delete_button),
        ],
    )
