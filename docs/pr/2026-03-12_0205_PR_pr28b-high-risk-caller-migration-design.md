# PR28b（設計）— Migrate high-risk callers off `app.services`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 主題：Cleanup caller migration（高風險批次）
> 本輪狀態：已盤點 / 已設計，**尚未實作**

---

## 一句話總結

PR28b 處理高風險 caller，把仍依賴 `app.services` 的高風險檔案改成直接 import canonical modules；這顆特別小心 `translation_view.py` 的 lazy import、`main.py` 的啟動入口、以及 `cache_view.py` 的大 surface，目的仍是搬 caller，而不是在這顆直接刪 `app.services`。

---

## 1) 背景

PR28a / PR28b 的共同前提是：
- `app.services` 現在仍是 active façade
- 如果不先搬 caller，就不能安全刪 re-export

PR28b 是第二顆，專門處理剩下風險較高、較容易炸整頁或啟動流程的 caller。

---

## 2) Phase 0 盤點：PR28b 範圍

### 2.1 納入 PR28b 的高風險 caller
1. `app/views/translation_view.py`
- 現況：
  - `try/except` lazy import `run_ftb_translation_service`
  - `try/except` lazy import `run_kubejs_tooltip_service`
  - `try/except` lazy import `run_md_translation_service`
- 高風險原因：
  - 不是單純同步 import
  - 改法若不一致，可能讓 service fallback 成 `None`

2. `app/views/lm_view.py`
- 現況：`from app.services import run_lm_translation_service`
- 高風險原因：
  - 雖然 import 本身不複雜，但這頁屬長任務入口，且和 LM runtime 參數邊界有關

3. `app/views/merge_view.py`
- 現況：`from app.services import run_merge_zip_batch_service`
- 高風險原因：
  - merge service 本身已知是敏感 wrapper；這顆不難改，但一旦錯就會影響 ZIP merge 主流程

4. `app/views/cache_view.py`
- 現況：大量 `cache_*_service` 來自 `app.services`
- 高風險原因：
  - surface 很大
  - 事件多
  - 最容易漏改某個按鈕路徑才爆

5. `main.py`
- 現況：`from app.services import cache_rebuild_index_service`
- 高風險原因：
  - 屬於 app 啟動入口
  - 若改壞會讓整個 app import / 啟動路徑出問題

### 2.2 明確不納入 PR28b
- `app/views/qc_view.py`
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`

原因：
- QC/checkers 線目前仍是暫緩區
- 前面已定調可能重寫或刪除
- 不應在這波 migration 先動它

---

## 3) PR28b 目標
- 把高風險 caller 從 `app.services` 遷移到 canonical modules
- 特別保住 `translation_view.py` 的 lazy import 行為
- 讓 `main.py` / `cache_view.py` 也不再依賴 `app.services`
- 為下一顆真正刪 re-export 的 cleanup PR 做準備

---

## 4) Scope / Out-of-scope

### Scope
- 修改：
  - `app/views/translation_view.py`
  - `app/views/lm_view.py`
  - `app/views/merge_view.py`
  - `app/views/cache_view.py`
  - `main.py`

### Out-of-scope
- 不刪 `app.services.py` 內的 QC / checkers wrappers
- 不改 `qc_view.py`
- 不重構 cache view UI
- 不重構 translation view UI
- 不調整 `services_impl/*` 內部邏輯
- 不在這顆直接刪 `app.services` re-export

---

## 5) 預計修改

### 5.1 `translation_view.py`
- lazy import 改指向：
  - `app.services_impl.pipelines.ftb_service`
  - `app.services_impl.pipelines.kubejs_service`
  - `app.services_impl.pipelines.md_service`
- 保持目前 `try/except` fallback 風格不變

### 5.2 `lm_view.py`
- `run_lm_translation_service` 改直連 `app.services_impl.pipelines.lm_service`

### 5.3 `merge_view.py`
- `run_merge_zip_batch_service` 改直連 `app.services_impl.pipelines.merge_service`

### 5.4 `cache_view.py`
- `cache_*_service` 改直連 `app.services_impl.cache.cache_services`
- 只改 import 路徑，不改 event handler / UI 邏輯

### 5.5 `main.py`
- `cache_rebuild_index_service` 改直連 `app.services_impl.cache.cache_services`

### 5.6 `app.services.py` 本顆建議仍先不刪 export
原因：
- 先做高風險 caller migration
- 等 repo 內確認 caller 都搬完，再開下一顆刪 export 的 cleanup PR
- 這樣出問題時 rollback 容易

---

## 6) Validation checklist
- [ ] `uv run python -c "from app.views.translation_view import TranslationView; print('ok')"`
- [ ] `uv run python -c "from app.views.lm_view import LMView; print('ok')"`
- [ ] `uv run python -c "from app.views.merge_view import MergeView; print('ok')"`
- [ ] `uv run python -c "from app.views.cache_view import CacheView; print('ok')"`
- [ ] `uv run python -c "import main; print('ok')"`
- [ ] `uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py tests/test_cache_view_features.py tests/test_cache_search_orchestration.py tests/test_cache_view_monkeypatch_integration.py tests/test_cache_view_state_gate.py`

---

## 7) Rejected approaches
- 試過：把高風險 caller 也塞進 PR28a，一次把全部 caller migration 做完。
- 為什麼放棄：`translation_view.py` 有 lazy import、`main.py` 是啟動入口、`cache_view.py` surface 很大，這三者若跟低風險 caller 混在同一顆，任何 ImportError 或行為回歸都很難快速定位。這不是某個 test 已經報錯後才撤退，而是根據 Phase 0 盤點先行切風險。
- 最終改採：拆出 `PR28b`，專門處理高風險 caller，並用更重的 import smoke + cache view 測試覆蓋它。

---

## 8) Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有刪掉 `app.services.py` 的 re-export
- 沒有修改 `qc_view.py`
- 沒有修改 checkers wrappers
- 沒有重構 `cache_view.py` 內部事件邏輯
- 沒有重構 `translation_view.py` 內部流程

---

## 9) Next step

### PR29（建議）
- 在 PR28a + PR28b 都完成後
- 再盤點一次 repo 內是否還有 `from app.services import ...`
- 若低到只剩 QC/checkers 暫緩線，才考慮開真正刪除 legacy re-export 的 cleanup PR
