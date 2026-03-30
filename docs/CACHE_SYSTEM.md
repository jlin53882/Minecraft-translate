# Cache 系統架構

> 更新：2026-03-30（由 `cache_store.py`、`cache_manager.py`、`cache_search.py` 逆向文件化）

---

## 目的

避免對相同原文重複呼叫 LLM。翻譯前先查 cache，命中則直接回傳；未命中才翻譯並寫入 cache。

---

## 核心元件

| 元件 | 檔案 | 職責 |
|------|------|------|
| CacheRuntimeState | `cache_store.py` | 持有翻譯快取本體（thread-safe dict） |
| cache_manager | `cache_manager.py` | 公開 API façade，協調載入/儲存/查詢 |
| cache_search | `cache_search.py` | SQLite FTS5 全文搜尋引擎 |
| cache_loader | `cache_loader.py` | 從磁碟讀取 cache 檔案 |
| cache_shards | `cache_shards.py` | 滾動分片管理（每片 2500 筆） |

---

## Cache 類型

```
CACHE_TYPES = ["lang", "patchouli", "ftbquests", "kubejs", "md"]
```

每個類型對應一種翻譯來源，各自獨立儲存。

---

## 資料結構

```python
CacheRuntimeState:
  translation_cache: dict[str, dict[str, Any]]   # {cache_type: {key: {src, dst, mod, path}}}
  cache_file_path: dict[str, Path]              # 各類型對應的磁碟檔案
  initialized: bool
  session_new_entries: dict[str, dict[str, Any]]  # 本次執行階段新增的項目（flush 前暂存）
  is_dirty: dict[str, bool]                    # 是否有未儲存的變更
  cache_lock: threading.RLock                   # 執行緒安全鎖
  metrics: CacheMetrics                        # PR66-A 效能監控
```

**Cache 條目格式**：
```python
{
    "src": "原始英文文字",   # 翻譯前
    "dst": "譯後繁體中文",   # 翻譯後
    "mod": "mod_id",         # 可選：所屬模組
    "path": "lang/en_us.json" # 可選：來源檔案
}
```

---

## 儲存格式：滾動分片

- 每個 cache_type 的資料寫入**分片檔案**（shard）
- 分片容量：**2500 筆/片**
- 檔案命名：`.cache_{type}_00123`（遞增流水號）
- 活躍分片標記：`cache_dir/.active`（目前使用中的分片編號）

容量滿時自動輪轉（`force_rotate_shard()` 可強制新建分片）。

---

## Cache 查找流程

```
lm_translator_main 翻譯一個 key:
  │
  ├─→ get_from_cache(type, key)
  │     └─→ 回傳 dst（string）或 None
  │
  ├─ Cache HIT → 直接使用，不呼叫 LLM
  │
  └─ Cache MISS → 呼叫 LLM → add_to_cache(type, key, src, dst)
                         └─→ 寫入 session_new_entries（記憶體）
                             └─→ session 結束 → save_translation_cache()
                                   └─→ 寫入磁碟分片
```

---

## 搜尋功能

`cache_search.py` 提供兩種搜尋：

| 功能 | 實作 | 用法 |
|------|------|------|
| 全文搜尋 | SQLite FTS5 | `search_cache(query, cache_type, limit)` |
| 模糊比對 | difflib SequenceMatcher | `find_similar_translations(text, threshold=0.6)` |

---

## 關鍵 API 一覽

```python
# 初始化（app 啟動時自動呼叫）
initialize_translation_cache()

# 查詢
get_from_cache(cache_type, key) -> str | None     # 回傳 dst
get_cache_entry(cache_type, key) -> dict | None    # 回傳完整條目

# 新增（記憶體）
add_to_cache(cache_type, key, src, dst, mod=..., path=...)
add_to_cache_batch(cache_type, [(key, src, dst), ...])

# 儲存（磁碟）
save_translation_cache(cache_type)                  # 將 session_new_entries flush 到分片

# 分片管理
get_active_shard_id(cache_type) -> str
force_rotate_shard(cache_type) -> bool

# 搜尋
search_cache(query, cache_type=None, limit=50)
find_similar_translations(text, threshold=0.6)
```

---

## 與翻譯流程的關係

在 `lm_translator_shared_loop.py` 的翻譯迴圈中：

1. 迴圈每次處理的 key，先查 `get_from_cache()`
2. 命中則直接 append 到結果；未命中才送 LLM
3. LLM 回傳後，`add_to_cache()` 寫入 session_new_entries
4. 整批完成後 `save_translation_cache()` 一次性寫入磁碟

---

## 注意事項

- **永久保存**：cache 不會過期，設計上永久保留所有翻譯記錄
- **Thread-safe**：寫入操作透過 `cache_lock`（`RLock`）保護；讀取 API 直接存取 shared dict，可在 Flet UI 的背景執行緒中使用
- **延遲寫入**：翻譯結果先寫記憶體（`session_new_entries`），session 結束才 flush 磁碟，避免大量小寫入
- **效能監控**：`CacheMetrics` 追蹤 hit/miss/load_ms/save_ms，另有 PR66-A 專門對此做過優化
