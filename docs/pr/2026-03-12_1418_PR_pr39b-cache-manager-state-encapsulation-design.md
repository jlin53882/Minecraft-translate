# PR39B 設計稿：`cache_manager` 狀態封裝與最終清理

## Summary
PR39B 是這輪重構的最後一顆硬仗。目標不是單純「把檔案再拆薄」，而是把 `cache_manager.py` 目前仍對外可見／可被直接碰觸的 **全域狀態相容層** 收斂為正式 API 與受控測試 seam。

這顆 PR 的核心目標：
1. 讓 runtime caller 不再直接依賴 `_translation_cache` / `_initialized`
2. 把狀態真正下沉到統一的 state holder（優先放在 `cache_store.py`）
3. 保留 `get_cache_dict_ref()` 的 live-reference 契約
4. 不破壞 `cache_services.py` 與 Flet UI 的現有互動方式

---

## Phase 0 盤點（必做）
- [x] 找出 runtime caller 對私有狀態的直接依賴
- [x] 找出 tests 對私有狀態的直接依賴
- [x] 盤點 UI / service 對 façade API 的依賴
- [x] baseline 測試確認

---

## Phase 1 設計範圍

### A. 建立正式 state holder
建議新增：
- `translation_tool/utils/cache_state.py`

內容：
- `cache_lock`
- `translation_cache`
- `cache_file_path`
- `initialized`
- `session_new_entries`
- `is_dirty`
- （必要時）getter/setter / reset helper

### B. `cache_manager.py` 改為真正 façade
保留：
- public API
- `__all__`
- live-reference 契約（`get_cache_dict_ref()`）
- 測試 seam（明確命名，不再靠隱式摸私有變數）

移除／收斂：
- `_translation_cache`
- `_initialized`
- `_session_new_entries`
- `_is_dirty`
- `_cache_file_path`
- `_cache_lock`

### C. runtime caller 遷移
重點先改：
- `translation_tool/core/lm_translator.py`
  - 不再 `from cache_manager import _translation_cache, _initialized`
  - 改走 `get_cache_dict_ref()` / `get_cache_entry()` / 正式 API

### D. tests 遷移
- `tests/test_cache_store.py`
- `tests/test_cache_search_orchestration.py`
- `tests/test_cache_manager_api_surface.py`

原則：
- 不再直接寫 `cache_manager._translation_cache = ...`
- 改用正式測試 seam（例如 `cache_state` 或 `cache_manager._get_state_for_test()`）

---

## Out of scope
- 不改 UI 路徑
- 不改 cache 資料格式
- 不改 search engine schema
- 不改 `cache_services.py` 回傳格式

---

## 刪除/移除/替換說明
- **刪除/替換項目**：`cache_manager.py` 中的私有全域狀態相容層
- **為什麼改**：現在它們仍被 runtime caller 與 tests 直接依賴，會阻止 façade 真正完成封裝
- **現況 caller**：
  - runtime：`lm_translator.py`
  - tests：`test_cache_store.py`, `test_cache_search_orchestration.py`, `test_cache_manager_api_surface.py`
- **替代路徑**：
  - runtime 改走 `get_cache_dict_ref()` / `get_cache_entry()` / 正式 API
  - tests 改走 `cache_state` 或正式測試 seam
- **風險**：若 live reference 契約壞掉，UI / service / search rebuild 會出現微妙錯誤
- **驗證依據**：cache tests + full pytest + live-reference guard

---

## Validation checklist
- [ ] `rg -n "from translation_tool\.utils\.cache_manager import _translation_cache, _initialized|cache_manager\._translation_cache|cache_manager\._initialized|cache_manager\._session_new_entries|cache_manager\._is_dirty|cache_manager\._cache_file_path" . --glob "*.py" --glob "!backups/**" --glob "!docs/**"`
- [ ] `uv run python -c "from translation_tool.utils.cache_manager import reload_translation_cache, save_translation_cache, search_cache; print('cache-manager-import-ok')"`
- [ ] `uv run pytest -q tests/test_cache_store.py tests/test_cache_shards.py tests/test_cache_search_orchestration.py tests/test_cache_manager_api_surface.py --basetemp=.pytest-tmp\pr39b-cache -o cache_dir=.pytest-cache\pr39b-cache`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr39b -o cache_dir=.pytest-cache\pr39b`

---

## Rejected approaches
1) **方案**：直接刪掉 `_translation_cache` 等私有變數，讓錯誤自然浮出來。  
   **放棄原因**：會讓 repo 在一段時間內全面紅燈，回歸範圍過大。  
2) **方案**：完全不動 tests，只做 runtime caller migration。  
   **放棄原因**：測試仍綁在舊狀態模型，等於相容層還是沒真正收掉。  
3) **最終採用**：先建 state holder / 正式 seam，再同步遷移 runtime 與 tests，最後移除舊私有狀態別名。
