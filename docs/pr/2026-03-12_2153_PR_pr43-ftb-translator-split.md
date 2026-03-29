# PR43：ftb translator split

## Summary
這顆 PR 把 `translation_tool/core/ftb_translator.py` 從單一大流程檔切成 export / clean / template / orchestration 四層。主目標是拆結構、補 focused tests，不改 `run_ftb_pipeline()` 的對外契約，也不改 FTB 翻譯策略。

---

## Phase 1 完成清單
- [x] 做了：新增 `ftb_translator_export.py`，收納 quests root resolve 與 raw export。
- [x] 做了：新增 `ftb_translator_clean.py`，收納 `deep_merge_3way()`、pending prune、raw → clean 輸出流程。
- [x] 做了：新增 `ftb_translator_template.py`，收納模板語系準備流程。
- [x] 做了：`ftb_translator.py` 改成相容入口，保留 `translate_directory_generator()` 與 `run_ftb_pipeline()` orchestration。
- [x] 做了：補 `tests/test_ftb_translator_export.py`、`tests/test_ftb_translator_clean.py`、`tests/test_ftb_pipeline_smoke.py`。
- [ ] 未做：KubeJS / MD pipeline 命名統一與 shared 收斂（原因：留給後續 PR44+ / PR47 處理）。

---

## What was done

### 1. 拆出 export layer
新增 `translation_tool/core/ftb_translator_export.py`：
- `resolve_ftbquests_quests_root_impl()`
- `export_ftbquests_raw_json_impl()`

這層只處理 quests root 定位與 raw JSON 輸出，不再跟 clean / template / inject 混在一起。

### 2. 拆出 clean layer
新增 `translation_tool/core/ftb_translator_clean.py`：
- `deep_merge_3way()`
- `prune_en_us_by_zh_tw()`
- `prune_flat_en_by_tw()`
- `clean_ftbquests_from_raw_impl()`

raw → pending/en_us → 整理後 zh_tw 的責任現在有獨立邊界，之後要看 bug 是 merge rule 還是 pipeline orchestration，會好判很多。

### 3. 拆出 template layer
新增 `translation_tool/core/ftb_translator_template.py`：
- `prepare_ftbquests_lang_template_only_impl()`

lang 模板準備現在不再夾在主流程大檔裡。

### 4. 保留 ftb_translator.py 當 façade + orchestration
`translation_tool/core/ftb_translator.py` 現在保留：
- `translate_directory_generator()`
- `run_ftb_pipeline()`
- 對 export / clean / template 的 wrapper

這樣外部 caller 仍走原本 import path，不會因為這顆 refactor 突然斷線。

### 5. 補 focused tests，鎖 contract
新增：
- `tests/test_ftb_translator_export.py`
- `tests/test_ftb_translator_clean.py`
- `tests/test_ftb_pipeline_smoke.py`

這是目前第一批 FTB 專屬 focused tests，不再只靠 full pytest 撐整條 pipeline。

---

## Important findings
- FTB 這條之前真的沒 focused tests，完全是典型「全倚賴 full pytest 看天吃飯」的高風險區。
- `run_ftb_pipeline()` 牽涉 raw export / clean / LM translate / inject 四段，這顆 PR 若直接 package 化或順手改 KubeJS / MD 命名，scope 會炸開。
- 目前最穩的做法就是 sibling modules + façade wrapper：風險低，caller 幾乎零感知。

---

## Validation checklist
- [x] `rg -n "def resolve_ftbquests_quests_root|def export_ftbquests_raw_json|def clean_ftbquests_from_raw|def prepare_ftbquests_lang_template_only|def run_ftb_pipeline" translation_tool/core/ftb_translator.py translation_tool/core/ftb_translator_export.py translation_tool/core/ftb_translator_clean.py translation_tool/core/ftb_translator_template.py`
- [x] `uv run pytest -q tests/test_ftb_translator_export.py tests/test_ftb_translator_clean.py tests/test_ftb_pipeline_smoke.py --basetemp=.pytest-tmp\pr43 -o cache_dir=.pytest-cache\pr43`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr43-full -o cache_dir=.pytest-cache\pr43-full`

## Test result
```text
$ rg -n "def resolve_ftbquests_quests_root|def export_ftbquests_raw_json|def clean_ftbquests_from_raw|def prepare_ftbquests_lang_template_only|def run_ftb_pipeline" translation_tool/core/ftb_translator.py translation_tool/core/ftb_translator_export.py translation_tool/core/ftb_translator_clean.py translation_tool/core/ftb_translator_template.py
translation_tool/core/ftb_translator_template.py:7:def prepare_ftbquests_lang_template_only_impl(
translation_tool/core/ftb_translator_export.py:8:def resolve_ftbquests_quests_root_impl(base_dir: str) -> str:
translation_tool/core/ftb_translator_export.py:31:def export_ftbquests_raw_json_impl(
translation_tool/core/ftb_translator_clean.py:81:def clean_ftbquests_from_raw_impl(
translation_tool/core/ftb_translator.py:172:def resolve_ftbquests_quests_root(base_dir: str) -> str:
translation_tool/core/ftb_translator.py:176:def export_ftbquests_raw_json(base_dir: str, *, output_dir: str | None = None) -> dict:
translation_tool/core/ftb_translator.py:187:def clean_ftbquests_from_raw(base_dir: str, *, output_dir: str | None = None) -> dict:
translation_tool/core/ftb_translator.py:197:def prepare_ftbquests_lang_template_only(
translation_tool/core/ftb_translator.py:212:def run_ftb_pipeline(

$ uv run pytest -q tests/test_ftb_translator_export.py tests/test_ftb_translator_clean.py tests/test_ftb_pipeline_smoke.py --basetemp=.pytest-tmp\pr43 -o cache_dir=.pytest-cache\pr43
....                                                                     [100%]
4 passed in 0.27s

$ uv run pytest -q --basetemp=.pytest-tmp\pr43-full -o cache_dir=.pytest-cache\pr43-full
........................................................................ [ 72%]
............................                                             [100%]
100 passed in 1.41s
```

---

## Rejected approaches
1) 試過：直接把 FTB pipeline 改成 package 結構，順便統一 KubeJS / MD 命名。
   - 為什麼放棄：這會把 PR43 直接升級成 repo-level 規格改造；爽是很爽，但風險與 scope 都超標。
   - 最終改採：先用同前綴 sibling modules 做低風險切分，caller 繼續走原 path。

2) 試過：只拆 helper，不補 focused tests。
   - 為什麼放棄：FTB 之前根本沒有專屬 focused tests，這樣等於只是把風險換位置，沒真的降風險。
   - 最終改採：先補 export / clean / pipeline smoke 三顆 tests，再用 full pytest 收尾。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 FTB 實際翻譯資料內容
- 沒有修改 plugin shared 規則
- 沒有處理 UI view
- 沒有順手統一 KubeJS / MD 的 pipeline 命名

---

## Next step

### PR44
- 用同樣節奏處理 `kubejs_translator.py`。
- 等 FTB / KubeJS / MD 三條 pipeline 都切乾淨後，再在 shared / cleanup PR 估是否要統一命名或搬 package。
