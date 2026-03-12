# PR35 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（non-UI）

### 新增檔案
- `translation_tool/core/lm_api_client.py`
- `translation_tool/core/lm_response_parser.py`
- `translation_tool/core/translatable_extractor.py`
- `translation_tool/core/translation_path_writer.py`

### 修改檔案
- `translation_tool/core/lm_translator_main.py`
- `tests/test_lm_translator_main_guards.py`

---

## 變更重點
### 1) `lm_translator_main.py` 角色收斂
保留：
- `translate_batch_smart`
- `DRY_RUN`
- `EXPORT_CACHE_ONLY`
- 相容 re-export（舊 caller 仍從 `lm_translator_main` import）

### 2) Phase 1 拆出的模組
- `lm_api_client.py`
  - `call_gemini_requests`
- `lm_response_parser.py`
  - `safe_json_loads`
  - `chunked`
- `translatable_extractor.py`
  - `find_patchouli_json`
  - `find_lang_json`
  - `is_lang_file`
  - `extract_translatables`
- `translation_path_writer.py`
  - `map_lang_output_path`
  - `set_by_path`

### 3) caller 策略
- **沒有改舊 caller import 路徑**
- `lm_translator.py` / plugins / `lm_translator_shared.py` 仍可從 `lm_translator_main` 取用既有符號

### 4) guard test 微調
- `tests/test_lm_translator_main_guards.py` 原本 monkeypatch `lm_translator_main.is_*`
- PR35 拆模組後，這兩個判定函式實際落在 `translatable_extractor.py`
- 因此測試改為 patch 新模組位置，讓 guard test 真正保護新邊界

---

## Validation checklist 實際輸出

### 1) caller 檢查（舊 import 路徑仍存在）
```text
> rg -n "from translation_tool\.core\.lm_translator_main import" translation_tool --glob "*.py"
translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:21:from translation_tool.core.lm_translator_main import translate_batch_smart , value_fully_translated
translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:26:from translation_tool.core.lm_translator_main import translate_batch_smart, value_fully_translated
translation_tool/plugins/md/md_lmtranslator.py:18:from translation_tool.core.lm_translator_main import translate_batch_smart, value_fully_translated
translation_tool/core/lm_translator_shared.py:18:from translation_tool.core.lm_translator_main import value_fully_translated
translation_tool/core/lm_translator.py:17:from translation_tool.core.lm_translator_main import (
```

### 2) import smoke
```text
> uv run python -c "from translation_tool.core.lm_translator_main import translate_batch_smart; print('lm-main-import-ok')"
lm-main-import-ok

> uv run python -c "from translation_tool.core.lm_translator import translate_directory_generator; print('lm-generator-import-ok')"
lm-generator-import-ok
```

### 3) guard test（PR35 調整後）
```text
> uv run pytest -q tests/test_lm_translator_main_guards.py --basetemp=.pytest-tmp\pr35-guards -o cache_dir=.pytest-cache\pr35-guards
.......                                                                  [100%]
7 passed in 0.23s
```

### 4) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr35-phase1 -o cache_dir=.pytest-cache\pr35-phase1
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 1.10s
```

---

## 差異與風險判斷
### baseline 對照
- PR34 完成後 baseline：`83 passed`
- PR35 Phase 1 後：`83 passed`
- 差異：`+0`（本 PR 以結構切分為主，未新增測試數量）

### 風險狀態
- ✅ 舊 caller 路徑未破壞
- ✅ import smoke 正常
- ✅ 全量 pytest 維持綠燈
- ⚠️ `tests/test_lm_translator_main_guards.py` 有跟著新邊界調整 monkeypatch 位置（這是預期內調整）

---

## 目前工作樹狀態（未 commit）
```text
M tests/test_lm_translator_main_guards.py
M translation_tool/core/lm_translator_main.py
?? translation_tool/core/lm_api_client.py
?? translation_tool/core/lm_response_parser.py
?? translation_tool/core/translatable_extractor.py
?? translation_tool/core/translation_path_writer.py
?? docs/pr/2026-03-12_1114_PR_pr35-lm-translator-main-module-split-phase1-design.md
?? docs/pr/2026-03-12_1301_PR_pr35-phase0-inventory-report.md
```

---

## 目前停點
- ✅ PR35 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
