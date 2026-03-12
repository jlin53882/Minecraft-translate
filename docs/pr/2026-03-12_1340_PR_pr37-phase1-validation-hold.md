# PR37 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：PR37 Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（non-UI）

### 新增檔案
- `translation_tool/utils/cache_loader.py`
- `translation_tool/utils/cache_overview.py`
- `translation_tool/utils/cache_search_facade.py`

### 修改檔案
- `translation_tool/utils/cache_manager.py`
- `tests/test_cache_manager_api_surface.py`

---

## 實作重點
### 1) `cache_manager.py` 變成更薄的 façade
保留：
- public API 入口
- 私有全域狀態名稱（相容層）
  - `_translation_cache`
  - `_initialized`
  - `_session_new_entries`
  - `_is_dirty`
  - `_cache_file_path`
  - `_cache_lock`

### 2) 內部責任下沉
- `cache_loader.py`
  - `load_shard_file`
  - `load_cache_type`
- `cache_overview.py`
  - `get_active_shard_id`
  - `build_cache_overview`
- `cache_search_facade.py`
  - `CacheSearchFacade`
  - 封裝 search engine / rebuild / query 對接

### 3) `__all__` 白名單
- PR37 補了 `cache_manager.__all__`
- 白名單只包含 public API 與必要常數
- **不暴露** `_translation_cache` / `_initialized` 等私有狀態

### 4) 相容層補洞
- 既有 tests 仍會 monkeypatch `cache_manager._load_cache_type`
- 所以 PR37 補回 `_load_cache_type()` wrapper，讓 internal seam 保持可注入

---

## Validation checklist 實際輸出

### 1) import smoke
```text
> uv run python -c "from translation_tool.utils.cache_manager import reload_translation_cache, save_translation_cache, search_cache; print('cache-manager-import-ok')"
cache-manager-import-ok
```

### 2) cache 相關測試
```text
> uv run pytest -q tests/test_cache_store.py tests/test_cache_shards.py tests/test_cache_search_orchestration.py tests/test_cache_manager_api_surface.py --basetemp=.pytest-tmp\pr37-cache -o cache_dir=.pytest-cache\pr37-cache
..............                                                           [100%]
14 passed in 0.52s
```

### 3) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr37-phase1 -o cache_dir=.pytest-cache\pr37-phase1
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 1.14s
```

### 4) `__all__` 輸出
```text
> uv run python -c "import translation_tool.utils.cache_manager as m; print(sorted(getattr(m,'__all__',[])))"
['ACTIVE_SHARD_FILE', 'CACHE_TYPES', 'ROLLING_SHARD_SIZE', 'add_to_cache', 'find_similar_translations', 'force_rotate_shard', 'get_active_shard_id', 'get_cache_dict_ref', 'get_cache_entry', 'get_cache_overview', 'get_from_cache', 'get_search_engine', 'get_session_new_count', 'initialize_translation_cache', 'rebuild_search_index', 'rebuild_search_index_for_type', 'reload_translation_cache', 'reload_translation_cache_type', 'save_translation_cache', 'search_cache']
```

---

## 數字對照
- PR37 Phase 0 baseline：`84 passed`
- PR37 Phase 1 後：`85 passed`
- 差異：`+1`（新增的 `__all__` 白名單 guard test）

---

## 風險與確認點
- ✅ `get_cache_dict_ref()` 的 live reference guard 仍通過
- ✅ `cache_view.py` 不直接 import `cache_manager`，UI 主要透過 `cache_services`，本次未改 service 契約
- ✅ 搜尋索引相關流程仍由 façade 委派，cache_search_orchestration 測試全綠
- ✅ 私有全域名稱仍存在，未打破 `lm_translator.py` / tests 的現有依賴

---

## 目前停點
- ✅ PR37 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
