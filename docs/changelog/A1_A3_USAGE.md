# A1 & A3 功能使用說明

本文件說明如何使用新增的 **A1 統一錯誤處理** 和 **A3 快取搜尋系統**。

---

## 📋 更新內容總覽

### A1: 統一錯誤處理機制 ✅
**檔案**: `translation_tool/utils/exceptions.py`

**功能**:
- 統一的異常類別（TranslationError, APIError, RateLimitError等）
- 自動化錯誤處理裝飾器（支援自動重試）
- 錯誤自動記錄到日誌檔案（`logs/errors_YYYY-MM-DD.log`）

### A3: 快取搜尋系統 ✅
**檔案**: `translation_tool/utils/cache_search.py`, `cache_manager.py`（新增函式）

**功能**:
- 全文搜尋（使用 SQLite FTS5）
- 模糊比對（相似度計算）
- 相似詞推薦
- **不實作快取過期**（所有快取永久保留）

---

## 🔥 A1: 統一錯誤處理

### 基本使用

#### 1. 拋出自訂異常

```python
from translation_tool.utils.exceptions import (
    TranslationError,
    APIError,
    RateLimitError,
    FileFormatError
)

# 基本異常
raise TranslationError("翻譯失敗")

# 帶上下文的異常
raise TranslationError("翻譯失敗", context={'file': 'test.json', 'line': 42})

# API 限流
raise RateLimitError(retry_after=600, api="gemini")

# 檔案格式錯誤
raise FileFormatError("JSON 格式錯誤", context={'file': 'test.json'})
```

#### 2. 使用錯誤處理裝飾器

```python
from translation_tool.utils.exceptions import handle_translation_errors
from translation_tool.utils.log_unit import log_error

@handle_translation_errors(log_func=log_error, auto_retry=True, max_retries=3)
def translate_batch(batch):
    """翻譯批次（自動處理限流錯誤）"""
    # ...翻譯邏輯
    if rate_limited:
        raise RateLimitError(retry_after=60)  # 裝飾器會自動等待並重試
    
    return result
```

**裝飾器參數**:
- `log_func`: 日誌函式（接收字串參數）
- `auto_retry`: 是否自動重試（針對 RateLimitError 和 OverloadError）
- `max_retries`: 最大重試次數（預設 3）

#### 3. 使用驗證函式

```python
from translation_tool.utils.exceptions import raise_if_invalid_json, raise_if_empty

# 檢查 JSON 必要欄位
data = {'name': 'test'}
raise_if_invalid_json(data, required_keys=['name', 'value'], source='test.json')
# 拋出 FileFormatError: JSON 格式錯誤：缺少必要欄位 ['value']

# 檢查空值
raise_if_empty(user_input, "使用者輸入")
# 如果空值，拋出 TranslationError: 使用者輸入 不可為空
```

### 錯誤日誌

所有透過裝飾器處理的錯誤會自動記錄到：
```
logs/errors_YYYY-MM-DD.log
```

日誌格式：
```
================================================================================
[2026-02-13 23:00:00] 錯誤發生於: translate_batch
錯誤類型: RateLimitError
錯誤訊息: API 限流，建議 600 秒後重試
錯誤上下文: {'retry_after': 600, 'api': 'gemini'}

堆疊追蹤:
Traceback (most recent call last):
  ...
================================================================================
```

---

## 🔍 A3: 快取搜尋系統

### 1. 搜尋快取

```python
from translation_tool.utils.cache_manager import search_cache

# 基本搜尋
results = search_cache("苦力怕", limit=50)

# 限定快取類型
results = search_cache("苦力怕", cache_type="lang", limit=20)

# 不使用模糊比對（更快但不會重新評分）
results = search_cache("苦力怕", use_fuzzy=False)

# 檢查結果
for r in results:
    print(f"{r['src']} → {r['dst']}")
    print(f"  類型: {r['type']}, 分數: {r.get('score', 0):.2f}")
```

**回傳結果格式**:
```python
[
    {
        'src': 'Creeper',
        'dst': '苦力怕',
        'type': 'lang',
        'mod': '',  # 模組名稱（如果有）
        'path': '',  # 檔案路徑（如果有）
        'score': 0.95,  # 相關度分數（0~1）
        'combined_score': 0.92  # 綜合分數（如果啟用 fuzzy）
    },
    ...
]
```

### 2. 尋找相似翻譯

```python
from translation_tool.utils.cache_manager import find_similar_translations

# 找出相似的翻譯
similar = find_similar_translations(
    "Creeper",
    cache_type="lang",
    threshold=0.6,  # 相似度門檻（0~1）
    limit=20
)

for item in similar:
    print(f"{item['src']} → {item['dst']}")
    print(f"  相似度: {item['similarity']:.2f}")
```

### 3. 重建搜尋索引

當快取更新後，需要重建搜尋索引：

```python
from translation_tool.utils.cache_manager import rebuild_search_index

# 重建索引（從記憶體快取）
rebuild_search_index()
```

**何時需要重建索引？**
- 第一次使用搜尋功能時
- 大量新增/修改快取後
- 搜尋結果不符預期時

**注意**: 
- 重建索引可能需要幾秒鐘（取決於快取大小）
- 重建期間不影響翻譯功能
- 索引檔案：`快取資料夾/search_index.db`

### 4. 直接使用搜尋引擎（進階）

```python
from translation_tool.utils.cache_search import CacheSearchEngine, FuzzyMatcher

# 建立搜尋引擎
with CacheSearchEngine(db_path="cache/search_index.db") as engine:
    
    # 加入單筆索引
    engine.index_cache_entry({
        'src': 'Hello',
        'dst': '你好',
        'mod': 'test',
        'path': 'lang/zh_tw.json',
        'type': 'lang'
    })
    
    # 批次加入索引
    entries = [
        {'src': 'World', 'dst': '世界', 'type': 'lang'},
        {'src': 'Game', 'dst': '遊戲', 'type': 'lang'},
    ]
    engine.index_batch(entries)
    
    # 搜尋
    results = engine.search("你好", limit=10)
    
    # 清空索引（慎用！）
    # engine.clear_index()

# 模糊比對器
matcher = FuzzyMatcher()

# 計算相似度
similarity = matcher.similarity("Creeper", "Creepr")  # 0.85
similarity = matcher.similarity("Creeper", "Zombie")  # 0.0

# 找出相似候選項
candidates = [
    {'src': 'Creeper', 'dst': '苦力怕'},
    {'src': 'Creep', 'dst': '爬行'},
]
similar = matcher.find_similar("Creeper", candidates, threshold=0.6, key_field='src')
```

---

## 🧪 測試

執行測試檔案：

```bash
python test_a1_a3_features.py
```

測試包含：
- ✅ A1 所有異常類別
- ✅ A1 錯誤處理裝飾器
- ✅ A1 驗證函式
- ✅ A3 搜尋引擎基本功能
- ✅ A3 模糊比對
- ✅ cache_manager 整合

---

## 📝 整合到現有程式碼

### 範例 1: 在 services.py 中使用錯誤處理

```python
# app/services.py
from translation_tool.utils.exceptions import (
    handle_translation_errors,
    APIError,
    FileFormatError
)
from translation_tool.utils.log_unit import log_error, log_info

@handle_translation_errors(log_func=log_error, auto_retry=True)
def run_translation_service(input_path, output_path):
    """執行翻譯任務（自動處理錯誤）"""
    
    # 驗證輸入
    if not input_path:
        raise FileFormatError("輸入路徑不可為空")
    
    # 呼叫翻譯邏輯
    try:
        result = translate_files(input_path, output_path)
        log_info(f"翻譯完成: {result}")
        return result
    except Exception as e:
        # 包裝成自訂異常
        raise APIError(f"翻譯失敗: {str(e)}", context={'input': input_path})
```

### 範例 2: 在 CacheView 中整合搜尋

```python
# app/views/cache_view.py
from translation_tool.utils.cache_manager import search_cache, rebuild_search_index

class CacheView(ft.Column):
    
    def _on_search_button_click(self, e):
        """搜尋按鈕點擊事件"""
        query = self.search_input.value
        cache_type = self.cache_type_dropdown.value
        
        # 執行搜尋
        results = search_cache(
            query,
            cache_type=cache_type if cache_type != "ALL" else None,
            limit=50
        )
        
        # 顯示結果
        self.results_list.controls.clear()
        for r in results:
            self.results_list.controls.append(
                ft.Text(f"{r['src']} → {r['dst']} (分數: {r.get('score', 0):.2f})")
            )
        
        self.page.update()
    
    def _on_rebuild_index_click(self, e):
        """重建索引按鈕"""
        self.status_text.value = "正在重建索引..."
        self.page.update()
        
        rebuild_search_index()
        
        self.status_text.value = "索引重建完成"
        self.page.update()
```

---

## ⚠️ 注意事項

### A1 錯誤處理
1. **日誌檔案大小**: 錯誤日誌按日分檔，建議定期清理舊日誌
2. **重試次數**: 預設最多重試 3 次，可根據需求調整
3. **裝飾器順序**: 如果同時使用多個裝飾器，`@handle_translation_errors` 應該放在最外層

### A3 快取搜尋
1. **索引檔案**: 約佔快取大小的 10~20%，需要額外磁碟空間
2. **索引更新**: 新增快取後不會自動更新索引，需手動呼叫 `rebuild_search_index()`
3. **FTS5 支援**: 需要 SQLite 3.9.0+，如果版本過舊會降級使用基本 LIKE 搜尋
4. **永久保留**: 快取不會過期，請確保有足夠儲存空間

---

## 🔧 常見問題

### Q: 搜尋找不到結果？
**A**: 請先執行 `rebuild_search_index()` 建立索引。

### Q: 錯誤日誌檔案太大？
**A**: 可以手動刪除 `logs/` 目錄下的舊日誌檔案。

### Q: 搜尋速度慢？
**A**: 
1. 檢查是否啟用 FTS5（SQLite 版本）
2. 降低 `limit` 參數
3. 關閉 `use_fuzzy`（模糊比對較耗時）

### Q: 如何在 UI 中顯示錯誤？
**A**: 使用裝飾器的 `log_func` 參數傳入 UI 的日誌函式：
```python
@handle_translation_errors(log_func=self._append_log)
def my_function():
    # ...
```

---

## 📚 相關檔案

- `translation_tool/utils/exceptions.py` - 異常定義與裝飾器
- `translation_tool/utils/cache_search.py` - 搜尋引擎
- `translation_tool/utils/cache_manager.py` - 快取管理（含搜尋整合）
- `test_a1_a3_features.py` - 測試檔案
- `logs/errors_YYYY-MM-DD.log` - 錯誤日誌
- `快取資料夾/search_index.db` - 搜尋索引資料庫

---

**版本**: 1.0  
**最後更新**: 2026-02-13  
**作者**: Claw (OpenClaw AI Assistant)
