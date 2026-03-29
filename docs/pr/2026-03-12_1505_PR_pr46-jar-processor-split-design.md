# PR46 設計稿：`translation_tool/core/jar_processor.py` discovery / extract / preview 分層

## Summary
PR46 要把 jar 處理從『一支檔案包所有 extraction/preview/report』拆成清楚的幾段。這顆 PR 很重要，因為 extractor view 的 preview 流程直接依賴這支核心模組；如果不先切乾淨，後面 PR54 的 UI 整理會很痛。

---

## Phase 0 盤點
- 目前 `translation_tool/core/jar_processor.py` 約 484 行。
- 主要職責混在一起：jar discovery、jar extract、generator update、preview summary/report。
- `app/views/extractor_view.py` 直接依賴 preview/extraction 能力，代表這支檔案其實同時卡在 core 與 UI seam。
- repo 目前缺少 jar processor 專屬 focused tests。

---

## 設計範圍
- 新增 `translation_tool/core/jar_processor_discovery.py`，集中 `find_jar_files()` 與路徑盤點。
- 新增 `translation_tool/core/jar_processor_extract.py`，集中 `_extract_from_jar()`、`_run_extraction_process()` 與 extract generator 基礎能力。
- 新增 `translation_tool/core/jar_processor_preview.py`，集中 `ExtractionSummary`、`preview_extraction_generator()`、`generate_preview_report()`。
- 保留 `jar_processor.py` 作為相容入口，對外仍匯出 `extract_lang_files_generator()`、`extract_book_files_generator()`、`preview_extraction_generator()`。
- 新增 `tests/test_jar_processor_find.py`、`tests/test_jar_processor_extract.py`、`tests/test_jar_preview_report.py`。

---

## Validation checklist
- [ ] `rg -n "def find_jar_files|def _extract_from_jar|def _run_extraction_process|def extract_lang_files_generator|def extract_book_files_generator|def preview_extraction_generator|class ExtractionSummary|def generate_preview_report" translation_tool/core/jar_processor*.py`
- [ ] `uv run pytest -q tests/test_jar_processor_find.py tests/test_jar_processor_extract.py tests/test_jar_preview_report.py --basetemp=.pytest-tmp\pr46 -o cache_dir=.pytest-cache\pr46`
- [ ] `uv run pytest -q tests/test_path_resolution.py --basetemp=.pytest-tmp\pr46-path -o cache_dir=.pytest-cache\pr46-path`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr46-full -o cache_dir=.pytest-cache\pr46-full`

---

## Rejected approaches
1) 試過：先去改 extractor view，讓 UI 直接分擔 preview/report 組裝。
2) 為什麼放棄：那等於把 core 未整理乾淨的複雜度往 UI 推，後面會更難測也更難替換。
3) 最終改採：先把 jar core 切乾淨，再讓 UI 只消費穩定的 generator / report API。

---

## Not included in this PR
- 不改 preview dialog UI。
- 不改抽取結果資料夾結構。
- 不改 log 格式。

---

## Next step
- PR47 針對 FTB / KubeJS / MD 的共用規則做 shared 收斂。
