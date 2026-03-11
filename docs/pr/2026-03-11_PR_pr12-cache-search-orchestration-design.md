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
  - metadata inference helper（mod/path）抽離成明確 helper（可在 `cache_search.py` 或獨立 `cache_metadata.py`）
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
- `translation_tool/utils/cache_manager.py`（中小改）
  - 保留同名對外函式
  - 內部只委派給 search orchestration，不直接處理所有細節
- （可選）`translation_tool/utils/cache_metadata.py`（新增）
  - 放 `_extract_path_from_composite_key/_infer_search_path/_infer_search_mod/_build_search_metadata`

### 設計要點
- 避免雙軌搜尋層：不新增平行 `cache_index.py`。
- 保持 `cache_manager.search_cache()` 可用，讓 `app/services.py` 無需變更。
- rebuild 與 single-type rebuild 都走同一套 entry builder，減少邏輯分叉。

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

---

## GO / NOT YET
**GO（中風險）**。

理由：可在 contract 不變前提下收斂 orchestration；但比 PR10/PR11 更容易踩到 UI 查詢相容與索引生命週期細節。

---

## One-line summary
PR12 可以做，但必須以 `cache_search.py` 為唯一搜尋核心，`cache_manager.py` 僅留 query façade 與相容入口。