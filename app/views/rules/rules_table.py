from __future__ import annotations

import flet as ft


def create_rule_row(view, from_text, to_text, rid: int, display_no: int):
    from_field = ft.TextField(value=from_text, border=ft.InputBorder.UNDERLINE, expand=True, on_change=view.on_text_change, multiline=True, text_size=14)
    from_field.data = {'rid': rid, 'field': 'from'}
    to_field = ft.TextField(value=to_text, border=ft.InputBorder.UNDERLINE, expand=True, on_change=view.on_text_change, multiline=True, text_size=14)
    to_field.data = {'rid': rid, 'field': 'to'}
    delete_button = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_400, tooltip='刪除此列', on_click=view.delete_row_clicked, data=rid)
    return ft.DataRow(
        data=rid,
        cells=[
            ft.DataCell(ft.Text(str(display_no), color=ft.Colors.GREY_600)),
            ft.DataCell(from_field),
            ft.DataCell(to_field),
            ft.DataCell(delete_button),
        ],
    )
