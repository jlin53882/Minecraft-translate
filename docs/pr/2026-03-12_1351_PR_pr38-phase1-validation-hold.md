# PR38 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：PR38 Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（Phase 2 / caller migration）

### 修改檔案
- `translation_tool/core/lm_translator_shared.py`
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py`
- `translation_tool/plugins/md/md_lmtranslator.py`
- `translation_tool/core/lm_translator.py`

---

## 變更內容
### 1) `lm_translator_shared.py`
- `value_fully_translated`
  - 從 `translation_tool.core.lm_translator_main`
  - 改為直接 import `translation_tool.core.lm_config_rules`

### 2) plugins 三檔
- `ftbquests_lmtranslator.py`
- `kubejs_tooltip_lmtranslator.py`
- `md_lmtranslator.py`

調整：
- `translate_batch_smart` 暫時仍從 `lm_translator_main` import
- `value_fully_translated` 改為直接從 `lm_config_rules` import

### 3) `lm_translator.py`
- 保留從 `lm_translator_main` import：
  - `DRY_RUN`
  - `EXPORT_CACHE_ONLY`
  - `translate_batch_smart`
- 改為直接 import：
  - `extract_translatables / find_patchouli_json / find_lang_json / is_lang_file` <- `translatable_extractor`
  - `map_lang_output_path / set_by_path` <- `translation_path_writer`
  - `value_fully_translated` <- `lm_config_rules`

---

## Validation checklist 實際輸出

### 1) 逐步驗證：`lm_translator_shared.py`
```text
> uv run pytest -q tests/test_cache_manager_api_surface.py tests/test_cache_search_orchestration.py --basetemp=.pytest-tmp\pr38-shared -o cache_dir=.pytest-cache\pr38-shared
.......                                                                  [100%]
7 passed in 0.56s
```

### 2) 逐步驗證：plugins 三檔 import smoke
```text
> uv run python -c "from translation_tool.plugins.ftbquests.ftbquests_lmtranslator import translate_ftb_pending_to_zh_tw; print('ftb-import-ok')"
ftb-import-ok

> uv run python -c "from translation_tool.plugins.kubejs.kubejs_tooltip_lmtranslator import translate_kubejs_pending_to_zh_tw; print('kjs-import-ok')"
kjs-import-ok

> uv run python -c "from translation_tool.plugins.md.md_lmtranslator import translate_md_pending; print('md-import-ok')"
md-import-ok
```

### 3) 逐步驗證：`lm_translator.py`
```text
> uv run python -c "from translation_tool.core.lm_translator import translate_directory_generator; print('lm-generator-import-ok')"
lm-generator-import-ok

> uv run pytest -q tests/test_lm_translator_main_guards.py --basetemp=.pytest-tmp\pr38-lm -o cache_dir=.pytest-cache\pr38-lm
.......                                                                  [100%]
7 passed in 0.22s
```

### 4) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr38-phase1 -o cache_dir=.pytest-cache\pr38-phase1
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 1.13s
```

### 5) caller 搜尋確認
```text
> rg -n "from translation_tool\.core\.lm_translator_main import" translation_tool --glob "*.py"
translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:26:from translation_tool.core.lm_translator_main import translate_batch_smart
translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:21:from translation_tool.core.lm_translator_main import translate_batch_smart
translation_tool/core/lm_translator.py:17:from translation_tool.core.lm_translator_main import (
translation_tool/plugins/md/md_lmtranslator.py:18:from translation_tool.core.lm_translator_main import translate_batch_smart
```

解讀：
- 目前 translation_tool 內已不再從 `lm_translator_main` 取 helper/value re-export
- 剩下的 caller 都是 **刻意保留**：
  - `translate_batch_smart`
  - `DRY_RUN`
  - `EXPORT_CACHE_ONLY`

---

## 數字對照
- PR38 Phase 0 baseline：`85 passed`
- PR38 Phase 1 後：`85 passed`
- 差異：`+0`

=> 這代表本次是純 caller import migration，沒有破壞既有行為。

---

## 目前停點
- ✅ PR38 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
