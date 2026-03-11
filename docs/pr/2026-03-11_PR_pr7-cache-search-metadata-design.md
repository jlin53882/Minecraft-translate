# PR7 設計稿

## PR Title
feat: populate cache search metadata for `mod` / `path`

## 1. 現狀分析

### 搜尋索引重建入口
目前專案內可重建 cache search index 的入口有三個：

1. `translation_tool/utils/cache_manager.py`
   - `rebuild_search_index()`：重建全部索引
   - `rebuild_search_index_for_type(cache_type)`：重建單一分類索引
2. `app/services.py`
   - `cache_rebuild_index_service()` → 內部直接呼叫 `cache_manager.rebuild_search_index()`
   - `cache_reload_service()` → `reload_translation_cache()` 後再 `rebuild_search_index()`
   - `cache_reload_type_service(cache_type)` → reload 單一 type 後再 `rebuild_search_index_for_type(cache_type)`
3. `main.py`
   - 啟動時背景執行 `cache_rebuild_index_service()`

### 實際重建指令
最直接可重建 index、讓新 metadata 生效的指令：

```powershell
uv run python -c "from translation_tool.utils.cache_manager import rebuild_search_index; rebuild_search_index()"
```

若要連記憶體 cache 一起重新載入再重建，可用：

```powershell
uv run python -c "from app.services import cache_reload_service; print(cache_reload_service())"
```

### 現有 search result 結構
`translation_tool/utils/cache_search.py` 的 `CacheSearchEngine.search()` 目前回傳：

- `key`
- `src`
- `dst`
- `mod`
- `path`
- `type`
- `score`（FTS）

`cache_manager.search_cache()` 再交給 `FuzzyMatcher.rank_results()` 後，還會多：

- `combined_score`

### 問題點
`cache_manager.rebuild_search_index()` / `rebuild_search_index_for_type()` 建索引時，`mod` 與 `path` 都是硬塞空字串：

```python
'mod': '',
'path': ''
```

所以 search result 雖然 schema 上有欄位，但內容沒上下文，等於半殘。

## 2. 要補什麼

### `path` 從哪裡取
優先順序：

1. `entry['path']`（若新資料已經存進 cache entry）
2. 依 `cache_type + key` 推導
   - `lang`：直接用 `key`
   - `patchouli` / `ftbquests` / `kubejs` / `md`：從 composite key `path|source_text` 取 `path` 段

### `mod` 從哪裡取
優先順序：

1. `entry['mod']`（若新資料已經存進 cache entry）
2. 從 `path` 推導
   - 若路徑含 `assets/<mod>/...` 或 `data/<mod>/...`，取 `<mod>`
3. `lang` fallback：從 lang key 的第二段推導，例如 `entity.minecraft.creeper` → `minecraft`
4. 特定 cache type fallback
   - `ftbquests` → `ftbquests`
   - `kubejs` → `kubejs`
   - `md` → `md`

### 寫到哪裡
Phase 1 先寫進「索引建置資料」：
- `translation_tool/utils/cache_manager.py`
  - 重建索引時不再塞空字串，而是先做 metadata inference

另外順手把 `add_to_cache()` 擴成可接受可選 `mod` / `path`，保持未來寫入相容，但不強迫所有 caller 同步大改。

## 3. Phase 1 實作清單

### `translation_tool/utils/cache_manager.py`
1. 新增 metadata helper：
   - `_extract_path_from_composite_key()`
   - `_infer_search_path()`
   - `_infer_search_mod()`
   - `_build_search_metadata()`
2. 調整 `add_to_cache()`：
   - 支援可選 `mod` / `path`
   - 舊 caller 不必修改
3. 修改 `rebuild_search_index()`：
   - 建索引 entries 時改用 `_build_search_metadata(...)`
4. 修改 `rebuild_search_index_for_type()`：
   - 同步使用 `_build_search_metadata(...)`

## 4. Validation checklist

- [ ] `uv run python -c "from translation_tool.utils import cache_manager; print('import ok')"`
- [ ] `uv run python -c "from translation_tool.utils.cache_manager import rebuild_search_index; rebuild_search_index()"`
- [ ] `uv run python -c "from translation_tool.utils.cache_manager import search_cache; import json; r = search_cache('creeper', limit=5); print(json.dumps(r[0], ensure_ascii=False, indent=2))"`
- [ ] 至少貼一筆 search result，確認 `mod` / `path` 非空字串
- [ ] `uv run pytest` 或至少 targeted smoke validation，不得引入新的 import / syntax error

## 5. Risk assessment

### 低風險
- 只補索引 metadata 推導，不重寫 search engine schema
- 舊 shard 仍可讀，因為 helper 支援從既有 key fallback 推導
- `add_to_cache()` 只做向後相容擴充，舊 caller 不會炸

### 中風險
- `path|source_text` 用 `split('|', 1)` 有理論上的歧義：若 path 本身含 `|` 會切錯
  - 但目前專案的 path 慣例看起來沒用這個字元，先接受
- `lang` 的 mod 推導使用 dotted key 第二段，對少數非標準 key 可能不完美
  - 但比空字串強很多，且不影響主搜尋功能

### 暫不處理
- 不在這顆 PR 同步改 UI 顯示欄位
- 不在這顆 PR 大規模回填所有 cache shard 的 schema
- 不重做 `cache_search_service()` 的 response schema
