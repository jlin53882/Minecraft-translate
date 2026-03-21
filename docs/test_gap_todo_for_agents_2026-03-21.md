# Minecraft Translator Flet — 補測任務清單（給其他 Agent 執行）

> 日期：2026-03-21  
> 專案路徑：`C:\Users\admin\Desktop\minecraft_translator_flet`  
> 用途：交給其他 agent 直接依清單補單元測試 / 補行為測試  
> 重要前提：**請在專案目錄內執行** `pytest -q`，不要用全域 Python 環境直接下結論。

---

## 一、任務目標

本專案測試數量已不少，核心翻譯模組（如 `lm_translator*`、`lang_merge*`、`kubejs_*`、`md_*`）已有一定覆蓋。

目前最需要補的，不是單純增加測試數量，而是補強以下區塊：

1. **錯誤處理核心**
2. **設定管理核心**
3. **service / orchestration glue code**
4. **cache / JSON 邊界行為**
5. **UI 狀態切換與防重入行為**

---

## 二、執行規則（必守）

1. **先補測，不先重構**。除非測試真的無法落地，否則避免順手改 production code 邏輯。
2. 每補完一批測試，至少驗證：
   - `pytest -q <新增/修改的測試檔>`
   - 若有影響共用模組，再跑：`pytest -q`
3. 若遇到現有程式設計與測試性衝突，**先記錄問題，再最小幅調整**。
4. 不要把 import smoke test 當作高覆蓋完成；優先補「實際行為」測試。
5. 所有新測試請盡量：
   - 使用 `pytest`
   - 使用 `tmp_path` / `monkeypatch` / `unittest.mock`
   - 避免真實網路 / 真實外部服務依賴

---

## 三、優先級總表

| 優先級 | 模組 / 區塊 | 建議測試檔 | 原因 |
|---|---|---|---|
| P0 | `translation_tool/utils/exceptions.py` | `tests/test_exceptions.py` | 錯誤分類、retry、decorator 核心，風險高 |
| P0 | `translation_tool/utils/config_manager.py` | `tests/test_config_manager.py` | config 載入/merge/驗證/路徑解析是基礎設施核心 |
| P1 | `app/services_impl/cache/cache_services.py` | `tests/test_cache_services.py` | service glue code，底層有測不等於 service 安全 |
| P1 | `app/services_impl/pipelines/*.py` | `tests/test_pipeline_services_*.py` | 參數轉接、session lifecycle、error mapping 容易出錯 |
| P1 | `translation_tool/utils/safe_json_loader.py` | `tests/test_safe_json_loader.py` | 壞 JSON / BOM / 缺檔 / 空檔是典型邊界 |
| P2 | `translation_tool/utils/cache_loader.py` | `tests/test_cache_loader.py` | cache 載入入口值得直接保護 |
| P2 | `translation_tool/utils/cache_search.py` | `tests/test_cache_search.py` | 搜尋結果、filter、fuzzy 行為需契約化 |
| P2 | `translation_tool/utils/log_unit.py` | `tests/test_log_unit.py` | log wrapper / progress / duration 易 silently 壞掉 |
| P2 | `translation_tool/utils/config_access.py` | `tests/test_config_access.py` | runtime config 入口應有 API 契約測試 |
| P2 | `translation_tool/utils/species_cache.py` | `tests/test_species_cache.py` | cache hit/miss/fallback 容易藏 bug |
| P3 | 各 view UI 狀態機 | 擴充既有 `*_view_characterization.py` 或新檔 | 現有多偏 characterization，不夠深 |
| P3 | `app/ui/quick_jump.py` | `tests/test_quick_jump.py` | 小功能但互動性高，易壞 |

---

## 四、詳細補測任務

---

# P0-1 `translation_tool/utils/exceptions.py`

## 目標
建立 `tests/test_exceptions.py`

## 為什麼要補
這是系統級錯誤處理核心。若 retry / 包裝 / re-raise 行為錯誤，會影響多條流程，但一般 happy path 測試很難發現。

## 建議補的測試案例

### A. decorator / retry 行為
- `handle_translation_errors` 遇到 `RateLimitError` 時會重試
- `handle_translation_errors` 遇到 `OverloadError` 時會重試，且 backoff 計算正確
- 達到最大重試次數後，會拋出最後錯誤
- 遇到 `APIError` / `ConfigError` / `CacheError` / `FileFormatError` 時，不應誤重試

### B. 驗證 helper
- `raise_if_invalid_json`：缺欄位時拋出預期錯誤
- `raise_if_invalid_json`：合法資料不拋錯
- `raise_if_empty`：空字串 / `None` / 空 list 等行為符合預期

### C. logging side effect
- `_log_error_to_file` 寫檔成功
- `_log_error_to_file` 寫檔失敗時，不應讓主流程再炸一次

## 驗收標準
- 新檔 `tests/test_exceptions.py` 可獨立通過
- 覆蓋主要例外分支與 retry 分支

---

# P0-2 `translation_tool/utils/config_manager.py`

## 目標
建立 `tests/test_config_manager.py`

## 為什麼要補
這是整個專案設定系統核心，包含：
- project root / path resolution
- load / save config
- 預設值合併
- schema / 型別驗證

這類模組一旦出錯，影響面很大。

## 建議補的測試案例

### A. 路徑解析
- `get_project_root()` 在不同 cwd 下仍指向正確專案根
- `resolve_project_path(None)` 行為正確
- `resolve_project_path(relative_path)` 正確相對於 project root
- `resolve_project_path(absolute_path)` 保持正確

### B. config load / save
- config 檔不存在時，回傳預設值
- 載入合法 config 時，能正確覆蓋預設值
- `save_config()` 後再 `load_config()` round-trip 正常

### C. deep merge
- 深層 dict merge 不會覆蓋掉兄弟節點
- override 為空時保留 default
- override 為部分欄位時只更新指定值

### D. validation
- 錯誤型別會拋出正確錯誤
- 合法型別會通過
- 關鍵欄位缺失時行為明確

## 驗收標準
- 能直接保護 config 讀寫與驗證邏輯
- 不依賴外部真實使用者設定檔

---

# P1-1 `app/services_impl/cache/cache_services.py`

## 目標
建立 `tests/test_cache_services.py`

## 為什麼要補
底層 cache store/shards/search 已有部分測試，但 service 層是 UI 對 core 的接點，容易出現：
- 參數轉接錯誤
- 回傳格式不一致
- 錯誤處理不一致

## 建議補的測試案例
- reload service 成功時回傳格式正確
- rebuild index 成功時回傳格式正確
- rebuild index 失敗時回傳錯誤結果正確
- rotate service 對合法/非法 `cache_type` 行為正確
- update dst service 在 key 不存在時回傳合理結果
- delete / invalidate / export 等 service 的成功與失敗路徑

## 驗收標準
- service 層的回傳契約穩定
- 不直接依賴真實大型 cache 目錄，可用 monkeypatch / tmp_path

---

# P1-2 `app/services_impl/pipelines/*.py`

## 目標
為各 pipeline service 建立專屬測試檔，建議分檔：
- `tests/test_pipeline_services_ftb.py`
- `tests/test_pipeline_services_kubejs.py`
- `tests/test_pipeline_services_lm.py`
- `tests/test_pipeline_services_md.py`
- `tests/test_pipeline_services_extract.py`
- `tests/test_pipeline_services_merge.py`
- `tests/test_pipeline_services_bundle.py`
- `tests/test_pipeline_services_lookup.py`

## 為什麼要補
`_task_runner` 有測，不等於各 pipeline service 自己的參數、session、error mapping 有測完整。

## 每個 service 至少要補的案例
- 正常呼叫路徑
- `dry_run=True` / flag 傳遞正確
- 底層 generator / function throw exception 時，service 回傳或上拋行為正確
- session lifecycle（start / finish / error）與預期一致
- 缺少必要輸入時 fail-fast

## 驗收標準
- 每個 service 至少有「正常 + 失敗」兩類測試
- 優先保護參數轉接與錯誤處理

---

# P1-3 `translation_tool/utils/safe_json_loader.py`

## 目標
建立 `tests/test_safe_json_loader.py`

## 為什麼要補
這是典型外部輸入邊界。真實資料最容易在這裡出問題。

## 建議補的測試案例
- 正常 JSON 檔可讀
- UTF-8 BOM 可處理
- 檔案不存在時行為明確
- 壞 JSON 時行為明確
- 空檔案 / 空白內容處理
- 非 dict root 的處理是否符合設計

## 驗收標準
- 覆蓋讀檔成功、解碼問題、格式問題三類分支

---

# P2-1 `translation_tool/utils/cache_loader.py`

## 目標
建立 `tests/test_cache_loader.py`

## 為什麼要補
cache 載入是 runtime state 的來源之一，值得有專屬測試，而不只靠間接覆蓋。

## 建議補的測試案例
- 空資料夾
- 單一 shard
- 多 shard 合併
- 一個 shard 壞掉但其他可讀時的策略
- 不存在目錄時的行為

---

# P2-2 `translation_tool/utils/cache_search.py`

## 目標
建立 `tests/test_cache_search.py`

## 為什麼要補
搜尋是高使用頻率功能，filter/fuzzy/metadata 常有隱性 bug。

## 建議補的測試案例
- 搜尋無結果
- 指定 `cache_type` filter
- fuzzy 開 / 關
- 結果排序 / 去重
- 壞 entry 時不應整體炸掉

---

# P2-3 `translation_tool/utils/log_unit.py`

## 目標
建立 `tests/test_log_unit.py`

## 為什麼要補
log wrapper 看起來簡單，但若 caller、progress、duration 壞掉，排查成本很高。

## 建議補的測試案例
- `log_info/log_warning/log_error/log_debug` 轉發正確
- `progress()` 在 0~1 以外時 clamp 正確
- `get_formatted_duration()` 對秒/分/時邊界格式正確
- 空字串 / unicode 訊息行為正常

---

# P2-4 `translation_tool/utils/config_access.py`

## 目標
建立 `tests/test_config_access.py`

## 為什麼要補
這是 runtime config 入口，應有穩定 API 契約。

## 建議補的測試案例
- `get_runtime_config()` 正確代理
- `resolve_runtime_path(None)` 行為正確
- `resolve_runtime_path(relative)` 相對 project root 正確
- `resolve_runtime_path(absolute)` 保持不變

---

# P2-5 `translation_tool/utils/species_cache.py`

## 目標
建立 `tests/test_species_cache.py`

## 為什麼要補
cache + fallback + 外部資料源類邏輯，最容易有 hit/miss/fallback 邊界 bug。

## 建議補的測試案例
- cache hit
- cache miss
- 查詢失敗 fallback
- 空 cache 初始化
- 壞 cache 檔處理

---

# P3-1 UI 狀態機測試（擴充既有 view tests）

## 目標
擴充以下現有測試類型，不只驗 control 存在：
- `test_bundler_view_characterization.py`
- `test_qc_view_characterization.py`
- `test_translation_view_characterization.py`
- `test_lookup_view_characterization.py`
- `test_rules_view_characterization.py`
- `test_config_view_characterization.py`（若無可新建）
- `test_extractor_view_characterization.py`
- `test_icon_preview_view_characterization.py`

## 為什麼要補
目前多數偏 characterization，不夠保護「互動行為」。

## 建議補的測試案例
- 開始執行後 UI 進入 busy 狀態
- 成功後回 ready
- 失敗後按鈕重新 enable
- 重複點擊時防重入
- stale callback 不污染新狀態
- snackbar / status / log 狀態一致

---

# P3-2 `app/ui/quick_jump.py`

## 目標
建立 `tests/test_quick_jump.py`

## 為什麼要補
互動型小元件容易在重構時壞掉，但通常沒人第一時間注意。

## 建議補的測試案例
- 初始化
- 搜尋/過濾
- 選取項目
- 取消/確認
- keyboard navigation

---

## 五、不建議當作完成證據的測試

以下類型可以保留，但**不要把它們當成補測完成的主要成果**：

- `test_high_priority_modules.py`
- `test_medium_priority_modules.py`
- `test_remaining_modules.py`

### 原因
這些多半只是：
- import 成功
- symbol 存在

它們只能算 sanity check，不能代表行為覆蓋完整。

---

## 六、建議執行順序（可直接派工）

### 第一批（先做）
1. `tests/test_exceptions.py`
2. `tests/test_config_manager.py`
3. `tests/test_safe_json_loader.py`

### 第二批
4. `tests/test_cache_services.py`
5. `tests/test_pipeline_services_ftb.py`
6. `tests/test_pipeline_services_kubejs.py`
7. `tests/test_pipeline_services_lm.py`
8. `tests/test_pipeline_services_md.py`

### 第三批
9. `tests/test_cache_loader.py`
10. `tests/test_cache_search.py`
11. `tests/test_log_unit.py`
12. `tests/test_config_access.py`
13. `tests/test_species_cache.py`

### 第四批
14. 擴充各 `*_view_characterization.py` 成行為測試
15. `tests/test_quick_jump.py`

---

## 七、交付要求（給執行 Agent）

完成每一批後，請一併回報：

1. 新增/修改了哪些測試檔
2. 每個測試檔主要覆蓋哪些 case
3. 執行了哪些驗證命令
4. 是否發現 production code 存在難測設計問題
5. 若有小幅改 production code，需說明原因與風險

---

## 八、最終一句話

本專案目前最值得補的，不是核心翻譯演算法本體，而是：

**錯誤處理、設定管理、service glue、cache/JSON 邊界、以及 UI 狀態行為。**
