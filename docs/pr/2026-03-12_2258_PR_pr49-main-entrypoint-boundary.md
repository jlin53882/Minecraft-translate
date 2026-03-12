# PR49：main entrypoint boundary

## Summary
這顆 PR 把 `main.py` 裡仍混在一起的 view registry 與 startup task 配線責任抽出去，讓 `main.py` 更像真正的 entrypoint。畫面組裝本體仍留在 main，不硬拆到看不懂；抽出去的是明顯的 registry / startup glue。

---

## Phase 1 完成清單
- [x] 做了：新增 `app/view_registry.py`，集中 navigation item 與 window size registry。
- [x] 做了：新增 `app/startup_tasks.py`，集中 startup 背景任務與索引重建 glue。
- [x] 做了：`main.py` 改成使用 view registry / startup tasks，保留 entrypoint + UI 組裝本體。
- [x] 做了：新增 focused tests：`tests/test_view_registry.py`、`tests/test_startup_tasks.py`。
- [x] 做了：保留 `main.py` 顯式 import `wrap_view` 的 guard 契約。
- [ ] 未做：layout / theme toggle 再往外拆（原因：這層還留在 main 反而比較可讀）。

---

## What was done

### 1. 抽出 view registry
新增 `app/view_registry.py`：
- `build_view_registry()`
- `get_window_size()`
- `build_navigation_destinations()`
- `VIEW_WINDOW_SIZES` / `DEFAULT_WINDOW_SIZE`

`main.py` 不再同時持有：
- `nav_destinations`
- `view_window_sizes`
- index 對齊的隱含規則

現在這些配線責任集中到 registry 模組。

### 2. 抽出 startup task glue
新增 `app/startup_tasks.py`：
- `rebuild_index_on_startup()`
- `start_background_startup_tasks()`

`main.py` 不再自己內嵌 `_rebuild_index_on_startup()` 與 thread 啟動細節。

### 3. 保留 main.py 作為 entrypoint + UI 組裝
`main.py` 現在保留：
- `bootstrap_runtime()`
- Flet page 初始化
- layout / theme toggle
- 呼叫 registry 建立導覽與 view
- 啟動 startup tasks

這樣入口還是可讀，不會變成只剩 20 行但你要跳五個檔案才能看懂的假瘦身。

---

## Important findings
- PR49 真正踩到的不是結構問題，而是 repo 內已有 guard test 明確要求 `main.py` 仍要顯式 import `wrap_view`。
- 一開始我把 `wrap_view` 只留在 `view_registry.py`，功能沒壞，但 `test_ui_refactor_guard.py` 直接失敗。
- 這不是你需要決策的架構分歧，所以我已直接修正：`main.py` 重新顯式 import `wrap_view`，保留 guard 契約。

---

## Validation checklist
- [x] `rg -n "nav_destinations|view_window_sizes|_rebuild_index_on_startup|cache_rebuild_index_service" main.py app --glob "*.py"`
- [x] `uv run pytest -q tests/test_main_imports.py tests/test_view_registry.py tests/test_startup_tasks.py --basetemp=.pytest-tmp\pr49 -o cache_dir=.pytest-cache\pr49`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr49-full -o cache_dir=.pytest-cache\pr49-full`

## Test result
```text
$ rg -n "nav_destinations|view_window_sizes|_rebuild_index_on_startup|cache_rebuild_index_service" main.py app --glob "*.py"
app/services_impl/cache/cache_services.py:220:def cache_rebuild_index_service() -> Dict[str, Any]:
app/startup_tasks.py:8:from app.services_impl.cache.cache_services import cache_rebuild_index_service
app/startup_tasks.py:15:        cache_rebuild_index_service()
app/views/cache_view.py:39:    cache_rebuild_index_service,  # A3 搜尋功能
app/views/cache_view.py:1827:            result = cache_rebuild_index_service()

$ uv run pytest -q tests/test_main_imports.py tests/test_view_registry.py tests/test_startup_tasks.py --basetemp=.pytest-tmp\pr49 -o cache_dir=.pytest-cache\pr49
.....                                                                    [100%]
5 passed in 0.60s

$ uv run pytest -q --basetemp=.pytest-tmp\pr49-full -o cache_dir=.pytest-cache\pr49-full
........................................................................ [ 56%]
........................................................                 [100%]
128 passed in 1.49s
```

---

## Rejected approaches
1) 試過：連 page theme toggle、layout 組裝也一起抽到新模組，讓 `main.py` 只剩 20 行。
   - 為什麼放棄：這樣短期看起來很瘦，實際閱讀反而要跳很多檔。
   - 最終改採：只抽 registry 與 startup task；畫面組裝仍留在 entrypoint。

2) 試過：讓 `wrap_view` 只留在 `view_registry.py`，main 不再顯式 import。
   - 為什麼放棄：repo 內已有 guard test 明確要求 main 保留這條相依，直接打破既有 refactor 契約。
   - 最終改採：main 顯式保留 `wrap_view` import，registry 只承接資料與 view factory。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 UI layout
- 沒有把 disabled views 重新接回 main
- 沒有處理 `qc_view.py`

---

## Next step

### PR50
- 進一步整理 config proxy 與 text processor 的舊式依賴。
- entrypoint 現在乾淨一點了，後面就能往 utilities / views 的相依層再收。
