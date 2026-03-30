# Cache View 架構

## 現況說明
同時存在舊版（`cache_view.py` 3549行 monolithic）和新版的 MVC 架構（`cache_manager/`）。
本文件說明目標架構（即 MVC 版本），以及與舊版的過渡關係。

## MVC 架構圖（文字版）
```
CacheView（主入口，ft.Column）
  └─ build_overview_page()
      ├─ styled_card("總覽") → overview_text
      └─ ResponsiveRow
          ├─ left: styled_card("分類狀態清單") → type_list
          └─ right
              ├─ styled_card("操作說明")
              └─ build_log_panel()

CacheManager（邏輯協調，非類別）
  ├─ CachePresenter（UI 顯示層轉換器）
  │   └─ status_label() / action_log()
  ├─ CacheState（資料容器）
  │   ├─ CacheQueryState
  │   ├─ CacheShardState
  │   └─ CacheHistoryState
  ├─ CacheActions（封裝操作）
  │   └─ run_cache_action() — ThreadPoolExecutor 執行 + SnackBar 進度
  ├─ CacheController（動作序號控制器）
  │   └─ begin_action() / is_current() — 防舊任務覆蓋新狀態
  ├─ CacheTypes（型別定義）
  │   ├─ CacheUiState(busy, reason, trace)
  │   └─ ActionState(action_id, reason, phase)
  ├─ CacheOverviewPanel（panels/overview_panel.py）
  ├─ CacheQueryPanel（panels/query_panel.py）
  └─ CacheShardPanel（panels/shard_panel.py）
```

## 與 Cache 系統的關係
`cache_manager/` 透過 `app.services_impl.cache.cache_services` 呼叫 `translation_tool/utils/cache_manager.py` API：
- `cache_get_overview_service` — 取得總覽統計
- `cache_search_service` — 關鍵字搜尋
- `cache_reload_service` / `cache_reload_type_service` — 重載
- `cache_save_all_service` — 儲存
- `cache_rotate_service` — 輪替分片
- `cache_update_dst_service` — 更新單筆 dst
- `cache_rebuild_index_service` — 重建全文索引

## 過渡狀態說明
- **舊版** `cache_view.py`（3549行）：仍是 UI 主入口，繼承 `ft.Column`
- **新版** `cache_manager/`：職責陸續抽離中，目前現況：
  - `cache_overview_panel.py` 已從舊版抽出「總覽頁組裝」
  - `panels/` 下三個面板類別為新 UI 元件
  - `cache_actions.py` 的 `run_cache_action` 已被舊版引用
- **目標**：以 MVC 版本完全替換 monolithic 版本

## 查詢流程
```
使用者輸入關鍵字
  → CacheQueryPanel._on_search()
  → cache_search_service()
  → translation_tool.utils.cache_manager (cache_search)
  → 回傳結果列表
  → CacheQueryPanel 渲染 ListView
```
