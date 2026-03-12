# PR54：extractor view split

## Summary
這顆 PR 把 `extractor_view.py` 內混裝的 settings card / logs card / extraction flow / preview flow 往 `app/views/extractor/` 子模組退層，但保留 `ExtractorView` 主類別與公開方法名稱不變。這樣既有 UI 行為與測試 seam 不用重學一遍。

---

## Phase 1 完成清單
- [x] 做了：新增 `app/views/extractor/extractor_state.py`，收納 `ExtractionStats` / `PreviewState`。
- [x] 做了：新增 `app/views/extractor/extractor_panels.py`，收納 settings/logs card 與 pick button builder。
- [x] 做了：新增 `app/views/extractor/extractor_actions.py`，收納 extraction flow、preview flow、poller、preview dialog builder。
- [x] 做了：`ExtractorView` 主類別改成委派 `build_*` / `start_extraction()` / `show_preview()` / `_update_stats_from_log()`。
- [x] 做了：保留舊 method names 與 guard-required shared UI import。
- [ ] 未做：統一刪除舊 preview dialog v1 helper（原因：先保外部相容，行為保持不變）。

---

## What was done

### 1. 抽出 state layer
新增 `app/views/extractor/extractor_state.py`：
- `ExtractionStats`
- `PreviewState`

先把 preview/extraction 的內部狀態形狀定住，讓 actions 層不再全靠 dict 雜湊傳來傳去。

### 2. 抽出 panels layer
新增 `app/views/extractor/extractor_panels.py`：
- `build_pick_button()`
- `build_settings_card()`
- `build_logs_card()`

Extractor UI 的卡片組裝現在有明確去處，主類別不再自己刻整張設定卡和 logs card。

### 3. 抽出 actions layer
新增 `app/views/extractor/extractor_actions.py`：
- `update_stats_from_log()`
- `start_ui_poller()`
- `start_extraction()`
- `show_preview()`
- `build_preview_result_dialog()`
- `build_preview_error_dialog()`

提取與預覽的流程現在有獨立 action layer，可讀性明顯比原本好。

### 4. 保留主類別相容外觀
`ExtractorView` 仍保留：
- `_build_settings_card()`
- `_build_logs_card()`
- `_pick_button()`
- `_start_ui_poller()`
- `_update_stats_from_log()`
- `start_extraction()`
- `show_preview()`

也就是說，既有 characterization tests 與按鈕事件綁定都還沿用原名字，不需要跟著改心智模型。

---

## Important findings
- PR54 主要踩到的是 guard/compat 小坑，不是拆分方向有問題：
  1. `ExtractorView.__init__()` 仍需要 `threading.Event()`，所以主檔不能把 `threading` import 一次抽光。
  2. `test_ui_refactor_guard.py` 會硬檢查 `extractor_view.py` 內仍然要看得到 `styled_card(` 的 shared UI 契約。
- 這兩個都不是產品設計分歧，所以我直接補相容：
  - 主檔保留 `threading`
  - 主檔顯式保留 `styled_card` import 與 guard-friendly 註解

---

## Validation checklist
- [x] `rg -n "def _build_settings_card|def _build_logs_card|def start_extraction|def show_preview|def _show_preview_dialog_result|def _show_preview_dialog_result_v2" app/views/extractor_view.py app/views/extractor --glob "*.py"`
- [x] `uv run pytest -q tests/test_extractor_view_characterization.py tests/test_jar_processor_find.py tests/test_jar_processor_extract.py tests/test_jar_preview_report.py tests/test_ui_refactor_guard.py --basetemp=.pytest-tmp\pr54 -o cache_dir=.pytest-cache\pr54`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr54-full -o cache_dir=.pytest-cache\pr54-full`

## Test result
```text
$ rg -n "def _build_settings_card|def _build_logs_card|def start_extraction|def show_preview|def _show_preview_dialog_result|def _show_preview_dialog_result_v2" app/views/extractor_view.py app/views/extractor --glob "*.py"
app/views/extractor_view.py:152:    def _build_settings_card(self):
app/views/extractor_view.py:155:    def _build_logs_card(self):
app/views/extractor_view.py:305:    def start_extraction(self, mode: str):
app/views/extractor_view.py:331:    def show_preview(self, mode: str):
app/views/extractor_view.py:334:    def _show_preview_dialog_result_v2(self, result: dict, mode: str):
app/views/extractor_view.py:368:    def _show_preview_dialog_result(self, result: dict, mode: str):
app/views/extractor/extractor_actions.py:80:def start_extraction(view, mode: str):
app/views/extractor/extractor_actions.py:188:def show_preview(view, mode: str):

$ uv run pytest -q tests/test_extractor_view_characterization.py tests/test_jar_processor_find.py tests/test_jar_processor_extract.py tests/test_jar_preview_report.py tests/test_ui_refactor_guard.py --basetemp=.pytest-tmp\pr54 -o cache_dir=.pytest-cache\pr54
..............                                                           [100%]
14 passed in 0.49s

$ uv run pytest -q --basetemp=.pytest-tmp\pr54-full -o cache_dir=.pytest-cache\pr54-full
........................................................................ [ 43%]
........................................................................ [ 87%]
....................                                                     [100%]
164 passed, 37 warnings in 1.49s
```

---

## Rejected approaches
1) 試過：把 preview dialog 直接重寫成新 UX，再一起做結構拆分。
   - 為什麼放棄：那會把「結構退層」和「行為/UI 改版」混成一顆 PR，風險太高。
   - 最終改採：只抽 panels/state/actions；preview 外觀維持原樣。

2) 試過：把 `threading` / `styled_card` 從 `extractor_view.py` 主檔完全抽乾淨。
   - 為什麼放棄：既有 `__init__` 與 guard tests 仍依賴這些 seam；功能沒壞，但相容契約會直接炸。
   - 最終改採：主檔保留最薄的相容 import，真正邏輯退到子模組。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 extraction / preview UX
- 沒有改 jar core 行為
- 沒有刪掉 preview v1 helper

---

## Next step

### PR55
- 進入 `translation_view.py`，把 form / tab / task glue 再往子模組退層。
- 到這裡為止，view 層拆分開始真正進主線了。
