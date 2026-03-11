# PR11 設計稿：State / Store 分層 + bootstrap 清晰化

## Summary
PR11 針對 `cache_manager.py` 的 in-memory state 與 CRUD API 做分層，建立 `cache_store.py`（名稱可微調），但保留 `cache_manager.py` façade，且暫不動 `lm_translator.py` 直接依賴的 `_translation_cache/_initialized`。

---

## Scope / Out of scope

### Scope
- 將以下責任下沉到 store 層：
  - cache in-memory 持有與讀寫流程（`add_to_cache/get_from_cache/get_cache_entry/get_cache_dict_ref` 實作）
  - `_is_dirty`、`_session_new_entries` 的 CRUD 管理
- bootstrap 流程明確化：
  - `initialize_translation_cache/reload_translation_cache/reload_translation_cache_type` 只保留 orchestration
  - 由 store/shards 明確分工
- 保留 `cache_manager.py` 對外函式與行為相容。

### Out of scope
- 不改 `translation_tool/core/lm_translator.py` 對 `_translation_cache/_initialized` 的直接 import。
- 不改搜尋 API contract。
- 不處理 import-time auto initialization 的最終移除（可先標註下一階段）。

---

## Current coupling / risk

1. `lm_translator.py` 目前直接 `from ...cache_manager import _translation_cache, _initialized`，是 PR11 最大風險點。
2. store 與 bootstrap 現在混在同檔，若直接搬 state 物件位置，會破壞既有 import side effects。
3. `_cache_lock`、`_is_dirty`、`_session_new_entries` 目前是共享狀態；拆分時若 lock 邊界錯誤，可能出現 dirty flag 失真。

---

## Proposed change

### 檔案變更建議
- `translation_tool/utils/cache_store.py`（新增）
  - 定義 store 物件（或函式集合）管理 cache dict / dirty / session new
  - 提供 CRUD 與 session flush API
- `translation_tool/utils/cache_manager.py`（中小改）
  - 保留現有 public API
  - `_translation_cache/_initialized` 仍可保留為 compatibility alias（避免打斷 `lm_translator.py`）
- 可選：`translation_tool/utils/cache_bootstrap.py`（若要再薄切）
  - 只包初始化/重載流程

### 設計要點
- **Compatibility-first**：先做 state 抽象，不做狀態持有主體搬遷。
- `cache_manager` 轉為 façade + orchestration，對外仍是唯一入口。
- 加註 TODO：待後續 PR 才移除 `lm_translator.py` 對內部全域變數依賴。

---

## Rejected approaches

1. **PR11 直接改掉 `lm_translator.py` 對 `_translation_cache/_initialized` 的依賴**
   - 範圍爆炸，跨到翻譯主流程，超出本顆風險預算。

2. **把 cache state 全改成 class singleton 並同步替換所有舊 API**
   - 需要大面積改 import 與調用，不符合「保 façade 相容」。

3. **維持現狀只補註解不分層**
   - 無法真正降低耦合，PR12 搜尋收斂也會卡住。

---

## Validation checklist

- `uv run pytest -q`
- 針對 PR11 額外建議：
  - `add_to_cache -> save_translation_cache -> reload_translation_cache` 行為回歸
  - `get_cache_entry/get_cache_dict_ref` 回傳 contract 不變
  - `_session_new_entries` 清空時機與 dirty flag 正確
  - `lm_translator.py` import 不破壞（smoke import）

---

## GO / NOT YET
**GO（有前提）**。

前提：
- 只做相容分層，不做依賴點大遷移；
- `_translation_cache/_initialized` 對外可見性先保留。

---

## One-line summary
PR11 可做，但必須走「store 分層先行、舊全域依賴暫時保留」的相容路線。