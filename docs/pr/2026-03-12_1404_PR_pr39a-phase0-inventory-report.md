# PR39A Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- 最新已推：`bbac7d1`（PR38）
- 目前已進入 PR39A，停在 Phase 0（尚未改 PR39A 目標程式碼）

---

## Phase 0 結論
PR39A 可以做，而且風險比 PR39B 低得多。

### A. `lm_translator_main` 現況
目前 translation_tool 內從 `lm_translator_main` import 的只剩：
- `translate_batch_smart`
- `DRY_RUN`
- `EXPORT_CACHE_ONLY`

也就是說：
- `extract_translatables`
- `find_patchouli_json`
- `find_lang_json`
- `is_lang_file`
- `map_lang_output_path`
- `set_by_path`
- `value_fully_translated`

這些 helper/value 的 runtime caller migration 已完成。  
**剩下還在用它們的，主要是 tests。**

### B. `lang_merger` 現況
runtime caller 基本只剩：
- `app/services_impl/pipelines/merge_service.py` -> `merge_zhcn_to_zhtw_from_zip`

但 tests 仍直接透過 `lang_merger` 取：
- `collapse_lang_lines`
- `parse_lang_text`
- `dump_lang_text`
- `is_mc_standard_lang_path`
- `export_filtered_pending`
- `merge_zhcn_to_zhtw_from_zip`

=> 代表 PR39A 的主要工作其實是：
1. 把 tests 改到 canonical 模組
2. 再移除 `lm_translator_main` / `lang_merger` 的 helper re-export

### C. baseline
- `uv run pytest -q --basetemp=.pytest-tmp\pr39a-phase0 -o cache_dir=.pytest-cache\pr39a-phase0`
- 結果：`85 passed in 1.11s`

---

## 風險判斷
### 可安全移除的相容層
- `lm_translator_main` 內 helper/value re-export
- `lang_merger` 內 helper re-export

### 本 PR 不碰的高風險區
- `cache_manager` 私有狀態封裝
  - 目前仍有 runtime caller / tests 直接碰 `_translation_cache`, `_initialized` 等
  - 這塊要留給 PR39B

---

## Phase 0 建議
- ✅ 可以進 Phase 1
- 建議順序：
  1. 先改 `tests/test_lm_translator_main_guards.py`
  2. 再改 `tests/test_lang_merger_guards.py`
  3. 確認 baseline fixture 不受影響
  4. 最後移除 `lm_translator_main` / `lang_merger` 的 helper re-export

---

## 目前停點
- ✅ PR39A Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待你確認放行）
