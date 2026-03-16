# cache_search.py 效能優化記錄

**優化日期**：2026-03-16  
**優化目標**：搜尋索引重建時間從 49 秒降至 4 秒

---

## 問題背景

### 現象
- 重建 101,899 條翻譯索引需要約 49 秒
- UI 啟動時會卡住將近 1 分鐘

### 瓶頸分析
1. **並行處理** - 101,899 筆資料逐筆處理耗時
2. **SQLite 寫入** - 每 5000 筆一批次，共 21 批次
3. **Windows 檔案鎖** - `os.replace()` 重試 10 次，每次失敗後指數退避等待

---

## 優化 1：並行處理 Metadata 建立

### 修改點
```python
# 新增引入
from concurrent.futures import ThreadPoolExecutor, as_completed

# 優化 build_index_entries() 函數
# 使用 4 執行緒並行處理每筆資料的 metadata 建立
def build_index_entries(cache_type: str, cache_dict: Dict[str, Any]):
    items = [(key, entry) for key, entry in cache_dict.items() if isinstance(entry, dict)]
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_build_single_entry, cache_type, key, entry): idx
            for idx, (key, entry) in enumerate(items)
        }
        results = [None] * len(items)
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:
                pass
    
    return [r for r in results if r is not None]
```

### 結果
- `build_index_entries(lang)`：76148 筆 → 2.6 秒
- CPU 多核心利用率提升

---

## 優化 2：改用直接寫入，避開 Windows 檔案鎖

### 原因
原本使用 tmp 檔案 + `os.replace()` 策略：
1. 建立 tmp 資料庫 → 寫入
2. 嘗試 `os.replace(tmp, 正式檔)` → 失敗（被鎖）
3. 重試 10 次，等待 1+2+3+...+10 = 55 秒

### 修改點
```python
def _do_rebuild_search_index(self, db_path, cache_types, cache_state):
    # 1. 先關閉舊引擎
    with self._lock:
        if self._engine is not None:
            self._engine.close()
            self._engine = None
    
    # 2. 刪除 WAL/SHM 檔案（SQLite 暫存檔）
    for suffix in ["-wal", "-shm"]:
        wal_file = db_path.with_name(db_path.name + suffix)
        if wal_file.exists():
            wal_file.unlink(missing_ok=True)
    
    # 3. 刪除舊資料庫
    if db_path.exists():
        db_path.unlink()
    
    # 4. 直接寫入新資料庫（不經過 tmp）
    tmp_engine = CacheSearchEngine(str(db_path))
    total_indexed = rebuild_from_cache_dicts(tmp_engine, cache_types, cache_state)
    
    # 5. 建立新引擎實例
    with self._lock:
        self._engine = CacheSearchEngine(str(db_path))
    
    return total_indexed
```

### 結果
- **49 秒 → 4 秒**（提升 91%）

---

## 優化 3：增加批次大小

### 修改點
```python
# 從 5000 改為 20000
def index_batch(self, entries: List[dict], batch_size: int = 20000):
```

### 結果
- 101,899 筆：21 批次 → 6 批次
- 減少迴圈次數，但主要時間已被 `build_index_entries` 佔據，效益有限

---

## 優化 4：改用 log_unit 日誌

### 修改點
```python
# 原本
import logging
logger = logging.getLogger(__name__)
logger.info("...")
logger.debug("...")

# 改為
from .log_unit import log_info, log_warning, log_debug
log_info("...")
log_debug("...")
```

### 好處
- 自動識別呼叫者模組名稱
- 支援 logging 原生格式化（`log_info("x=%s", x)`）
- stacklevel 修正，確保日誌顯示正確行號

---

## 最終結果

| 項目 | 優化前 | 優化後 |
|------|--------|--------|
| 總時間 | 49 秒 | 4 秒 |
| 減少 | - | 45 秒 (91%) |

### 日誌級別分布
| 等級 | 用途 |
|------|------|
| `log_info` | 偵測到舊表格重建 |
| `log_warning` | FTS5 初始化失敗 |
| `log_debug` | 效能時間（預設不顯示）|

---

## 未來優化方向（數據量大時）

當數據量達到 100 萬條以上時：
1. **停用部分 PRAGMA**（如 `synchronous = OFF`，但有損壞風險）
2. **考慮替代方案**：Whoosh、ElasticSearch、Meilisearch

---

## 檔案變更清單

- `translation_tool/utils/cache_search.py`

### 新增引入
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from .log_unit import log_info, log_warning, log_debug
```

### 函數變更
- `build_index_entries()` - 改為並行處理
- `_build_single_entry()` - 新增，單筆處理輔助函數
- `index_batch()` - batch_size 從 5000 改為 20000
- `_do_rebuild_search_index()` - 改為直接寫入，避開檔案鎖