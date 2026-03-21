with open('app/views/merge_view.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: self.zip_list_view should be ft.ListView with horizontal scroll
old_zip_view = "        # ZIP 清單\n        self.zip_list_view = ft.Column(\n            [ft.Text(\"尚未加入任何 ZIP 檔案\", size=12, color=theme.GREY_400)],\n            scroll=\"auto\",\n            spacing=4,\n            tight=True,\n        )"
new_zip_view = "        # ZIP 清單（橫向滾動）\n        self.zip_list_view = ft.ListView(\n            [],\n            expand=True,\n            direction=ft.Axis.HORIZONTAL,\n            spacing=8,\n        )\n        # 空的 ZIP list 顯示提示\n        self._zip_empty_placeholder = ft.Text(\"尚未加入任何 ZIP 檔案\", size=12, color=theme.GREY_400)"
print('Fix1 found:', old_zip_view[:60] in content)
content = content.replace(old_zip_view, new_zip_view)

# Fix 2: Remove the two inline duplicate ListViews in controls
old_bad_1 = "                    ft.Container(height=8),\n                    # ZIP chips with horizontal scroll\n                    ft.Container(\n                        content=ft.ListView(\n                            expand=True,\n                            scroll=\"auto\",\n                            direction=ft.Axis.HORIZONTAL,\n                            controls=[],  # will be populated at runtime via self.zip_list_view\n                        ),\n                        height=60,\n                    ),\n                    ft.Container(height=4),\n                    # Existing zip list view (reused for runtime population)\n                    ft.Container(\n                        content=ft.ListView(\n                            expand=True,\n                            scroll=\"auto\",\n                            direction=ft.Axis.HORIZONTAL,\n                            controls=self.zip_list_view.controls,\n                        ),\n                        height=60,\n                    ),"
new_bad_1 = "                    ft.Container(height=4),\n                    ft.Container(\n                        content=self.zip_list_view,\n                        height=60,\n                    ),"
print('Fix2 found:', old_bad_1[:60] in content)
content = content.replace(old_bad_1, new_bad_1)

with open('app/views/merge_view.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
