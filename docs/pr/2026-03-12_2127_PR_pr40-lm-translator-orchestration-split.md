# PR40：lm_translator orchestration split（第一段：scan/extract 拆分）

## Summary
本 PR 的目標是把 `translation_tool/core/lm_translator.py::translate_directory_generator()` 內「掃描 + 解析 + 抽取可翻譯文字」這段 orchestration 從巨大入口中拆出來，降低 `lm_translator.py` 的責任混裝。

原則：
- **不改 runtime 行為/契約**（log 文案除外也盡量不動）
- **先補測試護欄，再動刀**
- 本 PR 只處理 scan/extract 段落；cache split / shared boundary 仍在後續 PR41+ 處理

---

## Phase 1 完成清單
- [x] 做了：新增 `translation_tool/core/lm_translator_scan.py`，承接目錄掃描與並行抽取。
- [x] 做了：`lm_translator.py` 改為呼叫 `scan_translatable_files()` / `extract_items_parallel()`，保留 `translate_directory_generator()` 作為唯一 public entry。
- [x] 做了：補上 `tests/test_lm_translator_dry_run.py` 與 `tests/test_lm_translator_output_writeback.py` 作為 characterization tests。
- [x] 做了：rebase 到 `origin/main` 後收斂 `lm_translator.py` 衝突，保留 scan split 與上游 docstring/排版修正。
- [ ] 未做：cache split / shared boundary / output writer split（原因：明確留到 PR41+，避免 scope 膨脹）。

---

## Phase 0 盤點

### 基線
- branch：`main` → 開新分支 `pr40-lm-translator-orchestration-split`
- 檔案：`translation_tool/core/lm_translator.py` baseline 621 行（重構後 561 行）
- 注意：`translation_tool/core/ftb_translator.py` 也有同名 `translate_directory_generator`，本 PR 只處理 **lm_translator.py**。

### 測試現況
- tests 不直接呼叫 `lm_translator.translate_directory_generator()`（搜尋 tests 無 match）
- 既有 guard：`tests/test_lm_translator_main_guards.py`

### baseline test（Phase 0）
- `uv run pytest -q tests/test_lm_translator_main_guards.py ...` → `7 passed`
- `uv run pytest -q ...` → `85 passed`

---

## What was done

### 1. 拆出 scan/extract orchestration
新增 `translation_tool/core/lm_translator_scan.py`：
- `scan_translatable_files(root)`：掃描 patchouli/lang JSON 檔
- `extract_items_parallel(files, export_lang, work_thread, logger)`：
  - 並行讀取 JSON
  - 抽取 translatables
  - 為 item 打上 `cache_type`
  - 回傳 `file_cache` + `all_items`

### 2. 讓 lm_translator.py 改走新 seam
`lm_translator.py` 的入口 `translate_directory_generator()` 保持不變，但把：
- 掃描段落改為 `scan_translatable_files(root)`
- 抽取段落改為 `extract_items_parallel(...)`

這樣後續 PR 可以沿著 orchestration seam 繼續拆，而不用再次打開巨大函式重切。

### 3. 收斂 characterization tests 到新 seam
原本兩顆新測試是 monkeypatch `find_patchouli_json` / `find_lang_json` / `extract_translatables` 等舊內部依賴；scan/extract 拆出後，這些 symbol 不再掛在 `lm_translator` module 上，造成測試失敗。

本次改為直接 patch：
- `scan_translatable_files()`
- `extract_items_parallel()`

讓測試鎖定新的 orchestration seam，而不是依賴已經被抽走的內部 import 細節。

---

## Important findings
- 這次 rebase onto `origin/main` 的實際衝突點集中在 `translation_tool/core/lm_translator.py` 的 import/header 區與上游 docstring 排版整理，不是 runtime 邏輯衝突。
- 真正會讓 focused tests 爆掉的不是主邏輯，而是 characterization tests 還在 patch 舊的內部 seam。
- 修正測試之後，focused tests 與 full pytest 都維持綠燈，代表這一段 refactor 目前仍守住 observable behavior。

---

## Validation checklist
- [x] `uv run pytest -q tests/test_lm_translator_main_guards.py tests/test_lm_translator_dry_run.py tests/test_lm_translator_output_writeback.py --basetemp=.pytest-tmp\pr40 -o cache_dir=.pytest-cache\pr40`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr40-full -o cache_dir=.pytest-cache\pr40-full`

## Test result
```text
$ uv run pytest -q tests/test_lm_translator_main_guards.py tests/test_lm_translator_dry_run.py tests/test_lm_translator_output_writeback.py --basetemp=.pytest-tmp\pr40 -o cache_dir=.pytest-cache\pr40
.........                                                                [100%]
9 passed in 0.26s

$ uv run pytest -q --basetemp=.pytest-tmp\pr40-full -o cache_dir=.pytest-cache\pr40-full
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 1.62s
```

---

## Rejected approaches
1) 試過：PR40 一次把 cache split / shared boundary / output writer 全部拆完。
   - 為什麼放棄：scope 太大，容易變成長鏈 refactor；一旦出現行為漂移很難定位。
   - 最終改採：PR40 先只拆 scan/extract（可獨立驗證），其餘留到 PR41+。

2) 試過：不補測試，直接抽程式碼。
   - 為什麼放棄：入口是 generator orchestration，行為漂移很難靠肉眼 review。
   - 最終改採：先補 dry-run 與 cache-hit writeback 的 characterization tests。

3) 試過：讓新測試繼續 patch `find_patchouli_json` / `find_lang_json` / `extract_translatables` 這些舊內部 import。
   - 為什麼放棄：scan/extract seam 抽出後，這些 symbol 不再是 `lm_translator` 的 module-level 依賴，測試直接 `AttributeError`。
   - 最終改採：改 patch `scan_translatable_files()` 與 `extract_items_parallel()`，測試鎖新 seam，不綁舊內部實作。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有調整 cache hit/miss 判定規則
- 沒有改 batch policy / API key rotate
- 沒有改輸出結果格式（json/.lang）
- 沒有引入新的 shared 抽象層
- 沒有處理 cache split / shared boundary / output writer split

---

## Next step

### PR41
- 延續 `lm_translator.py` orchestration 切分，處理 shared boundary / cache / output 相關責任收斂。
- 若要再補 characterization tests，優先鎖新的 seam，不要重新綁回舊 import 細節。
