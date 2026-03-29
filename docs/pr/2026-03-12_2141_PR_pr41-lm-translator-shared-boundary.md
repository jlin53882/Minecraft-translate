# PR41：lm_translator shared boundary

## Summary
這顆 PR 把 `translation_tool/core/lm_translator_shared.py` 內混在一起的 cache rule / preview writer / recording / translate loop 拆到各自子模組，讓 shared 層回到薄 façade。這樣 PR40 抽出的 orchestration seam 往下就有乾淨邊界可接，但既有 caller 先不用一起搬家。

---

## Phase 1 完成清單
- [x] 做了：新增 `lm_translator_shared_cache.py`，收納 `CacheRule`、預設 cache rule、cache hit 驗證與 `fast_split_items_by_cache()`。
- [x] 做了：新增 `lm_translator_shared_preview.py`，收納 `TouchSet`、`write_dry_run_preview()`、`write_cache_hit_preview()`。
- [x] 做了：新增 `lm_translator_shared_recording.py`，收納 `TranslationRecorder` 的 JSON/CSV 匯出責任。
- [x] 做了：新增 `lm_translator_shared_loop.py`，收納 `TranslateLoopResult`、`_get_default_batch_size()`、`translate_items_with_cache_loop()`。
- [x] 做了：把 `lm_translator_shared.py` 改成薄 façade / import aggregator，保留既有 import surface。
- [x] 做了：補 `tests/test_lm_translator_shared_preview.py`、`tests/test_lm_translator_shared_recording.py`、`tests/test_lm_translator_cache_split.py`。
- [ ] 未做：caller migration 到新子模組（原因：設計稿已明確留待後續 cleanup PR，避免本顆 scope 混成 migration）。

---

## What was done

### 1. 把 shared cache 規則獨立出來
新增 `translation_tool/core/lm_translator_shared_cache.py`，把以下責任集中：
- `CacheRule`
- `get_default_cache_rules()`
- `STRICT_SRC_TYPES`
- `_is_valid_hit()`
- `fast_split_items_by_cache()`

這讓 cache split 的規則不再和 preview / recording / loop 黏在同一檔。

### 2. 把 preview writer 與 touch bookkeeping 拆出來
新增 `translation_tool/core/lm_translator_shared_preview.py`：
- `TouchSet`
- `write_dry_run_preview()`
- `write_cache_hit_preview()`

preview 類的 I/O 之後可以單獨測，不再依附 translate loop。

### 3. 把 recording 拆成獨立模組
新增 `translation_tool/core/lm_translator_shared_recording.py`：
- `TranslationRecorder.record()`
- `TranslationRecorder.export_json()`
- `TranslationRecorder.export_csv()`

這顆責任現在變得夠單純，之後若要擴充欄位或輸出格式，不會碰到 loop 邏輯。

### 4. 把 translate loop 獨立出來
新增 `translation_tool/core/lm_translator_shared_loop.py`：
- `TranslateLoopResult`
- `_get_default_batch_size()`
- `translate_items_with_cache_loop()`

loop 本身仍保留原先的 batch / progress / save cache 行為，只是從 shared 大雜燴中抽離。

### 5. 保留相容 façade，避免 caller migration 混進來
`translation_tool/core/lm_translator_shared.py` 現在只做 re-export：
- 既有 caller 仍可從原路徑 import
- 本 PR 專注 boundary refactor，不把 migration 風險一起吞

---

## Important findings
- 實際 caller 還很多：`md_lmtranslator.py`、`ftbquests_lmtranslator.py`、`kubejs_tooltip_lmtranslator.py`、`ftb_translator.py`、`kubejs_translator.py`、`md_translation_assembly.py` 都還依賴 `lm_translator_shared.py`。
- 所以 PR41 若直接刪 shared façade，會立刻把 boundary refactor 變成 caller migration PR，風險不值得。
- 原設計稿 checklist 提到 `tests/test_lm_translator_cache_split.py`，repo 當下其實還不存在；這次順手補上，避免 checklist 自己踩空。

---

## Validation checklist
- [x] `rg -n "class CacheRule|class TranslationRecorder|def translate_items_with_cache_loop|def write_dry_run_preview|def write_cache_hit_preview" translation_tool/core/lm_translator_shared.py translation_tool/core/lm_translator_shared_cache.py translation_tool/core/lm_translator_shared_preview.py translation_tool/core/lm_translator_shared_recording.py translation_tool/core/lm_translator_shared_loop.py`
- [x] `uv run pytest -q tests/test_lm_translator_shared_preview.py tests/test_lm_translator_shared_recording.py --basetemp=.pytest-tmp\pr41 -o cache_dir=.pytest-cache\pr41`
- [x] `uv run pytest -q tests/test_lm_translator_cache_split.py tests/test_lm_translator_dry_run.py tests/test_lm_translator_output_writeback.py --basetemp=.pytest-tmp\pr41-lm -o cache_dir=.pytest-cache\pr41-lm`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr41-full -o cache_dir=.pytest-cache\pr41-full`

## Test result
```text
$ rg -n "class CacheRule|class TranslationRecorder|def translate_items_with_cache_loop|def write_dry_run_preview|def write_cache_hit_preview" translation_tool/core/lm_translator_shared.py translation_tool/core/lm_translator_shared_cache.py translation_tool/core/lm_translator_shared_preview.py translation_tool/core/lm_translator_shared_recording.py translation_tool/core/lm_translator_shared_loop.py
translation_tool/core/lm_translator_shared_recording.py:11:class TranslationRecorder:
translation_tool/core/lm_translator_shared_preview.py:25:def write_dry_run_preview(
translation_tool/core/lm_translator_shared_preview.py:46:def write_cache_hit_preview(
translation_tool/core/lm_translator_shared_cache.py:11:class CacheRule:
translation_tool/core/lm_translator_shared_loop.py:47:def translate_items_with_cache_loop(

$ uv run pytest -q tests/test_lm_translator_shared_preview.py tests/test_lm_translator_shared_recording.py --basetemp=.pytest-tmp\pr41 -o cache_dir=.pytest-cache\pr41
....                                                                     [100%]
4 passed in 0.22s

$ uv run pytest -q tests/test_lm_translator_cache_split.py tests/test_lm_translator_dry_run.py tests/test_lm_translator_output_writeback.py --basetemp=.pytest-tmp\pr41-lm -o cache_dir=.pytest-cache\pr41-lm
....                                                                     [100%]
4 passed in 0.25s

$ uv run pytest -q --basetemp=.pytest-tmp\pr41-full -o cache_dir=.pytest-cache\pr41-full
........................................................................ [ 77%]
.....................                                                    [100%]
93 passed in 1.28s
```

---

## Rejected approaches
1) 試過：直接刪掉 `lm_translator_shared.py`，所有 caller 一次改新路徑。
   - 為什麼放棄：caller 散在多個 plugin / core 模組，這樣會把 boundary refactor 變成 migration PR；任何漏改都會擴散成多點爆炸。
   - 最終改採：先保留 façade 做 re-export，真正 cleanup 留到後續專門 PR。

2) 試過：只改檔案結構，不補 focused tests。
   - 為什麼放棄：preview / recording / cache split 都是 shared seam，沒 focused tests 很容易變成「full pytest 綠，但 shared contract 偷漂移」。
   - 最終改採：補 preview / recording / cache split 三顆 focused tests，再用 full pytest 收尾。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 `lm_translator_main.py` 的 engine 邏輯
- 沒有改 public result dict 格式
- 沒有改 cache rule 的實際判定規則
- 沒有搬 caller 到新子模組 import path
- 沒有刪除 `lm_translator_shared.py`

---

## Next step

### PR42
- 進入 `lang_merge_content.py`，把 lang merge 的內容處理層切開。
- 若後續要做 shared cleanup，應集中在專門的 compatibility/dead-code PR，再統一移除 façade。
