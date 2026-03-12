# PR46：jar processor split

## Summary
這顆 PR 把 `translation_tool/core/jar_processor.py` 從 discovery / extract / preview / report 全混在一起的狀態拆成清楚幾層。主檔保留相容入口，讓 extractor view 繼續吃穩定的 generator / report API，但 core 邊界已經乾淨很多。

---

## Phase 1 完成清單
- [x] 做了：新增 `jar_processor_discovery.py`，收納 `find_jar_files()`。
- [x] 做了：新增 `jar_processor_extract.py`，收納 hash、jar basename normalize、`_extract_from_jar`、`_run_extraction_process` 的核心實作。
- [x] 做了：新增 `jar_processor_preview.py`，收納 `ExtractionSummary`、`preview_extraction_generator()` 核心實作、`generate_preview_report()`。
- [x] 做了：`jar_processor.py` 改成 façade，相容匯出 `extract_lang_files_generator()`、`extract_book_files_generator()`、`preview_extraction_generator()`。
- [x] 做了：新增 focused tests：`test_jar_processor_find.py`、`test_jar_processor_extract.py`、`test_jar_preview_report.py`。
- [ ] 未做：preview dialog UI / 抽取結果資料夾結構調整（原因：本 PR 只切 core）。

---

## What was done

### 1. 拆出 discovery layer
新增 `translation_tool/core/jar_processor_discovery.py`：
- `find_jar_files()`

jar 掃描邏輯現在不再和 extraction / preview / report 混在一支檔案裡。

### 2. 拆出 extract layer
新增 `translation_tool/core/jar_processor_extract.py`：
- `get_file_hash()`
- `_normalize_jar_base_name()`
- `extract_from_jar_impl()`
- `run_extraction_process_impl()`

JAR 內容提取、內容比對、平行提取流程，現在有獨立核心模組。

### 3. 拆出 preview/report layer
新增 `translation_tool/core/jar_processor_preview.py`：
- `ExtractionSummary`
- `preview_extraction_generator_impl()`
- `generate_preview_report()`

preview 與報告生成功能現在不再綁死在 extraction 主流程裡。

### 4. 保留 jar_processor.py 當 façade
`translation_tool/core/jar_processor.py` 目前保留：
- `_extract_from_jar()` façade
- `_run_extraction_process()` façade
- `extract_lang_files_generator()`
- `extract_book_files_generator()`
- `preview_extraction_generator()`
- `ExtractionSummary` / `generate_preview_report` 相容匯出

這樣 `app/views/extractor_view.py` 不需要跟著大搬家。

---

## Important findings
- jar processor 這顆其實是 core 與 UI 之間的痛點，因為 extractor view 直接依賴 preview/extraction 能力；先把 core 拆乾淨，PR54 才不會變成 UI 幫 core 擦屁股。
- 這次 focused tests 很值：find / extract / preview report 三塊都能獨立驗，不用全靠 full pytest 猜哪裡炸。
- façade strategy 依然守住，沒有引入 caller migration 風險。

---

## Validation checklist
- [x] `rg -n "def find_jar_files|def _extract_from_jar|def _run_extraction_process|def extract_lang_files_generator|def extract_book_files_generator|def preview_extraction_generator|class ExtractionSummary|def generate_preview_report" translation_tool/core/jar_processor.py translation_tool/core/jar_processor_discovery.py translation_tool/core/jar_processor_extract.py translation_tool/core/jar_processor_preview.py`
- [x] `uv run pytest -q tests/test_jar_processor_find.py tests/test_jar_processor_extract.py tests/test_jar_preview_report.py --basetemp=.pytest-tmp\pr46 -o cache_dir=.pytest-cache\pr46`
- [x] `uv run pytest -q tests/test_path_resolution.py --basetemp=.pytest-tmp\pr46-path -o cache_dir=.pytest-cache\pr46-path`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr46-full -o cache_dir=.pytest-cache\pr46-full`

## Test result
```text
$ rg -n "def find_jar_files|def _extract_from_jar|def _run_extraction_process|def extract_lang_files_generator|def extract_book_files_generator|def preview_extraction_generator|class ExtractionSummary|def generate_preview_report" translation_tool/core/jar_processor.py translation_tool/core/jar_processor_discovery.py translation_tool/core/jar_processor_extract.py translation_tool/core/jar_processor_preview.py
translation_tool/core/jar_processor_discovery.py:10:def find_jar_files(folder_path: str) -> List[str]:
translation_tool/core/jar_processor.py:39:def _extract_from_jar(jar_path: str, output_root: str, target_regex: re.Pattern) -> Dict[str, Any]:
translation_tool/core/jar_processor.py:43:def _run_extraction_process(
translation_tool/core/jar_processor.py:56:def extract_lang_files_generator(mods_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
translation_tool/core/jar_processor.py:68:def extract_book_files_generator(mods_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
translation_tool/core/jar_processor.py:77:def preview_extraction_generator(mods_dir: str, mode: str) -> Generator[Dict[str, Any], None, None]:
translation_tool/core/jar_processor_preview.py:14:class ExtractionSummary:
translation_tool/core/jar_processor_preview.py:42:def preview_extraction_generator_impl(
translation_tool/core/jar_processor_preview.py:102:def generate_preview_report(result: Dict[str, Any], mode: str, output_path: str) -> str:

$ uv run pytest -q tests/test_jar_processor_find.py tests/test_jar_processor_extract.py tests/test_jar_preview_report.py --basetemp=.pytest-tmp\pr46 -o cache_dir=.pytest-cache\pr46
.....                                                                    [100%]
5 passed in 0.11s

$ uv run pytest -q tests/test_path_resolution.py --basetemp=.pytest-tmp\pr46-path -o cache_dir=.pytest-cache\pr46-path
...                                                                      [100%]
3 passed in 0.14s

$ uv run pytest -q --basetemp=.pytest-tmp\pr46-full -o cache_dir=.pytest-cache\pr46-full
........................................................................ [ 63%]
..........................................                               [100%]
114 passed in 1.50s
```

---

## Rejected approaches
1) 試過：先去改 extractor view，讓 UI 直接分擔 preview/report 組裝。
   - 為什麼放棄：那只是把 core 的複雜度往 UI 推，後面只會更難測。
   - 最終改採：先把 jar core 切乾淨，再讓 UI 只消費穩定 API。

2) 試過：只拆 extraction，不碰 preview/report。
   - 為什麼放棄：preview/report 本來就是這顆的責任混裝來源之一，不一起切只是留半套。
   - 最終改採：discovery / extract / preview 一次切乾淨，但維持 façade 不動 caller。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 preview dialog UI
- 沒有改抽取結果資料夾結構
- 沒有改 log 格式

---

## Next step

### PR47
- 針對 FTB / KubeJS / MD 的共用規則做 shared 收斂。
- extractor view 那邊之後就可以靠比較乾淨的 jar API 收斂，不用直接碰一團 core 雜邏輯。
