# PR39A Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：PR39A Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容

### 修改檔案
- `tests/test_lm_translator_main_guards.py`
- `tests/test_lang_merger_guards.py`
- `translation_tool/core/lm_translator_main.py`
- `translation_tool/core/lang_merger.py`

### 新增文件
- `docs/pr/2026-03-12_1403_PR_pr39a-final-compat-cleanup-lm-and-merger-design.md`
- `docs/pr/2026-03-12_1404_PR_pr39a-phase0-inventory-report.md`

---

## Phase 1 實際變更

### 1) tests 遷移到 canonical 模組
#### `tests/test_lm_translator_main_guards.py`
改為直接測：
- `lm_response_parser.safe_json_loads`
- `translatable_extractor.find_lang_json`
- `translatable_extractor.extract_translatables`
- `translation_path_writer.set_by_path`

#### `tests/test_lang_merger_guards.py`
改為直接測：
- `lang_codec.collapse_lang_lines`
- `lang_codec.parse_lang_text`
- `lang_codec.dump_lang_text`
- `lang_codec.is_mc_standard_lang_path`
- `lang_merge_content.export_filtered_pending`

### 2) `lm_translator_main.py` 瘦身
已移除 helper/value re-export import：
- `extract_translatables`
- `find_patchouli_json`
- `find_lang_json`
- `is_lang_file`
- `map_lang_output_path`
- `set_by_path`
- `value_fully_translated`

保留：
- `translate_batch_smart`
- `DRY_RUN`
- `EXPORT_CACHE_ONLY`

### 3) `lang_merger.py` 清理
- 已移除純 helper re-export import（codec 類）
- 保留主入口 `merge_zhcn_to_zhtw_from_zip()` 真正執行時需要的 direct imports：
  - `_process_content_or_copy_file`
  - `export_filtered_pending`
  - `remove_empty_dirs`
  - `_process_single_mod`

> 中途踩到一個點：如果把 `lang_merger` 的 content import 一次拔乾淨，baseline fixture 會炸，因為主入口本體仍直接使用 `_process_content_or_copy_file`。已改為「保留主入口內部依賴，但移除外部 helper re-export」。

---

## Validation checklist 實際輸出

### 1) tests 遷移後先驗證（尚未拔相容層）
```text
> uv run pytest -q tests/test_lm_translator_main_guards.py --basetemp=.pytest-tmp\pr39a-lmtests -o cache_dir=.pytest-cache\pr39a-lmtests
.......                                                                  [100%]
7 passed in 0.04s

> uv run pytest -q tests/test_lang_merger_guards.py --basetemp=.pytest-tmp\pr39a-mergertests -o cache_dir=.pytest-cache\pr39a-mergertests
.........                                                                [100%]
9 passed in 0.06s

> uv run pytest -q tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr39a-baseline -o cache_dir=.pytest-cache\pr39a-baseline
.                                                                        [100%]
1 passed in 0.09s
```

### 2) 拔相容層後的 import smoke
```text
> uv run python -c "from translation_tool.core.lm_translator_main import translate_batch_smart; print('lm-main-import-ok')"
lm-main-import-ok

> uv run python -c "from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip; print('lang-merger-import-ok')"
lang-merger-import-ok
```

### 3) focus tests（拔相容層後）
```text
> uv run pytest -q tests/test_lm_translator_main_guards.py tests/test_lang_merger_guards.py tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr39a-focus -o cache_dir=.pytest-cache\pr39a-focus
.................                                                        [100%]
17 passed in 0.10s
```

### 4) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr39a -o cache_dir=.pytest-cache\pr39a
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 1.17s
```

### 5) hidden caller 檢查
```text
> rg -n "from translation_tool\.core\.lm_translator_main import|lm_main\.|from translation_tool\.core\.lang_merger import|lang_merger\." tests translation_tool --glob "*.py" --glob "!backups/**" --glob "!docs/**"
C:\Users\admin\Desktop\minecraft_translator_flet\tests\test_lang_merger_zip_baseline.py:89:    updates = list(lang_merger.merge_zhcn_to_zhtw_from_zip(str(zip_path), str(output_dir), False))
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\plugins\kubejs\kubejs_tooltip_lmtranslator.py:26:from translation_tool.core.lm_translator_main import translate_batch_smart
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\core\lm_translator.py:17:from translation_tool.core.lm_translator_main import (
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\plugins\ftbquests\ftbquests_lmtranslator.py:21:from translation_tool.core.lm_translator_main import translate_batch_smart
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\plugins\md\md_lmtranslator.py:18:from translation_tool.core.lm_translator_main import translate_batch_smart
```

解讀：
- `lm_translator_main` 剩下的 runtime caller 都是刻意保留的入口：
  - `translate_batch_smart`
  - `DRY_RUN`
  - `EXPORT_CACHE_ONLY`
- `lang_merger` 剩下的外部使用只剩 baseline fixture 測主入口，這是預期

---

## 數字對照
- PR39A Phase 0 baseline：`85 passed`
- PR39A Phase 1 後：`85 passed`
- 差異：`+0`

---

## 目前停點
- ✅ PR39A Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
