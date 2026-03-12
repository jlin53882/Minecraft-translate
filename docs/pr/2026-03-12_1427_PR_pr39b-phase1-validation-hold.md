# PR39B Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：PR39B Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容

### 新增 / 重寫檔案
- `translation_tool/utils/cache_store.py`（新增 runtime state holder）
- `translation_tool/utils/cache_manager.py`（重寫為真正 façade）

### 修改檔案
- `translation_tool/core/lm_translator.py`
- `tests/test_cache_store.py`
- `tests/test_cache_search_orchestration.py`
- `tests/test_cache_manager_api_surface.py`

### 新增文件
- `docs/pr/2026-03-12_1418_PR_pr39b-cache-manager-state-encapsulation-design.md`
- `docs/pr/2026-03-12_1419_PR_pr39b-phase0-inventory-report.md`

---

## Phase 1 實際變更

### 1) runtime state 下沉到 `cache_store.py`
新增：
- `CacheRuntimeState`
- `get_runtime_state()`
- `reset_runtime_state(cache_types)`
- `ensure_runtime_maps(cache_types)`

狀態已下沉：
- `translation_cache`
- `cache_file_path`
- `initialized`
- `session_new_entries`
- `is_dirty`
- `cache_lock`

### 2) `cache_manager.py` 改為 façade
- 不再自行持有 `_translation_cache/_initialized/...` 全域狀態
- 改透過 `cache_store` 的 runtime state 存取
- 保留正式 public API：
  - `reload_translation_cache`
  - `reload_translation_cache_type`
  - `save_translation_cache`
  - `get_cache_entry`
  - `get_cache_dict_ref`
  - `search_cache`
  - `rebuild_search_index` ...
- 新增正式初始化檢查 API：
  - `is_cache_initialized()`

### 3) 拆除 runtime 越權存取
#### `lm_translator.py`
已移除：
- `from translation_tool.utils.cache_manager import _translation_cache, _initialized`

改為：
- `get_cache_dict_ref("lang")`
- `get_cache_dict_ref("patchouli")`

=> runtime 已不再直接碰 cache_manager private state。

### 4) tests 從白盒直摸改為 state holder seam
#### `tests/test_cache_store.py`
- 改用 `cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)`
- 不再直接寫 `cache_manager._translation_cache = ...`

#### `tests/test_cache_search_orchestration.py`
- 改用 `cache_store.reset_runtime_state(...)`
- 搜尋 façade reset 仍透過 `cache_manager._search_facade = None`（這是 search façade 專屬測試 seam，不屬於 runtime state）

#### `tests/test_cache_manager_api_surface.py`
- live-reference guard 改以 `cache_store.reset_runtime_state(...)` 佈置 state
- 不再直接硬寫 `cache_manager._translation_cache / _initialized`

---

## Validation checklist 實際輸出

### 1) import smoke
```text
> uv run python -c "from translation_tool.utils.cache_manager import reload_translation_cache, save_translation_cache, search_cache, get_cache_dict_ref; print('cache-manager-import-ok')"
cache-manager-import-ok
```

### 2) cache 相關 focus tests
```text
> uv run pytest -q tests/test_cache_store.py tests/test_cache_shards.py tests/test_cache_search_orchestration.py tests/test_cache_manager_api_surface.py --basetemp=.pytest-tmp\pr39b-cache -o cache_dir=.pytest-cache\pr39b-cache
..............                                                           [100%]
14 passed in 0.54s
```

### 3) runtime / tests 越權存取檢查
```text
> rg -n "from translation_tool\.utils\.cache_manager import _translation_cache, _initialized|cache_manager\._translation_cache|cache_manager\._initialized|cache_manager\._session_new_entries|cache_manager\._is_dirty|cache_manager\._cache_file_path" translation_tool tests --glob "*.py" --glob "!backups/**" --glob "!docs/**"
(no output, exit code 1)
```

### 4) 新 seam / 正式 API 使用檢查
```text
> rg -n "get_cache_dict_ref\(|is_cache_initialized\(|cache_store\.reset_runtime_state\(|cache_store\.get_runtime_state\(" translation_tool tests --glob "*.py" --glob "!backups/**" --glob "!docs/**"
...（可見 lm_translator.py 已改用 get_cache_dict_ref；tests 已改用 cache_store.reset_runtime_state）
```

### 5) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr39b -o cache_dir=.pytest-cache\pr39b
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 1.14s
```

---

## 數字對照
- PR39B Phase 0 baseline：`85 passed`
- PR39B Phase 1 後：`85 passed`
- 差異：`+0`

---

## 關鍵契約確認
- ✅ `get_cache_dict_ref()` live reference 契約仍成立
- ✅ `lm_translator.py` 已不再繞過 façade 直接摸 private state
- ✅ tests 已從白盒直摸 private globals，改為走 state holder seam
- ✅ `cache_services.py` / UI 依賴的 façade API 未破壞

---

## 目前停點
- ✅ PR39B Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
