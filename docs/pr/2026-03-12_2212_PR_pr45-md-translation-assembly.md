# PR45：md translation assembly split

## Summary
這顆 PR 把 `translation_tool/core/md_translation_assembly.py` 內混在一起的 progress proxy、step glue、統計邏輯拆開，讓 `run_md_pipeline()` 保留原契約，但 step1/2/3 與 progress adapter 都可以被單獨測。

---

## Phase 1 完成清單
- [x] 做了：新增 `md_translation_progress.py`，收納 `_ProgressProxy`。
- [x] 做了：新增 `md_translation_steps.py`，收納 `step1_extract` / `step2_translate` / `step3_inject` 的核心實作。
- [x] 做了：新增 `md_translation_stats.py`，收納 lang mode normalization、pending doc counting、step2 stats logging。
- [x] 做了：`md_translation_assembly.py` 改成入口 orchestrator + façade wrapper。
- [x] 做了：新增 focused tests：`tests/test_md_pipeline_steps.py`、`tests/test_md_progress_proxy.py`。
- [ ] 未做：Markdown plugin 本身的抽取/回寫策略重寫（原因：本 PR 只整理 step glue / progress / stats）。

---

## What was done

### 1. 拆出 progress layer
新增 `translation_tool/core/md_translation_progress.py`：
- `_ProgressProxy`

UI-facing 的 progress 區段映射不再和 pipeline orchestration 混在同一檔。

### 2. 拆出 step layer
新增 `translation_tool/core/md_translation_steps.py`：
- `step1_extract_impl()`
- `step2_translate_impl()`
- `step3_inject_impl()`

step-level logic 現在有獨立模組，後面若要改 step 行為或補 focused tests，不需要再打開整個 assembly 入口。

### 3. 拆出 stats layer
新增 `translation_tool/core/md_translation_stats.py`：
- `normalize_lang_mode()`
- `count_json_files()`
- `count_md_pending_docs()`
- `log_md_step2_stats()`

統計與 log 文案邏輯，現在不再夾在 step glue 中間。

### 4. 保留 assembly 作為入口 orchestrator
`translation_tool/core/md_translation_assembly.py` 現在保留：
- façade wrapper：`step1_extract()` / `step2_translate()` / `step3_inject()`
- 總入口：`run_md_pipeline()`

這樣 caller 不必改 import，符合「只切結構、不改 contract」的主原則。

---

## Important findings
- PR45 最大的坑不是程式，而是 checklist 裡 `rg` 原本用了 PowerShell 不吃的萬用字元寫法；實際修正成明列檔案後，檢查正常通過。
- 這顆檔案雖然沒 FTB/KubeJS 那麼大，但 progress proxy / stats / step glue 混在一起時，一樣讓 focused testing 很難下手；拆完之後可測性明顯好很多。
- 這次 façade strategy 依然守住，沒有引入 caller migration 成本。

---

## Validation checklist
- [x] `rg -n "class _ProgressProxy|def step1_extract|def step2_translate|def step3_inject|def run_md_pipeline" translation_tool/core/md_translation_assembly.py translation_tool/core/md_translation_progress.py translation_tool/core/md_translation_stats.py translation_tool/core/md_translation_steps.py`
- [x] `uv run pytest -q tests/test_md_pipeline_steps.py tests/test_md_progress_proxy.py --basetemp=.pytest-tmp\pr45 -o cache_dir=.pytest-cache\pr45`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr45-full -o cache_dir=.pytest-cache\pr45-full`

## Test result
```text
$ rg -n "class _ProgressProxy|def step1_extract|def step2_translate|def step3_inject|def run_md_pipeline" translation_tool/core/md_translation_assembly.py translation_tool/core/md_translation_progress.py translation_tool/core/md_translation_stats.py translation_tool/core/md_translation_steps.py
translation_tool/core/md_translation_steps.py:8:def step1_extract_impl(
translation_tool/core/md_translation_steps.py:148:def step2_translate_impl(*, pending_dir: str, translated_dir: str, session, progress_base: float, progress_span: float, dry_run: bool, write_new_cache: bool, progress_proxy_cls, translate_md_pending_fn, progress_fn) -> Dict[str, Any]:
translation_tool/core/md_translation_steps.py:161:def step3_inject_impl(*, input_dir: str, json_dir: str, final_dir: str, session, progress_base: float, progress_span: float, iter_json_files_fn, load_items_from_json_fn, apply_item_to_md_lines_fn, map_lang_in_rel_path_allow_zh_fn, progress_fn) -> Dict[str, Any]:
translation_tool/core/md_translation_assembly.py:59:def step1_extract(
translation_tool/core/md_translation_assembly.py:87:def step2_translate(
translation_tool/core/md_translation_assembly.py:111:def step3_inject(
translation_tool/core/md_translation_assembly.py:135:def run_md_pipeline(
translation_tool/core/md_translation_progress.py:6:class _ProgressProxy:

$ uv run pytest -q tests/test_md_pipeline_steps.py tests/test_md_progress_proxy.py --basetemp=.pytest-tmp\pr45 -o cache_dir=.pytest-cache\pr45
....                                                                     [100%]
4 passed in 0.31s

$ uv run pytest -q --basetemp=.pytest-tmp\pr45-full -o cache_dir=.pytest-cache\pr45-full
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 1.40s
```

---

## Rejected approaches
1) 試過：保留單檔，只靠區塊註解把 progress / stats / steps 隔開。
   - 為什麼放棄：看起來省檔案，但可測邊界還是不存在，後面一樣沒人敢動。
   - 最終改採：直接切成 progress / steps / stats 三層。

2) 試過：只補 focused tests，不拆 assembly 邊界。
   - 為什麼放棄：step glue 和 UI progress 耦合太重，測試會一直卡在 monkeypatch 泥巴戰。
   - 最終改採：先拆邊界，再補 focused tests。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 Markdown 抽取/回寫策略
- 沒有調整 log 文案與 UI 呈現
- 沒有重寫 plugin 模組

---

## Next step

### PR46
- 接續處理 jar 掃描、抽取、預覽、報表分層。
- 若後續要做 shared convergence，MD 這條現在已經有比較像樣的接點了。
