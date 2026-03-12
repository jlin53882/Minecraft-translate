# PR38 設計稿：`lm_translator_main` caller migration（Phase 2）

## Summary
將現有 caller 逐步改為直接 import 拆分後的新模組，降低 `lm_translator_main` 的 re-export 壓力。

---

## Phase 0 盤點（必做）
- [x] 盤點 `lm_translator_main` 現有 caller
- [x] 盤點哪些符號其實已有 canonical 新位置
- [x] baseline 測試確認
- [x] 確認本 PR 不動 UI

---

## Phase 1 設計範圍
### 修改 caller
- `translation_tool/core/lm_translator.py`
- `translation_tool/core/lm_translator_shared.py`
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py`
- `translation_tool/plugins/md/md_lmtranslator.py`

### 目標遷移
- `value_fully_translated` -> `translation_tool.core.lm_config_rules`
- `extract_translatables/find_patchouli_json/find_lang_json/is_lang_file` -> `translation_tool.core.translatable_extractor`
- `map_lang_output_path/set_by_path` -> `translation_tool.core.translation_path_writer`
- `translate_batch_smart` 暫時仍留在 `lm_translator_main`
- `DRY_RUN / EXPORT_CACHE_ONLY` 暫時仍留在 `lm_translator_main`

---

## Out of scope
- 不移除 `lm_translator_main` re-export
- 不改 UI
- 不改 `translate_batch_smart` 本體

---

## 刪除/移除/替換說明
- **替換項目**：caller 的 import 路徑
- **為什麼改**：讓 caller 直接依賴 canonical 模組，降低 re-export 負擔
- **現況 caller**：core + plugins
- **替代路徑**：見上方「目標遷移」
- **風險**：漏改 import / alias 不一致 / 測試 monkeypatch 位置偏移
- **驗證依據**：caller 檢索 + import smoke + 全量 pytest

---

## Validation checklist
- [ ] `rg -n "from translation_tool\.core\.lm_translator_main import" translation_tool --glob "*.py"`
- [ ] `uv run python -c "from translation_tool.core.lm_translator import translate_directory_generator; print('lm-generator-import-ok')"`
- [ ] `uv run python -c "from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw; print('ftb-import-ok')"`
- [ ] `uv run python -c "from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import translate_kubejs_pending_to_zh_tw; print('kjs-import-ok')"`
- [ ] `uv run python -c "from translation_tool.plugins.md.md_lmtranslator import translate_md_pending; print('md-import-ok')"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr38 -o cache_dir=.pytest-cache\pr38`

---

## Rejected approaches
1) **方案**：直接刪 `lm_translator_main` re-export。  
   **放棄原因**：破壞面太大，caller 還沒全部遷完。  
2) **方案**：同一顆 PR 連 `translate_batch_smart` 位置一起改。  
   **放棄原因**：會把 import migration 與 orchestration 調整混在一起。  
3) **最終採用**：先改 caller 的 helper/value import，`translate_batch_smart` 暫留原位。
