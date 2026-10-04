# Flet worker-thread 契約盤點（#114）

契約（Flet 1.0；詳見 issue #114）：

- worker thread **不得直接** mutate Flet Control，也不得直接 `page.update()` / `show_snack()`；UI 更新必須排回 event loop（`page.run_task`、`self._ui(...)`、`UiBatcher`、`TaskSession` 事件輪詢、`loop.call_soon_threadsafe`）。
- sync event handler 內不得做阻塞工作（長 I/O、迴圈、`time.sleep`）。
- 長生命週期的訂閱／輪詢要有 teardown。

盤點方式：

```bash
grep -rnE 'threading\.Thread|threading\.Timer|Thread\(' app --include=*.py
```

並逐一閱讀 worker 與它呼叫的同模組方法。`threading.Timer` 已無使用（`app/ui/debounce.py` 的 `Debouncer` 以 `page.run_task` + `asyncio.sleep` 取代）。

## 結果：22 個 `threading.Thread` 啟動點，全部符合契約

| 位置 | 回到 UI 的方式 |
|---|---|
| `translation/translation_actions.py`（FTB／KubeJS／MD 三處） | worker 只寫 `TaskSession`；`page.run_task(_poll_session)` 在 loop 上同步 |
| `qc_base.py` | `UiBatcher`（內部 `page.run_task`） |
| `pipeline/pipeline_view.py`（兩處） | `self._ui(...)`（`page.run_task`） |
| `pipeline/pipeline_extract_dialog.py` | worker 只寫 `preview_state`；`poll_preview` 以 `page.run_task` 啟動 |
| `merge_view.py`（`_run_merge`、`poll`） | session + `page.run_task(_sync_ui)` |
| `rules_view.py`、`rules/rules_actions.py`（三處） | `_run_on_ui_thread`（`loop.call_soon_threadsafe`；掛載前暫存到 `did_mount`） |
| `lookup_view.py`（兩處） | `self._run_on_ui(...)`（`page.run_task`） |
| `extractor/extractor_dialog.py`（`run_extraction`、`do_scan`） | `UiBatcher`／`run_on_ui`／`page.run_task` poller |
| `dashboard_view.py` | `work()` 只讀資料，`_apply_on_ui` 以 `page.run_task` 套用 |
| `bundler_view.py` | `UiBatcher` |
| `lm_view.py` | worker 只寫 session；`page.run_task(self._poll_session)` |
| `startup_tasks.py` | 純索引重建，不碰 UI |
| `shell/config_effects.py` | `on_reloaded` → `AppShell._submit_ui`（`page.run_task`） |

## 本次修正

- `icon_preview_view.py`：掃描在 `asyncio.to_thread` 內，進度 callback 原本直接賦值 `progress_text.value` / `progress_bar.value`。改為 `IconPreviewView._set_progress()` 只記下最新值，賦值與刷新由 event loop 上的 `_refresh_progress()` 完成；掃描結束時清掉延遲的進度，避免重新顯示進度條。

## 已知限制（未在本 PR 處理）

事件迴圈上的同步阻塞：

- `icon_preview_view.py`：`_detect_source_mode` 與 `_begin_scan` 在 `run_task` 之前對來源資料夾做 `glob("*.jar")` / `rglob("en_us.json")`；`_try_use_cached_entries` 讀取 L2 快取 JSON。大型模組包會短暫卡住畫面。
- `pipeline/pipeline_extract_dialog.py`：`show_preview_result` 同步呼叫 `find_jar_files(mods)`。

缺少明確 teardown 的輪詢（皆會在任務結束或頁面卸載造成 `RuntimeError` 時結束，不會無限執行）：

- `translation_actions.py` 與 `lm_view.py` 的 `_poll_session`（view 沒有 `will_unmount`）。
- `merge_view.py` 的 `poll`（`_ui_stop` 只在 DONE／ERROR 設定）。

已有 teardown：`bundler_view.py` 的 `will_unmount`（取消 config 訂閱）、`AppShell.dispose()`（取消 tasks／config 訂閱）。

這些需要真實視窗（卸載時序、大型資料夾）驗證後再改，建議隨 #114 的後續階段處理。
