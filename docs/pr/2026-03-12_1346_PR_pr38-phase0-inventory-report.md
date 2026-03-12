# PR38 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- 最新已推：`235b289`（PR37）
- 目前已進入 PR38，停在 Phase 0（尚未改 PR38 目標程式碼）

---

## Phase 0 結論
PR38 的方向很乾淨：
- 不碰 UI
- 不改 `translate_batch_smart` 本體
- 只把 caller 從 `lm_translator_main` 的 helper re-export 改到 canonical 新模組

目前盤點結果：

### A. 需要遷移的 caller
1. `translation_tool/core/lm_translator.py`
   - 目前從 `lm_translator_main` 拿：
     - `DRY_RUN`
     - `EXPORT_CACHE_ONLY`
     - `extract_translatables`
     - `find_patchouli_json`
     - `find_lang_json`
     - `is_lang_file`
     - `map_lang_output_path`
     - `set_by_path`
     - `translate_batch_smart`
     - `value_fully_translated`

2. `translation_tool/core/lm_translator_shared.py`
   - 目前從 `lm_translator_main` 拿：
     - `value_fully_translated`

3. plugins
   - `ftbquests_lmtranslator.py`
   - `kubejs_tooltip_lmtranslator.py`
   - `md_lmtranslator.py`
   - 目前都從 `lm_translator_main` 拿：
     - `translate_batch_smart`
     - `value_fully_translated`

### B. canonical 新位置已存在
- `value_fully_translated` -> `lm_config_rules.py`
- `extract_translatables / find_patchouli_json / find_lang_json / is_lang_file` -> `translatable_extractor.py`
- `map_lang_output_path / set_by_path` -> `translation_path_writer.py`

### C. 目前不該動的符號
- `translate_batch_smart`
- `DRY_RUN`
- `EXPORT_CACHE_ONLY`

這三個還留在 `lm_translator_main` 比較安全。

---

## baseline 測試
命令：
- `uv run pytest -q --basetemp=.pytest-tmp\pr38-phase0 -o cache_dir=.pytest-cache\pr38-phase0`

結果：
- `85 passed in 1.12s`

---

## Phase 0 建議
- ✅ 可以進 Phase 1
- 建議只做 import migration，不做功能行為修改
- 優先順序：
  1. `lm_translator_shared.py`
  2. plugins 三檔
  3. `lm_translator.py`

> 原因：先處理 value/helper 依賴較單純的 caller，再動 `lm_translator.py` 這顆較大的 core caller

---

## 目前停點
- ✅ PR38 Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待確認放行）
