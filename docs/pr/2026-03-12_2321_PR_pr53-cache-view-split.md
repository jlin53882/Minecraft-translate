# PR53：cache view split

## Summary
這顆 PR 對 `cache_view.py` 採最保守的拆法：不直接動整顆 `CacheView` 外部行為，而是把已經明顯可分離的 state / history store / action runner 抽到 `app/views/cache_manager/`，讓 cache view 從超大單檔往可維護邊界退一步。

---

## Phase 1 完成清單
- [x] 做了：新增 `cache_manager/cache_state.py`，收納 query / shard / history state dataclasses。
- [x] 做了：新增 `cache_manager/cache_history_store.py`，收納 history append/load/active-file rotation。
- [x] 做了：新增 `cache_manager/cache_actions.py`，收納 `_run_action()` 的共用控制流。
- [x] 做了：`cache_view.py` 改成委派上述模組，但保留原 method names / monkeypatch seam。
- [x] 做了：新增 focused tests：`tests/test_cache_state.py`、`tests/test_cache_history_store.py`。
- [ ] 未做：把整顆 query/shard UI panel 再往外拆（原因：這顆先收 state/store/action 三塊，避免一次打開整顆巨檔）。

---

## What was done

### 1. 抽出 state layer
新增 `app/views/cache_manager/cache_state.py`：
- `CacheQueryState`
- `CacheShardState`
- `CacheHistoryState`

這些 dataclass 先把 query/shard/history 的預設形狀固定下來，不再全部靠 `CacheView.__init__` 一行一行手灌。

### 2. 抽出 history store layer
新增 `app/views/cache_manager/cache_history_store.py`：
- `history_now_ts()`
- `history_dirs()`
- `history_active_default()`
- `history_load_active()`
- `history_save_active()`
- `history_append_event()`
- `history_load_recent()`

原本 `cache_view.py` 內那整段 history 檔案輪替 / jsonl mirror / recent scan 邏輯，現在有獨立模組可測。

### 3. 抽出 action runner layer
新增 `app/views/cache_manager/cache_actions.py`：
- `run_cache_action()`

把 `ui_busy` guard、action trace log、success/error/finally 狀態收束進共用 runner，`CacheView._run_action()` 改成薄委派。

### 4. 保留 cache_view 相容外觀
`cache_view.py` 目前仍保留：
- `_run_action()`
- `_history_*` methods
- 各種 `query_*` / `shard_*` 實例欄位名稱

也就是說：
- 測試/monkeypatch 既有呼叫點還活著
- 但底層責任已經開始分層

---

## Important findings
- `cache_view.py` 這顆真的很大，但也不能一口氣硬拆；因為它有不少既有 tests 是直接 `CacheView.__new__()` 後 patch method/property。
- 所以 PR53 的正解是：**先抽純狀態、純儲存、純控制流**，不要急著動 UI composition。
- 這次 targeted tests 與 full pytest 都綠，代表這種「外觀不變、內部退層」的做法目前站得住。

---

## Validation checklist
- [x] `uv run pytest -q tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_state.py tests/test_cache_history_store.py tests/test_cache_view_features.py tests/test_cache_view_monkeypatch_integration.py tests/test_cache_view_state_gate.py --basetemp=.pytest-tmp\pr53 -o cache_dir=.pytest-cache\pr53`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr53-full -o cache_dir=.pytest-cache\pr53-full`

## Test result
```text
$ uv run pytest -q tests/test_cache_controller.py tests/test_cache_presenter.py tests/test_cache_state.py tests/test_cache_history_store.py tests/test_cache_view_features.py tests/test_cache_view_monkeypatch_integration.py tests/test_cache_view_state_gate.py --basetemp=.pytest-tmp\pr53 -o cache_dir=.pytest-cache\pr53
....................                                                     [100%]
20 passed in 0.82s

$ uv run pytest -q --basetemp=.pytest-tmp\pr53-full -o cache_dir=.pytest-cache\pr53-full
........................................................................ [ 43%]
........................................................................ [ 87%]
....................                                                     [100%]
164 passed, 37 warnings in 1.88s
```

---

## Rejected approaches
1) 試過：直接把 `CacheView` 主類別大拆成多個 panel/controller 類別。
   - 為什麼放棄：這顆測試與 monkeypatch seam 很多，直接大拆風險太高，很容易一口氣炸滿地。
   - 最終改採：先抽 state / history store / action runner 三塊純責任層。

2) 試過：只新增 tests，不先抽任何內部責任。
   - 為什麼放棄：cache view 這顆已經不是單靠多補 tests 就能改善可維護性。
   - 最終改採：補最小新 tests，同時做低風險內部分層。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 cache view 對外 UI 行為
- 沒有改 presenter/controller 文案
- 沒有拆 query/shard 詳細 panel UI
- 沒有處理 Flet deprecation warnings

---

## Next step

### PR54
- 進入 extractor_view split，利用 PR46 的 jar core 邊界，把 view glue 再收乾淨。
- cache view 先停在「有退層，但不破相容」這個安全位置。
