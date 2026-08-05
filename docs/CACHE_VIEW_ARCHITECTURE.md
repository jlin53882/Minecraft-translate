# Cache View 架構

## 定位

CacheView（`app/views/cache_view.py`，約 3500 行）是快取系統的 UI 入口，功能包含：

| 區域 | 功能 |
|------|------|
| 總覽 | 各 cache_type 統計、重載、儲存、新分片/補滿舊檔、輪替分片、重建全文索引 |
| 查詢 | 依 key / dst / 關鍵字搜尋（含全文索引），分頁結果 + 單筆編輯 |
| 分片編輯 | 進入 shard → 選 key → 編輯 dst（preview/raw 模式）、復原、複製、套用歷史版本 |
| 歷史 | 查詢區與分片區共用一套浮動視窗，查看/套用歷史事件 |

## 檔案結構（實際現況）

```
app/views/cache_view.py                    ← 主入口（ft.Column，monolithic）
app/views/cache_manager/                   ← 從 cache_view 抽離的模組（實際被引用）
  ├─ cache_actions.py          run_cache_action()          ← 被引用
  ├─ cache_history_store.py    history_* 7 個函式          ← 被引用
  ├─ cache_overview_panel.py   build_overview_page()       ← 被引用
  └─ cache_state.py            CacheQueryState/ShardState/HistoryState ← 被引用
```

**注意**：`cache_manager/panels/`（CacheOverviewPanel / CacheQueryPanel / CacheShardPanel）、`cache_controller.py`、`cache_presenter.py`、`cache_log_panel.py`、`cache_shared_widgets.py`、`cache_types.py` 目前**沒有被任何 app/tests 程式碼引用**，是未接線的草案模組；`app/views/cache/`（cache_view_optimized / cache_query_view）也是死程式碼（其 `__init__.py` 甚至 import 不存在的 cache_modal_*）。維護時不要依賴這些模組。

## 呼叫鏈（實際）

```
CacheView（主入口）
  ├─ 總覽：_load_overview() → cache_get_overview_service() → _render_type_list()
  │        動作：_run_action() → run_cache_action(view, reason, work_fn, ...)
  │              ├─ ThreadPoolExecutor 執行 work_fn + SnackBar 進度
  │              └─ _on_reload_all / _on_save_all_new / _on_rotate_one ... 各自組 work_fn
  ├─ 查詢：_on_query_search() → cache_search_service() → _render_query_results()
  │        _on_select_result() → cache_get_entry_service() → 單筆編輯 → cache_update_dst_service()
  ├─ 分片：_load_shard_rows() → _load_shard_keys() → _load_shard_entry() → 編輯 dst / 歷史
  └─ 歷史：cache_history_store（history_load_active / history_save_active / history_append_event /
           history_load_recent）— 查詢與分片共用浮動視窗（_on_open_history_window）
```

## 主要方法（cache_view.py）

### 總覽區
- `_build_overview_page()`：組裝總覽頁（UI 組裝已抽到 `cache_overview_panel.build_overview_page`）
- `_run_action(reason, work_fn)`：共用動作包裝（busy 狀態 + SnackBar）
- `_on_reload_all` / `_on_reload_one` / `_on_save_all_new` / `_on_save_all_fill` / `_on_save_one_new` / `_on_save_one_fill` / `_on_rotate_one` / `_on_rebuild_index` / `_on_refresh_stats`

### 查詢區
- `_on_query_search()` → `cache_search_service` → `_render_query_results()`
- `_render_query_detail()` / `_on_apply_dst()` / `_on_revert_dst()` / `_on_restore_latest_query()`
- 分頁：`_set_query_page` + `_on_page_first/prev/next/last/jump` + `_on_page_size_change`

### 分片區
- `_load_shard_rows` → `_load_shard_keys(cache_type, filename)` → `_on_select_shard_key` → `_load_shard_entry` → `_render_shard_src_panel` / `_render_shard_dst_panel`
- dst 編輯：`_on_shard_dst_apply` / `_on_shard_dst_revert` / `_on_shard_dst_copy` / `_on_shard_dst_restore_latest`
- 動態高度計算：`_dynamic_*_height/width` 系列（`_on_page_resized` 觸發）

### 歷史（共用）
- `cache_history_store`：`history_now_ts()`、`history_dirs()`、`history_active_default()`、`history_load_active()`、`history_save_active()`、`history_append_event()`、`history_load_recent()`
- UI：`_on_open_history_window(source="query"|"shard")` → 浮動視窗（可拖曳/縮放）→ `_on_apply_selected_history`

## UI 狀態管理

- `_set_state(busy, reason, trace)`：busy 鎖定 + 原因顯示；`_refresh_disabled_state()` 依 busy 禁用按鈕
- `_mark_dirty(area)` / `_schedule_update()` / `_do_update()` / `_batch_refresh()`：髒標記 + 批次 UI 更新（避免大量小 update 凍結）
- `_append_log` / `_notify(message, level)`：日誌與 SnackBar 通知

## 與 Cache 系統（utils 層）的關係

經由 `app/services_impl/cache/cache_services.py` 呼叫 `translation_tool/utils/cache_manager.py`：
- `cache_get_overview_service` / `cache_search_service` / `cache_get_entry_service`
- `cache_reload_service` / `cache_reload_type_service`
- `cache_save_all_service` / `cache_rotate_service` / `cache_update_dst_service` / `cache_rebuild_index_service`

> 底層 cache 資料結構與分片格式見 `CACHE_SYSTEM.md`；關鍵字搜尋的效能設計見 `cache_search_optimization.md`。

## 維護注意

1. 大量事件回呼觸發背景操作；避免舊任務覆蓋新狀態時需配合 action 序號機制（草案中有 CacheController，但尚未接線，目前靠 `run_cache_action` 的 busy 鎖）。
2. 新增總覽動作：加 service → 加 `_on_*` handler → `_run_action` 包裝 → `_refresh_disabled_state`。
3. 歷史事件 append 後要 `_render_shard_history` / `_render_query_history` 重繪。
