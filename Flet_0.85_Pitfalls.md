# Flet 0.85 Pitfalls / 採坑記錄

記錄在 Flet 0.85 開發過程中遇到的坑。

---

## 1. TextField 的 helper 屬性名稱

**問題**：Flet 0.85 的 `TextField` 没有 `helper_text` 屬性。

**正確寫法**：
```python
# 錯誤（property 不存在）
tf.helper_text = "some text"

# 正確（Flet 0.85 用 helper）
tf.helper = "some text"
```

**其他相關屬性**：
- `helper` - helper text 內容
- `helper_max_lines` - 最大行數
- `helper_style` - TextStyle

---

## 2. 背景執行緒不能直接操作 UI 控制項

**問題**：在背景執行緒（如 `threading.Thread`）中直接修改 Flet UI 控制項（如 `log_view.controls.append()`），變更會被 Flet 忽略，UI 不會更新。

**原因**：Flet UI 更新必須在主執行緒（main thread）中進行。

**解法**：使用 `page.run_task()` 把 UI 更新包成 async task：

```python
# 錯誤（跨執行緒直接更新）
view._append_log_line(log_msg)

# 正確（包成 run_task）
async def _do_append_log(_):
    view._append_log_line(log_msg)
view.page.run_task(_do_append_log, None)
```

進度條更新也用了同樣的模式，所以進度條正常但日誌沒更新的原因就在這裡。

---

## 3. Border.all() 在 Flet 0.85 已移除

**問題**：`ft.Border.all()` 在 Flet >= 0.85 不存在。

**正確寫法**：
```python
# 錯誤
border = ft.Border.all(color=theme.OUTLINE)

# 正確
border = ft.Border(
    top=ft.BorderSide(color=theme.OUTLINE),
    right=ft.BorderSide(color=theme.OUTLINE),
    bottom=ft.BorderSide(color=theme.OUTLINE),
    left=ft.BorderSide(color=theme.OUTLINE),
)
```

---

## 4. `page.open()` / `page.close()` 已廢除

**問題**：Flet 0.80+ 已廢除 `page.open()` 和 `page.close()`。

**正確寫法**：用 `overlay` + `open` 屬性：
```python
dialog = ft.AlertDialog(...)
page.overlay.append(dialog)
dialog.open = True
```

---

## 5. `run_task` 必須傳入 async coroutine function

**問題**：如果傳入的不是 async 函式，`run_task` 會失敗或行為異常。

**正確寫法**：
```python
async def _do_update(_):
    view.status_text.value = "處理中"
    view.page.update()

view.page.run_task(_do_update, None)
```

---

## 6. `ft.app()` 已廢除，改用 `ft.run()`

**問題**：`ft.app(target=main)` 在 Flet 0.80+ 顯示 deprecation warning。

**正確寫法**：
```python
# 錯誤
ft.app(target=main)

# 正確
ft.run(target=main)
```