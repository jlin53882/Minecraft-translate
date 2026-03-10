# Minecraft Translator Flet - 優化與改進分析報告 v2.0

**分析日期**: 2026-02-13  
**分析者**: Claw (OpenClaw AI Assistant)  
**專案位置**: `C:\Users\admin\Desktop\minecraft_translator_flet`  
**更新說明**: 整合更多網路研究資料，新增競品深度分析與市場定位建議

---

## 📋 執行摘要

本報告針對 `minecraft_translator_flet` 專案進行全面分析，包含：
1. 當前功能與架構評估
2. 競品深度分析（CFPA、RPMTW、釘宮翻譯組）
3. 網路上翻譯工具的核心痛點
4. 優化建議（程式碼、效能、流程）
5. UI/UX 改進方向與設計系統
6. 建議新增的功能與實作優先順序
7. 市場定位與差異化策略

---

## 🎯 當前專案功能概覽

### 核心功能矩陣

| 功能模組 | 當前狀態 | 主要能力 | 技術亮點 |
|---------|---------|---------|---------|
| **翻譯工作台** | ✅ 完整 | FTB/KubeJS/Markdown 翻譯 | 多流程整合 |
| **JAR 提取器** | ✅ 完整 | 自動提取語言檔 | 版本號清理、路徑標準化 |
| **機器翻譯** | ✅ 完整 | Gemini API 批次翻譯 | 動態批次調整、多 key 輪替 |
| **檔案合併** | ✅ 完整 | 多 ZIP 合併、簡繁轉換 | zh_cn→zh_tw、英文提取 |
| **簡繁規則** | ✅ 完整 | 自訂詞彙替換 | 匯入/匯出 |
| **快取管理** | 🚧 開發中 | 翻譯快取統計與查詢 | 查詢功能未完成 |
| **品管工具** | ✅ 完整 | 未翻譯檢查、簡繁比對 | 自動化品管 |

### 架構優勢 ✅
- **分層清晰**: `app/` (UI) + `translation_tool/` (核心邏輯) 職責分明
- **模組化設計**: 每個功能獨立成 view，易於擴展
- **非同步處理**: 使用 `TaskSession` 避免 UI 凍結
- **錯誤處理**: LM 翻譯有完整的重試與批次縮減機制（自動應對 API 限制）
- **配置化**: 使用 `config.json` 集中管理參數，支援多模型
- **現代 UI**: 基於 Flet (Material Design 3)，支援深色/淺色模式

---

## 🔍 競品深度分析

### 競品 #1: CFPA (Chinese Formatting & Packing Assistant)
**官網**: https://cfpa.site/  
**GitHub**: https://github.com/CFPAOrg/Minecraft-Mod-Language-Package  
**定位**: 簡體中文翻譯專案（社群協作型）

#### 優勢
- ✅ **社群規模大**: 數千名貢獻者，覆蓋絕大部分熱門模組
- ✅ **協作機制完善**: 使用 Weblate 平台，支援線上協作、審核流程
- ✅ **自動更新**: i18nupdatemod 模組自動下載最新翻譯
- ✅ **資源豐富**: 提供詞典、Wiki、翻譯工具鏈結
- ✅ **持續維護**: GitHub Actions 自動打包，更新頻繁

#### 劣勢
- ❌ **僅限簡中**: 不支援繁體中文
- ❌ **依賴線上平台**: Weblate 需要網路連線
- ❌ **學習成本**: 新手需要學習 Weblate 使用方式
- ❌ **無離線工具**: 缺乏本地化的桌面應用

#### 對你專案的啟示
1. **借鑒其翻譯資源庫**：整合 Minecraft Wiki 譯名、CFPA 詞典查詢
2. **參考其協作流程**：實作簡易的審核機制
3. **避免其劣勢**：提供離線、繁體支援、零學習成本的桌面工具

---

### 競品 #2: RPMTW (RPG Maker Taiwan)
**GitHub**: https://github.com/RPMTW/ResourcePack-Mod-zh_tw  
**狀態**: ⚠️ 已於 2026/01/27 封存（read-only）

#### 曾經的優勢
- ✅ **繁體翻譯**: 支援 3128 個模組（人工翻譯）
- ✅ **遊戲內整合**: 模組提供宇宙通訊、快速回報錯誤等功能
- ✅ **自動更新**: 自動下載翻譯更新
- ✅ **機器翻譯備用**: 人工翻譯不足時可用機器翻譯

#### 停止維護原因（推測）
- ❌ **維護負擔大**: 巴哈論壇提到「個人因素到一半就斷炊了」
- ❌ **協作機制不足**: 依賴少數核心貢獻者
- ❌ **技術債務**: 更新流程複雜，難以持續

#### 對你專案的啟示
1. **自動化至上**: 減少人工介入，才能長期維護
2. **降低協作門檻**: 讓更多人能貢獻翻譯（而非只有核心團隊）
3. **市場機會**: RPMTW 停止後，繁體翻譯工具有空缺

---

### 競品 #3: 釘宮翻譯組 (ModsTranslationPack)
**Modrinth**: https://modrinth.com/resourcepack/modstranslationpack  
**定位**: 繁體中文翻譯資源包（持續更新）

#### 優勢
- ✅ **持續更新**: 支援 1.18 ~ 1.21.x
- ✅ **資源包形式**: 無需 Mod，直接使用
- ✅ **社群推薦**: 巴哈論壇用戶推薦優先使用

#### 劣勢
- ❌ **更新頻率未知**: 依賴釘宮翻譯組的維護速度
- ❌ **無工具支援**: 只有成品資源包，沒有翻譯工具
- ❌ **覆蓋範圍**: 未知是否覆蓋冷門模組

#### 對你專案的啟示
1. **互補定位**: 你的工具可以「產出」類似的資源包
2. **社群整合**: 可以整合釘宮翻譯組的翻譯（避免重複工作）
3. **工具化優勢**: 提供「製作翻譯包的工具」，而非「翻譯包本身」

---

### 競品 #4: Minecraft Mods Translator (by Maz-T)
**GitHub**: https://github.com/Maz-T/Minecraft-Mods-Translator  
**定位**: 本地翻譯工具（1.16+）

#### 優勢
- ✅ **本地化**: 離線使用
- ✅ **自動化**: 解包 JAR → 翻譯 → 重新打包

#### 劣勢
- ❌ **功能單一**: 主要處理 lang 檔案
- ❌ **UI 缺失**: 命令列工具，無圖形介面
- ❌ **翻譯來源**: 依賴外部翻譯 API 或人工輸入

#### 對你專案的啟示
1. **UI 是優勢**: Flet 圖形介面大幅降低使用門檻
2. **功能更全面**: 你已支援 Patchouli、FTB、KubeJS 等複雜格式
3. **翻譯整合**: 你的 Gemini API 整合比單純命令列更方便

---

### 競品總結表

| 工具 | 類型 | 簡/繁 | 協作 | 離線 | 自動化 | UI | 維護狀態 |
|------|------|-------|------|------|--------|----|----|
| **CFPA** | 社群平台 | 簡中 | ⭐⭐⭐⭐⭐ | ❌ | ⭐⭐⭐⭐ | Web | ✅ 活躍 |
| **RPMTW** | 模組+資源包 | 繁中 | ⭐⭐ | ✅ | ⭐⭐⭐ | 遊戲內 | ❌ 已封存 |
| **釘宮翻譯組** | 資源包 | 繁中 | ⭐ | ✅ | ⭐ | 無 | ✅ 活躍 |
| **Maz-T 工具** | CLI 工具 | 不限 | ❌ | ✅ | ⭐⭐⭐ | CLI | ⚠️ 不明 |
| **你的工具** | 桌面應用 | 繁中 | ⭐ | ✅ | ⭐⭐⭐⭐⭐ | Flet | ✅ 開發中 |

---

## 🚨 網路上的翻譯工具痛點（來自研究）

### 痛點 #1: **PR 回應時間不確定**
> "The time it takes for mod authors to accept translation PRs is uncertain, and there may not necessarily be someone to translate them."  
> — AutoTranslation Mod

**影響**: 
- 玩家等待翻譯更新時間過長
- 模組作者缺乏翻譯資源
- 翻譯者難以追蹤貢獻狀態

**你的解決方案**:
- ✅ **資源包形式**: 不需要等 PR，直接使用
- ✅ **本地化工具**: 玩家可以自己翻譯

---

### 痛點 #2: **缺乏自動化工具**
> "Seriously, no one has developed any well-made tool that avoids some things so that the translation doesn't break or has character limits per line..."  
> — Reddit /r/feedthebeast

**影響**:
- 手動提取/打包繁瑣
- 容易破壞格式（JSON、特殊符號）
- 無法批次處理大量模組

**你的解決方案**:
- ✅ **JAR 自動提取**: 一鍵提取所有語言檔
- ✅ **格式保護**: 自動保留 `§`, `%s`, `{...}` 等符號
- ✅ **批次處理**: 支援多模組同時處理

---

### 痛點 #3: **機器翻譯品質不穩定**
現有的遊戲內即時翻譯 Mod (如 AutoTranslation) 使用 Google Translate：
- 無法保留遊戲術語一致性
- 無法處理上下文（如 Patchouli 手冊的連貫性）
- 翻譯錯誤頻繁（如 "Minecraft" → "當個創世神"）

**你的解決方案**:
- ✅ **Gemini API**: 比 Google Translate 更理解上下文
- ✅ **System Prompt 優化**: 針對 Patchouli、lang 檔案定制 prompt
- ✅ **術語黑名單**: 跳過 "Minecraft"、"Discord" 等不應翻譯的詞
- 🚧 **建議新增**: 術語資料庫（確保 "Creeper" 統一翻成「苦力怕」）

---

### 痛點 #4: **簡繁轉換問題**
許多繁體翻譯是從簡體轉換而來，但：
- 詞彙差異大（例：「服务器」→「伺服器」vs「服務器」）
- 缺乏台灣用語資料庫
- 自動轉換工具不夠聰明

**你的解決方案**:
- ✅ **replace_rules.json**: 支援自訂簡繁替換規則
- ✅ **zh_cn → zh_tw 流程**: 自動轉換並補充缺失翻譯
- 🚧 **建議新增**: 台灣用語詞庫（遊戲術語、網路用語）

---

### 痛點 #5: **缺乏協作機制**
- 多人翻譯時缺乏衝突管理
- 無法追蹤誰翻譯了什麼
- 難以審查與校對

**你的解決方案**:
- ❌ **當前不支援**: 單人使用為主
- 🚧 **建議新增**: 簡易的版本控制、快照機制

---

### 痛點 #6: **學習成本高**
- Weblate、Crowdin 等平台需要學習
- 命令列工具不友善
- 缺乏視覺化介面

**你的解決方案**:
- ✅ **Flet UI**: 圖形介面，降低門檻
- ✅ **工作流程整合**: 一個工具完成全流程
- ✅ **即時日誌**: 清楚顯示執行狀態

---

## 💡 優化建議

### A. 程式碼與架構優化

#### A1. 統一錯誤處理機制 🔥 高優先
**現狀**: 每個 view 都有自己的錯誤處理邏輯  
**問題**: 
- 錯誤訊息格式不一致
- 難以追蹤錯誤來源
- 缺乏統一的錯誤回報機制

**建議**:
```python
# 新增 translation_tool/utils/exceptions.py
class TranslationError(Exception):
    """Base exception for translation errors"""
    def __init__(self, message: str, context: dict = None):
        self.message = message
        self.context = context or {}
        super().__init__(self.message)

class APIError(TranslationError):
    """API related errors"""
    pass

class RateLimitError(APIError):
    """Raised when API rate limit is hit"""
    def __init__(self, retry_after: int = 600):
        super().__init__(f"Rate limit exceeded, retry after {retry_after}s")
        self.retry_after = retry_after

class FileFormatError(TranslationError):
    """File format errors (JSON, lang, etc.)"""
    pass

# 統一的錯誤處理裝飾器
def handle_translation_errors(log_func=None):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except RateLimitError as e:
                if log_func:
                    log_func(f"⏱️ API 限流: {e.message}")
                # 自動切換 API key 或等待
                time.sleep(e.retry_after)
                return wrapper(*args, **kwargs)  # 重試
            except APIError as e:
                if log_func:
                    log_func(f"❌ API 錯誤: {e.message}")
                raise
            except TranslationError as e:
                if log_func:
                    log_func(f"⚠️ 翻譯錯誤: {e.message}")
                # 記錄到錯誤日誌檔
                log_error_to_file(e)
                raise
        return wrapper
    return decorator

# 使用範例
@handle_translation_errors(log_func=lambda msg: print(msg))
def translate_batch(batch):
    # ...翻譯邏輯
    pass
```

**效益**:
- 統一的錯誤格式
- 自動化的錯誤處理（重試、記錄）
- 更容易除錯

---

#### A2. 改進批次處理邏輯 🔥 高優先
**現狀**: `lm_translator_main.py` 的 while 迴圈複雜度高（200+ 行）  
**問題**:
- 難以理解流程
- 難以測試
- 難以擴展（例如加入新的 API 提供商）

**建議**: 使用狀態機模式
```python
# 新增 translation_tool/core/batch_manager.py
from enum import Enum, auto

class BatchState(Enum):
    INIT = auto()
    TRANSLATING = auto()
    RETRYING = auto()
    SHRINKING = auto()
    SUCCESS = auto()
    FAILED = auto()

class BatchManager:
    """管理批次翻譯的狀態與大小調整"""
    
    def __init__(self, initial_size: int, min_size: int, shrink_factor: float):
        self.batch_size = initial_size
        self.min_size = min_size
        self.shrink_factor = shrink_factor
        self.state = BatchState.INIT
        self.retry_count = 0
        self.max_retries = 3
    
    def shrink(self):
        """縮小批次大小"""
        new_size = max(int(self.batch_size * self.shrink_factor), self.min_size)
        if new_size == self.batch_size:
            # 已到最小值，無法再縮小
            return False
        self.batch_size = new_size
        self.state = BatchState.SHRINKING
        return True
    
    def should_retry(self, error: Exception) -> bool:
        """根據錯誤類型決定是否重試"""
        if isinstance(error, RateLimitError):
            return self.retry_count < self.max_retries
        elif isinstance(error, OverloadError):
            # 503 overload → 縮小批次
            return self.shrink()
        return False
    
    def record_success(self):
        """記錄成功，重置重試計數"""
        self.state = BatchState.SUCCESS
        self.retry_count = 0
    
    def record_failure(self, error: Exception):
        """記錄失敗"""
        self.retry_count += 1
        if not self.should_retry(error):
            self.state = BatchState.FAILED

# 在 lm_translator_main.py 使用
batch_mgr = BatchManager(initial_size=200, min_size=50, shrink_factor=0.5)

while data_remaining:
    batch = get_next_batch(batch_mgr.batch_size)
    
    try:
        result = translate_with_api(batch)
        batch_mgr.record_success()
        write_results(result)
    
    except Exception as e:
        batch_mgr.record_failure(e)
        
        if batch_mgr.state == BatchState.FAILED:
            log_error(f"批次處理失敗: {e}")
            break
        elif batch_mgr.state == BatchState.SHRINKING:
            log_warning(f"縮小批次至 {batch_mgr.batch_size}")
            continue
```

**效益**:
- 邏輯清晰，易於理解
- 可測試（每個狀態轉換可獨立測試）
- 易於擴展（新增狀態或策略）

---

#### A3. 快取系統改進 🔥 中優先
**現狀**: 快取管理功能齊全，但查詢功能還在開發中  

**建議 1: 全文搜尋**
```python
# 使用 SQLite FTS5 (Full-Text Search)
import sqlite3

class CacheSearchEngine:
    """快取全文搜尋引擎"""
    
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self._init_fts_table()
    
    def _init_fts_table(self):
        """建立 FTS5 虛擬表"""
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS cache_fts USING fts5(
                source_text,
                translated_text,
                mod_name,
                file_path,
                tokenize = 'unicode61'
            )
        """)
    
    def index_cache_entry(self, entry: dict):
        """將快取條目加入索引"""
        self.conn.execute("""
            INSERT INTO cache_fts (source_text, translated_text, mod_name, file_path)
            VALUES (?, ?, ?, ?)
        """, (entry['src'], entry['dst'], entry['mod'], entry['path']))
        self.conn.commit()
    
    def search(self, query: str, limit: int = 50) -> list:
        """搜尋快取（支援中英文、模糊比對）"""
        cursor = self.conn.execute("""
            SELECT source_text, translated_text, mod_name, file_path,
                   rank
            FROM cache_fts
            WHERE cache_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit))
        
        return [dict(zip(['src', 'dst', 'mod', 'path', 'score'], row))
                for row in cursor.fetchall()]
```

**建議 2: 相似詞推薦（Fuzzy Matching）**
```python
from difflib import SequenceMatcher

class FuzzyMatcher:
    """模糊比對器"""
    
    @staticmethod
    def similarity(a: str, b: str) -> float:
        """計算兩個字串的相似度 (0~1)"""
        return SequenceMatcher(None, a, b).ratio()
    
    def find_similar(self, query: str, candidates: list, threshold: float = 0.6) -> list:
        """找出相似的候選項"""
        results = []
        for candidate in candidates:
            score = self.similarity(query, candidate['src'])
            if score >= threshold:
                results.append({**candidate, 'similarity': score})
        
        return sorted(results, key=lambda x: x['similarity'], reverse=True)
```

**建議 3: 快取過期機制**
```python
from datetime import datetime, timedelta

class CacheExpiryManager:
    """快取過期管理"""
    
    def __init__(self, ttl_days: int = 90):
        self.ttl = timedelta(days=ttl_days)
    
    def is_expired(self, cache_entry: dict) -> bool:
        """檢查快取是否過期"""
        created_at = datetime.fromisoformat(cache_entry.get('created_at'))
        return datetime.now() - created_at > self.ttl
    
    def mark_for_review(self, entry: dict):
        """標記為需要審查（而非直接刪除）"""
        entry['status'] = 'review_needed'
        entry['reason'] = 'expired'
```

**效益**:
- 快速找到歷史翻譯
- 減少重複翻譯
- 避免舊翻譯影響新內容

---

#### A4. 多執行緒安全性 🔥 中優先
**現狀**: 使用 `threading` 但未見明顯的 lock 機制  
**潛在問題**:
- 多個 view 同時寫入日誌可能衝突
- 快取讀寫可能競爭
- 配置檔讀取可能不一致

**建議**:
```python
import threading
from queue import Queue

# 1. 日誌寫入使用 Queue（執行緒安全）
class ThreadSafeLogger:
    """執行緒安全的日誌記錄器"""
    
    def __init__(self):
        self.log_queue = Queue()
        self.worker = threading.Thread(target=self._process_logs, daemon=True)
        self.worker.start()
    
    def _process_logs(self):
        """背景執行緒處理日誌寫入"""
        while True:
            log_entry = self.log_queue.get()
            if log_entry is None:  # 停止信號
                break
            
            # 實際寫入日誌檔
            with open("app.log", "a", encoding="utf-8") as f:
                f.write(log_entry + "\n")
    
    def log(self, message: str):
        """加入日誌到佇列"""
        self.log_queue.put(message)
    
    def stop(self):
        """停止日誌處理"""
        self.log_queue.put(None)
        self.worker.join()

# 2. 配置檔讀取使用 RLock（讀多寫少）
class ThreadSafeConfig:
    """執行緒安全的配置管理"""
    
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._config = {}
        self._lock = threading.RLock()
        self.reload()
    
    def reload(self):
        """重新載入配置"""
        with self._lock:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
    
    def get(self, key: str, default=None):
        """執行緒安全的讀取"""
        with self._lock:
            return self._config.get(key, default)
    
    def set(self, key: str, value):
        """執行緒安全的寫入"""
        with self._lock:
            self._config[key] = value
            self._save()
    
    def _save(self):
        """儲存配置（必須在 lock 內呼叫）"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)
```

**效益**:
- 避免競爭條件
- 防止資料損壞
- 提高穩定性

---

### B. 效能優化

#### B1. JAR 提取效能 🔥 中優先
**現狀**: `jar_processor.py` 使用 `concurrent.futures` 但未見明顯的記憶體管理  
**潛在問題**:
- 大型 JAR 檔（100MB+）可能佔用過多記憶體
- 同時處理多個 JAR 可能導致記憶體不足

**建議**:
```python
import zipfile
from contextlib import contextmanager

@contextmanager
def open_jar_streaming(jar_path: str):
    """串流式讀取 JAR 檔案"""
    zf = zipfile.ZipFile(jar_path, 'r')
    try:
        yield zf
    finally:
        zf.close()

def extract_from_jar_optimized(jar_path: str, output_root: str, target_regex):
    """優化的 JAR 提取（記憶體友善）"""
    extracted_count = 0
    
    with open_jar_streaming(jar_path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            
            normalized_path = member.filename.replace('\\', '/')
            if not target_regex.search(normalized_path):
                continue
            
            # 串流式讀取（不將整個檔案載入記憶體）
            with zf.open(member) as source:
                output_path = os.path.join(output_root, normalized_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                
                # 分塊讀取與寫入（預設 8KB）
                with open(output_path, 'wb') as target:
                    while chunk := source.read(8192):
                        target.write(chunk)
            
            extracted_count += 1
    
    return extracted_count

# 限制並行數量（避免開太多檔案）
from concurrent.futures import ThreadPoolExecutor

def process_jars_with_limit(jar_files: list, max_workers: int = 4):
    """限制並行數量的 JAR 處理"""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(extract_from_jar_optimized, jar)
                   for jar in jar_files]
        
        for future in futures:
            result = future.result()
            yield result
```

**效益**:
- 記憶體使用穩定
- 可處理超大 JAR 檔
- 避免 OOM (Out of Memory)

---

#### B2. UI 響應速度 🔥 高優先
**現狀**: Flet UI 有時會在大量日誌時變慢  
**問題**:
- `log_view` 有數千條日誌時滾動卡頓
- 每條日誌即時更新會頻繁觸發重繪

**建議 1: 虛擬化列表**
```python
# 使用 Flet 的虛擬化功能（僅渲染可見項目）
class VirtualizedLogView(ft.UserControl):
    """虛擬化的日誌檢視（只渲染可見部分）"""
    
    def __init__(self, max_visible: int = 100):
        super().__init__()
        self.logs = []
        self.max_visible = max_visible
        self.scroll_position = 0
    
    def add_log(self, message: str):
        self.logs.append(message)
        
        # 只保留最近 1000 條（避免無限增長）
        if len(self.logs) > 1000:
            self.logs = self.logs[-1000:]
        
        # 批次更新（每 10 條才重繪一次）
        if len(self.logs) % 10 == 0:
            self.update()
    
    def build(self):
        # 只渲染可見範圍的日誌
        visible_logs = self.logs[-self.max_visible:]
        
        return ft.ListView(
            controls=[ft.Text(log) for log in visible_logs],
            auto_scroll=True,
            height=400,
        )
```

**建議 2: 批次更新**
```python
import asyncio

class BatchedLogger:
    """批次更新的日誌器"""
    
    def __init__(self, log_view: ft.ListView, batch_interval: float = 0.5):
        self.log_view = log_view
        self.pending_logs = []
        self.batch_interval = batch_interval
        self.timer = None
    
    def add_log(self, message: str):
        """加入日誌到待處理佇列"""
        self.pending_logs.append(message)
        
        # 啟動計時器（批次更新）
        if self.timer is None:
            self.timer = asyncio.create_task(self._flush_after_delay())
    
    async def _flush_after_delay(self):
        """延遲後批次更新"""
        await asyncio.sleep(self.batch_interval)
        
        # 一次性加入所有待處理的日誌
        for log in self.pending_logs:
            self.log_view.controls.append(ft.Text(log))
        
        self.pending_logs.clear()
        self.log_view.update()
        self.timer = None
```

**效益**:
- UI 流暢度大幅提升
- 記憶體使用降低
- 更好的使用者體驗

---

#### B3. API 請求優化 🔥 高優先
**現狀**: 遇到 429 rate limit 會等待 600 秒  
**問題**:
- 等待時間過長，使用者體驗差
- 無法預測何時會到達限制
- 缺乏多 API 提供商支援

**建議 1: 預測性限流**
```python
from collections import deque
from datetime import datetime, timedelta

class PredictiveRateLimiter:
    """預測性限流器"""
    
    def __init__(self, rpm_limit: int = 60):
        self.rpm_limit = rpm_limit
        self.request_history = deque()  # 最近的請求時間戳
        self.window = timedelta(minutes=1)
    
    def can_request(self) -> bool:
        """檢查是否可以發送請求"""
        now = datetime.now()
        
        # 清除超過時間窗口的請求
        while self.request_history and \
              now - self.request_history[0] > self.window:
            self.request_history.popleft()
        
        return len(self.request_history) < self.rpm_limit
    
    def wait_time(self) -> float:
        """計算需要等待的時間（秒）"""
        if self.can_request():
            return 0.0
        
        # 計算最舊的請求何時過期
        oldest = self.request_history[0]
        wait_until = oldest + self.window
        return (wait_until - datetime.now()).total_seconds()
    
    def record_request(self):
        """記錄一次請求"""
        self.request_history.append(datetime.now())
```

**建議 2: API 健康度監控**
```python
class APIHealthMonitor:
    """API 健康度監控"""
    
    def __init__(self):
        self.stats = {}  # key → {success: int, failure: int, avg_latency: float}
    
    def record_success(self, api_key: str, latency: float):
        """記錄成功請求"""
        if api_key not in self.stats:
            self.stats[api_key] = {'success': 0, 'failure': 0, 'latencies': []}
        
        self.stats[api_key]['success'] += 1
        self.stats[api_key]['latencies'].append(latency)
    
    def record_failure(self, api_key: str):
        """記錄失敗請求"""
        if api_key not in self.stats:
            self.stats[api_key] = {'success': 0, 'failure': 0, 'latencies': []}
        
        self.stats[api_key]['failure'] += 1
    
    def get_health_score(self, api_key: str) -> float:
        """計算健康度分數 (0~1)"""
        if api_key not in self.stats:
            return 1.0
        
        stats = self.stats[api_key]
        total = stats['success'] + stats['failure']
        
        if total == 0:
            return 1.0
        
        success_rate = stats['success'] / total
        
        # 考慮延遲（低延遲加分）
        if stats['latencies']:
            avg_latency = sum(stats['latencies']) / len(stats['latencies'])
            latency_penalty = min(avg_latency / 10.0, 0.5)  # 最多扣 0.5 分
        else:
            latency_penalty = 0.0
        
        return max(success_rate - latency_penalty, 0.0)
    
    def get_best_key(self) -> str:
        """取得目前最佳的 API key"""
        if not self.stats:
            return None
        
        return max(self.stats.keys(), key=self.get_health_score)
```

**建議 3: 支援多 API 提供商**
```python
from abc import ABC, abstractmethod

class TranslationProvider(ABC):
    """翻譯提供商抽象介面"""
    
    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        pass

class GeminiProvider(TranslationProvider):
    def translate(self, text, source_lang, target_lang):
        # Gemini API 實作
        pass
    
    def get_name(self):
        return "Gemini"

class DeepLProvider(TranslationProvider):
    def translate(self, text, source_lang, target_lang):
        # DeepL API 實作
        pass
    
    def get_name(self):
        return "DeepL"

class ClaudeProvider(TranslationProvider):
    def translate(self, text, source_lang, target_lang):
        # Claude API 實作
        pass
    
    def get_name(self):
        return "Claude"

class ProviderRouter:
    """智慧路由器（根據健康度選擇提供商）"""
    
    def __init__(self, providers: list[TranslationProvider]):
        self.providers = providers
        self.monitor = APIHealthMonitor()
    
    def translate_with_fallback(self, text, source_lang, target_lang):
        """嘗試所有提供商直到成功"""
        for provider in self.providers:
            try:
                start = time.time()
                result = provider.translate(text, source_lang, target_lang)
                latency = time.time() - start
                
                self.monitor.record_success(provider.get_name(), latency)
                return result
            
            except Exception as e:
                self.monitor.record_failure(provider.get_name())
                continue
        
        raise Exception("All providers failed")
```

**效益**:
- 減少等待時間
- 提高成功率
- 降低 API 成本

---

## 🎨 UI/UX 改進建議

### UI1. 設計系統統一化 🔥 高優先

#### 問題
- 各 view 風格不完全一致
- 缺乏明確的視覺層級
- 部分元件樣式硬編碼

#### 解決方案: 建立設計系統
```python
# 新增 app/design_system.py
import flet as ft

class DesignTokens:
    """設計 token（顏色、間距、字體）"""
    
    # 顏色
    class Colors:
        PRIMARY = ft.Colors.INDIGO
        SUCCESS = ft.Colors.GREEN_700
        WARNING = ft.Colors.ORANGE_700
        ERROR = ft.Colors.RED_700
        INFO = ft.Colors.BLUE_700
        
        SURFACE = ft.Colors.SURFACE
        BACKGROUND = ft.Colors.SURFACE_VARIANT
        OUTLINE = ft.Colors.OUTLINE_VARIANT
    
    # 間距
    class Spacing:
        XS = 4
        SM = 8
        MD = 12
        LG = 16
        XL = 20
        XXL = 24
    
    # 圓角
    class Radius:
        SM = 6
        MD = 10
        LG = 12
        XL = 15
    
    # 字體
    class Typography:
        H1 = 22
        H2 = 18
        H3 = 16
        BODY = 14
        CAPTION = 12

class Components:
    """標準元件庫"""
    
    @staticmethod
    def card(title: str, icon: str, content: ft.Control, **kwargs) -> ft.Container:
        """標準卡片元件"""
        return ft.Container(
            padding=DesignTokens.Spacing.LG,
            bgcolor=DesignTokens.Colors.SURFACE,
            border_radius=DesignTokens.Radius.LG,
            border=ft.border.all(1, DesignTokens.Colors.OUTLINE),
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon, color=DesignTokens.Colors.PRIMARY),
                    ft.Text(title, size=DesignTokens.Typography.H3, weight=ft.FontWeight.BOLD),
                ], spacing=DesignTokens.Spacing.SM),
                ft.Divider(height=1),
                content,
            ], spacing=DesignTokens.Spacing.MD),
            **kwargs
        )
    
    @staticmethod
    def status_chip(label: str, status: str) -> ft.Chip:
        """狀態標籤（成功/警告/錯誤）"""
        colors = {
            'success': DesignTokens.Colors.SUCCESS,
            'warning': DesignTokens.Colors.WARNING,
            'error': DesignTokens.Colors.ERROR,
            'info': DesignTokens.Colors.INFO,
        }
        
        return ft.Chip(
            label=ft.Text(label),
            bgcolor=colors.get(status, ft.Colors.GREY_200),
        )
    
    @staticmethod
    def primary_button(text: str, icon: str, on_click) -> ft.ElevatedButton:
        """主要按鈕"""
        return ft.ElevatedButton(
            text,
            icon=icon,
            on_click=on_click,
            style=ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=DesignTokens.Colors.PRIMARY,
                shape=ft.RoundedRectangleBorder(radius=DesignTokens.Radius.SM),
                padding=DesignTokens.Spacing.LG,
            ),
        )
    
    @staticmethod
    def secondary_button(text: str, icon: str, on_click) -> ft.OutlinedButton:
        """次要按鈕"""
        return ft.OutlinedButton(
            text,
            icon=icon,
            on_click=on_click,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=DesignTokens.Radius.SM),
                padding=DesignTokens.Spacing.LG,
            ),
        )

# 使用範例
card = Components.card(
    title="翻譯設定",
    icon=ft.Icons.SETTINGS,
    content=ft.Column([
        Components.status_chip("執行中", "info"),
        Components.primary_button("開始翻譯", ft.Icons.PLAY_ARROW, lambda e: start_translation()),
    ])
)
```

**效益**:
- 視覺一致性
- 易於維護（修改一處即可）
- 符合 Material Design 3 規範

---

### UI2. 改進現有頁面

#### 翻譯工作台 (translation_view.py) 🔥 中優先

**新增功能 1: 最近使用的路徑**
```python
class RecentPathsManager:
    """最近使用路徑管理"""
    
    def __init__(self, max_recent: int = 5):
        self.max_recent = max_recent
        self.recent_paths = []
        self._load()
    
    def add(self, path: str):
        """加入路徑（自動去重並排序）"""
        if path in self.recent_paths:
            self.recent_paths.remove(path)
        
        self.recent_paths.insert(0, path)
        self.recent_paths = self.recent_paths[:self.max_recent]
        self._save()
    
    def get_dropdown_options(self) -> list[ft.dropdown.Option]:
        """取得下拉選單選項"""
        return [ft.dropdown.Option(path) for path in self.recent_paths]
    
    def _load(self):
        """從檔案載入"""
        try:
            with open('.recent_paths.json', 'r') as f:
                self.recent_paths = json.load(f)
        except:
            pass
    
    def _save(self):
        """儲存到檔案"""
        with open('.recent_paths.json', 'w') as f:
            json.dump(self.recent_paths, f)

# 在 UI 中使用
recent_mgr = RecentPathsManager()

input_path_dropdown = ft.Dropdown(
    label="輸入路徑（或選擇最近使用）",
    options=recent_mgr.get_dropdown_options(),
    on_change=lambda e: input_path_field.value = e.control.value
)
```

**新增功能 2: 翻譯進度儀表板**
```python
class TranslationDashboard(ft.UserControl):
    """翻譯進度儀表板"""
    
    def __init__(self):
        super().__init__()
        self.total_items = 0
        self.completed_items = 0
        self.start_time = None
    
    def start(self, total: int):
        """開始翻譯"""
        self.total_items = total
        self.completed_items = 0
        self.start_time = time.time()
    
    def update_progress(self, completed: int):
        """更新進度"""
        self.completed_items = completed
        self.update()
    
    def _estimate_remaining_time(self) -> str:
        """估算剩餘時間"""
        if self.completed_items == 0:
            return "計算中..."
        
        elapsed = time.time() - self.start_time
        rate = self.completed_items / elapsed  # items/sec
        remaining = self.total_items - self.completed_items
        
        eta_seconds = int(remaining / rate)
        
        if eta_seconds < 60:
            return f"{eta_seconds} 秒"
        elif eta_seconds < 3600:
            return f"{eta_seconds // 60} 分鐘"
        else:
            return f"{eta_seconds // 3600} 小時 {(eta_seconds % 3600) // 60} 分鐘"
    
    def build(self):
        progress_pct = (self.completed_items / self.total_items * 100) if self.total_items > 0 else 0
        
        return ft.Container(
            padding=16,
            bgcolor=ft.Colors.SURFACE,
            border_radius=12,
            content=ft.Column([
                ft.Text("翻譯進度", size=16, weight=ft.FontWeight.BOLD),
                
                # 進度條
                ft.ProgressBar(value=progress_pct / 100, height=12, color=ft.Colors.BLUE),
                
                # 統計資訊
                ft.Row([
                    ft.Text(f"{self.completed_items}/{self.total_items} 條目", size=14),
                    ft.Text(f"{progress_pct:.1f}%", size=14, weight=ft.FontWeight.BOLD),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                
                # 時間資訊
                ft.Row([
                    ft.Icon(ft.Icons.SCHEDULE, size=16),
                    ft.Text(f"預估剩餘: {self._estimate_remaining_time()}", size=12),
                ], spacing=4),
            ], spacing=8)
        )
```

**新增功能 3: 批次匯入**
```python
def import_multiple_zips(self, e: ft.FilePickerResultEvent):
    """批次匯入多個 ZIP"""
    if not e.files:
        return
    
    # 顯示匯入清單
    import_list = ft.Column([
        ft.Row([
            ft.Icon(ft.Icons.ARCHIVE, size=20),
            ft.Text(f.name),
            ft.IconButton(
                icon=ft.Icons.DELETE,
                on_click=lambda _: self._remove_from_import(f.path)
            )
        ])
        for f in e.files
    ])
    
    # 顯示對話框確認
    dialog = ft.AlertDialog(
        title=ft.Text("批次匯入確認"),
        content=ft.Container(
            content=import_list,
            height=300,
        ),
        actions=[
            ft.TextButton("取消", on_click=lambda _: dialog.close()),
            ft.ElevatedButton("開始處理", on_click=lambda _: self._start_batch_import(e.files)),
        ]
    )
    
    self.page.dialog = dialog
    dialog.open = True
    self.page.update()
```

---

#### JAR 提取器 (extractor_view.py) 🔥 中優先

**新增功能 1: 預覽模式**
```python
def preview_extraction(self, jar_files: list):
    """預覽將要提取的檔案（不實際執行）"""
    preview_results = []
    
    for jar_path in jar_files:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            lang_files = [f for f in zf.namelist() 
                         if f.endswith('.json') or f.endswith('.lang')]
            
            preview_results.append({
                'jar': os.path.basename(jar_path),
                'files': lang_files,
                'count': len(lang_files),
            })
    
    # 顯示預覽對話框
    preview_content = ft.Column([
        ft.Text(f"🗂️ {r['jar']}: {r['count']} 個檔案", size=14)
        for r in preview_results
    ])
    
    total_files = sum(r['count'] for r in preview_results)
    
    dialog = ft.AlertDialog(
        title=ft.Text(f"預覽提取結果（共 {total_files} 個檔案）"),
        content=ft.Container(content=preview_content, height=400),
        actions=[
            ft.TextButton("取消", on_click=lambda _: dialog.close()),
            ft.ElevatedButton("確認提取", on_click=lambda _: self._start_extraction()),
        ]
    )
    
    self.page.dialog = dialog
    dialog.open = True
    self.page.update()
```

**新增功能 2: 黑名單模組**
```python
class ModBlacklist:
    """模組黑名單（跳過已知有問題的模組）"""
    
    BLACKLIST = [
        "optifine",  # OptiFine 有特殊格式
        "forgemod",  # Forge 本身
        "minecraft",  # 原版（通常不需要提取）
    ]
    
    @classmethod
    def should_skip(cls, jar_name: str) -> tuple[bool, str]:
        """檢查是否應跳過（回傳 (是否跳過, 原因)）"""
        jar_lower = jar_name.lower()
        
        for blacklisted in cls.BLACKLIST:
            if blacklisted in jar_lower:
                return True, f"黑名單模組: {blacklisted}"
        
        return False, ""
    
    @classmethod
    def add_to_blacklist(cls, mod_name: str):
        """動態加入黑名單"""
        if mod_name not in cls.BLACKLIST:
            cls.BLACKLIST.append(mod_name.lower())
            cls._save()
    
    @classmethod
    def _save(cls):
        """儲存黑名單"""
        with open('mod_blacklist.json', 'w') as f:
            json.dump(cls.BLACKLIST, f)

# 使用
for jar in jar_files:
    should_skip, reason = ModBlacklist.should_skip(jar)
    if should_skip:
        log_info(f"⏭️ 跳過 {jar}: {reason}")
        continue
```

**新增功能 3: 提取結果摘要**
```python
class ExtractionSummary:
    """提取結果摘要"""
    
    def __init__(self):
        self.success = []
        self.warnings = []
        self.failures = []
    
    def add_success(self, jar_name: str, file_count: int):
        self.success.append({'jar': jar_name, 'files': file_count})
    
    def add_warning(self, jar_name: str, reason: str):
        self.warnings.append({'jar': jar_name, 'reason': reason})
    
    def add_failure(self, jar_name: str, error: str):
        self.failures.append({'jar': jar_name, 'error': error})
    
    def display(self) -> ft.Column:
        """顯示摘要"""
        return ft.Column([
            ft.Text("📊 提取摘要", size=18, weight=ft.FontWeight.BOLD),
            ft.Divider(),
            
            # 成功
            ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=20),
                ft.Text(f"成功: {len(self.success)} 個模組", size=14),
            ]),
            
            # 警告
            ft.Row([
                ft.Icon(ft.Icons.WARNING, color=ft.Colors.ORANGE, size=20),
                ft.Text(f"警告: {len(self.warnings)} 個模組", size=14),
            ]),
            
            # 失敗
            ft.Row([
                ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED, size=20),
                ft.Text(f"失敗: {len(self.failures)} 個模組", size=14),
            ]),
            
            ft.Divider(),
            
            # 詳細資訊（可展開）
            ft.ExpansionTile(
                title=ft.Text("查看詳細資訊"),
                controls=[
                    ft.Text(f"✅ {s['jar']}: {s['files']} 個檔案")
                    for s in self.success
                ] + [
                    ft.Text(f"⚠️ {w['jar']}: {w['reason']}")
                    for w in self.warnings
                ] + [
                    ft.Text(f"❌ {f['jar']}: {f['error']}")
                    for f in self.failures
                ],
            ),
        ], spacing=8)
```

---

#### 機器翻譯 (lm_view.py) 🔥 高優先

**新增功能 1: 預設配置選單**
```python
class TranslationPresets:
    """翻譯預設配置"""
    
    PRESETS = {
        "快速模式": {
            "description": "大批次，允許部分失敗，優先速度",
            "batch_size": 300,
            "min_batch_size": 100,
            "retry_on_error": False,
            "dry_run": False,
        },
        "精確模式": {
            "description": "小批次，完整檢查，確保品質",
            "batch_size": 100,
            "min_batch_size": 50,
            "retry_on_error": True,
            "dry_run": False,
        },
        "經濟模式": {
            "description": "優先使用快取，減少 API 呼叫",
            "batch_size": 200,
            "min_batch_size": 50,
            "use_cache_first": True,
            "skip_if_cached": True,
        },
        "測試模式": {
            "description": "Dry-run，只分析不發送 API",
            "dry_run": True,
        },
    }
    
    @classmethod
    def apply_preset(cls, preset_name: str, config: dict) -> dict:
        """套用預設配置"""
        if preset_name not in cls.PRESETS:
            return config
        
        preset = cls.PRESETS[preset_name]
        return {**config, **preset}

# UI 使用
preset_dropdown = ft.Dropdown(
    label="選擇預設配置",
    options=[ft.dropdown.Option(name) for name in TranslationPresets.PRESETS.keys()],
    on_change=lambda e: self._apply_preset(e.control.value),
)

def _apply_preset(self, preset_name: str):
    """套用預設配置到 UI"""
    preset = TranslationPresets.PRESETS[preset_name]
    
    # 更新 UI 元件
    self.batch_size_field.value = str(preset.get('batch_size', 200))
    self.dry_run_switch.value = preset.get('dry_run', False)
    
    # 顯示說明
    self.preset_description.value = preset['description']
    
    self.update()
```

**新增功能 2: 成本估算**
```python
class CostEstimator:
    """API 成本估算器"""
    
    # Gemini 2.5 Flash 定價（範例）
    PRICING = {
        "gemini-2.5-flash": {
            "input": 0.000075,   # per 1K tokens
            "output": 0.0003,    # per 1K tokens
        },
        "gemini-pro": {
            "input": 0.00025,
            "output": 0.0005,
        },
    }
    
    @staticmethod
    def estimate_tokens(text: str) -> int:
        """估算 token 數量（簡易版：1 中文字 ≈ 2 tokens）"""
        chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
        english_words = len(text.split())
        
        return chinese_chars * 2 + english_words
    
    @classmethod
    def estimate_cost(cls, texts: list[str], model: str = "gemini-2.5-flash") -> dict:
        """估算翻譯成本"""
        if model not in cls.PRICING:
            return {"error": "未知模型"}
        
        input_tokens = sum(cls.estimate_tokens(t) for t in texts)
        # 假設輸出 tokens 約為輸入的 1.2 倍
        output_tokens = int(input_tokens * 1.2)
        
        pricing = cls.PRICING[model]
        input_cost = (input_tokens / 1000) * pricing["input"]
        output_cost = (output_tokens / 1000) * pricing["output"]
        total_cost = input_cost + output_cost
        
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "total_cost_usd": total_cost,
            "total_cost_twd": total_cost * 31,  # 假設匯率 1 USD = 31 TWD
        }

# UI 顯示
def show_cost_estimate(self):
    """顯示成本估算"""
    # 取得所有待翻譯文字
    texts = self._get_pending_texts()
    
    estimate = CostEstimator.estimate_cost(texts, model="gemini-2.5-flash")
    
    dialog = ft.AlertDialog(
        title=ft.Text("💰 成本估算"),
        content=ft.Column([
            ft.Text(f"輸入 tokens: {estimate['input_tokens']:,}"),
            ft.Text(f"輸出 tokens（估算）: {estimate['output_tokens']:,}"),
            ft.Divider(),
            ft.Text(f"預估成本: ${estimate['total_cost_usd']:.4f} USD"),
            ft.Text(f"約等於: NT${estimate['total_cost_twd']:.2f}", weight=ft.FontWeight.BOLD),
        ]),
        actions=[ft.TextButton("關閉", on_click=lambda _: dialog.close())],
    )
    
    self.page.dialog = dialog
    dialog.open = True
    self.page.update()
```

**新增功能 3: 翻譯品質評分**
```python
class QualityScorer:
    """翻譯品質評分器"""
    
    @staticmethod
    def check_formatting(source: str, translated: str) -> dict:
        """檢查格式符號是否保留"""
        issues = []
        
        # 檢查佔位符
        source_placeholders = re.findall(r'%[sd]|\{[0-9]+\}|\$\([^)]+\)', source)
        trans_placeholders = re.findall(r'%[sd]|\{[0-9]+\}|\$\([^)]+\)', translated)
        
        if set(source_placeholders) != set(trans_placeholders):
            issues.append({
                'type': 'placeholder_mismatch',
                'severity': 'error',
                'message': f"佔位符不一致: {source_placeholders} vs {trans_placeholders}"
            })
        
        # 檢查顏色代碼
        source_colors = re.findall(r'§[0-9a-fk-or]', source)
        trans_colors = re.findall(r'§[0-9a-fk-or]', translated)
        
        if len(source_colors) != len(trans_colors):
            issues.append({
                'type': 'color_code_mismatch',
                'severity': 'warning',
                'message': f"顏色代碼數量不一致"
            })
        
        return issues
    
    @staticmethod
    def check_over_translation(translated: str) -> list:
        """檢查是否過度翻譯"""
        issues = []
        
        # 不該翻譯的詞
        should_not_translate = ["Minecraft", "Discord", "GitHub", "Forge", "Fabric"]
        
        for term in should_not_translate:
            if term.lower() in translated.lower():
                # 檢查是否被翻譯了
                if term not in translated:
                    issues.append({
                        'type': 'over_translation',
                        'severity': 'warning',
                        'message': f"'{term}' 不應翻譯"
                    })
        
        # 檢查是否有「當個創世神」（Minecraft 的錯誤翻譯）
        if "當個創世神" in translated:
            issues.append({
                'type': 'incorrect_term',
                'severity': 'error',
                'message': "請使用 'Minecraft' 而非 '當個創世神'"
            })
        
        return issues
    
    @classmethod
    def score(cls, source: str, translated: str) -> dict:
        """計算品質分數 (0~100)"""
        issues = []
        issues.extend(cls.check_formatting(source, translated))
        issues.extend(cls.check_over_translation(translated))
        
        # 計算扣分
        error_penalty = sum(10 for i in issues if i['severity'] == 'error')
        warning_penalty = sum(5 for i in issues if i['severity'] == 'warning')
        
        score = max(100 - error_penalty - warning_penalty, 0)
        
        return {
            'score': score,
            'grade': 'A' if score >= 90 else 'B' if score >= 70 else 'C' if score >= 50 else 'D',
            'issues': issues,
        }
```

---

#### 快取管理 (cache_view.py) 🔥 低優先

**新增功能: 視覺化統計**
```python
import matplotlib.pyplot as plt
from io import BytesIO
import base64

class CacheVisualizer:
    """快取視覺化"""
    
    @staticmethod
    def create_pie_chart(data: dict) -> str:
        """建立圓餅圖（翻譯來源分佈）"""
        labels = ['人工翻譯', '機器翻譯', '社群翻譯', '快取']
        sizes = [
            data.get('manual', 0),
            data.get('machine', 0),
            data.get('community', 0),
            data.get('cache', 0),
        ]
        
        fig, ax = plt.subplots()
        ax.pie(sizes, labels=labels, autopct='%1.1f%%')
        
        # 轉為 base64 圖片
        buf = BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode()
        
        return f"data:image/png;base64,{img_base64}"
    
    @staticmethod
    def create_timeline_chart(history: list) -> str:
        """建立時間線圖（翻譯量隨時間變化）"""
        dates = [h['date'] for h in history]
        counts = [h['count'] for h in history]
        
        fig, ax = plt.subplots()
        ax.plot(dates, counts, marker='o')
        ax.set_xlabel('日期')
        ax.set_ylabel('翻譯數量')
        ax.set_title('翻譯量趨勢')
        
        buf = BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode()
        
        return f"data:image/png;base64,{img_base64}"

# 在 Flet 中顯示
chart_base64 = CacheVisualizer.create_pie_chart(stats_data)

chart_image = ft.Image(
    src=chart_base64,
    width=400,
    height=300,
)
```

---

### UI3. 新增快捷操作 🔥 中優先

#### 問題
- 每次都要點很多次才能執行常見流程
- 缺乏鍵盤快捷鍵
- 無法批次操作

#### 解決方案 1: 工作流程模板
```python
# 新增 app/views/workflow_view.py
class Workflow:
    """工作流程定義"""
    
    def __init__(self, name: str, steps: list, description: str = ""):
        self.name = name
        self.steps = steps
        self.description = description
    
    def execute(self, context: dict):
        """執行工作流程"""
        for step in self.steps:
            yield step.execute(context)

class WorkflowStep:
    """工作流程步驟"""
    
    def __init__(self, name: str, action: callable):
        self.name = name
        self.action = action
    
    def execute(self, context: dict):
        return self.action(context)

# 預定義工作流程
WORKFLOWS = {
    "完整翻譯流程": Workflow(
        name="完整翻譯流程",
        description="從 JAR 提取到最終輸出的完整流程",
        steps=[
            WorkflowStep("提取 JAR", lambda ctx: extract_jars(ctx['jar_folder'])),
            WorkflowStep("合併檔案", lambda ctx: merge_files(ctx['extracted_folder'])),
            WorkflowStep("機器翻譯", lambda ctx: machine_translate(ctx['merged_folder'])),
            WorkflowStep("品質檢查", lambda ctx: quality_check(ctx['translated_folder'])),
            WorkflowStep("打包輸出", lambda ctx: bundle_output(ctx['output_folder'])),
        ],
    ),
    
    "快速更新流程": Workflow(
        name="快速更新流程",
        description="只翻譯新增部分",
        steps=[
            WorkflowStep("合併新檔案", lambda ctx: merge_new_files(ctx['new_zip'])),
            WorkflowStep("只翻譯新增", lambda ctx: translate_new_only(ctx['merged_folder'])),
            WorkflowStep("輸出", lambda ctx: bundle_output(ctx['output_folder'])),
        ],
    ),
    
    "品質檢查流程": Workflow(
        name="品質檢查流程",
        description="檢查並修正翻譯問題",
        steps=[
            WorkflowStep("檢查未翻譯", lambda ctx: check_untranslated(ctx['folder'])),
            WorkflowStep("比對簡繁", lambda ctx: compare_variants(ctx['folder'])),
            WorkflowStep("術語一致性", lambda ctx: check_glossary(ctx['folder'])),
            WorkflowStep("格式檢查", lambda ctx: check_formatting(ctx['folder'])),
        ],
    ),
}

# UI
class WorkflowView(ft.Column):
    def __init__(self):
        super().__init__()
        
        self.controls = [
            ft.Text("🔄 工作流程", size=22, weight=ft.FontWeight.BOLD),
            
            # 工作流程列表
            ft.Column([
                self._create_workflow_card(name, workflow)
                for name, workflow in WORKFLOWS.items()
            ]),
        ]
    
    def _create_workflow_card(self, name: str, workflow: Workflow):
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Column([
                    ft.Text(name, size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(workflow.description, size=12, color=ft.Colors.GREY_700),
                    
                    # 步驟預覽
                    ft.Column([
                        ft.Row([
                            ft.Icon(ft.Icons.ARROW_RIGHT, size=16),
                            ft.Text(step.name, size=12),
                        ])
                        for step in workflow.steps
                    ]),
                    
                    # 執行按鈕
                    ft.ElevatedButton(
                        "執行此流程",
                        icon=ft.Icons.PLAY_ARROW,
                        on_click=lambda _: self._execute_workflow(workflow),
                    ),
                ]),
            ),
        )
```

**解決方案 2: 右鍵選單**
```python
class ContextMenu:
    """右鍵選單"""
    
    @staticmethod
    def create_file_context_menu(file_path: str, page: ft.Page):
        """建立檔案右鍵選單"""
        return ft.PopupMenuButton(
            items=[
                ft.PopupMenuItem(
                    text="在檔案總管中開啟",
                    icon=ft.Icons.FOLDER_OPEN,
                    on_click=lambda _: os.startfile(os.path.dirname(file_path)),
                ),
                ft.PopupMenuItem(
                    text="複製路徑",
                    icon=ft.Icons.CONTENT_COPY,
                    on_click=lambda _: page.set_clipboard(file_path),
                ),
                ft.PopupMenuItem(
                    text="標記為已審查",
                    icon=ft.Icons.CHECK_CIRCLE,
                    on_click=lambda _: mark_as_reviewed(file_path),
                ),
                ft.PopupMenuItem(
                    text="移至隔離區",
                    icon=ft.Icons.BLOCK,
                    on_click=lambda _: quarantine_file(file_path),
                ),
            ],
        )
```

**解決方案 3: 鍵盤快捷鍵**
```python
# 在 main.py 加入
class KeyboardShortcuts:
    """鍵盤快捷鍵管理"""
    
    def __init__(self, page: ft.Page):
        self.page = page
        self.handlers = {
            "ctrl+r": self.reload_cache,
            "ctrl+t": self.start_translation,
            "ctrl+l": self.clear_logs,
            "ctrl+f": self.open_search,
            "ctrl+s": self.save_current,
            "ctrl+n": self.new_project,
        }
    
    def handle_keyboard(self, e: ft.KeyboardEvent):
        """處理鍵盤事件"""
        key_combo = ""
        
        if e.ctrl:
            key_combo += "ctrl+"
        if e.shift:
            key_combo += "shift+"
        if e.alt:
            key_combo += "alt+"
        
        key_combo += e.key.lower()
        
        if key_combo in self.handlers:
            self.handlers[key_combo]()
            e.handled = True
    
    def reload_cache(self):
        # 重新載入快取邏輯
        pass
    
    # ...其他處理函式

# 在 main() 中啟用
shortcuts = KeyboardShortcuts(page)
page.on_keyboard_event = shortcuts.handle_keyboard
```

---

## 🆕 建議新增功能

### 功能 #1: **翻譯記憶庫 (Translation Memory)** 🔥 高優先

#### 目的
解決「相同文字重複翻譯」問題，節省 API 成本

#### 實作
```python
# translation_tool/utils/translation_memory.py
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher

class TranslationMemory:
    """翻譯記憶庫（基於 SQLite）"""
    
    def __init__(self, db_path: str = "translation_memory.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_db()
    
    def _init_db(self):
        """初始化資料庫"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                source_lang TEXT DEFAULT 'en',
                target_lang TEXT DEFAULT 'zh-tw',
                context TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                confidence REAL DEFAULT 1.0,
                source TEXT DEFAULT 'manual',
                UNIQUE(source_text, source_lang, target_lang)
            )
        """)
        
        # 建立索引加速查詢
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_text 
            ON translations(source_text)
        """)
        
        self.conn.commit()
    
    def add(self, source: str, target: str, context: str = None, 
            source_type: str = 'manual', confidence: float = 1.0):
        """加入翻譯記憶"""
        self.conn.execute("""
            INSERT OR REPLACE INTO translations 
            (source_text, target_text, context, source, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (source, target, context, source_type, confidence, datetime.now()))
        self.conn.commit()
    
    def search_exact(self, source: str) -> dict | None:
        """精確搜尋"""
        cursor = self.conn.execute("""
            SELECT target_text, confidence, source, created_at
            FROM translations
            WHERE source_text = ?
            ORDER BY confidence DESC, updated_at DESC
            LIMIT 1
        """, (source,))
        
        row = cursor.fetchone()
        if row:
            return {
                'target': row[0],
                'confidence': row[1],
                'source': row[2],
                'created_at': row[3],
            }
        return None
    
    def search_similar(self, source: str, threshold: float = 0.8) -> list:
        """模糊搜尋（相似度 >= threshold）"""
        # 先取得所有候選
        cursor = self.conn.execute("""
            SELECT source_text, target_text, confidence, source
            FROM translations
            WHERE length(source_text) BETWEEN ? AND ?
        """, (len(source) * 0.7, len(source) * 1.3))
        
        results = []
        for row in cursor.fetchall():
            similarity = SequenceMatcher(None, source, row[0]).ratio()
            
            if similarity >= threshold:
                results.append({
                    'source': row[0],
                    'target': row[1],
                    'confidence': row[2],
                    'source_type': row[3],
                    'similarity': similarity,
                })
        
        return sorted(results, key=lambda x: x['similarity'], reverse=True)
    
    def suggest(self, source: str) -> str | None:
        """建議翻譯（先精確，再模糊）"""
        # 先嘗試精確匹配
        exact = self.search_exact(source)
        if exact and exact['confidence'] >= 0.9:
            return exact['target']
        
        # 再嘗試模糊匹配
        similar = self.search_similar(source, threshold=0.85)
        if similar and similar[0]['similarity'] >= 0.95:
            return similar[0]['target']
        
        return None
    
    def import_from_dict(self, translations: dict, source_type: str = 'import'):
        """批次匯入翻譯"""
        for source, target in translations.items():
            self.add(source, target, source_type=source_type)
    
    def export_to_dict(self) -> dict:
        """匯出為字典"""
        cursor = self.conn.execute("""
            SELECT source_text, target_text
            FROM translations
            WHERE confidence >= 0.8
        """)
        
        return dict(cursor.fetchall())
```

#### UI 呈現
```python
class TranslationMemoryView(ft.Column):
    """翻譯記憶庫管理介面"""
    
    def __init__(self, tm: TranslationMemory):
        super().__init__()
        self.tm = tm
        
        self.search_field = ft.TextField(
            label="搜尋原文",
            on_submit=self.search,
        )
        
        self.results_list = ft.ListView(expand=True)
        
        self.controls = [
            ft.Text("💾 翻譯記憶庫", size=18, weight=ft.FontWeight.BOLD),
            
            self.search_field,
            
            ft.Text("搜尋結果:"),
            self.results_list,
            
            ft.Row([
                ft.ElevatedButton("匯入", on_click=self.import_tm),
                ft.ElevatedButton("匯出", on_click=self.export_tm),
            ]),
        ]
    
    def search(self, e):
        query = self.search_field.value
        
        # 精確搜尋
        exact = self.tm.search_exact(query)
        similar = self.tm.search_similar(query)
        
        self.results_list.controls.clear()
        
        if exact:
            self.results_list.controls.append(
                ft.Card(
                    content=ft.Container(
                        padding=10,
                        content=ft.Column([
                            ft.Text("✅ 精確匹配", weight=ft.FontWeight.BOLD),
                            ft.Text(f"原文: {query}"),
                            ft.Text(f"譯文: {exact['target']}"),
                            ft.Text(f"信心度: {exact['confidence']:.0%}"),
                        ]),
                    ),
                )
            )
        
        for match in similar[:5]:
            self.results_list.controls.append(
                ft.Card(
                    content=ft.Container(
                        padding=10,
                        content=ft.Column([
                            ft.Text(f"🔍 相似度: {match['similarity']:.0%}"),
                            ft.Text(f"原文: {match['source']}"),
                            ft.Text(f"譯文: {match['target']}"),
                        ]),
                    ),
                )
            )
        
        self.update()
```

**效益**:
- 減少 API 呼叫（節省成本）
- 確保術語一致性
- 加速翻譯流程

---

### 功能 #2: **術語資料庫 (Glossary)** 🔥 高優先

#### 目的
確保遊戲術語翻譯一致性

#### 實作
```python
# translation_tool/utils/glossary.py
class Glossary:
    """遊戲術語資料庫"""
    
    # 內建術語（Minecraft 常見譯名）
    BUILTIN_TERMS = {
        # 生物
        "Creeper": "苦力怕",
        "Enderman": "終界使者",
        "Zombie": "殭屍",
        "Skeleton": "骷髏",
        "Spider": "蜘蛛",
        
        # 物品
        "Spawn Egg": "生怪蛋",
        "Cobblestone": "鵝卵石",
        "Diamond": "鑽石",
        "Redstone": "紅石",
        
        # 地點
        "Nether": "地獄",
        "End": "終界",
        "Overworld": "主世界",
        
        # 不應翻譯
        "Minecraft": "Minecraft",
        "Forge": "Forge",
        "Fabric": "Fabric",
    }
    
    def __init__(self, custom_terms: dict = None):
        self.terms = {**self.BUILTIN_TERMS}
        
        if custom_terms:
            self.terms.update(custom_terms)
    
    def translate_term(self, term: str) -> str:
        """翻譯術語（不存在則回傳原文）"""
        return self.terms.get(term, term)
    
    def check_consistency(self, translations: dict) -> list:
        """檢查翻譯中的術語一致性"""
        issues = []
        
        for source, translated in translations.items():
            for term_en, term_zh in self.terms.items():
                # 檢查英文術語是否存在
                if term_en.lower() in source.lower():
                    # 檢查翻譯是否正確
                    if term_zh not in translated:
                        # 進一步檢查是否有其他錯誤譯名
                        wrong_translations = self._find_wrong_translations(term_en)
                        
                        for wrong in wrong_translations:
                            if wrong in translated:
                                issues.append({
                                    'source': source,
                                    'translated': translated,
                                    'term': term_en,
                                    'expected': term_zh,
                                    'found': wrong,
                                    'severity': 'error',
                                })
                                break
        
        return issues
    
    def _find_wrong_translations(self, term: str) -> list:
        """找出常見的錯誤譯名"""
        wrong_mappings = {
            "Minecraft": ["當個創世神", "我的世界"],
            "Creeper": ["爬行者", "苦力帕"],
            "Cobblestone": ["圓石"],
        }
        
        return wrong_mappings.get(term, [])
    
    def add_term(self, english: str, chinese: str):
        """加入自訂術語"""
        self.terms[english] = chinese
        self._save()
    
    def remove_term(self, english: str):
        """移除術語"""
        if english in self.terms and english not in self.BUILTIN_TERMS:
            del self.terms[english]
            self._save()
    
    def _save(self):
        """儲存自訂術語"""
        custom_terms = {k: v for k, v in self.terms.items() 
                       if k not in self.BUILTIN_TERMS}
        
        with open('custom_glossary.json', 'w', encoding='utf-8') as f:
            json.dump(custom_terms, f, ensure_ascii=False, indent=2)
    
    def _load(self):
        """載入自訂術語"""
        try:
            with open('custom_glossary.json', 'r', encoding='utf-8') as f:
                custom_terms = json.load(f)
                self.terms.update(custom_terms)
        except FileNotFoundError:
            pass
```

#### UI 呈現
```python
class GlossaryView(ft.Column):
    """術語管理介面"""
    
    def __init__(self, glossary: Glossary):
        super().__init__()
        self.glossary = glossary
        
        self.term_list = ft.ListView(expand=True)
        self._refresh_list()
        
        self.controls = [
            ft.Text("📖 術語資料庫", size=18, weight=ft.FontWeight.BOLD),
            
            # 新增術語
            ft.Row([
                ft.TextField(hint_text="英文", expand=True, ref=self.en_field := ft.Ref()),
                ft.TextField(hint_text="中文", expand=True, ref=self.zh_field := ft.Ref()),
                ft.IconButton(icon=ft.Icons.ADD, on_click=self.add_term),
            ]),
            
            # 術語列表
            self.term_list,
        ]
    
    def _refresh_list(self):
        """刷新術語列表"""
        self.term_list.controls.clear()
        
        for en, zh in sorted(self.glossary.terms.items()):
            is_builtin = en in self.glossary.BUILTIN_TERMS
            
            self.term_list.controls.append(
                ft.Card(
                    content=ft.Container(
                        padding=10,
                        content=ft.Row([
                            ft.Text(en, expand=True),
                            ft.Icon(ft.Icons.ARROW_RIGHT),
                            ft.Text(zh, expand=True),
                            ft.IconButton(
                                icon=ft.Icons.DELETE,
                                disabled=is_builtin,
                                on_click=lambda _, e=en: self.remove_term(e),
                            ) if not is_builtin else ft.Container(),
                        ]),
                    ),
                )
            )
    
    def add_term(self, e):
        en = self.en_field.current.value
        zh = self.zh_field.current.value
        
        if en and zh:
            self.glossary.add_term(en, zh)
            self._refresh_list()
            self.update()
    
    def remove_term(self, term: str):
        self.glossary.remove_term(term)
        self._refresh_list()
        self.update()
```

**效益**:
- 術語一致性
- 避免低品質翻譯
- 加速審查流程

---

### 功能 #3: **社群翻譯整合** 🔥 中優先

#### 目的
自動下載現有社群翻譯並合併（避免重複工作）

#### 實作
```python
# translation_tool/community/integration.py
import requests
from pathlib import Path

class CommunityIntegration:
    """社群翻譯整合"""
    
    SOURCES = {
        "CFPA": {
            "name": "CFPA 簡中翻譯",
            "url": "https://cfpa.site/",
            "api": "https://api.cfpa.site/translations",
            "lang": "zh-cn",
        },
        "釘宮翻譯組": {
            "name": "ModsTranslationPack 繁中",
            "url": "https://modrinth.com/resourcepack/modstranslationpack",
            "download_url": "https://cdn.modrinth.com/data/...",
            "lang": "zh-tw",
        },
    }
    
    @classmethod
    def search_mod(cls, mod_name: str) -> list:
        """搜尋模組翻譯"""
        results = []
        
        for source_id, source in cls.SOURCES.items():
            try:
                # 這裡需要根據實際 API 調整
                # 範例：查詢 CFPA
                if source_id == "CFPA":
                    # response = requests.get(f"{source['api']}/search?q={mod_name}")
                    # ...
                    pass
                
                results.append({
                    'source': source_id,
                    'name': source['name'],
                    'available': True,
                    'lang': source['lang'],
                })
            except Exception as e:
                results.append({
                    'source': source_id,
                    'name': source['name'],
                    'available': False,
                    'error': str(e),
                })
        
        return results
    
    @classmethod
    def download_translation(cls, source_id: str, mod_name: str, output_path: Path):
        """下載社群翻譯"""
        source = cls.SOURCES.get(source_id)
        if not source:
            raise ValueError(f"Unknown source: {source_id}")
        
        # 實際下載邏輯（依各社群平台 API 而定）
        # 這裡只是範例
        pass
    
    @classmethod
    def merge_community_translation(cls, local: dict, community: dict, 
                                    strategy: str = "prefer_local") -> dict:
        """合併社群翻譯與本地翻譯"""
        merged = {}
        
        all_keys = set(local.keys()) | set(community.keys())
        
        for key in all_keys:
            local_value = local.get(key)
            community_value = community.get(key)
            
            if strategy == "prefer_local":
                # 優先使用本地翻譯
                merged[key] = local_value if local_value else community_value
            
            elif strategy == "prefer_community":
                # 優先使用社群翻譯
                merged[key] = community_value if community_value else local_value
            
            elif strategy == "merge":
                # 兩者都保留（標記來源）
                if local_value and community_value:
                    merged[key] = {
                        'local': local_value,
                        'community': community_value,
                        'needs_review': local_value != community_value,
                    }
                else:
                    merged[key] = local_value or community_value
        
        return merged
```

#### UI 呈現
```python
class CommunityTranslationView(ft.Column):
    """社群翻譯整合介面"""
    
    def __init__(self):
        super().__init__()
        
        self.mod_search = ft.TextField(
            label="搜尋模組名稱",
            on_submit=self.search,
        )
        
        self.results_list = ft.ListView(expand=True)
        
        self.controls = [
            ft.Text("🌐 社群翻譯整合", size=18, weight=ft.FontWeight.BOLD),
            
            self.mod_search,
            ft.ElevatedButton("搜尋", on_click=self.search),
            
            ft.Text("可用的社群翻譯:"),
            self.results_list,
        ]
    
    def search(self, e):
        mod_name = self.mod_search.value
        results = CommunityIntegration.search_mod(mod_name)
        
        self.results_list.controls.clear()
        
        for result in results:
            if result['available']:
                self.results_list.controls.append(
                    ft.Card(
                        content=ft.Container(
                            padding=10,
                            content=ft.Column([
                                ft.Text(result['name'], weight=ft.FontWeight.BOLD),
                                ft.Text(f"語言: {result['lang']}"),
                                ft.ElevatedButton(
                                    "下載並合併",
                                    on_click=lambda _, r=result: self.download(r),
                                ),
                            ]),
                        ),
                    )
                )
        
        self.update()
    
    def download(self, result: dict):
        # 下載並合併邏輯
        pass
```

**效益**:
- 避免重複翻譯
- 快速獲取基礎翻譯
- 專注於校對與優化

---

### 功能 #4: **版本管理與回溯** 🔥 中優先

#### 目的
避免誤刪或錯誤翻譯無法復原

#### 實作
```python
# translation_tool/utils/version_control.py
import shutil
from datetime import datetime
from pathlib import Path

class VersionControl:
    """簡易版本控制系統"""
    
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.snapshots_dir = self.workspace / ".snapshots"
        self.snapshots_dir.mkdir(exist_ok=True)
    
    def create_snapshot(self, message: str = "") -> str:
        """建立快照"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_id = f"snapshot_{timestamp}"
        snapshot_path = self.snapshots_dir / snapshot_id
        
        # 複製當前工作區
        shutil.copytree(
            self.workspace,
            snapshot_path,
            ignore=shutil.ignore_patterns('.snapshots', '__pycache__', '*.pyc'),
        )
        
        # 儲存元資料
        metadata = {
            'id': snapshot_id,
            'timestamp': timestamp,
            'message': message,
            'files_count': sum(1 for _ in snapshot_path.rglob('*') if _.is_file()),
        }
        
        with open(snapshot_path / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return snapshot_id
    
    def list_snapshots(self) -> list:
        """列出所有快照"""
        snapshots = []
        
        for snapshot_dir in sorted(self.snapshots_dir.iterdir(), reverse=True):
            if not snapshot_dir.is_dir():
                continue
            
            metadata_file = snapshot_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                    snapshots.append(metadata)
        
        return snapshots
    
    def restore(self, snapshot_id: str):
        """恢復到某個快照"""
        snapshot_path = self.snapshots_dir / snapshot_id
        
        if not snapshot_path.exists():
            raise ValueError(f"Snapshot {snapshot_id} not found")
        
        # 先建立當前狀態的快照（安全措施）
        self.create_snapshot(message=f"Auto backup before restore to {snapshot_id}")
        
        # 清除當前工作區（除了 .snapshots）
        for item in self.workspace.iterdir():
            if item.name == '.snapshots':
                continue
            
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        
        # 恢復快照
        for item in snapshot_path.iterdir():
            if item.name == 'metadata.json':
                continue
            
            dest = self.workspace / item.name
            
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    
    def diff(self, snapshot_a: str, snapshot_b: str) -> dict:
        """比較兩個快照的差異"""
        path_a = self.snapshots_dir / snapshot_a
        path_b = self.snapshots_dir / snapshot_b
        
        changes = {
            'added': [],
            'deleted': [],
            'modified': [],
        }
        
        files_a = {f.relative_to(path_a): f for f in path_a.rglob('*') if f.is_file()}
        files_b = {f.relative_to(path_b): f for f in path_b.rglob('*') if f.is_file()}
        
        # 新增的檔案
        for rel_path in files_b.keys() - files_a.keys():
            changes['added'].append(str(rel_path))
        
        # 刪除的檔案
        for rel_path in files_a.keys() - files_b.keys():
            changes['deleted'].append(str(rel_path))
        
        # 修改的檔案
        for rel_path in files_a.keys() & files_b.keys():
            if files_a[rel_path].stat().st_size != files_b[rel_path].stat().st_size:
                changes['modified'].append(str(rel_path))
        
        return changes
```

#### UI 呈現
```python
class VersionControlView(ft.Column):
    """版本歷史介面"""
    
    def __init__(self, vc: VersionControl):
        super().__init__()
        self.vc = vc
        
        self.snapshots_list = ft.ListView(expand=True)
        self._refresh_list()
        
        self.controls = [
            ft.Text("🕐 版本歷史", size=18, weight=ft.FontWeight.BOLD),
            
            # 建立快照
            ft.Row([
                ft.TextField(
                    hint_text="快照說明",
                    expand=True,
                    ref=self.snapshot_msg := ft.Ref(),
                ),
                ft.ElevatedButton(
                    "建立快照",
                    icon=ft.Icons.SAVE,
                    on_click=self.create_snapshot,
                ),
            ]),
            
            # 快照列表
            self.snapshots_list,
        ]
    
    def _refresh_list(self):
        """刷新快照列表"""
        snapshots = self.vc.list_snapshots()
        
        self.snapshots_list.controls.clear()
        
        for snapshot in snapshots:
            self.snapshots_list.controls.append(
                ft.Card(
                    content=ft.Container(
                        padding=10,
                        content=ft.Column([
                            ft.Text(snapshot['message'] or "(無說明)", weight=ft.FontWeight.BOLD),
                            ft.Text(f"時間: {snapshot['timestamp']}", size=12),
                            ft.Text(f"檔案數: {snapshot['files_count']}", size=12),
                            
                            ft.Row([
                                ft.ElevatedButton(
                                    "恢復",
                                    icon=ft.Icons.RESTORE,
                                    on_click=lambda _, s=snapshot: self.restore(s['id']),
                                ),
                                ft.OutlinedButton(
                                    "比較",
                                    icon=ft.Icons.COMPARE,
                                    on_click=lambda _, s=snapshot: self.show_diff(s['id']),
                                ),
                            ]),
                        ]),
                    ),
                )
            )
    
    def create_snapshot(self, e):
        message = self.snapshot_msg.current.value or ""
        self.vc.create_snapshot(message)
        self._refresh_list()
        self.update()
    
    def restore(self, snapshot_id: str):
        # 顯示確認對話框
        dialog = ft.AlertDialog(
            title=ft.Text("確認恢復"),
            content=ft.Text(f"確定要恢復到快照 {snapshot_id}？\n當前狀態會先自動備份。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: dialog.close()),
                ft.ElevatedButton(
                    "確認恢復",
                    on_click=lambda _: self._do_restore(snapshot_id, dialog),
                ),
            ],
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
    
    def _do_restore(self, snapshot_id: str, dialog: ft.AlertDialog):
        self.vc.restore(snapshot_id)
        dialog.close()
        self._refresh_list()
        self.update()
```

**效益**:
- 安全性提升
- 可追蹤變更歷史
- 錯誤可復原

---

### 功能 #5: **翻譯品質檢查（自動化）** 🔥 高優先

已在前面「優化建議 > UI 改進 > 機器翻譯」中詳細說明（`QualityScorer` class）

**補充：整合到工作流程**
```python
# 在翻譯完成後自動執行品質檢查
def translate_and_check(texts: list[str]) -> dict:
    """翻譯 + 品質檢查"""
    results = {
        'translations': [],
        'issues': [],
        'avg_score': 0.0,
    }
    
    for text in texts:
        translated = translate_with_api(text)
        quality = QualityScorer.score(text, translated)
        
        results['translations'].append({
            'source': text,
            'target': translated,
            'score': quality['score'],
            'grade': quality['grade'],
        })
        
        if quality['issues']:
            results['issues'].extend(quality['issues'])
    
    # 計算平均分數
    if results['translations']:
        results['avg_score'] = sum(t['score'] for t in results['translations']) / len(results['translations'])
    
    return results
```

---

### 功能 #6: **匯出格式支援** 🔥 低優先

#### 目的
讓翻譯成果能輸出到不同平台

#### 支援格式
1. **Minecraft 資源包 (.zip)** - ✅ 已支援
2. **Crowdin CSV** - 新增
3. **XLIFF (XML Localization Interchange File Format)** - 標準翻譯交換格式
4. **翻譯報告 (Markdown/PDF)** - 記錄翻譯統計

#### 實作
```python
# translation_tool/exporters/
class CrowdinCSVExporter:
    """匯出為 Crowdin CSV 格式"""
    
    @staticmethod
    def export(translations: dict, output_path: Path):
        import csv
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Identifier', 'Source', 'Translation', 'Context'])
            
            for key, value in translations.items():
                writer.writerow([key, key, value, ''])

class XLIFFExporter:
    """匯出為 XLIFF 格式"""
    
    @staticmethod
    def export(translations: dict, source_lang: str = 'en', 
               target_lang: str = 'zh-TW', output_path: Path = None):
        from xml.etree import ElementTree as ET
        
        xliff = ET.Element('xliff', version='1.2')
        file_elem = ET.SubElement(xliff, 'file', {
            'source-language': source_lang,
            'target-language': target_lang,
            'datatype': 'plaintext',
        })
        
        body = ET.SubElement(file_elem, 'body')
        
        for key, value in translations.items():
            trans_unit = ET.SubElement(body, 'trans-unit', id=key)
            source = ET.SubElement(trans_unit, 'source')
            source.text = key
            
            target = ET.SubElement(trans_unit, 'target')
            target.text = value
        
        tree = ET.ElementTree(xliff)
        tree.write(output_path, encoding='utf-8', xml_declaration=True)

class ReportExporter:
    """匯出翻譯報告"""
    
    @staticmethod
    def export_markdown(stats: dict, output_path: Path):
        """匯出 Markdown 報告"""
        report = f"""# 翻譯報告

## 統計資訊
- 總條目數: {stats['total']}
- 已翻譯: {stats['translated']} ({stats['translated']/stats['total']*100:.1f}%)
- 未翻譯: {stats['untranslated']}

## 品質分數
- 平均分數: {stats['avg_quality_score']:.1f}/100
- A 級: {stats['grade_a']} 條
- B 級: {stats['grade_b']} 條
- C 級: {stats['grade_c']} 條
- D 級: {stats['grade_d']} 條

## 問題摘要
- 格式錯誤: {stats['format_errors']}
- 術語不一致: {stats['glossary_issues']}
- 過度翻譯: {stats['over_translation']}

## 詳細問題列表
"""
        
        for issue in stats['issues']:
            report += f"\n### {issue['type']}\n"
            report += f"- 檔案: {issue['file']}\n"
            report += f"- 原文: {issue['source']}\n"
            report += f"- 譯文: {issue['target']}\n"
            report += f"- 說明: {issue['message']}\n"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
```

---

## 🚀 實作優先順序建議（更新版）

### P0 - 立即實作（1-2 週）🔥
1. **統一錯誤處理機制** - 提高穩定性、易於除錯
2. **UI 設計系統統一** - 改善使用體驗、視覺一致性
3. **翻譯品質檢查** - 避免低品質輸出、自動偵測常見錯誤
4. **術語資料庫** - 確保 Minecraft 術語一致性

### P1 - 短期實作（2-4 週）🔥
5. **翻譯記憶庫** - 減少重複工作、節省 API 成本
6. **批次處理邏輯重構** - 降低複雜度、提高可維護性
7. **工作流程模板** - 提高效率、降低使用門檻
8. **UI 響應速度優化** - 虛擬化列表、批次更新

### P2 - 中期實作（1-2 月）
9. **社群翻譯整合** - 避免重複工作、快速獲取基礎翻譯
10. **版本管理與回溯** - 提高安全性、可追蹤變更
11. **API 請求優化** - 預測性限流、健康度監控
12. **多執行緒安全性** - 避免競爭條件

### P3 - 長期實作（3+ 月）
13. **匯出格式支援** - Crowdin、XLIFF、報告
14. **JAR 提取效能優化** - 記憶體友善、支援超大檔案
15. **協作模式（未來）** - 多人翻譯、衝突解決

---

## 📊 技術債務清單（更新版）

### 高優先 🔥
1. `lm_translator_main.py` 的 while 迴圈需要重構（過於複雜，200+ 行）
2. 缺乏單元測試（`tests/` 資料夾存在但覆蓋率未知）
3. 日誌系統缺乏日誌輪替（logs/ 可能無限增長）
4. `config.json` 的 API key 應該移到環境變數或加密儲存（安全性）

### 中優先
5. 快取系統缺乏過期機制
6. 部分 view 的程式碼重複（可抽離 base class）
7. `services.py` 過於龐大（27KB），應拆分為多個檔案
8. 缺乏 type hints 在部分舊程式碼

### 低優先
9. 文件註解不夠詳細（特別是核心邏輯）
10. 部分變數命名不夠清晰（如 `zf`、`ctx`）
11. 缺乏 CI/CD 自動化測試

---

## 🎯 市場定位與差異化策略

### 你的工具定位
**「面向個人使用者的離線翻譯工具」**

- ✅ **離線優先**: 不依賴線上平台，完全本地化
- ✅ **繁體支援**: 填補 RPMTW 停止後的空缺
- ✅ **全自動化**: 從 JAR 提取到最終輸出，一條龍
- ✅ **零學習成本**: Flet 圖形介面，不需要學 Weblate
- ✅ **AI 驅動**: Gemini API 提供高品質機器翻譯

### 與競品的差異化

| 特性 | 你的工具 | CFPA | RPMTW | Maz-T 工具 |
|------|---------|------|-------|-----------|
| **離線使用** | ✅ | ❌ | ✅ | ✅ |
| **繁體支援** | ✅ | ❌ | ✅ (已停) | ✅ |
| **圖形介面** | ✅ | Web | 遊戲內 | ❌ |
| **自動化程度** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **協作功能** | ❌ | ✅ | ❌ | ❌ |
| **社群規模** | - | 大 | 中（已停） | 小 |

### 潛在使用者
1. **模組包製作者**: 需要快速翻譯大量模組
2. **繁體玩家**: RPMTW 停止後缺乏替代方案
3. **翻譯愛好者**: 想貢獻翻譯但不想學 Weblate
4. **YouTuber/實況主**: 需要快速產出繁中模組包

### 推廣策略建議
1. **YouTube 教學影片**: 展示完整流程（參考已有的繁中教學影片）
2. **巴哈姆特論壇**: 在 Minecraft 版發文（目前有討論熱度）
3. **GitHub Release**: 定期發布版本，附詳細更新日誌
4. **整合釘宮翻譯組**: 成為其「製作工具」而非競爭對手

---

## 📚 學習資源建議（更新版）

### 如果要深入改進此專案，建議學習：

#### 前端/UI
1. **Flet 官方文件**: https://flet.dev/docs/
2. **Material Design 3**: https://m3.material.io/
3. **UI/UX 設計原則**: "Don't Make Me Think" by Steve Krug

#### 後端/架構
4. **設計模式**: "Head First Design Patterns"（狀態機、策略模式）
5. **非同步程式設計**: Python `asyncio` vs `threading` vs `multiprocessing`
6. **資料庫優化**: SQLite 效能調校、FTS5 全文搜尋

#### 翻譯技術
7. **翻譯記憶庫**: TMX 格式標準、Translation Memory eXchange
8. **CAT 工具**: Trados、MemoQ 等專業翻譯工具的設計理念
9. **自然語言處理**: spaCy、NLTK（用於翻譯品質評估）

#### API 整合
10. **Gemini API 文件**: https://ai.google.dev/gemini-api/docs
11. **DeepL API**: 高品質翻譯 API
12. **Claude API**: Anthropic 的 LLM（支援更長上下文）

---

## 📝 總結

### 專案優勢 ✅
- ✅ **功能完整**: 涵蓋翻譯全流程（提取 → 翻譯 → 合併 → 輸出）
- ✅ **架構清晰**: 分層設計，易於維護與擴展
- ✅ **自動化程度高**: 減少手動操作，提高效率
- ✅ **使用現代技術**: Flet (Material 3)、Gemini API
- ✅ **繁體支援**: 填補市場空缺（RPMTW 已停止）

### 改進空間 ⚠️
- ⚠️ **UI 一致性可再提升**: 建議建立設計系統
- ⚠️ **缺乏協作機制**: 適合個人使用，多人協作需加強
- ⚠️ **翻譯品質檢查可加強**: 建議新增自動化檢查
- ⚠️ **效能在大型模組包可能不足**: 需優化記憶體使用

### 核心建議 🎯
1. **先完善基礎**（P0）:
   - 統一錯誤處理
   - UI 設計系統
   - 翻譯品質檢查
   - 術語資料庫

2. **再擴展功能**（P1）:
   - 翻譯記憶庫
   - 批次處理重構
   - 工作流程模板

3. **最後優化效能**（P2）:
   - 當處理大型模組包時再針對性優化
   - API 請求優化
   - 多執行緒安全性

### 市場機會 🚀
- RPMTW 停止後，繁體翻譯工具有市場需求
- 釘宮翻譯組持續更新，但缺乏配套工具
- 可定位為「製作翻譯包的工具」而非「翻譯包本身」
- 潛在使用者：模組包製作者、繁體玩家、翻譯愛好者

---

**報告結束**  
如有任何問題或需要更詳細的實作建議，請隨時詢問！

---

## 附錄A: 網路研究來源（更新版）

### 繁體中文社群
1. **RPMTW 繁體翻譯資源包** (已封存)
   - https://github.com/RPMTW/ResourcePack-Mod-zh_tw
   - 巴哈討論: https://forum.gamer.com.tw/C.php?bsn=18673&snA=189309
   - 核心發現: 維護負擔大、個人因素導致停止

2. **釘宮翻譯組 (ModsTranslationPack)**
   - https://modrinth.com/resourcepack/modstranslationpack
   - 持續更新至 1.21.x
   - 被推薦為 RPMTW 停止後的替代方案

3. **繁中翻譯教學影片**
   - https://www.youtube.com/watch?v=z40QMPm8nfM (2026/01/02)
   - 標題：輕鬆將模組包繁體中文化

### 簡體中文社群
4. **CFPA 簡中翻譯專案**
   - https://github.com/CFPAOrg/Minecraft-Mod-Language-Package
   - 官網: https://cfpa.site/
   - i18nupdatemod: 自動更新模組
   - 完善的翻譯資源庫（詞典、Wiki、工具）

### 英文社群
5. **AutoTranslation Mod**
   - https://github.com/Moirstral/AutoTranslation
   - 核心痛點：PR 回應慢、缺乏即時翻譯

6. **Reddit /r/feedthebeast 討論**
   - https://www.reddit.com/r/feedthebeast/comments/cisijj/
   - 核心需求：避免格式破壞、字數限制、AI 翻譯工具

7. **Minecraft Mod Translator (Python)**
   - https://github.com/Maz-T/Minecraft-Mods-Translator
   - 本地翻譯工具，適用於 1.16+

---

## 附錄B: 競品分析矩陣（更新版）

### 功能矩陣

| 功能 | 你的工具 | CFPA | RPMTW | 釘宮翻譯組 | Maz-T | AutoTranslation |
|------|---------|------|-------|-----------|-------|----------------|
| **JAR 自動提取** | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **機器翻譯整合** | ✅ (Gemini) | ❌ | ✅ (Google) | ❌ | ✅ | ✅ (Google) |
| **簡繁轉換** | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| **翻譯快取** | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| **品質檢查** | 🚧 (開發中) | ⭐⭐⭐ | ⭐⭐ | ❌ | ❌ | ❌ |
| **術語管理** | 🚧 (建議新增) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **版本控制** | 🚧 (建議新增) | ✅ (Weblate) | ❌ | ❌ | ❌ | ❌ |
| **協作功能** | ❌ | ✅⭐⭐⭐⭐⭐ | ❌ | ❌ | ❌ | ❌ |
| **離線使用** | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| **圖形介面** | ✅ | Web | 遊戲內 | ❌ | ❌ | 遊戲內 |

### 使用者體驗矩陣

| 指標 | 你的工具 | CFPA | RPMTW | 釘宮翻譯組 | Maz-T |
|------|---------|------|-------|-----------|-------|
| **學習成本** | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐ |
| **安裝難度** | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐⭐ |
| **操作便利性** | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| **錯誤處理** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | - | ⭐⭐ |
| **文件完整度** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |

---

**檔案位置**: `C:\Users\admin\Desktop\minecraft_translator_flet_analysis_v2.md`  
**版本**: v2.0  
**更新內容**: 整合網路研究、競品深度分析、市場定位建議
