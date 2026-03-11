# PR9 設計稿：`cache_manager.py` 分層重構（Phase 0 / 設計 / 可行性驗證）

## 範圍與目標
- 範圍：`translation_tool/utils/cache_manager.py`（現況盤點 + 分層設計）
- 本輪不做 production 大改，只產出可執行的拆分方案與驗證策略
- 對齊既有結論：沿用 `cache_search.py`，不發明平行 search module

---

## 1) 現狀盤點（責任列表 + 高耦合點）

### A. `cache_manager.py` 目前同時承擔的責任
1. **cache bootstrap / lifecycle**
   - `initialize_translation_cache()`
   - `reload_translation_cache()`
   - `reload_translation_cache_type()`
2. **storage in-memory CRUD**
   - `_translation_cache` / `_session_new_entries` / `_is_dirty`
   - `add_to_cache()` / `get_from_cache()` / `get_cache_entry()` / `get_cache_dict_ref()`
3. **shard persistence**
   - `_get_active_shard_path()` / `_rotate_shard_if_needed()`
   - `_save_entries_to_active_shards()` / `save_translation_cache()`
   - `_write_json_atomic()`
4. **search metadata inference**
   - `_infer_search_path()` / `_infer_search_mod()` / `_build_search_metadata()`
5. **search engine lifecycle + query 入口**
   - `get_search_engine()`
   - `rebuild_search_index()` / `rebuild_search_index_for_type()`
   - `search_cache()` / `find_similar_translations()`
6. **ops / overview**
   - `get_cache_overview()`
   - `force_rotate_shard()`
7. **import-time side effect**
   - 模組尾端直接 `initialize_translation_cache()`

### B. 高耦合點（本輪最關鍵）
1. **核心呼叫點多且分散**
   - `app/services.py`：cache 管理 UI service 直接倚賴多個 API（reload/save/search/rebuild/overview）
   - `lm_translator.py` / `lm_translator_shared.py`：翻譯主流程直接依賴 add/save/reload/get_* API
2. **`lm_translator.py` 直接 import 內部全域狀態**
   - `from ...cache_manager import _translation_cache, _initialized`
   - 這是最硬的耦合，代表不能一開始就把 internal state 大搬家
3. **search 與 storage 在同模組互纏**
   - `rebuild_search_index()` 直接走 `_translation_cache`
   - `_build_search_metadata()` 又綁到 cache key/schema 假設
4. **save path 依賴 `_cache_file_path` 與 active shard 機制**
   - bootstrap + persistence + state tracking 互相耦合

---

## 2) 可先拆 / 不能先拆的邊界

### 可先拆（低風險、可保持 API 相容）
1. **Shard persistence 純函式層**
   - `_write_json_atomic`, `_get_active_shard_path`, `_rotate_shard_if_needed`, `_save_entries_to_active_shards`
   - 可先抽到 `cache_shards.py`，由 `cache_manager.py` 薄轉接
2. **Search metadata inference helper**
   - `_extract_path_from_composite_key`, `_infer_search_path`, `_infer_search_mod`, `_build_search_metadata`
   - 可抽到 `cache_metadata.py`，降低 `cache_manager.py` 雜訊
3. **Overview 組裝邏輯（非核心寫路徑）**
   - `get_cache_overview()` 的資料彙整可先抽成 helper，不改外部 service API

### 不能先拆（現在拆風險高）
1. **`_translation_cache` / `_initialized` 的持有位置**
   - 因 `lm_translator.py` 有直接 import 內部變數，先動會立即破壞相容
2. **一次重寫 bootstrap + search engine lifecycle**
   - import-time initialization 與 reload/search rebuild 目前行為已被多處依賴
3. **把 search 全面搬離 `cache_manager.py` 對外 API**
   - `app/services.py` 與 UI 已用 `cache_manager.search_cache()` 等入口，現階段應保留 façade

---

## 3) GO / NOT YET 判斷與理由

## 判斷：**GO（可進入 PR10）**
理由：
1. 邊界可先做「內部分層 + 外部 API 不變」的安全拆法
2. 既有 `cache_search.py` 已成熟，PR10 可優先處理 storage/shard，不碰 search contract
3. 已有可執行驗證（`uv run pytest`）可做回歸守門

### 但有前提（若不滿足就轉 NOT YET）
- PR10 必須明確承諾：
  1) **不移除** `cache_manager.py` façade
  2) **不改** `lm_translator.py` 直接依賴點（先只做相容層）
  3) 每步驟都有 import + cache 行為測試

---

## 4) PR10 / PR11 / PR12 建議拆法

## PR10（第一刀，建議）— **Shard/Persistence 抽層，API 零破壞**
目標：
- 新增 `translation_tool/utils/cache_shards.py`
- 將 shard 寫入與 rotate 邏輯搬入，但 `cache_manager.py` 對外函式名稱保持不變

包含：
- 搬移：`_write_json_atomic`, `_get_active_shard_path`, `_rotate_shard_if_needed`, `_save_entries_to_active_shards`
- `cache_manager.py` 改為呼叫新模組
- 補 targeted tests（至少覆蓋：active shard 決策、rotate、save chunk）

不包含：
- 不動 search API
- 不動 `_translation_cache` 持有位置

---

## PR11 — **State/Store 分層 + bootstrap 清晰化（仍保 façade）**
目標：
- 將 state 與 CRUD 核心整理為 `cache_store.py`（或等價命名）
- `cache_manager.py` 只保留 orchestration + compatibility façade

包含：
- `add_to_cache/get_from_cache/get_cache_entry/get_cache_dict_ref` 實作下沉
- reload/init 流程明確化（仍維持現有對外行為）

限制：
- 暫不移除 import-time init（可先加可控開關，再評估下一步）

---

## PR12 — **Search orchestration 收斂（沿用 `cache_search.py`）**
目標：
- 將 `get_search_engine/rebuild/search/find_similar` 的實作責任收斂
- `cache_manager.py` 保留穩定 façade，search 細節盡量下沉到 search module

包含：
- 單一 search lifecycle 管理點
- metadata inference 與 index rebuild 的責任邊界文件化

不做：
- 不新增平行 `cache_index.py`

---

## 5) Rejected approaches（至少 2 個）

1. **Rejected #1：一次性把 `cache_manager.py` 全拆完 + 改全部 import**
- 原因：呼叫點跨 `app/services.py`、`lm_translator*.py`，且有直接 import 內部狀態，爆炸半徑過大

2. **Rejected #2：新增第二套 search module（如 `cache_index.py`）與 `cache_search.py` 並存**
- 原因：會造成雙軌搜尋實作，責任更亂，且違反 PR7/既有設計方向

3. **Rejected #3：先改 `lm_translator.py` 移除 `_translation_cache` 直讀，再做 cache 拆分**
- 原因：雖然理論上更乾淨，但牽涉翻譯主流程行為，已超出 PR9/PR10 的風險預算

---

## 6) Validation checklist

### 本輪（PR9 設計）已執行
- [x] `uv run pytest tests/test_main_imports.py tests/test_cache_view_features.py -q`
- [x] 結果：`6 passed`
- [x] 驗證涵蓋 import guard + cache view 相關行為（含 cache service 路徑）

### PR10 起每顆都應執行
- [ ] `uv run pytest -q`（至少 targeted + 一次較完整回歸）
- [ ] smoke：import `translation_tool.utils.cache_manager` 成功
- [ ] smoke：cache save/reload/overview 不變
- [ ] smoke：cache search/rebuild index 路徑不變
- [ ] diff 檢查：僅限當期 scope，禁止順手大改無關模組

---

## 7) 一句話總結
**`cache_manager.py` 可以拆，但要走「先抽 shard/persistence、保 façade 相容、最後再收斂 search」的漸進式三階段路線，現在是 GO。**
