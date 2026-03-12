# PR40 設計稿：`translation_tool/core/lm_translator.py` orchestration 分層

## Summary
PR40 的工作不是改翻譯邏輯，而是把 `translate_directory_generator()` 從單塊流程，整理成幾個可以被測試、可以被閱讀、也能繼續被下一顆 PR 接手的 orchestration 區段。這顆 PR 只做邊界切分，不碰 batch policy、cache 契約、輸出結果格式。

---

## Phase 0 盤點
- 目前 `translation_tool/core/lm_translator.py` **621 行**（2026-03-12 baseline），public 入口核心仍是 `translate_directory_generator()`。
- 注意：`translation_tool/core/ftb_translator.py` 也有一個同名 `translate_directory_generator(input_dir: str)`；PR40 只處理 **lm_translator.py 這支**（避免誤改）。
- `lm_translator.py` 同時承擔：路徑初始化、掃描（Patchouli/Lang）、並行抽取、cache hit/miss 分流、dry-run preview、batch glue、writeback、summary；抽象層級混在同一個 generator 裡。
- 現有 repo **tests 不直接呼叫** `lm_translator.translate_directory_generator()`（搜尋 tests 無 match），coverage 目前主要靠：
  - `tests/test_lm_translator_main_guards.py`（7 tests）
  - full pytest（85 tests）
- baseline 驗證輸出（Phase 0）：
  - `uv run pytest -q tests/test_lm_translator_main_guards.py ...` → `7 passed`
  - `uv run pytest -q ...` → `85 passed`
- PR39B 之後 cache runtime state 應只走 façade API（例：`get_cache_dict_ref()`）；PR40 不允許把 private state 依賴拉回來。

---

## 設計範圍
- 保留 `translate_directory_generator()` 作為唯一 public entry；外部 caller 與 service wrapper 不改 import path。
- 新增 `translation_tool/core/lm_translator_scan.py`，承接目錄掃描、translatable items 蒐集、輸入資料正規化。
- 新增 `translation_tool/core/lm_translator_output.py`，承接 cache-hit writeback、output flush、export_lang 寫出與 summary 組裝。
- 新增 `translation_tool/core/lm_translator_runtime.py`，承接 batch translation loop glue、進度更新與結果回收；真正翻譯仍呼叫既有 `lm_translator_shared` / `lm_translator_main` 能力。
- `lm_translator.py` 只留下：config 載入、路徑前置檢查、呼叫 scan/runtime/output 三段、統一 yield 更新 dict。
- 新增 focused tests：`tests/test_lm_translator_cache_split.py`、`tests/test_lm_translator_dry_run.py`、`tests/test_lm_translator_output_writeback.py`，先鎖住目前 observable behavior 再拆。

---

## Validation checklist
- [ ] `rg -n "def translate_directory_generator|yield \{|write_dry_run_preview|write_cache_hit_preview" translation_tool/core/lm_translator.py translation_tool/core/lm_translator_*.py`
- [ ] `uv run pytest -q tests/test_lm_translator_cache_split.py tests/test_lm_translator_dry_run.py tests/test_lm_translator_output_writeback.py --basetemp=.pytest-tmp\pr40 -o cache_dir=.pytest-cache\pr40`
- [ ] `uv run pytest -q tests/test_lm_translator_main_guards.py --basetemp=.pytest-tmp\pr40-main-guards -o cache_dir=.pytest-cache\pr40-main-guards`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr40-full -o cache_dir=.pytest-cache\pr40-full`

---

## Rejected approaches
1) 試過：一次把 `lm_translator.py`、`lm_translator_shared.py`、`lm_translator_main.py` 三層一起重切。
2) 為什麼放棄：scope 太大，任何 import 漏改都會讓 full pytest 爆成一片，根本很難定位是 orchestration 還是 engine 層壞掉。
3) 最終改採：先只切 `lm_translator.py` 的 orchestration，shared 與 engine 留到 PR41 之後各自收斂。

---

## Not included in this PR
- 不改 Gemini API call、retry policy、batch shrink 策略。
- 不改 cache schema、不改 output file layout。
- 不順手修 `lm_translator_main.py` 內既有歷史問題。

---

## Next step
- PR41 接著把 `lm_translator_shared.py` 的 preview / recorder / loop helper 做真正模組邊界整理。
