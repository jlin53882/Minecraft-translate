# PR39A Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：PR39A Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容

### 修改檔案
- `tests/test_lm_translator_main_guards.py`
- `tests/test_lang_merger_guards.py`
- `translation_tool/core/lm_translator_main.py`
- `translation_tool/core/lang_merger.py`

---

## 變更內容
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

### 2) `lm_translator_main.py` 最後瘦身
移除 helper/value re-export import：
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
- orchestration 真的需要的依賴

### 3) `lang_merger.py` 相容層清理
移除「只為外部 helper re-export 存在」的 import 群：
- `collapse_lang_lines`
- `dump_lang_text`
- `is_mc_standard_lang_path`
- `parse_lang_text`
- 其他 codec helper

保留主入口 `merge_zhcn_to_zhtw_from_zip()` 真正執行時需要的 direct imports：
- `_process_content_or_copy_file`
- `export_filtered_pending`
- `remove_empty_dirs`
- `_process_single_mod`

> 中途發現：如果把 `lang_merger` 的 content import 全拔光，baseline fixture 會失敗，因為主入口本體仍直接使用 `_process_content_or_copy_file`。
> 所以這次採「移除外部 helper re-export，但保留入口內部真正依賴」的版本。

---

## Validation checklist 實際輸出

### 1) import smoke
```text
> uv run python -c "from translation_tool.core.lm_translator_main import translate_batch_smart; print('lm-main-import-ok')"
lm-main-import-ok

> uv run python -c "from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip; print('lang-merger-import-ok')"
lang-merger-import-ok
```

### 2) focus tests
```text
> uv run pytest -q tests/test_lm_translator_main_guards.py tests/test_lang_merger_guards.py tests/test_lang_merger_zip_baseline.py --basetemp=.pytest-tmp\pr39a-focus -o cache_dir=.pytest-cache\pr39a-focus
.................                                                        [100%]
17 passed in 0.10s
```

### 3) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr39a -o cache_dir=.pytest-cache\pr39a
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 1.17s
```

### 4) hidden caller 檢查
```text
> rg -n "from translation_tool\.core\.lm_translator_main import|lm_main\.|from translation_tool\.core\.lang_merger import|lang_merger\." tests translation_tool --glob "*.py" --glob "!backups/**" --glob "!docs/**"
C:\Users\admin\Desktop\minecraft_translator_flet\tests\test_lang_merger_zip_baseline.py:89:    updates = list(lang_merger.merge_zhcn_to_zhtw_from_zip(str(zip_path), str(output_dir), False))
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\plugins\kubejs\kubejs_tooltip_lmtranslator.py:26:from translation_tool.core.lm_translator_main import translate_batch_smart
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\core\lm_translator.py:17:from translation_tool.core.lm_translator_main import (
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\plugins\ftbquests\ftbquests_lmtranslator.py:21:from translation_tool.core.lm_translator_main import translate_batch_smart
C:\Users\admin\Desktop\minecraft_translator_flet\translation_tool\plugins\md\md_lmtranslator.py:18:from translation_tool.core.lm_translator_main import translate_batch_smart
```

解讀：
- `lm_translator_main` 剩下的 caller 都是刻意保留的 runtime 入口：
  - `translate_batch_smart`
  - `DRY_RUN`
  - `EXPORT_CACHE_ONLY`
- `lang_merger` 剩下的外部使用只剩 baseline fixture 測試在測主入口，這是預期

---

## 數字對照
- PR39A Phase 0 baseline：`85 passed`
- PR39A Phase 1 後：`85 passed`
- 差異：`+0`

---

## 目前停點
- ✅ PR39A Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
