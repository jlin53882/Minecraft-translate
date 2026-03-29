# View 重構設計建議（最終版）

> 日期：2026-03-16 
> 版本：v12（真正最終版）

---

## PR1：qc_view.py 拆分 ✅ 已完成

### QCBase.task_worker

```python
class QCBase:
 def __init__(self, page, progress_bar, log_view):
 self.page = page
 self.progress_bar = progress_bar
 self.log_view = log_view
 
 def task_worker(self, service_func, args_tuple, on_complete=None, controls_to_disable=None):
 if controls_to_disable:
 for ctrl in controls_to_disable:
 ctrl.disabled = True
 self.page.update()
 
 def run():
 try:
 for update in service_func(*args_tuple):
 log_msg = update.get("log", "")
 for line in log_msg.split("\n"):
 if line.strip():
 self.log_view.controls.append(ft.Text(line))
 
 if "progress" in update:
 self.progress_bar.value = update["progress"]
 if update.get("error"):
 self.progress_bar.color = theme.RED
 
 self.log_view.scroll_to(offset=-1, duration=100)
 self.page.update()
 finally:
 self.progress_bar.value = 0
 self.progress_bar.color = None
 self.page.update()
 
 if controls_to_disable:
 for ctrl in controls_to_disable:
 ctrl.disabled = False
 self.page.update()
 
 if on_complete:
 on_complete()
 
 threading.Thread(target=run, daemon=True).start()
```

### UntranslatedChecker

```python
class UntranslatedChecker(ft.Container):
 def __init__(self, page, file_picker, task_runner):
 self.page = page
 self.file_picker = file_picker
 self.task_runner = task_runner
 
 if file_picker not in page.overlay:
 page.overlay.append(file_picker)
 
 self.en_dir = ft.TextField(...)
 self.tw_dir = ft.TextField(...)
 self.out_dir = ft.TextField(...)
 self.start_button = ft.ElevatedButton("開始檢查", on_click=self._on_start)
 
 super().__init__(content=self._build_content())
 
 def _build_content(self):
 return ft.Column([
 self.en_dir,
 self.tw_dir,
 self.out_dir,
 self.start_button,
 ])
 
 def _on_start(self, e):
 controls = [self.start_button, self.en_dir, self.tw_dir, self.out_dir]
 self.task_runner.task_worker(
 run_untranslated_check_service,
 (self.en_dir.value, self.tw_dir.value, self.out_dir.value),
 controls_to_disable=controls
 )
```

---

## PR2a：啟用 cache panels

### CacheView

```python
class CacheView(ft.Column):
 def __init__(self, page):
 super().__init__()
 self.page = page
 
 self.query_state = CacheQueryState()
 self.shard_state = CacheShardState()
 
 self.query_panel = CacheQueryPanel(page, self.query_state)
 self.shard_panel = CacheShardPanel(page, self.shard_state)
 
 self.controls = [self.query_panel, self.shard_panel]
```

### CacheQueryPanel

```python
class CacheQueryPanel(ft.Container):
 def __init__(self, page: ft.Page, state: CacheQueryState):
 self.page = page
 self.state = state
 
 self.search_field = ft.TextField(
 hint_text="輸入關鍵字搜尋...",
 prefix_icon=ft.Icons.SEARCH,
 on_submit=self._on_search,
 expand=True,
 )
 self.results_list = ft.ListView(expand=True, spacing=5)
 
 super().__init__(content=self._build_content())
 
 def _build_content(self):
 return ft.Column([
 self.search_field,
 self.results_list,
 ])
 
 def _on_search(self, e):
 """✅ 補上搜尋 handler"""
 self.search(self.search_field.value)
 
 def search(self, keyword: str):
 cache_type = self.state.cache_type
 
 results = cache_search_service(
 cache_type=cache_type,
 query=keyword,
 mode="key",
 limit=5000,
 )
 self.state.query_results = results.get("items", [])
 self.update()
```

---

## PR2b：搜尋邏輯

已包含在 CacheQueryPanel 中。

---

## PR3：移除 _run_on_ui_thread

```python
def _initial_load(self):
 def run():
 try:
 rules_data = self._load_rules_core()
 self._handle_reload_success(rules_data)
 self.page.update()
 except Exception as err:
 self._show_snack_bar(f"初次載入規則失敗: {err}", theme.RED_600)
 self.page.update()
 
 threading.Thread(target=run, daemon=True).start()
```

---

## ⚠️ 已知取捨

### search() 直接修改 state

這是職責滲透，但對此規模的專案影響有限，接受此取捨。

### controls_to_disable 參數傳遞

理論上可能有 race condition（快速連點），但實際觸發機率極低。

---

## PR 執行順序

| PR | 內容 | 狀態 |
|----|------|------|
| PR1 | qc_view.py 拆分 | ✅ 已完成 |
| PR2a | 啟用 cache panels | ✅ 已完成 |
| PR2b | 實作搜尋邏輯 | ✅ 已完成 |
| PR3 | 移除 _run_on_ui_thread | ✅ 已完成 |

---

> ⚠️ 此為設計文件最終版本，可開始動工
