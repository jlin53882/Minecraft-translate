# PR39B Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- 最新已推：`7212508`（PR39A）
- 目前已進入 PR39B，停在 Phase 0（尚未改 PR39B 目標程式碼）

---

## Phase 0 結論（先講重點）
PR39B 能做，但它的難度明顯高於 PR39A。  
原因不是 API 多，而是 **狀態（state）還沒真正封裝完**。

### 目前最重要的發現
1. **runtime 仍有直接依賴私有狀態**
   - `translation_tool/core/lm_translator.py`
     - 仍有：
       - `from translation_tool.utils.cache_manager import _translation_cache, _initialized`
   - 這是 PR39B 第一個必拆點

2. **tests 大量直接摸 private globals**
   - `tests/test_cache_store.py`
   - `tests/test_cache_search_orchestration.py`
   - `tests/test_cache_manager_api_surface.py`
   - 都直接操作：
     - `_translation_cache`
     - `_initialized`
     - `_session_new_entries`
     - `_is_dirty`
     - `_cache_file_path`

3. **UI / service 層相對乾淨**
   - `app/services_impl/cache/cache_services.py`
   - 只走正式 façade API：
     - `reload_translation_cache`
     - `reload_translation_cache_type`
     - `save_translation_cache`
     - `search_cache`
     - `get_cache_dict_ref`
     - `get_cache_entry`
     - `rebuild_search_index`
   - 這代表 UI 相容性風險主要在 live-reference 契約，而不是 import 路徑

4. **baseline 穩定**
   - `uv run pytest -q --basetemp=.pytest-tmp\pr39b-phase0 -o cache_dir=.pytest-cache\pr39b-phase0`
   - 結果：`85 passed in 1.15s`

---

## 具體風險判斷

### A. 不能直接刪的東西
目前不能直接把這些名字從 `cache_manager.py` 拔掉：
- `_translation_cache`
- `_initialized`
- `_session_new_entries`
- `_is_dirty`
- `_cache_file_path`
- `_cache_lock`

理由：
- runtime caller 仍在用
- tests 仍在用

### B. 先遷移哪裡最划算
建議順序：
1. `lm_translator.py`（runtime 唯一直接吃 private state 的地方）
2. `tests/test_cache_manager_api_surface.py`（live-reference guard 核心）
3. `tests/test_cache_search_orchestration.py`
4. `tests/test_cache_store.py`
5. 最後才移除 `cache_manager.py` 的 private aliases

### C. live-reference 契約是這顆 PR 的 SSOT
`get_cache_dict_ref()` 現在的 guard 明確要求：
- 回傳的是 live object
- 改回傳值要能直接反映到底層 state

PR39B 不管怎麼封裝，都不能把這條搞壞。

---

## Phase 0 建議
- ✅ 可以進 Phase 1
- 但建議採「state holder + seam」策略，而不是暴力刪 private globals
- 這顆 PR 的真正完成標準不是檔案變薄，而是：
  1. runtime caller 不再碰 private state
  2. tests 不再直接硬寫 `cache_manager._translation_cache = ...`
  3. `get_cache_dict_ref()` live-reference 契約仍成立

---

## 目前停點
- ✅ PR39B Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待你確認放行）
