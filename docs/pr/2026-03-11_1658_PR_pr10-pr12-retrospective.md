# PR10～PR12 回顧報告

> 專案：`Minecraft_translator_flet`
> 範圍：`cache_manager` 重構主線
> 時間：2026-03-11
> 回顧對象：PR10 / PR11 / PR12

---

## 一句話總結

PR10～PR12 的核心成果，是把原本混在 `cache_manager.py` 裡的 shard/persistence、state/store、search orchestration 三種責任拆成較清楚的分層，同時保留 `cache_manager.py` 作為 façade / compatibility 入口，讓重構落地後沒有破壞既有外部契約。

---

## 這條主線原本要解的問題

在 PR10 開始前，`translation_tool/utils/cache_manager.py` 同時承擔：

- cache bootstrap / lifecycle
- in-memory cache state
- dirty / session tracking
- shard persistence
- active shard rotation
- atomic write
- search metadata inference
- search engine lifecycle
- rebuild index
- query façade

直接結果是：

1. 檔案過胖，責任邊界模糊
2. 改 shard 容易碰到 search
3. 改 state 容易碰到 persistence
4. search rebuild / metadata / query contract 糾纏在一起
5. 測試難以對準真正的責任切面

---

## PR10：Shard / Persistence 抽層

### 目標
先把最容易獨立、最容易測、最不容易破壞外部契約的 shard / persistence 邏輯抽出去。

### 實際落地
- 新增 `translation_tool/utils/cache_shards.py`
- 保留 `cache_manager.py` façade / wrapper：
  - `_write_json_atomic`
  - `_get_active_shard_path`
  - `_rotate_shard_if_needed`
  - `_save_entries_to_active_shards`
- 新增 `tests/test_cache_shards.py`

### 成果
- shard 寫入、rotate、atomic save 不再混在 manager 的主邏輯裡
- persistence 細節被清楚隔離
- 後續若要調整 shard 策略，不必先碰 search / query 相關程式

### 驗證
- `tests/test_cache_shards.py` 通過
- full pytest 通過（當時 31 passed）

### 評價
PR10 是這條主線裡風險最低、收益最穩的一刀，成功替後續 PR11 / PR12 清出空間。

---

## PR11：State / Store 分層 + bootstrap 清晰化

### 目標
把 state / store / CRUD 邏輯從 `cache_manager.py` 中抽出，但保留相容性，不直接去動最危險的 global state ownership。

### 實際落地
- 新增 `translation_tool/utils/cache_store.py`
- 新增 `tests/test_cache_store.py`
- `cache_manager.py` 改為：
  - façade + orchestration
  - lock owner
  - compatibility layer

### 關鍵設計決策
- `_cache_lock` **留在** `cache_manager.py`
- `cache_store.py` 視為 **非 thread-safe 純操作層**
- `_translation_cache / _initialized` ownership **不搬移**
- 不新增 `cache_bootstrap.py`
- 不動 `lm_translator.py` 對 `_translation_cache / _initialized` 的直接依賴

### 成果
- manager 不再同時扛資料操作與流程協調
- state/store 操作開始有穩定切面
- 但又避免把 PR11 膨脹成會直接炸掉 translation core 的危險重構

### 驗證
- `tests/test_cache_store.py` 通過
- full pytest 通過（34 passed）
- `cache_manager` import smoke 通過
- `lm_translator` import smoke 通過

### 評價
PR11 的價值在於：它沒有追求一次拆乾淨，而是先把內部抽象建立起來，保留外部相容，這讓後續 PR12 得以正式改走 store accessor 路線。

---

## PR12：Search orchestration 收斂

### 目標
讓 search engine lifecycle、metadata inference、rebuild orchestration 從 `cache_manager.py` 正式收斂到 `cache_search.py`，但不破壞 search API contract 與 UI 行為。

### 實際落地
- `translation_tool/utils/cache_search.py`
  - 收納 metadata helper：
    - `_extract_path_from_composite_key`
    - `_infer_search_path`
    - `_infer_search_mod`
    - `_build_search_metadata`
  - 新增 orchestration helper：
    - `build_index_entries(...)`
    - `rebuild_from_cache_dicts(...)`
  - 新增 `SearchOrchestrator`
- `translation_tool/utils/cache_manager.py`
  - 保留 search façade
  - 內部改委派給 `SearchOrchestrator`
- 新增 `tests/test_cache_search_orchestration.py`

### 關鍵設計決策
- 正式依賴 PR11 的 store accessor
- **不新增** `cache_metadata.py`
- 索引生命週期採 **先建後切換**
- rebuild 期間查詢仍走舊引擎，至少保證不 crash

### 成果
- search 相關責任終於集中到正確的模組
- `cache_manager.py` 不再自己混 search rebuild / metadata / query 細節
- PR7 補上的 metadata 邏輯有正式歸位

### 驗證
- full pytest 通過（37 passed）
- `cache_manager` import smoke 通過
- `cache_search_service` import smoke 通過
- schema 欄位相容 smoke 通過
- single-type rebuild isolation 通過
- double rebuild clean 通過
- rebuild 期間 query 不 crash 通過

### 評價
PR12 的完成，代表這條 `cache_manager` 重構主線真正收尾：search 子系統不再只是 manager 裡的一坨流程，而是變成可辨識、可維護的正式模組責任。

---

## 目前這三顆 PR 帶來的整體影響

### 正面影響
1. `cache_manager.py` 不再是萬能神檔
2. shard / store / search 三層邊界已經清楚很多
3. 測試覆蓋面跟著分層切面一起提升
4. 後續若要查問題，定位成本比以前低很多
5. 架構整理是「保相容地落地」，不是拆完才發現整條主流程壞掉

### 仍存在的痛點
1. `lm_translator.py` 仍直接依賴 `_translation_cache / _initialized`
   - 這是目前最大的殘留耦合點
2. `cache_manager.py` 雖然瘦了，但仍持有 façade / orchestration / compatibility / global state ownership
3. `cache_search.py` 變成新的重要模組，後續若 search 邏輯持續擴大，仍可能成為下一個熱點
4. Windows 下 build-then-switch 仍存在極短切換窗口
   - 目前可接受，因為至少不 crash
5. `.agentlens` 中原本提到的 `app/services.py` 偏胖問題仍在

---

## 目前最值得的管理判斷

### 這輪是否成功？
**是，而且是成功收尾。**

原因不是因為 commit 多，而是因為它同時做到：
- 邊界真的變清楚
- 外部相容沒破
- 測試跟著補上
- 文件也同步落地，不是只改 code

### 現在適不適合立刻再開新的大型主線？
**不建議立刻連開。**

比較合理的是：
- 先收尾
- 先使用 / 觀察
- 有新痛點再開新主線

### 如果之後真的要開下一條主線，優先候選是什麼？
**`app/services.py` 分拆**

因為相較於直接碰 translation core：
- 風險較低
- 收益明確
- 比較符合目前專案重構順序

---

## Commit 索引（本輪主線）
- `cc02e88` — PR10：extract cache shard persistence into cache_shards
- `cc98fd6` — PR11：extract cache state/store helpers into cache_store
- `c610d94` — PR12：consolidate cache search orchestration into cache_search

補充文件 / follow-up：
- `d1faf01` — PR10 文件補實作結果
- `136c5ef` — PR10 follow-up 語意清晰化
- `9cb9c34` — PR11 / PR12 設計稿補強

---

## 最終總結

PR10～PR12 最大的成果，不是單一函式被搬去哪裡，而是這條主線終於把：

- persistence
- state/store
- search orchestration

從 `cache_manager.py` 這個神檔裡拆成了比較像樣的分層架構。

而目前最大的殘留風險，也很清楚：

> `lm_translator.py` 仍直接依賴 cache 內部全域狀態。

所以這輪的結論可以很簡單：

**這條重構主線已經成功落地，可以收；下一輪若要再開，不該無腦繼續深拆 cache，而應優先評估 `app/services.py` 分拆是否更值得。**
