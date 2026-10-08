# Cache View 架構

## 定位

CacheView（`app/views/cache_view.py` 為主入口；功能實作以 mixin 拆在 `app/views/cache_manager/cache_view_*.py`）是快取系統的 UI 入口，頁面標題「快取管理」（副標「翻譯快取分片儲存與全文檢索；命中愈高，API 花費愈低」），預設停在「總覽 / 管理」分頁，以 `ft.Tabs` 分為「總覽 / 管理」與「查詢」兩個主分頁（`_build_main_tabs`），功能包含：

| 區域 | 功能 |
|------|------|
| 總覽 | 四張統計卡（快取總筆數 / 有變更的類型 / 快取類型 / 最近儲存）、預設收合的「詳細資訊」；下方左側「分類狀態清單」（各 cache_type 卡片：重新載入 / 新分片 / 補滿舊檔 / 輪替分片 / 分析 / 切換查詢），右側「操作」卡（狀態列、重新載入全部、刷新統計、重建搜尋索引、「補滿舊檔」高風險確認勾選）、預設收合的「按鈕說明」與「日誌」（預設開啟「只看警告以上」，另有複製全部 / 清空） |
| 查詢 | 查詢頁內有「查詢區」與「分類/分片」兩個子分頁（預設「查詢區」）。查詢區為「查詢區塊（Explorer）」：關鍵字輸入（placeholder「輸入 key / dst / 關鍵字」）+「搜尋」「清空」，模式與分類兩個下拉預設皆「全部」（Key / DST / 全部模式，含全文索引），左側「結果列表」（空時顯示「沒有搜尋結果」）、右側「內容檢視」（Key / 類型 / Shard / Cache 狀態、可展開 SRC）與「歷史紀錄」按鈕，底部為分頁列（每頁預設 50）與「套用」「還原」「還原最新」 |
| 分片編輯 | 「分類/分片」子分頁依 cache_type 列出卡片（分片編號、狀態、筆數、shard 使用量、「切換查詢」與預設收合的「分片清單」）；進入 shard → 選 key → SRC 預覽（preview/raw 模式）、編輯 dst、復原、複製、還原最新、套用歷史版本 |
| 歷史 | 查詢區與分片區各有一個浮動視窗（標題「版本歷史紀錄」，預設隱藏，由「歷史紀錄」按鈕開啟），查看/套用歷史事件 |

## 檔案結構（實際現況）

```
app/views/cache_view.py                         ← 主入口（ft.Column）：__init__、生命週期、忙碌／停用狀態、髒標記刷新
app/views/cache_manager/
  ├─ cache_view_overview.py     CacheOverviewMixin       總覽：類型清單、批次操作、日誌
  ├─ cache_view_query.py        CacheQueryMixin          查詢：搜尋、結果顯示／分頁、單筆編輯
  ├─ cache_view_query_widgets.py CacheQueryWidgetsMixin  查詢頁（含兩個浮動歷史視窗）的 widgets 組裝
  ├─ cache_view_shard.py        CacheShardMixin          分片：清單、key 清單、分頁、dst 複製
  ├─ shard_reader.py            分片檔摘要（key 數量／key 清單）以檔案簽名記憶；背景預熱，event loop 上只剩記憶命中
  ├─ cache_view_shard_detail.py CacheShardDetailMixin    分片詳情：SRC 預覽、DST 編輯
  ├─ cache_view_shard_widgets.py CacheShardWidgetsMixin  分片頁與主分頁版面的 widgets 組裝
  ├─ cache_view_history.py      CacheHistoryMixin        查詢／分片共用的歷史視窗與還原
  ├─ cache_actions.py          run_cache_action()          ← 被引用
  ├─ cache_history_store.py    history_* 函式              ← 被引用
  ├─ cache_overview_panel.py   build_overview_page()       ← 被引用（內部再用 cache_log_panel.py 的 build_log_panel）
  └─ cache_state.py            CacheQueryState/ShardState/HistoryState ← 被引用
```

`CacheView` 以多重繼承組合上述 mixin；mixin 方法內容由原本單一檔案原樣搬出（#114），所有方法仍以 `CacheView._xxx` 呼叫。**測試要 monkeypatch 服務函式時，請 patch 方法實際所在的模組**（例如 `cache_view_query` 的 `cache_search_service`、`cache_view_shard_detail` 的 `cache_update_dst_service`）。

**注意**：`CacheView` 與其 mixin 只使用 `cache_actions`、`cache_history_store`、`cache_overview_panel`（連帶 `cache_log_panel`）、`cache_state`。曾經存在、但沒有接線的 Controller／Presenter（MVC 雛形）與舊的獨立查詢／分片面板已移除：一個功能只保留一套架構，需要 Controller／Presenter 時再另開重構正式導入。

## 呼叫鏈（實際）

```
CacheView（主入口）
  ├─ 總覽：_load_overview() → cache_get_overview_service() → _render_type_list()
  │        動作：_run_action() → run_cache_action(view, reason, work_fn, ...)
  │              ├─ mounted App 先向 OperationRegistry admission，再由 owner worker 執行 work_fn（`show_progress` 時顯示 SnackBar 進度）
  │              └─ _on_reload_all / _on_save_all_new / _on_rotate_one ... 各自組 work_fn
  ├─ 查詢：_on_query_search() → _compute_query_results()（背景執行緒，內部呼叫 cache_search_service）→ _apply_query_results() → _render_query_results()
  │        _on_select_result() → 詳情（cache_get_entry_service）→ _on_apply_dst() → cache_update_dst_service()
  ├─ 分片：_load_shard_rows() → _load_shard_keys() → _load_shard_entry() → 編輯 dst / 歷史
  └─ 歷史：cache_history_store（history_load_active / history_save_active / history_append_event /
           history_load_recent）— 查詢與分片共用浮動視窗（_on_open_history_window）
```

## 主要方法（cache_view.py）

### 總覽區
- `_build_overview_page()`：組裝總覽頁（UI 組裝已抽到 `cache_overview_panel.build_overview_page`）
- `_run_action(reason, work_fn, success_msg, show_progress=False)`：共用動作包裝（busy 狀態 + SnackBar），委派 `run_cache_action`
- `_on_reload_all` / `_on_reload_one` / `_on_save_all_new` / `_on_save_all_fill` / `_on_save_one_new` / `_on_save_one_fill` / `_on_rotate_one` / `_on_analyze_one` / `_on_jump_to_query_type` / `_on_rebuild_index` / `_on_refresh_stats`
- 「補滿舊檔」為高風險動作：總覽頁「操作」卡片內有確認勾選 `chk_danger_confirm`（「我了解「補滿舊檔」會覆寫既有分片（高風險）」，預設未勾選）；`_on_save_all_fill` / `_on_save_one_fill` 在未勾選時只提示「尚未勾選高風險確認」而不執行

### 查詢區
- `_on_query_search()`：模式 `KEY` / `DST` / `ALL`（預設 ALL）、分類預設全部；mounted App 由 Registry 持有搜尋 worker，以 query sequence 只套用最後一次結果 → `_render_query_results()`
- `_render_query_detail()` / `_on_apply_dst()` / `_on_revert_dst()` / `_on_restore_latest_query()`
- 分頁：`_set_query_page` + `_on_page_first/prev/next/last/jump` + `_on_page_size_change`

### 分片區
- **分片讀取的執行緒契約（#114）**：`_load_shard_rows` / `_load_shard_keys` 只查 `shard_reader` 的記憶（檔案簽名未變就不再解析 JSON）；`_fetch_overview`（載入總覽的背景執行緒）與 `run_cache_action` 的背景工作結束前呼叫 `_warm_shard_cache` 預熱（含歷史索引 `warm_history_index`）。`_load_shard_entry` 僅在記憶體快取找不到該筆時才 fallback 讀檔（LRU 只留最近 2 個分片）。歷史紀錄 `cache_history_store`：讀取以 `{key: events}` 索引記憶，append 就地更新；整份 json 鏡像由單一背景執行緒寫入（`history_flush` 可等待）。
- `_load_shard_rows` → `_load_shard_keys(cache_type, filename)` → `_on_select_shard_key` → `_load_shard_entry` → `_render_shard_src_panel` / `_render_shard_dst_panel`
- dst 編輯：`_on_shard_dst_apply` / `_on_shard_dst_revert` / `_on_shard_dst_copy` / `_on_shard_dst_restore_latest`
- 動態高度計算：`_dynamic_*_height/width` 系列（`_on_page_resized` 觸發）
- 分片工作區：`_open_shard_workspace_tab` / `_on_back_to_shard_list` / `_on_select_shard_row`

### 歷史（共用）
- `cache_history_store`：`history_now_ts()`、`history_dirs()`、`history_active_default()`、`history_load_active()`、`history_save_active()`、`history_append_event()`、`history_load_recent()`（`CacheHistoryMixin` 以 `_history_*` 包裝）
- UI：`_on_open_history_window(source="query"|"shard")` → 浮動視窗（可拖曳/縮放）→ 查詢區 `_on_apply_selected_history`、分片區 `_on_shard_apply_selected_history`；切換主分頁或查詢子分頁時自動關閉（`_on_tab_change` / `_on_query_sub_tab_change`）

## UI 狀態管理

- `_set_state(busy, reason, trace)`：busy 鎖定 + 原因顯示；`_refresh_disabled_state()` 依 busy 禁用按鈕
- `_mark_dirty(area)` / `_schedule_update()` / `_do_update()` / `_batch_refresh()`：髒標記 + 批次 UI 更新（避免大量小 update 凍結）
- `_append_log` / `_notify(message, level)`：日誌與 SnackBar 通知

## 與 Cache 系統（utils 層）的關係

經由 `app/services_impl/cache/cache_services.py` 呼叫 `translation_tool/utils/cache_manager.py`：
- `cache_get_overview_service` / `cache_search_service` / `cache_get_entry_service`
- `cache_reload_service` / `cache_reload_type_service`
- `cache_save_all_service`（`write_new_shard=True` 為「新分片」、`False` 為「補滿舊檔」）/ `cache_rotate_service` / `cache_update_dst_service` / `cache_rebuild_index_service`

> 底層 cache 資料結構與分片格式見 `CACHE_SYSTEM.md`；關鍵字搜尋的效能設計見 `cache_search_optimization.md`。

## Cache 系統邏輯細節

### 分片（`translation_tool/utils/cache_shards.py`）
- **分片命名**：編號分片 `{cache_type}_{id:05d}.json`（如 `lang_00001.json`），`.active` 指標檔記錄目前 active 分片 id；另有時間戳分片 `{cache_type}_{mmddHHMMSS}-{seq}.json`（`_write_timestamp_shard`），新舊順序記在 `.shard_order`，不依賴檔名
- **`_get_active_shard_path`**：`.active` 不存在時自動掃描現有編號分片、取最大 id 初始化指標（無分片時預設 `00001`）
- **`_rotate_shard_if_needed`**：`len(data) >= rolling_shard_size` 時切到下一片；`rolling_shard_size` 由 `cache_manager.py` 的常數 `ROLLING_SHARD_SIZE`（**2500**）傳入（不是設定項）
- **手動輪替**：`force_rotate_active_shard`（總覽的「輪替分片」，經 `cache_rotate_service` → `force_rotate_shard`）把 `.active` 前進一號
- **跨行程安全**：旋轉與寫入都用 `.active.lock` 檔案鎖（`_shard_lock`；Windows `msvcrt` / POSIX `fcntl`），讀取-合併-寫入在同一個鎖內完成
- **`_save_entries_to_active_shards`**：分段寫入，active 分片容量不足時續寫並旋轉；`force_new_shard=True`（「新分片」動作）或一次寫入筆數超過分片上限時，整批寫入一個時間戳分片，不動 `.active` 指標
- **原子寫入 `_write_json_atomic`**：寫 `.tmp` → `fsync` → `os.replace`（避免資料遺失）

### 總覽統計（`translation_tool/utils/cache_overview.py` 的 `build_cache_overview`）
- 每 cache_type：`entries_count`（該型別總條目）、`is_dirty`、`session_new_count`（本次 session 新增條目數）、`active_shard_entries`、`shard_capacity`
- 頂層：`total_entries`、`dirty_type_count`（有未存變更的型別數）

### 查詢（`cache_search_service`）
- 先以搜尋引擎（FTS5，`cache_manager.search_cache`）查詢，無結果或失敗時降級為 `_search_linear` 線性掃描記憶體快取；回傳 `items`（`key` / `rank` / `preview` / `score`）、`truncated`、`limit`
- 全文索引由 `cache_rebuild_index_service` 重建

## 維護注意

1. 大量事件回呼觸發背景操作；避免舊任務覆蓋新狀態：總覽動作靠 `run_cache_action` 的 busy 鎖，查詢靠 `_query_seq`。
2. 新增總覽動作：加 service → 加 `_on_*` handler → `_run_action` 包裝 → `_refresh_disabled_state`。
3. 歷史事件 append 後要 `_render_shard_history` / `_render_query_history` 重繪。
