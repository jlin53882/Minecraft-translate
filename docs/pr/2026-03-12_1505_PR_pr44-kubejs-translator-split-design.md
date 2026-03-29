# PR44 設計稿：`translation_tool/core/kubejs_translator.py` 流程拆分

## Summary
PR44 是整個 non-UI 主線裡風險最高的幾顆之一。這顆 PR 只做結構拆分：把 JSON I/O、clean/merge、root resolve、step orchestration 拆開，讓 `run_kubejs_pipeline()` 留作穩定入口。

---

## Phase 0 盤點
- 目前 `translation_tool/core/kubejs_translator.py` 約 730 行，是目前最大的 non-UI 核心流程檔之一。
- 檔案同時包含 JSON 讀寫、deep merge/prune、root resolve、三步驟 pipeline orchestration。
- repo 目前缺少 KubeJS 專屬 focused tests。
- KubeJS 與 plugin shared 的 path rules、JSON I/O 重複風險高，若先不切乾淨，PR47 很難安全收斂共用 helper。

---

## 設計範圍
- 新增 `translation_tool/core/kubejs_translator_io.py`，集中 JSON read/write 與檔案格式保證。
- 新增 `translation_tool/core/kubejs_translator_clean.py`，集中 clean / merge / prune helpers。
- 新增 `translation_tool/core/kubejs_translator_paths.py`，集中 `resolve_kubejs_root()` 與相關 path/root 判定。
- `translation_tool/core/kubejs_translator.py` 只保留 `step1_extract_and_clean()`、`step2_translate_lm()`、`step3_inject()` 與 `run_kubejs_pipeline()` 的 orchestration glue；內部 helper 移出。
- 新增 `tests/test_kubejs_cleaning.py`、`tests/test_kubejs_pipeline_steps.py`、`tests/test_kubejs_path_resolution.py`。

---

## Validation checklist
- [ ] `rg -n "def _read_json_dict_orjson|def _write_json_orjson|def clean_kubejs_from_raw|def resolve_kubejs_root|def step1_extract_and_clean|def step2_translate_lm|def step3_inject|def run_kubejs_pipeline" translation_tool/core/kubejs_translator*.py`
- [ ] `uv run pytest -q tests/test_kubejs_cleaning.py tests/test_kubejs_pipeline_steps.py tests/test_kubejs_path_resolution.py tests/test_path_resolution.py --basetemp=.pytest-tmp\pr44 -o cache_dir=.pytest-cache\pr44`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr44-full -o cache_dir=.pytest-cache\pr44-full`

---

## Rejected approaches
1) 試過：把 KubeJS 三個 step 直接收成三支新 public module，順手改所有 caller。
2) 為什麼放棄：這會讓外部 API surface 漏掉一堆相容風險，而且會和『只切結構、不改 contract』的主原則衝突。
3) 最終改採：保留原 public pipeline 函式，只把內部 helper 與 path/io/clean 層拆出。

---

## Not included in this PR
- 不改 `run_kubejs_pipeline()` 呼叫方式。
- 不改 KubeJS 產出資料夾結構。
- 不改 LM translator shared 邏輯。

---

## Next step
- PR45 進入 Markdown pipeline，處理 progress proxy 與 step glue。
