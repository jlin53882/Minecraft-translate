# Cache_mange 設計稿（完整版本 v1.1）

> 適用專案：`minecraft_translator_flet`
> 
> 目標：在**不改大架構**前提下，新增一個可維運的 Cache 管理 UI（含查詢、檢視、編輯 DST、落盤流程）

---

## 0. 本次實作需新增/修改檔案

### 新增
- `app/views/cache_view.py`：Cache 管理主畫面（Summary / Table / Inspector Tabs / Logs）

### 修改
- `main.py`：加入 `CacheView` 導覽入口
- `app/services.py`：新增 cache UI 專用 service 介面
- `translation_tool/utils/cache_manager.py`：補充 overview/stats helper API
- `Cache_mange設計.md`：維護本設計與變更清單

---

## 1. 設計目標

這頁聚焦三件事：

1. **看狀態**：每種 cache 的總筆數、dirty、active shard
2. **做操作**：reload / save(new) /（進階）fill active / rotate
3. **查內容**：Key 與 DST 兩種查詢、命中列表、詳細檢視、DST 修改

---

## 2. 資訊架構（同一個 View）

單一 `CacheView`，分為左/右兩大區。

- 左側：Summary、全域操作、Per-Type 表格、日誌
- 右側：Inspector（**同一個區塊，用 Tabs 切兩頁**）
  - Tab A：Key 查詢
  - Tab B：DST 查詢/修改

> 不拆成兩個獨立頁面，降低心智負擔，保留切換效率。

---

## 3. UI 版面規格

### 3.1 Summary Cards（頂部）

5 張卡：`lang / patchouli / ftbquests / kubejs / md`

每張顯示：
- Cache Type
- Entries（總數）
- Session New（本次新增）
- Dirty 狀態（CLEAN / DIRTY）
- Active Shard ID
- Last Shard File（可選）

全域摘要顯示：
- Total Entries
- Dirty Types
- Last Reload Time
- Last Save Time
- Cache Root Path

### 3.2 全域操作區（Global Actions）

安全操作：
- Reload All
- Refresh Stats
- Open Cache Folder
- Save All (New Shard)

高風險操作（進階）：
- Save All (Fill Active)
- Rotate Active Shard
- Cleanup Old Shards（Phase 2）

安全開關：
- `我知道這會覆蓋檔案，確認執行。`

### 3.3 Per-Type Table

欄位：
- Type
- Total
- New
- Status
- Active Shard
- Last File
- Actions

每列 Actions：
- Query
- Save(New)
- Reload
- More（進階）

### 3.4 Inspector（右側，Tabs）

#### Tab A：Key 查詢
- 類型下拉
- Key 關鍵字輸入
- 查詢 / 清空
- 命中清單（分頁）
- 詳細內容（唯讀）
- Copy DST

#### Tab B：DST 查詢/修改
- 類型下拉
- DST 關鍵字輸入
- 查詢 / 清空
- 命中清單（分頁）
- 詳細內容（DST 可編）
- 套用變更 / 還原
- 顯示 `Modified (not saved)` 狀態

### 3.5 日誌區（Log Console）
- 顯示 INFO/WARN/ERROR
- Clear / Copy
- Only Error filter
- 建議上限 500~1000 行

---

## 4. 查詢規格（關鍵）

### 4.1 查詢模式

Inspector 查詢分兩種：
- Key 模式：prefix/contains
- DST 模式：contains（大小寫不敏感）

### 4.2 命中資料結構（統一）

```python
{"key": "...", "rank": 1, "preview": "..."}
```

- `key`：cache key
- `rank`：排序優先級（0/1/2）
- `preview`：DST 模式時顯示短預覽（可空）

### 4.3 排序規則（最相關在最前）

- `0`: exact
- `1`: startswith
- `2`: contains
- 最終排序：`(rank, key)`

### 4.4 分頁規格

- 顯示：`第 X / Y 頁`
- 預設每頁：50（可切 20/50/100）
- 支援上一頁/下一頁/輸入跳頁
- 跳頁輸入框僅允許數字 + Enter 觸發

### 4.5 結果過多防護

`cache_search_service(..., limit=5000)`

若超過上限：
- 顯示提示：`結果過多，已取前 5000 筆`
- 仍保留分頁（針對已取資料）

---

## 5. 編輯與儲存語意（必須清楚）

### 5.1 Inspector 按鈕語意

- **套用變更**：只更新記憶體 cache（`add_to_cache`）並標記 dirty
- **還原**：回到載入時 dst

### 5.2 落盤行為

- 真正寫檔不在 Inspector 做
- 由全域按鈕執行：
  - Save All (New Shard)
  - Save All (Fill Active)

### 5.3 文案要求

- 套用成功提示：`已修改，尚未落盤（Dirty）`
- 避免混用「儲存」造成誤解

---

## 6. 編輯保護（防誤操作）

若 DST 有未套用變更，以下動作前需確認：
- 切換 Tab（DST → Key）
- 切換 DST 類型下拉
- 點選其他命中 key
- 重新查詢

彈窗文案：
- `尚有未套用變更，是否捨棄？`

---

## 7. Busy 與按鈕禁用規則

新增全域狀態：
- `ui_busy: bool`
- `busy_reason: str`（RELOADING / SAVING）

當全域操作執行中：
- 禁用：搜尋、套用變更、type 切換（可選）
- 高風險按鈕一定禁用
- Inspector 顯示 badge：`RELOADING...` / `SAVING...`

---

## 8. 狀態與色彩規格

狀態枚舉：
- READY
- DIRTY
- RELOADING
- SAVING
- ERROR

色彩：
- READY：`GREEN_600`
- DIRTY：`AMBER_700`
- SAVING/RELOADING：`BLUE_600`
- ERROR：`RED_600`

---

## 9. State 方案（建議）

```python
self.state = {
  "overview": {},
  "types": {},
  "ui_busy": False,
  "busy_reason": "",

  "key_query": "",
  "key_type": "lang",
  "key_hits_all": [],
  "key_page": 1,
  "key_page_size": 50,
  "key_total_pages": 1,
  "key_selected_key": None,

  "dst_query": "",
  "dst_type": "lang",
  "dst_hits_all": [],
  "dst_page": 1,
  "dst_page_size": 50,
  "dst_total_pages": 1,
  "dst_selected_key": None,
  "dst_original_value": "",
  "dst_current_value": "",
  "dst_modified": False,

  "last_reload_at": None,
  "last_save_at": None,
}
```

> `danger_enabled` 不獨立存 state，直接用 `chk_danger_confirm.value` 作為唯一來源。

---

## 10. Service 介面建議

```python
cache_get_overview_service()
cache_reload_service()
cache_save_all_service(write_new_shard: bool)
cache_search_service(cache_type: str, query: str, mode: str, limit: int = 5000)
cache_get_entry_service(cache_type: str, key: str)
cache_update_dst_service(cache_type: str, key: str, new_dst: str)
```

---

## 11. Logging 規範

- 服務層與操作流程統一用 `log_unit.py`
  - `log_info`
  - `log_warning`
  - `log_error`
- UI Log Console 只做展示與過濾，不散落自建 logger 行為

---

## 12. 開發分期（建議）

### MVP（先上線）
- Summary + Table
- Reload / Save(New) / Refresh
- Inspector Tabs（Key + DST）
- DST 套用變更（記憶體）
- 分頁、排序、日誌

### Phase 2（增強）
- Save(Fill Active)
- Rotate / Cleanup
- Recent modified list
- 命中率統計、報表輸出

---

## 13. 驗收清單（Done 定義）

- [ ] Key/DST 兩個 Tab 在同一 View 可切換
- [ ] 查詢結果分頁顯示 `X / Y`
- [ ] 相關度排序正確（exact > startswith > contains）
- [ ] DST 可編輯，套用後該 type 變 DIRTY
- [ ] 全域 Save 才會落盤
- [ ] busy 狀態時按鈕禁用一致
- [ ] 高風險操作需要勾選確認
- [ ] 大量查詢不會卡死（limit + 分頁）
- [ ] 日誌可清空/複製/過濾錯誤

---

以上為目前完整設計稿，可直接作為實作基準。