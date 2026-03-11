# PR28a（設計）— Migrate low-risk callers off `app.services`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 主題：Cleanup caller migration（低風險批次）
> 本輪狀態：已盤點 / 已設計，**尚未實作**

---

## 一句話總結

PR28a 先處理低風險 caller，把目前仍 `from app.services import ...` 的低風險 view 改成直接 import canonical modules，為後續移除 `app.services` re-export 鋪路；本顆只動同步 import、邊界清楚、無 lazy import / 啟動流程耦合的 caller。

---

## 1) 背景

PR17~PR25 完成的是：
- 將實作搬到 `app/services_impl/*`
- `app/services.py` 保留 façade / re-export

但目前 repo 真實狀態是：
- 許多 caller 還在直接 `from app.services import ...`
- 所以 `app.services` 目前仍是 active API surface，不是 dead shim

因此如果要做 PR28，正確順序不是直接刪 re-export，而是：
1. 先搬 caller
2. 確認 repo 內已無對應依賴
3. 再刪 `app.services` 中不再需要的 re-export

---

## 2) 為什麼先拆 PR28a / PR28b

拆兩顆的理由：
- `PR28a` 只動低風險 caller，diff 小、回歸面集中
- `PR28b` 再處理高風險 caller（lazy import / main.py / cache_view）
- 出問題時比較好定位，不會一顆 PR 同時混 UI page、main entry、lazy import 與 cache orchestration

---

## 3) Phase 0 盤點：PR28a 範圍

### 3.1 納入 PR28a 的低風險 caller
1. `app/views/config_view.py`
- 現況：`from app.services import load_config_json, save_config_json`
- 建議改成：`from app.services_impl.config_service import load_config_json, save_config_json`

2. `app/views/rules_view.py`
- 現況：`from app.services import load_replace_rules, save_replace_rules`
- 建議改成：`from app.services_impl.config_service import load_replace_rules, save_replace_rules`

3. `app/views/bundler_view.py`
- 現況：`from app.services import run_bundling_service, load_config_json`
- 建議改成：
  - `run_bundling_service` → `app.services_impl.pipelines.bundle_service`
  - `load_config_json` → `app.services_impl.config_service`

4. `app/views/lookup_view.py`
- 現況：`from app.services import run_manual_lookup_service, run_batch_lookup_service`
- 建議改成：`from app.services_impl.pipelines.lookup_service import ...`

5. `app/views/extractor_view.py`
- 現況：`from app.services import run_lang_extraction_service, run_book_extraction_service`
- 建議改成：`from app.services_impl.pipelines.extract_service import ...`

### 3.2 為什麼這五個算低風險
- 都是同步 import
- 沒有 `try/except` lazy import
- 不牽涉 `main.py` 啟動入口
- 不直接碰 `cache_view.py` 那種 event surface 很大的頁面
- 對應 canonical module 都已存在且已經被 PR17~PR19 驗證過

### 3.3 明確不納入 PR28a
- `app/views/translation_view.py`（lazy import，高風險）
- `app/views/lm_view.py`
- `app/views/merge_view.py`
- `app/views/cache_view.py`
- `main.py`
- `app/views/qc_view.py`

---

## 4) PR28a 目標
- 將低風險 caller 從 `app.services` 遷移到 canonical modules
- 不改邏輯，只改 import path
- 讓 `app.services.py` 之後可以安全刪除對應的低風險 re-export

---

## 5) Scope / Out-of-scope

### Scope
- 修改：
  - `app/views/config_view.py`
  - `app/views/rules_view.py`
  - `app/views/bundler_view.py`
  - `app/views/lookup_view.py`
  - `app/views/extractor_view.py`
- 視需要：同步更新 PR 文件或註解，說明 canonical import 已落地

### Out-of-scope
- 不改 `app/services.py` 的高風險 re-export
- 不改 `translation_view.py`
- 不改 `lm_view.py`
- 不改 `merge_view.py`
- 不改 `cache_view.py`
- 不改 `main.py`
- 不碰 `qc_view.py` / checkers 線

---

## 6) 預計修改

### 6.1 import migration
- `config_view.py`
  - `app.services` → `app.services_impl.config_service`
- `rules_view.py`
  - `app.services` → `app.services_impl.config_service`
- `bundler_view.py`
  - `run_bundling_service` 直連 `bundle_service`
  - `load_config_json` 直連 `config_service`
- `lookup_view.py`
  - 直連 `lookup_service`
- `extractor_view.py`
  - 直連 `extract_service`

### 6.2 `app/services.py` 本顆建議先不刪 export
雖然理論上低風險 caller 搬完後，可開始刪部分 re-export；
但我建議 PR28a 先只做 caller migration，不在同一顆再刪 export。
原因：
- 把「搬 caller」與「刪 façade」分成兩步，定位更清楚
- 萬一有漏掉的 hidden caller，rollback 比較單純

---

## 7) Validation checklist
- [ ] `uv run python -c "from app.views.config_view import ConfigView; print('ok')"`
- [ ] `uv run python -c "from app.views.rules_view import RulesView; print('ok')"`
- [ ] `uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"`
- [ ] `uv run python -c "from app.views.lookup_view import LookupView; print('ok')"`
- [ ] `uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"`
- [ ] `uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py`

---

## 8) Rejected approaches
- 試過：把低風險 caller 與高風險 caller（`translation_view.py` / `cache_view.py` / `main.py`）合併成一顆 PR28 一次做完。
- 為什麼放棄：Phase 0 盤點顯示 caller 橫跨 11 個檔案，且 `translation_view.py` 有 lazy import、`main.py` 有啟動入口、`cache_view.py` surface 太大；若一顆全做，出問題時很難判斷是低風險同步 import，還是高風險 lazy import / main / cache orchestration 造成。這不是 test 已失敗才放棄，而是基於回歸面與 diff 規模先行降風險。
- 最終改採：拆成 `PR28a`（低風險 caller migration）與 `PR28b`（高風險 caller migration）兩顆。

---

## 9) Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有刪除 `app.services.py` 的 re-export
- 沒有修改 `translation_view.py`
- 沒有修改 `lm_view.py`
- 沒有修改 `merge_view.py`
- 沒有修改 `cache_view.py`
- 沒有修改 `main.py`
- 沒有碰 `qc_view.py` / checkers 線

---

## 10) Next step

### PR28b
- 處理高風險 caller：
  - `translation_view.py`
  - `lm_view.py`
  - `merge_view.py`
  - `cache_view.py`
  - `main.py`
- 待兩顆 caller migration 都完成後，再考慮開下一顆真正刪 `app.services` re-export 的 cleanup PR
