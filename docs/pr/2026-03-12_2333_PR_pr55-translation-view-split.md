# PR55：translation view split

## Summary
這顆 PR 把 `translation_view.py` 內混在一起的三個 tab builder、path/action row、三條 pipeline worker glue、UI timer 往 `app/views/translation/` 子模組退層。主類別仍保留既有公開方法名稱，讓 PR51 的 characterization tests 與外部事件綁定可以無痛續用。

---

## Phase 1 完成清單
- [x] 做了：新增 `app/views/translation/translation_state.py`，收納最小執行狀態。
- [x] 做了：新增 `app/views/translation/translation_panels.py`，收納 path row / action row 與 FTB/KJS/MD 三個 tab builder。
- [x] 做了：新增 `app/views/translation/translation_actions.py`，收納 `_run_ftb/_run_kjs/_run_md` 與 `_start_ui_timer` 的核心實作。
- [x] 做了：`TranslationView` 主類別改成薄 façade，保留 `_build_*`、`_run_*`、`_start_ui_timer` 方法名。
- [x] 做了：保留 service / `TaskSession` 相容 seam，讓 tests 可繼續 monkeypatch 主模組。
- [ ] 未做：把 FTB/KJS/MD 各自升成獨立 view（原因：這會改變 UI 導航模型，不屬於單純 refactor）。

---

## What was done

### 1. 抽出 state layer
新增 `app/views/translation/translation_state.py`：
- `TranslationRunState`

先把 `picker_target_field` / `session` / `ui_timer_running` 這類執行態集中成明確 shape，雖然目前很薄，但之後還有擴張空間。

### 2. 抽出 panels layer
新增 `app/views/translation/translation_panels.py`：
- `build_path_row()`
- `build_action_row()`
- `build_ftb_tab()`
- `build_kjs_tab()`
- `build_md_tab()`

三個 tab 的 UI 組裝現在有明確模組可放，不再全部擠在主 view 類別中。

### 3. 抽出 actions layer
新增 `app/views/translation/translation_actions.py`：
- `run_ftb()`
- `run_kjs()`
- `run_md()`
- `start_ui_timer()`

這層承接 service 呼叫、worker thread、UI timer / snapshot 更新，讓 `TranslationView` 主類別回到比較像 view façade 的角色。

### 4. 保留主類別相容 façade
`translation_view.py` 目前仍保留：
- `_build_ftb_tab()` / `_build_kjs_tab()` / `_build_md_tab()`
- `_run_ftb()` / `_run_kjs()` / `_run_md()`
- `_start_ui_timer()`

此外，主 view 額外保留：
- `self.run_ftb_translation_service`
- `self.run_kubejs_tooltip_service`
- `self.run_md_translation_service`
- `self.TaskSession`

這樣 action layer 雖然抽出去了，但 monkeypatch 還是可以從主模組走，PR51 的測試不用整包改寫。

---

## Important findings
- `translation_view.py` 的核心風險在於：它不是單純 UI，裡面混了很多 service glue / thread glue / snapshot polling。
- 所以 PR55 的正解不是「拆得越碎越好」，而是：
  - panels 拆出去
  - actions 拆出去
  - façade 留在主類別
- 這樣才不會讓 characterization tests 直接失血。

---

## Validation checklist
- [x] `rg -n "def _build_ftb_tab|def _build_kjs_tab|def _build_md_tab|def _run_ftb|def _run_kjs|def _run_md|def _start_ui_timer" app/views/translation_view.py app/views/translation --glob "*.py"`
- [x] `uv run pytest -q tests/test_translation_view_characterization.py tests/test_pipeline_services_session_lifecycle.py --basetemp=.pytest-tmp\pr55 -o cache_dir=.pytest-cache\pr55`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr55-full -o cache_dir=.pytest-cache\pr55-full`

## Test result
```text
$ rg -n "def _build_ftb_tab|def _build_kjs_tab|def _build_md_tab|def _run_ftb|def _run_kjs|def _run_md|def _start_ui_timer" app/views/translation_view.py app/views/translation --glob "*.py"
app/views/translation_view.py:199:    def _build_ftb_tab(self) -> ft.Control:
app/views/translation_view.py:202:    def _build_kjs_tab(self) -> ft.Control:
app/views/translation_view.py:205:    def _build_md_tab(self) -> ft.Control:
app/views/translation_view.py:236:    def _run_ftb(self, *, dry_run: bool):
app/views/translation_view.py:239:    def _run_kjs(self, *, dry_run: bool):
app/views/translation_view.py:242:    def _run_md(self, *, dry_run: bool):
app/views/translation_view.py:248:    def _start_ui_timer(self):

$ uv run pytest -q tests/test_translation_view_characterization.py tests/test_pipeline_services_session_lifecycle.py --basetemp=.pytest-tmp\pr55 -o cache_dir=.pytest-cache\pr55
....                                                                     [100%]
4 passed in 0.44s

$ uv run pytest -q --basetemp=.pytest-tmp\pr55-full -o cache_dir=.pytest-cache\pr55-full
........................................................................ [ 43%]
........................................................................ [ 87%]
....................                                                     [100%]
164 passed, 37 warnings in 1.59s
```

---

## Rejected approaches
1) 試過：把 FTB/KJS/MD 各自升成三支獨立 View。
   - 為什麼放棄：那已經不是單純 refactor，而是 UI 導航與產品互動模型變更。
   - 最終改採：仍保留同一頁三 tab，只把內部 panels/actions/state 分層。

2) 試過：直接讓 actions layer 重新 import service 與 `TaskSession`，完全不透過主 view。
   - 為什麼放棄：這會打破既有 monkeypatch seam，PR51 的 characterization tests 會失去穩定 patch 點。
   - 最終改採：主 view 保留 service / `TaskSession` 相容欄位，actions layer 經由 view 存取。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 tab 版面
- 沒有改翻譯 service API
- 沒有處理 LM 專屬 view

---

## Next step

### PR56
- 進入 config/rules 這兩顆表單型 view，收斂表單 row / save glue / validation。
- 到這裡 view 主線就會大致收一輪了。
