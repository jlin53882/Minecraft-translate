# PR37 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- PR36 baseline fixture 已推送：`6ceb449`
- PR36 Phase 1 已完成並推送：`1cf26e3`
- 目前已進入 PR37，停在 Phase 0（尚未改 PR37 目標程式碼）

---

## Phase 0 結論（先講重點）
PR37 可以做，但它比表面上更敏感，因為 `cache_manager.py` 不只是 façade，還承載了：
1. 對外 public API
2. 內部全域狀態（`_translation_cache`, `_initialized`, `_session_new_entries`, `_is_dirty` ...）
3. 搜尋索引 orchestrator (`cache_search.py`)
4. 少數 caller 直接 import 私有全域變數

=> 正確策略不是「大拆」，而是：
**先把 `cache_manager.py` 收斂成薄 façade，但 Phase 1 必須保留全域狀態相容層。**

---

## 1) public API 清單（目前可見）
`cache_manager.py` 目前對外提供至少以下 API：
- `initialize_translation_cache`
- `reload_translation_cache`
- `reload_translation_cache_type`
- `save_translation_cache`
- `add_to_cache`
- `get_cache_entry`
- `get_cache_dict_ref`
- `get_cache_overview`
- `force_rotate_shard`
- `get_search_engine`
- `rebuild_search_index`
- `rebuild_search_index_for_type`
- `search_cache`
- `find_similar_translations`

另外還暴露常數 / 狀態：
- `CACHE_TYPES`
- `ROLLING_SHARD_SIZE`
- `ACTIVE_SHARD_FILE`
- `_translation_cache`
- `_initialized`
- `_session_new_entries`
- `_is_dirty`
- `_cache_file_path`
- `_cache_lock`

---

## 2) caller 盤點

### A. 正常 façade caller（可維持不動）
- `app/services_impl/cache/cache_services.py`
  - 幾乎全部都透過 `cache_manager.*` API 呼叫
- `translation_tool/core/lm_translator_main.py`
- `translation_tool/core/lm_translator_shared.py`
- `translation_tool/core/lm_translator.py`

### B. 直接吃私有全域的 caller（PR37 風險點）
- `translation_tool/core/lm_translator.py`
  - `from translation_tool.utils.cache_manager import _translation_cache, _initialized`

### C. tests 直接操作內部狀態
- `tests/test_cache_manager_api_surface.py`
- `tests/test_cache_store.py`
- `tests/test_cache_search_orchestration.py`

結論：
- PR37 **不能直接移除私有全域變數**
- 至少 Phase 1 要保留這些名稱可用，否則會破壞現有 caller 與 tests

---

## 3) live reference 風險（你特別提醒的點）
`get_cache_dict_ref()` 目前的 guard test 已驗證：
- 回傳的是 live reference，不是 copy
- 修改回傳物件，會直接反映到 `_translation_cache`

=> PR37 若把 `_translation_cache` 下沉到別的模組：
- `get_cache_dict_ref()` 仍必須回傳同一份 live object
- 不能偷偷改成 `dict(cache)` 或淺拷貝

這是 **Phase 1 不可破壞契約**。

---

## 4) UI / Flet 風險盤點
我查了 `app/views`：
- `cache_view.py` **沒有直接 import `translation_tool.utils.cache_manager`**
- UI 走的是：
  - `app.services_impl.cache.cache_services`

這代表：
- PR37 **不需要直接改 UI import 路徑**
- UI 風險主要來自 service 回傳結構/語義是否改變，而不是 module path

=> 只要 `cache_services.py` 對接的 `cache_manager` façade 契約不變，Flet UI 風險相對低。

---

## 5) 本地索引 / 搜尋引擎風險盤點
`cache_search.py` 目前承擔：
- `CacheSearchEngine`
- `CacheSearchOrchestrator`
- `rebuild_search_index`
- `rebuild_search_index_for_type`
- `search_cache`
- `find_similar_translations`

`cache_manager.py` 只是持有 orchestrator / engine 的入口與 cache state 對接層。

=> PR37 要注意：
- 不要破壞 `cache_search.py` 與 `_translation_cache` 的資料交換方式
- `rebuild_search_index()` / `search_cache()` 的對外行為要保持一致
- `cache_rebuild_index_service()` 依賴 `cache_manager.rebuild_search_index()` + `get_cache_dict_ref()` 的組合結果

---

## 6) baseline 測試
命令：
- `uv run pytest -q --basetemp=.pytest-tmp\pr37-phase0 -o cache_dir=.pytest-cache\pr37-phase0`

結果：
- `84 passed in 1.17s`

---

## 7) Phase 1 建議策略
### 建議做法
1. 保留 `cache_manager.py` 為 façade
2. 新增 `__all__`，只白名單公開 API
3. 內部邏輯盡量轉調既有：
   - `cache_store.py`
   - `cache_shards.py`
   - `cache_search.py`
4. **暫時保留** 私有全域變數名稱（相容層）
   - `_translation_cache`
   - `_initialized`
   - `_session_new_entries`
   - `_is_dirty`
   - `_cache_file_path`
   - `_cache_lock`

### 這顆不該做的事
- 不要一次把私有全域完全藏起來
- 不要改 UI / service 層路徑
- 不要改 cache 資料格式

---

## 目前停點
- ✅ PR37 Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待你確認放行）
