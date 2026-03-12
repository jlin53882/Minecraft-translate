# PR57：dead code and compat cleanup

## Summary
這顆 PR 不做大掃除，而是只清四個有明確證據支持、且已被新邊界完整取代的殘留 compat/dead code。原則跟設計稿一致：沒有證據的不刪，`app.services` 與 `LazyConfigProxy` 都先留著。

---

## Phase 1 完成清單
- [x] 做了：inventory 檢查 `app.services` / `LazyConfigProxy` / 舊 helper caller 分布。
- [x] 做了：刪除 `_task_runner.run_generator_task()`（無 caller）。
- [x] 做了：刪除 `ExtractorView` 舊 preview v1 helper 群（無 caller）。
- [x] 做了：刪除 `rules_view.py` 內 `_perform_reload()` / `_perform_save()` 殘留 wrapper（無 caller）。
- [x] 做了：保留 `app.services` 與 `LazyConfigProxy`，因 evidence 顯示還不能動。
- [ ] 未做：`qc_view.py` / `app.services.py` 最終命運（原因：這題已明確留給 PR58 / 後續 QC 決策）。

---

## 刪除/移除/替換說明

### 1. `_task_runner.run_generator_task()`
- 為什麼改：PR48 後 pipeline wrappers 實際只用 `run_callable_task()`。
- 為什麼能刪：全文搜尋只命中定義，沒有任何 caller。
- 目前誰在用或沒人在用：`rg` 只剩 `_task_runner.py` 自己。
- 替代路徑：無；目前不存在 generator-based wrapper caller。
- 風險：若未來又想做 generator wrapper，需要重新引入或新增新 helper。
- 如何驗證：targeted lifecycle tests + full pytest 全綠。

### 2. `ExtractorView` 舊 preview v1 helper 群
刪除項目：
- `_show_preview_dialog_result()`
- `_show_preview_dialog_error()`
- `_close_dialog()`
- `_start_from_preview()`

- 為什麼改：PR54 後 extractor 已全面切到 overlay/v2 路徑。
- 為什麼能刪：全文搜尋只命中這些方法自己的定義，沒有任何 caller。
- 目前誰在用或沒人在用：無 caller；實際使用的是 `_show_preview_dialog_result_v2()` / `_show_preview_dialog_error_v2()` / `_close_dialog_overlay()` / `_start_from_preview_overlay()`。
- 替代路徑：v2 overlay helpers。
- 風險：若外部手動反射呼叫這些舊私有方法會失效；但 repo 內無證據顯示存在這種使用。
- 如何驗證：extractor characterization + jar tests + full pytest 全綠。

### 3. `rules_view.py` 殘留 wrapper
刪除項目：
- `_perform_reload()`
- `_perform_save()`

- 為什麼改：PR56 後 reload/save thread glue 已收斂到 `rules_actions.py`。
- 為什麼能刪：全文搜尋只命中定義，無 caller。
- 目前誰在用或沒人在用：無 caller；實際入口是 `reload_rules_clicked()` → `start_reload_thread()`、`save_rules_clicked()` → `start_save_thread()`。
- 替代路徑：`app/views/rules/rules_actions.py`
- 風險：若未來有人直呼叫這兩個私有方法會失效；但 repo 內無此用法。
- 如何驗證：rules characterization + full pytest 全綠。

### 4. 明確不刪：`app.services.py`
- 為什麼沒改：`qc_view.py` 仍直接 `from app.services import ...`。
- 為什麼不能刪：這不是無 caller dead code，而是 active QC façade。
- 目前誰在用：`app/views/qc_view.py`
- 替代路徑：暫無；依既有決策，留待 PR58 / QC 線最後決定。
- 風險：若在這顆 PR 硬砍，QC 線會直接壞。
- 如何驗證：inventory 證據已保留。

### 5. 明確不刪：`LazyConfigProxy`
- 為什麼沒改：inventory 顯示 `config_manager.py` 內仍明確保留 `config = LazyConfigProxy()`；這是還活著的 compat seam。
- 為什麼不能刪：PR50 只做顯式 helper 收斂，沒有做完整 caller migration。
- 目前誰在用：目前至少作為 compat surface 存在，無充分證據可安全刪除。
- 替代路徑：`config_access.get_runtime_config()` / `resolve_runtime_path()`，但尚未完成全域替換。
- 風險：現在硬砍可能造成低頻 caller / monkeypatch seam 斷裂。
- 如何驗證：PR50 結論與 inventory 一致，因此本顆保留不動。

---

## Important findings
- `rg` 在 PR57 這種 cleanup PR 裡比直覺更重要：很多『看起來像死碼』的東西，其實只是低頻 compat seam。
- 這顆最重要的成果不是刪了多少，而是**明確知道哪些現在不能刪**。
- 特別是：
  - `app.services.py`：仍是 QC active façade
  - `LazyConfigProxy`：仍是 active compat seam

---

## Validation checklist
- [x] `rg -n "TODO|Deprecated|legacy|compat|wrapper|shim|re-export|from .* import \*" translation_tool app --glob "*.py"`
- [x] `rg -n "lm_translator_shared|lang_merge_content|config = LazyConfigProxy|app\.services" translation_tool app tests --glob "*.py"`
- [x] `rg -n "_show_preview_dialog_result\(|run_generator_task\(|_perform_reload\(|_perform_save\(" app tests translation_tool --glob "*.py"` → 無命中（rg exit code 1，代表清理目標已不存在）
- [x] `uv run pytest -q tests/test_ui_refactor_guard.py tests/test_extractor_view_characterization.py tests/test_rules_view_characterization.py tests/test_pipeline_services_session_lifecycle.py tests/test_pipeline_services_error_handling.py --basetemp=.pytest-tmp\pr57 -o cache_dir=.pytest-cache\pr57`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr57-full -o cache_dir=.pytest-cache\pr57-full`

## Test result
```text
$ uv run pytest -q tests/test_ui_refactor_guard.py tests/test_extractor_view_characterization.py tests/test_rules_view_characterization.py tests/test_pipeline_services_session_lifecycle.py tests/test_pipeline_services_error_handling.py --basetemp=.pytest-tmp\pr57 -o cache_dir=.pytest-cache\pr57
...............                                                          [100%]
15 passed, 4 warnings in 0.46s

$ uv run pytest -q --basetemp=.pytest-tmp\pr57-full -o cache_dir=.pytest-cache\pr57-full
........................................................................ [ 43%]
........................................................................ [ 87%]
....................                                                     [100%]
164 passed, 37 warnings in 1.56s
```

---

## Rejected approaches
1) 試過：順手把 `app.services.py` 一起清掉。
   - 為什麼放棄：`qc_view.py` 仍在用，這不是 dead code。
   - 最終改採：明確標示為保留項，留待 PR58 最終處理。

2) 試過：順手把 `LazyConfigProxy` 一起砍掉。
   - 為什麼放棄：PR50 只做顯式 helper 收斂，還沒完成完整 migration；現在砍是賭。
   - 最終改採：保留作 compat seam，這顆只刪有完整 caller 證據的殘留殼。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有處理 `qc_view.py`
- 沒有刪掉 `app.services.py`
- 沒有刪掉 `LazyConfigProxy`
- 沒有處理 UI deprecation warnings

---

## Next step

### PR58
- 最後才碰 QC 舊線與 `app.services.py` 最終命運。
- 到這裡主線 refactor 基本都收完了，剩下的就是真正需要判斷『保留/重構/淘汰』的 QC 決策題。
