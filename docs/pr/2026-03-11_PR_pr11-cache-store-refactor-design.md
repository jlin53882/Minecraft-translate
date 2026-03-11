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
- 明確定義 `cache_store.py` 的函式簽名與 lock 邊界，避免實作時再做一次設計決策。

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
  - 定義 store 層函式集合，管理 cache dict / dirty / session new
  - 建議最少提供以下介面：
    - `def get_entry(cache_dict: dict, key: str) -> Optional[dict]: ...`
      - 回傳完整 entry（例如 `{"src": ..., "dst": ..., ...}`）
    - `def get_value(cache_dict: dict, key: str) -> Optional[str]: ...`
      - 只回傳 entry 的 `dst` 欄位；若 key 不存在或 entry 無 `dst`，回傳 `None`
    - `def add_entry(cache_dict: dict, key: str, entry: dict) -> None: ...`
    - `def get_cache_type_dict(cache_state: dict[str, dict], cache_type: str) -> dict: ...`
      - 統一取得某個 `cache_type` 的 in-memory dict ref；取代語意不清的 `get_dict_ref(cache_dict)`
    - `def mark_dirty(is_dirty: dict, cache_type: str) -> None: ...`
    - `def clear_dirty(is_dirty: dict, cache_type: str) -> None: ...`
    - `def get_session_entries(session_new_entries: dict, cache_type: str) -> dict: ...`
    - `def flush_session_entries(session_new_entries: dict, cache_type: str) -> dict: ...`
- `translation_tool/utils/cache_manager.py`（中小改）
  - 保留現有 public API
  - `_translation_cache/_initialized` 保留在這一層作 compatibility alias（避免打斷 `lm_translator.py`）
  - `_cache_lock` 也留在這一層；`cache_store.py` 先視為 **非 thread-safe 純操作層**，由 manager 負責 synchronization
- **本顆不新增 `cache_bootstrap.py`**
  - bootstrap 拆分條件尚不足，避免把 PR11 再膨脹成另一顆設計決策

### 設計要點
- **Compatibility-first**：先做 state 抽象，不做狀態持有主體搬遷。
- `cache_manager` 轉為 façade + orchestration，對外仍是唯一入口。
- `_translation_cache/_initialized` 的 ownership 在 PR11 **不搬移**；store 只操作 manager 傳入的 dict / flag ref，不自行持有第二份 state。
- `_cache_lock` 明確留在 `cache_manager.py`；`cache_store.py` 不自行上鎖，避免 lock ownership 模糊與雙重同步風險。
- PR12 之後若需 rebuild/search 取得 in-memory cache dict，正式路線應透過 `cache_store.get_cache_type_dict(...)` 這類 accessor，而不是再新增一條直接讀 manager 全域變數的路線。
- 加註 TODO：待後續 PR 才移除 `lm_translator.py` 對內部全域變數依賴。

---

## Rejected approaches

1. **PR11 直接改掉 `lm_translator.py` 對 `_translation_cache/_initialized` 的依賴**
   - 範圍爆炸，跨到翻譯主流程，超出本顆風險預算。

2. **把 cache state 全改成 class singleton 並同步替換所有舊 API**
   - 需要大面積改 import 與調用，不符合「保 façade 相容」。

3. **維持現狀只補註解不分層**
   - 無法真正降低耦合，PR12 搜尋收斂也會卡住。

4. **在 PR11 同時引入 `cache_bootstrap.py` 再拆一次初始化流程**
   - 會讓本顆同時承擔 store 分層 + bootstrap 切分兩種設計決策，範圍過大。

---

## Validation checklist

- `uv run pytest -q`
- 針對 PR11 額外建議：
  - `add_to_cache -> save_translation_cache -> reload_translation_cache` 行為回歸
  - `get_cache_entry/get_cache_type_dict` 回傳 contract 不變
  - `_session_new_entries` 清空時機與 dirty flag 正確
  - `lm_translator.py` import 不破壞（smoke import）
  - 交錯案例：多次連續 `add_to_cache` 後 `_session_new_entries` 累積正確
  - 交錯案例：`add_to_cache -> save_translation_cache -> add_to_cache` 後 dirty flag 不丟失
  - 交錯案例：save 後再 reload，state / dirty / session entries 一致

---

## GO / NOT YET
**GO（有前提）**。

前提：
- 只做相容分層，不做依賴點大遷移；
- `_translation_cache/_initialized` 對外可見性先保留。

---

## 與 PR12 的依賴關係
- PR12 若需要 rebuild/search 取得 in-memory cache dict，應優先走 PR11 定義的 store accessor（例如 `get_cache_type_dict(...)`）。
- 過渡期若 PR12 與尚未落地的 PR11 並行設計，可以在 `cache_manager.py` façade 暫時保留相容轉接；但正式目標仍是讓內部依賴收斂到 store accessor，而不是繼續擴散直接讀 manager 全域變數。

## One-line summary
PR11 可做，但必須走「store 分層先行、舊全域依賴暫時保留」的相容路線。