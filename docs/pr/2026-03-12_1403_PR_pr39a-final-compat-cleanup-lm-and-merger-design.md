# PR39A 設計稿：最終相容層清理（`lm_translator_main` + `lang_merger`）

## Summary
在 PR31~PR38 完成模組切分與 caller migration 後，這顆 PR 目標是清掉仍然殘留的 **helper / value / codec / content 類 re-export 轉接層**，讓：
- `lm_translator_main.py` 收斂為真正的 orchestration 模組
- `lang_merger.py` 收斂為真正的 merge 入口模組

> 本 PR **不處理 `cache_manager` 私有狀態封裝**；那會是 PR39B。

---

## Phase 0 盤點（必做）
- [x] 盤點 `lm_translator_main` 仍被誰 import
- [x] 盤點 `lang_merger` 仍被誰 import
- [x] 確認 helper/value re-export 是否還有 runtime caller
- [x] baseline 測試確認

---

## Phase 1 設計範圍

### A. `lm_translator_main.py`
保留：
- `translate_batch_smart`
- `DRY_RUN`
- `EXPORT_CACHE_ONLY`
- 其內部真正需要的直接依賴

移除 re-export：
- `extract_translatables`
- `find_patchouli_json`
- `find_lang_json`
- `is_lang_file`
- `map_lang_output_path`
- `set_by_path`
- `value_fully_translated`

### B. `lang_merger.py`
保留：
- `merge_zhcn_to_zhtw_from_zip`
- 其內部真正需要的直接依賴

移除 re-export：
- `collapse_lang_lines`
- `dump_lang_text`
- `is_mc_standard_lang_path`
- `parse_lang_text`
- `export_filtered_pending`
- 其他純 helper 轉接

### C. 測試調整
- `tests/test_lm_translator_main_guards.py`
  - 直接改測 canonical 模組：
    - `translatable_extractor.py`
    - `translation_path_writer.py`
    - `lm_response_parser.py`
- `tests/test_lang_merger_guards.py`
  - 直接改測：
    - `lang_codec.py`
    - `lang_merge_content.py`
- `tests/test_lang_merger_zip_baseline.py`
  - 仍保留測 `lang_merger.merge_zhcn_to_zhtw_from_zip`

---

## Out of scope
- 不處理 `cache_manager` 私有狀態相容層
- 不修改 UI
- 不修改 `translate_batch_smart` 與 `merge_zhcn_to_zhtw_from_zip` 的核心流程邏輯

---

## 刪除/移除/替換說明
- **刪除/替換項目**：`lm_translator_main.py` 與 `lang_merger.py` 內為過渡期保留的 helper re-export
- **為什麼改**：目前 caller migration 已完成，繼續保留會讓 canonical 邊界模糊
- **現況 caller**：目前 runtime caller 只依賴真正入口；helper re-export 主要只剩 tests 仍在用
- **替代路徑**：
  - `translatable_extractor.py`
  - `translation_path_writer.py`
  - `lm_response_parser.py`
  - `lang_codec.py`
  - `lang_merge_content.py`
- **風險**：測試若沒同步改到 canonical 模組，會在 import/monkeypatch 階段炸掉
- **驗證依據**：caller 檢索 + import smoke + helper tests + baseline fixture + 全量 pytest

---

## Validation checklist
- [ ] `rg -n "from translation_tool\.core\.lm_translator_main import" translation_tool --glob "*.py"`
- [ ] `rg -n "from translation_tool\.core\.lang_merger import|lang_merger\." tests translation_tool --glob "*.py"`
- [ ] `uv run python -c "from translation_tool.core.lm_translator_main import translate_batch_smart; print('lm-main-import-ok')"`
- [ ] `uv run python -c "from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip; print('lang-merger-import-ok')"`
- [ ] `uv run pytest -q tests/test_lm_translator_main_guards.py tests/test_lang_merger_guards.py tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr39a-focus -o cache_dir=.pytest-cache\pr39a-focus`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr39a -o cache_dir=.pytest-cache\pr39a`

---

## Rejected approaches
1) **方案**：PR39 一次連 `cache_manager` 私有狀態封裝一起做。  
   **放棄原因**：風險型態不同，混在同顆 PR 不利驗證與回退。  
2) **方案**：先刪 re-export，再慢慢修 tests。  
   **放棄原因**：容易造成一段時間 repo 處於紅燈。  
3) **最終採用**：PR39A 先清 `lm_translator_main` / `lang_merger` 的相容層，PR39B 再處理 `cache_manager`。
