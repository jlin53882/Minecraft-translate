# PR49 設計稿：`main.py` 啟動責任收斂

## Summary
`main.py` 現在其實已經比之前乾淨，但它還同時扮演 entrypoint、view registry、視窗尺寸表、startup task glue。PR49 的任務是把這些配線責任抽出去，讓 `main.py` 真正只像 entrypoint。

---

## Phase 0 盤點
- 目前 `main.py` 約 148 行。
- 已驗證 import-time logging side effect 已移除，`bootstrap_runtime()` 只在 `__main__` 呼叫。
- 檔案仍直接持有 `nav_destinations`、`view_window_sizes` 與 `_rebuild_index_on_startup()`。
- repo 已有 `tests/test_main_imports.py`，可作為入口責任整理的 baseline guard。

---

## 設計範圍
- 新增 `app/view_registry.py`，集中定義 navigation item、view factory、window size 設定；避免兩份資料靠 index 對齊。
- 新增 `app/startup_tasks.py`，集中 `_rebuild_index_on_startup()` 與背景 thread 啟動 glue。
- `main.py` 保留：page 初始化、theme toggle、呼叫 view registry 建立 UI、觸發 startup tasks。
- 保持 `bootstrap_runtime()` 只做 config + logging 初始化，不偷塞其他 side effect。
- 新增 `tests/test_view_registry.py`、`tests/test_startup_tasks.py`，並保留 `tests/test_main_imports.py`。

---

## Validation checklist
- [ ] `rg -n "nav_destinations|view_window_sizes|_rebuild_index_on_startup|cache_rebuild_index_service" main.py app --glob "*.py"`
- [ ] `uv run pytest -q tests/test_main_imports.py tests/test_view_registry.py tests/test_startup_tasks.py --basetemp=.pytest-tmp\pr49 -o cache_dir=.pytest-cache\pr49`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr49-full -o cache_dir=.pytest-cache\pr49-full`

---

## Rejected approaches
1) 試過：連 page theme toggle、layout 組裝也一起抽到新模組，讓 `main.py` 只剩 20 行。
2) 為什麼放棄：這會把 UI 組裝邏輯過度拆散，短期看起來很瘦，實際閱讀反而要跳很多檔。
3) 最終改採：只抽 registry 與 startup task；畫面組裝仍留在 entrypoint，保持入口可讀性。

---

## Not included in this PR
- 不改 UI layout。
- 不把 disabled views 重新接回 main。
- 不處理 `qc_view.py`。

---

## Next step
- PR50 進一步整理 config proxy 與 text processor 的舊式依賴。
