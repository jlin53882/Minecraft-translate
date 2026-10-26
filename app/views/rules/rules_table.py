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
    from_field.helper_style = ft.TextStyle(size=11, color=C.GOLD)
    to_field = kit.text_field(
        value=to_text,
        multiline=True,
        mono=True,
        expand=True,
        on_change=view.on_text_change,
    )
    to_field.data = {"rid": rid, "field": "to"}
    issue_icon = ft.Icon(ft.Icons.WARNING_AMBER, color=C.GOLD, size=18, visible=False)
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
            ft.DataCell(ft.Row([issue_icon, delete_button], spacing=0, tight=True)),
        ],
    )


def apply_row_status(row, target: str, error: str, hint: str) -> None:
    """套用驗證結果：出錯的欄位標紅並顯示原因；通過但可疑時只在 from 下方提醒。"""
    from_field, to_field = row.cells[1].content, row.cells[2].content
    for name, field in (("from", from_field), ("to", to_field)):
        bad = bool(error) and name == target
        field.border_color = C.RED if bad else None
        field.error = error if bad else None
    from_field.helper = None if error else (hint or None)


def set_row_issues(row, messages: list[str]) -> None:
    """列尾的警示圖示：有診斷問題時顯示，懸停說明全部原因（不阻擋儲存）。"""
    icon = row.cells[3].content.controls[0]
    icon.visible = bool(messages)
    icon.tooltip = "\n".join(messages) if messages else None


def row_delete_button(row):
    """列尾的刪除按鈕。"""
    return row.cells[3].content.controls[1]
