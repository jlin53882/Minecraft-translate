# PR44：kubejs translator split

## Summary
這顆 PR 把 `translation_tool/core/kubejs_translator.py` 內混在一起的 JSON I/O、clean/merge、root resolve 與 step orchestration 拆開，讓 `run_kubejs_pipeline()` 繼續作為穩定入口，但內部責任邊界變乾淨、可測。

---

## Phase 1 完成清單
- [x] 做了：新增 `kubejs_translator_io.py`，收納 JSON read/write helper。
- [x] 做了：新增 `kubejs_translator_clean.py`，收納 `_is_filled_text`、`deep_merge_3way_flat`、`prune_en_by_tw_flat`、`clean_kubejs_from_raw` 的核心實作。
- [x] 做了：新增 `kubejs_translator_paths.py`，收納 `resolve_kubejs_root()` 的 path/root 判定。
- [x] 做了：`kubejs_translator.py` 改成 façade + orchestration，保留 step1/2/3 與 `run_kubejs_pipeline()` 對外 surface。
- [x] 做了：新增 focused tests：`test_kubejs_cleaning.py`、`test_kubejs_pipeline_steps.py`、`test_kubejs_path_resolution.py`。
- [ ] 未做：KubeJS 與其他 pipeline 的 shared helper 統一命名/收斂（原因：留給 PR47）。

---

## What was done

### 1. 拆出 path layer
新增 `translation_tool/core/kubejs_translator_paths.py`：
- `resolve_kubejs_root_impl()`

專門處理 KubeJS 根目錄定位，不再和 clean / pipeline step 混在一起。

### 2. 拆出 JSON I/O layer
新增 `translation_tool/core/kubejs_translator_io.py`：
- `read_json_dict_orjson_impl()`
- `write_json_orjson_impl()`

BOM / trailing comma / pretty-print 這些檔案格式保證，現在有獨立責任邊界。

### 3. 拆出 clean layer
新增 `translation_tool/core/kubejs_translator_clean.py`：
- `is_filled_text_impl()`
- `deep_merge_3way_flat_impl()`
- `prune_en_by_tw_flat_impl()`
- `clean_kubejs_from_raw_impl()`

raw → pending/final 的清理與 merge 邏輯，現在不再綁死在主 pipeline 檔案裡。

### 4. 保留主檔作為 façade + orchestration
`translation_tool/core/kubejs_translator.py` 目前保留：
- `_is_filled_text()` façade
- `deep_merge_3way_flat()` façade
- `prune_en_by_tw_flat()` façade
- `_read_json_dict_orjson()` / `_write_json_orjson()` façade
- `clean_kubejs_from_raw()` façade
- `resolve_kubejs_root()` façade
- `step1_extract_and_clean()` / `step2_translate_lm()` / `step3_inject()` / `run_kubejs_pipeline()`

這樣外部 caller 不用跟著改 import path，符合「只切結構、不改 contract」。

---

## Important findings
- KubeJS 跟 FTB 一樣，之前也缺 focused tests；這顆 PR 補上之後，至少 path / clean / step 邏輯不是只能靠 full pytest 硬撐。
- `run_kubejs_pipeline()` 內部雖然大，但最大的風險不在 orchestration，而在 path / io / clean 三層混在一起時很難定位問題；先把底層拆乾淨，後面 shared convergence 才有機會安全做。
- 這次 façade 策略維持得住，所以沒有引入 caller migration 成本。

---

## Validation checklist
- [x] `rg -n "def _read_json_dict_orjson|def _write_json_orjson|def clean_kubejs_from_raw|def resolve_kubejs_root|def step1_extract_and_clean|def step2_translate_lm|def step3_inject|def run_kubejs_pipeline" translation_tool/core/kubejs_translator*.py`
- [x] `uv run pytest -q tests/test_kubejs_cleaning.py tests/test_kubejs_pipeline_steps.py tests/test_kubejs_path_resolution.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr44 -o cache_dir=.pytest-cache\pr44`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr44-full -o cache_dir=.pytest-cache\pr44-full`

## Test result
```text
$ uv run pytest -q tests/test_kubejs_cleaning.py tests/test_kubejs_pipeline_steps.py tests/test_kubejs_path_resolution.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr44 -o cache_dir=.pytest-cache\pr44
........                                                                 [100%]
8 passed in 0.29s

$ uv run pytest -q --basetemp=.pytest-tmp\pr44-full -o cache_dir=.pytest-cache\pr44-full
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 1.44s
```

---

## Rejected approaches
1) 試過：把 KubeJS 三個 step 直接收成三支新 public module，順手改所有 caller。
   - 為什麼放棄：這會讓外部 API surface 冒出一堆相容風險，直接違反「只切結構、不改 contract」的主原則。
   - 最終改採：保留原 public pipeline 函式，只把內部 helper 與 path/io/clean 層拆出。

2) 試過：只補 full pytest，不補 KubeJS focused tests。
   - 為什麼放棄：那樣根本看不出是 path 壞、clean 壞，還是 pipeline glue 壞。
   - 最終改採：補 path resolution / cleaning / pipeline step 三顆 focused tests，再用 full pytest 收尾。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 `run_kubejs_pipeline()` 呼叫方式
- 沒有改 KubeJS 產出資料夾結構
- 沒有改 LM translator shared 邏輯
- 沒有處理跨 pipeline shared convergence

---

## Next step

### PR45
- 進入 Markdown pipeline，處理 progress proxy 與 step glue。
- 等 KubeJS / FTB / MD 都拆乾淨後，再到 PR47 做 plugin shared convergence。
