# Cache View 重構設計（方案 A）

## 現況分析

### 目前架構
- `cache_view.py`：3392 行，單一檔案管理三個功能區塊
- 使用 `ft.Tabs` 切換兩個主 tab：
  - **Tab 1**: 總覽 / 管理（Overview）
  - **Tab 2**: 查詢 + 分片（Query + Shard 在同一頁）

### 現有子模組（已分出）
| 檔案 | 行數 | 職責 |
|------|------|------|
| `cache_overview_panel.py` | 114 | 總覽 UI 組裝 |
| `overview_panel.py` | 66 | 總覽內容 |
| `query_panel.py` | 51 | 查詢面板 |
| `shard_panel.py` | 42 | 分片面板 |
| `cache_actions.py` | 96 | 動作執行 |
| `cache_presenter.py` | 62 | 呈現邏輯 |
| `cache_controller.py` | 45 | 控制邏輯 |
| `cache_state.py` | 33 | 狀態管理 |
| `cache_history_store.py` | 133 | 歷史紀錄 |

### 問題
- cache_view.py 仍有 3392 行，太大
- 三個功能區塊（Overview/Query/Shard）耦合在同一檔案
- 每次 did_mount 會初始化全部資料，啟動慢

---

## 重構目標

將 `cache_view.py` 拆分為三個獨立的 View：

1. **CacheOverviewView** - 總覽 / 管理
2. **CacheQueryView** - 查詢（唯讀搜尋）
3. **CacheShardView** - 分片編輯

每個 View：
- 獨立檔案（~500-800 行）
- 獨立生命周期
- 可獨立 Lazy Loading

---

## 拆分細節

### 1. CacheOverviewView（總覽頁）

**新檔案**: `app/views/cache_overview_view.py`

**職責**:
- 顯示各 cache_type 統計
- 重新載入按鈕
- 重建搜尋索引按鈕
- 日誌顯示區

**從 cache_view.py 移出**:
- `self.overview_text`, `self.overview_status`, `self.overview_trace`
- `self.btn_reload_all`, `self.btn_refresh_stats`, `self.btn_rebuild_index`
- `self.type_list`, `self.log_list`, `self.btn_log_clear`, `self.btn_log_copy`
- `_build_overview_page()` 整個方法
- `_on_reload_all()`, `_on_refresh_stats()`, `_on_rebuild_index()`
- `_load_overview()`, `_render_logs()`, `_on_log_filter_changed()`

**依賴**:
- `cache_actions.py` - 執行 reload/rebuild
- `cache_state.py` - 狀態管理
- `log_unit.py` - 日誌

---

### 2. CacheQueryView（查詢頁）

**新檔案**: `app/views/cache_query_view.py`

**職責**:
- 關鍵字搜尋
- 搜尋結果列表
- 單筆詳情檢視
- 歷史紀錄浮動視窗
- DST 唯讀檢視（不可編輯）

**從 cache_view.py 移出**:
- 所有 Query 相關的 UI 元件
- `_build_query_entry_page()` 中的查詢區塊
- 查詢相關的事件處理
- 歷史紀錄浮動視窗

**依賴**:
- `cache_search.py` - 搜尋引擎
- `cache_state.py` - 查詢狀態
- `cache_history_store.py` - 歷史紀錄

---

### 3. CacheShardView（分片頁）

**新檔案**: `app/views/cache_shard_view.py`

**職責**:
- 分類/分片清單
- Key 列表
- SRC/DST 編輯
- 歷史紀錄浮動視窗

**從 cache_view.py 移出**:
- 所有 Shard 相關的 UI 元件
- 分片導航區塊
- Key 列表區塊
- SRC/DST 編輯區塊
- 分片相關的事件處理
- 歷史紀錄浮動視窗（Shard 用）

**依賴**:
- `cache_actions.py` - 存檔動作
- `cache_state.py` - 分片狀態
- `cache_history_store.py` - 歷史紀錄

---

## 實作步驟

### Phase 0: 盤點與準備

1. **建立新目錄結構**
   ```
   app/views/cache/
   ├── __init__.py
   ├── cache_overview_view.py    # 新增
   ├── cache_query_view.py       # 新增
   └── cache_shard_view.py      # 新增
   ```

2. **建立共同基底**（可選）
   - 若三個 View 有共同 UI 元件，可建立 `CacheBaseView(ft.Column)`
   - 目前不建議，先保持完全獨立

### Phase 1: 建立 CacheOverviewView

1. 複製 cache_view.py 到 cache_overview_view.py
2. 刪除 Query/Shard 相關程式碼
3. 只保留 Overview 相關的：
   - UI 元件
   - 事件處理
   - did_mount 中的 overview 初始化
4. 修改 class 名稱為 `CacheOverviewView`
5. 測試編譯

### Phase 2: 建立 CacheQueryView

1. 複製 cache_view.py 到 cache_query_view.py
2. 刪除 Overview/Shard 相關程式碼
3. 只保留 Query 相關的
4. 修改 class 名稱為 `CacheQueryView`
5. 測試編譯

### Phase 3: 建立 CacheShardView

1. 複製 cache_view.py 到 cache_shard_view.py
2. 刪除 Overview/Query 相關程式碼
3. 只保留 Shard 相關的
4. 修改 class 名稱為 `CacheShardView`
5. 測試編譯

### Phase 4: 更新 view_registry.py

1. 新增三個 view 的 import mapping
2. 更新 NavigationRail labels
3. 測試切換

### Phase 5: 清理

1. 刪除舊 cache_view.py（或標記為 deprecated）
2. 清理重複的 import
3. 更新所有 import cache_view 的檔案

---

## 程式碼遷移對照表

### Overview 遷移

| 原始位置 | 目標 |
|---------|------|
| `cache_view.py` | `cache_overview_view.py` |
| `self.overview_*` | `self.overview_*` |
| `_build_overview_page()` | 改為 `__init__` 內容 |
| `_on_reload_all()` | 搬移 |
| `_on_refresh_stats()` | 搬移 |
| `_on_rebuild_index()` | 搬移 |
| `_load_overview()` | 搬移 |
| `_render_logs()` | 搬移 |

### Query 遷移

| 原始位置 | 目標 |
|---------|------|
| `cache_view.py` | `cache_query_view.py` |
| `self.tf_query_*` | 搬移 |
| `self.query_*` (結果) | 搬移 |
| `self.query_history_*` | 搬移 |
| `_build_query_entry_page()` | 改為 `__init__` 內容 |
| `_on_query_search()` | 搬移 |
| `_render_query_results()` | 搬移 |
| `_render_query_detail()` | 搬移 |

### Shard 遷移

| 原始位置 | 目標 |
|---------|------|
| `cache_view.py` | `cache_shard_view.py` |
| `self.shard_*` | 搬移 |
| `self.shard_history_*` | 搬移 |
| 分片導航 UI | 搬移 |
| Key 列表 UI | 搬移 |
| SRC/DST 編輯 UI | 搬移 |
| `_render_query_type_shard_page()` | 搬移 |
| `_load_shard_rows()` | 搬移 |
| `_on_shard_*()` | 搬移 |

---

## 風險與注意事項

### 1. 共享狀態
- `cache_state.py` 的狀態可能在多個 View 共用
- 需確認是否需要跨 View 通訊
- 若需要，可使用 Flet 的 EventChannel 或共享 presenter

### 2. did_mount 時機
- 每個 View 首次顯示時才會觸發 did_mount
- 這正好達到 Lazy Loading 效果
- 但確保每個 View 初始狀態正確

### 3. UI 元素參考
- 某些事件處理可能參照其他區塊的 UI
- 搬移時需一併搬移相關元件

### 4. 測試策略
- 每建立一個 View 就測試編譯
- 確保 import 正確
- 最後整体測試切換功能

---

## 預期結果

| 指標 | 拆分前 | 拆分後 |
|------|--------|--------|
| cache_view.py 行數 | 3392 | 刪除 |
| 每個 View 平均行數 | - | ~600-800 |
| 啟動時間 | 慢（全部初始化） | 快（按需載入） |
| 可維護性 | 低 | 高 |

---

## 驗收清單

- [ ] CacheOverviewView 可正常顯示統計
- [ ] CacheQueryView 可正常搜尋
- [ ] CacheShardView 可正常編輯分片
- [ ] 三個 View 可正常切換
- [ ] 原有功能不受影響
- [ ] 所有單元測試通過
