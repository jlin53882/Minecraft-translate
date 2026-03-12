# PR35 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- PR34 已完成並推送：`c625f10`
- 目前已進入 PR35，停在 Phase 0（尚未改 PR35 目標程式碼）

---

## Phase 0 結論
`translation_tool/core/lm_translator_main.py` 目前 857 行、10 個頂層函式。  
它同時承擔了 4 類責任：
1. API 呼叫：`call_gemini_requests`
2. 路徑/檔案規則：`find_patchouli_json` / `find_lang_json` / `map_lang_output_path` / `is_lang_file`
3. 結構抽取/回寫：`extract_translatables` / `set_by_path`
4. 主翻譯 orchestration：`translate_batch_smart`

=> Phase 1 切分方向是對的，而且目前 import 依賴是可盤清的，**可以放行進 Phase 1**。

---

## 1) import 依賴圖（誰在用 `lm_translator_main`）

### 直接使用 `translate_batch_smart`
- `translation_tool/core/lm_translator.py`
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py`
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py`
- `translation_tool/plugins/md/md_lmtranslator.py`

### 直接使用 helper / 常數
- `translation_tool/core/lm_translator.py`
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
- `translation_tool/core/lm_translator_shared.py`
  - `value_fully_translated`

### 測試直接引用
- `tests/test_lm_translator_main_guards.py`
  - `safe_json_loads`
  - `find_lang_json`
  - `extract_translatables`
  - `set_by_path`

---

## 2) 可能循環引用風險

### 目前已存在的關係
- `lm_translator.py` -> `lm_translator_main.py`
- `lm_translator_shared.py` -> `lm_translator_main.py`（只拿 `value_fully_translated`）
- plugins -> `lm_translator_main.py`（拿 `translate_batch_smart` / `value_fully_translated`）

### 風險判斷
目前 **沒有直接 cycle**，但 PR35 若拆錯方向，很容易自撞：

#### 高風險情境 A
若新拆出的模組反過來 import `lm_translator.py`，就會形成：
- `lm_translator.py` -> `lm_translator_main.py` -> `新模組` -> `lm_translator.py`

#### 高風險情境 B
若 `lm_translator_shared.py` 也改去 import 新拆模組，而新拆模組又 import `lm_translator_shared.py`，會形成 shared cycle。

### 安全做法
新拆出的：
- `lm_api_client.py`
- `lm_response_parser.py`
- `translatable_extractor.py`
- `translation_path_writer.py`

應只依賴：
- `lm_config_rules.py`
- `cache_manager.py`
- `config_manager.py`
- Python stdlib

**不要**反向 import：
- `lm_translator.py`
- `lm_translator_shared.py`
- plugins

---

## 3) 建議搬移函式清單（逐個符號）

### A. `lm_api_client.py`
- `call_gemini_requests`

### B. `lm_response_parser.py`
- `safe_json_loads`
- `chunked`

### C. `translatable_extractor.py`
- `find_patchouli_json`
- `find_lang_json`
- `is_lang_file`
- `extract_translatables`

### D. `translation_path_writer.py`
- `map_lang_output_path`
- `set_by_path`

### E. `lm_translator_main.py` 保留
- `translate_batch_smart`
- module-level flags：`DRY_RUN`, `EXPORT_CACHE_ONLY`
- 相容 re-export（至少 Phase 1 先保留）

---

## 4) 對外 API 保留名單（Phase 1 不得破壞）
因為現況 caller 已存在，以下名稱 **Phase 1 必須仍可從 `lm_translator_main` 拿到**：
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

`call_gemini_requests` 目前看起來沒有外部 caller，可視為內部搬移候選。

---

## 5) 硬編碼路徑 / 規則盤點
`lm_translator_main.py` 內目前已知硬編碼/半硬編碼規則：
- `assets/*/{dir_name}/**/*.json`（Patchouli 掃描）
- `assets/*/lang/*.json`（Lang 掃描）
- `en_us.json -> zh_tw.json`（輸出路徑映射）
- `replace_rules.json`（在翻譯流程中作為預設規則檔）

=> Phase 1 若搬移，**規則本身不應改動**，只搬函式位置。

---

## 6) baseline 測試
命令：
- `uv run pytest -q --basetemp=.pytest-tmp\pr35-phase0 -o cache_dir=.pytest-cache\pr35-phase0`

結果：
- `83 passed in 1.07s`

---

## Phase 0 建議
- ✅ 可以進 Phase 1
- 建議策略：
  1. 先新增 4 個新模組
  2. 先搬函式，不改內容
  3. `lm_translator_main.py` 先保留 re-export 與 orchestrator 角色
  4. 先讓所有舊 caller 繼續從 `lm_translator_main` import，避免同顆 PR 再動 caller

---

## 目前停點
- ✅ PR35 Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待你確認放行）
