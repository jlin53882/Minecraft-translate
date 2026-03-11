# PR12 設計稿：Search orchestration 收斂（沿用 `cache_search.py`）

## Summary
PR12 目標是讓 `cache_manager.py` 不再同時承擔 search engine lifecycle、metadata 組裝、query façade 細節；沿用現有 `cache_search.py`，在不破壞既有 search API contract 下完成責任收斂。

---

## Scope / Out of scope

### Scope
- 保留既有 search 對外 API：
  - `rebuild_search_index`
  - `rebuild_search_index_for_type`
  - `search_cache`
  - `find_similar_translations`
- 收斂內部責任：
  - search engine 建立/關閉/重建流程集中
  - metadata inference helper 直接放進 `cache_search.py` 底部：
    - `_extract_path_from_composite_key`
    - `_infer_search_path`
    - `_infer_search_mod`
    - `_build_search_metadata`
  - **不新增 `cache_metadata.py`**，原因：這四個 helper 目前只被 search 路徑使用；若未來 `cache_store.py` 也需要，再獨立成模組
  - `cache_manager` 僅保留 façade。

### Out of scope
- 不改查詢結果欄位 contract（`key/src/dst/mod/path/type/score`）。
- 不重寫 FTS/Fuzzy 核心演算法。
- 不動 UI service 的回傳格式（`app/services.py` 目前有向後相容轉換）。

---

## Current coupling / risk

1. `cache_manager.py` 目前混有 search singleton、index rebuild、metadata inference，還同時管理 cache persistence。
2. `app/services.py::cache_search_service` 依賴既有欄位與排序規則，若 contract 變動會直接影響 UI。
3. rebuild 時會刪 `search_index.db` 後重建，若生命週期管理不一致，容易產生「引擎指向舊連線/舊檔」問題。

---

## Proposed change

### 檔案變更建議
- `translation_tool/utils/cache_search.py`（擴充）
  - 新增/收斂 orchestration helper：例如 `build_index_entries(...)`、`rebuild_from_cache_dicts(...)`
  - `build_index_entries(...)` 負責將單筆 cache entry 轉換成 search index 所需格式
  - `rebuild_from_cache_dicts(...)` 內部呼叫 `build_index_entries(...)`，負責 orchestration：取 dict → 逐筆轉換 → 寫入索引
  - `rebuild_from_cache_dicts(...)` 取得 in-memory cache dict 的方式：透過 `cache_store.get_cache_type_dict(...)`，不直接讀 `cache_manager` 全域變數
- `translation_tool/utils/cache_manager.py`（中小改）
  - 保留同名對外函式
  - 內部只委派給 search orchestration，不直接處理所有細節
- 不新增 `translation_tool/utils/cache_metadata.py`

### 設計要點
- 避免雙軌搜尋層：不新增平行 `cache_index.py`。
- 保持 `cache_manager.search_cache()` 可用，讓 `app/services.py` 無需變更。
- rebuild 與 single-type rebuild 都走同一套 entry builder，減少邏輯分叉。
- 索引生命週期採「先建後切換」策略：在暫存路徑建立新索引完成後，再原子切換引擎指向；rebuild 期間若有查詢進來，繼續使用舊引擎，不中斷服務。重建完成後負責清理暫存檔。

---

## Rejected approaches

1. **另外新增全新搜尋模組並淘汰 `cache_search.py`**
   - 重複實作且遷移成本高，與既有方向衝突。

2. **只在 `cache_manager.py` 內做函式重排，不真正抽離 orchestration**
   - 看似整理，實際耦合不變，無法降低後續維護成本。

3. **直接調整 search 回傳 schema（例如改欄位名稱）**
   - 會破壞 `app/services.py` 既有相容轉換與 UI 行為。

---

## Validation checklist

- `uv run pytest -q`
- 針對 PR12 額外建議：
  - `rebuild_search_index` 可從現有記憶體 cache 完整重建
  - `rebuild_search_index_for_type` 僅重建單一 type 且不污染其他 type
  - `search_cache` 與 `find_similar_translations` 回傳欄位保持相容
  - `app/services.py::cache_search_service` smoke 測試（mode=key/dst/ALL）
  - 連續兩次 `rebuild_search_index` 後，不留下殭屍連線或孤立 `.db` / `.tmp` 檔
  - `rebuild_search_index` 執行期間，`search_cache` 行為可預期（先建後切換策略：舊引擎仍可查詢）

---

## 與 PR11 的依賴關係
- PR12 依賴 PR11 完成後才開工（正式路線）；若需並行設計，`rebuild_from_cache_dicts(...)` 過渡期可從 `cache_manager` façade 取 dict，但正式目標是改走 `cache_store.get_cache_type_dict(...)`。
- PR12 不應新增任何直接讀 `cache_manager` 模組全域變數的路線，避免讓已收斂的 store accessor 又長出旁路。

---

## GO / NOT YET
**GO（中風險）**。

前提：
- 索引生命週期策略已選定（本稿採「先建後切換」）；
- `rebuild_from_cache_dicts(...)` 取 dict 路線已確認走 `cache_store` accessor；
- `cache_metadata.py` 不新增，helper 直接放 `cache_search.py`。

理由：可在 contract 不變前提下收斂 orchestration；但比 PR10/PR11 更容易踩到 UI 查詢相容與索引生命週期細節。

---

## One-line summary
PR12 可以做，但必須以 `cache_search.py` 為唯一搜尋核心，`cache_manager.py` 僅留 query façade 與相容入口。