# PR10：Shard / Persistence 抽層（`cache_shards.py`）

> 狀態：✅ 已完成並驗證
> 實作 commit：`cc02e88`

## Summary
PR10 的目標是把 `cache_manager.py` 內「分片寫入與輪替」責任抽出，形成可測試、可替換的 persistence 模組；`cache_manager.py` 保留 façade 與 API 相容。

---

## Scope / Out of scope

### Scope
- 抽出下列函式到新模組（建議 `translation_tool/utils/cache_shards.py`）：
  - `_write_json_atomic`
  - `_get_active_shard_path`
  - `_rotate_shard_if_needed`
  - `_save_entries_to_active_shards`
- `cache_manager.py` 改成薄轉接（wrapper），保留既有呼叫點與函式名稱相容。
- 補齊分片邏輯的單元測試（rotate、active shard 決策、跨 shard 續寫、force_new_shard）。

### Out of scope
- 不改 `add_to_cache/get_*` 的 state 持有位置。
- 不改 search engine lifecycle（`get_search_engine/rebuild_search_index/search_cache`）。
- 不改 `lm_translator.py` 對 `cache_manager` 內部狀態相依。

---

## Current coupling / risk

1. `cache_manager.py` 同時承擔 state + persistence + search orchestration，單檔責任過重。
2. persistence 目前直接吃 `_cache_file_path`、`ACTIVE_SHARD_FILE`、`ROLLING_SHARD_SIZE`，抽層若不設 context 參數會造成反向耦合。
3. `_save_entries_to_active_shards` 含 I/O、輪替、容量計算，回歸風險在：
   - shard id 演進錯誤
   - force new shard 時覆蓋錯誤
   - active pointer 與實體檔案不一致

---

## Proposed change

### 檔案變更建議
- `translation_tool/utils/cache_shards.py`（新增）
  - 放純 persistence/shard 函式
  - 接收明確參數（`type_dir`, `cache_type`, `rolling_shard_size`, `active_shard_file`, `entries`）
- `translation_tool/utils/cache_manager.py`（小改）
  - 保留舊函式名稱，但實作改呼叫 `cache_shards`。
  - 不動外部 API contract。
- `tests/`（新增對應測試）
  - 建議 `tests/test_cache_shards.py`

### 設計要點
- `cache_shards` 只做「檔案與分片」，不持有全域 `_translation_cache`。
- `cache_manager` 繼續負責 lock 與 session new entries 的切片時機。
- 先做「函式搬遷 + wrapper」，不做命名革命，讓 PR 足夠小。

---

## Rejected approaches

1. **一次把 state + persistence + search 全部搬出 `cache_manager.py`**
   - 拆太大，回歸風險不可控，不符合本輪「逐顆可開工」。

2. **直接刪除 `cache_manager` 內同名函式，強迫所有呼叫方改 import 新模組**
   - 會立即影響 `app/services.py` 與核心翻譯流程，破壞相容性。

3. **把 shard 參數寫死在 `cache_shards.py` 模組常數**
   - 會讓測試難注入，不利後續多 cache_type 擴展。

---

## Validation checklist

- `uv run pytest -q`（至少一次全域/半全域回歸）
- 針對 PR10 額外建議：
  - active shard 不存在時，能正確回推最新 shard id
  - shard 已滿時能 rotate 並續寫
  - `force_new_shard=True` 先切片再寫
  - atomic write 保證 `.tmp -> target` 覆蓋流程
- smoke：
  - import `translation_tool.utils.cache_manager` 成功
  - `cache_save_all_service()` 行為不變（至少不拋例外）

---

## GO / NOT YET
**GO**。

理由：PR10 可以做到「內部重構、外部零破壞」，且邊界清晰、驗證面具體，適合作為 PR9 後的第一刀。

---

## One-line summary
先拆 shard/persistence 到 `cache_shards.py`，`cache_manager.py` 維持 façade，相容前提下可安全開工。

---

## 實作結果

### 已完成變更
- 新增 `translation_tool/utils/cache_shards.py`
- `cache_manager.py` 保留同名 façade / wrapper：
  - `_write_json_atomic`
  - `_get_active_shard_path`
  - `_rotate_shard_if_needed`
  - `_save_entries_to_active_shards`
- 新增 `tests/test_cache_shards.py`

### 實作原則是否符合設計
- ✅ 保留 `cache_manager.py` façade，相容未破
- ✅ 沒有去動 `add_to_cache/get_*` 的 state 持有位置
- ✅ 沒有去動 search engine lifecycle
- ✅ 沒有去碰 `lm_translator.py` 對 internal state 的依賴

## 驗證結果（實測）
- `uv run pytest tests/test_cache_shards.py -q` → **4 passed**
- `uv run pytest tests/test_main_imports.py tests/test_cache_view_features.py -q` → **6 passed**
- `uv run pytest -q` → **31 passed**
- `uv run python -c "from translation_tool.utils import cache_manager; print('import ok')"` → **import ok**
- `uv run python -c "from app.services import cache_get_overview_service; r=cache_get_overview_service(); print('service smoke ok', isinstance(r, dict), 'types' in r)"` → **service smoke ok True True**

## 風險 / 注意事項（落地後）
- 目前是「邏輯搬遷 + façade 轉接」，外部 API 相容維持；若外部有非常細的 monkeypatch 依賴 `cache_manager` 內部實作細節，仍需留意。
- `cache_shards.py` 現在已採顯式參數注入，後續 PR11 / PR12 更容易接續拆分。

## 最終狀態
**PR10 已完成並驗證通過**。