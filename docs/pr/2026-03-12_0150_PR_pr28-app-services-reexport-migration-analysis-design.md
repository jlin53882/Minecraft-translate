# PR28（分析 + 設計）— Migrate callers off `app.services` and remove legacy re-exports

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 原始想法：`Cleanup: Remove legacy import shims and cross-module re-exports`
> 本文件狀態：分析完成 / 設計完成 / **尚未實作**

---

## 一句話結論

PR28 **不能直接刪掉** `app/services.py` 裡目前的 re-export，因為它們現在仍然是正式 caller 正在使用的 façade 入口；如果要清掉這層，必須先把所有 caller 遷移到 canonical modules，確認 repo 內已無 `from app.services import ...` 的依賴後，才能安全刪除。

---

## 1) 背景與原始目標

你原本想做的事情是：
- 清掉 `app/services.py` 裡為過渡期保留的相容性層
- 刪掉所有「注意：為維持相容性而保留」之類註解
- 讓模組依賴更單純，不再透過 cross-module re-export 繞路

方向本身是對的，但目前 repo 的實際狀態不是「這些 re-export 已經沒人在用」，而是：
- `app.services` 仍是目前 UI / main 使用中的**正式 façade 層**
- 它不是 dead shim；它還是 active API surface

所以 PR28 若照原始描述直接做，風險不是 cleanup，而是直接把現行 caller 的 import 打斷。

---

## 2) Phase 0 盤點：目前誰還在依賴 `app.services`

### 2.1 全域搜尋結果
實際搜尋：
- `rg -n "from app\.services import|import app\.services" app tests main.py`

目前仍直接依賴 `app.services` 的檔案有：
- `main.py`
- `app/views/cache_view.py`
- `app/views/bundler_view.py`
- `app/views/lookup_view.py`
- `app/views/qc_view.py`
- `app/views/config_view.py`
- `app/views/extractor_view.py`
- `app/views/lm_view.py`
- `app/views/merge_view.py`
- `app/views/translation_view.py`
- `app/views/rules_view.py`

這代表：
- `app.services` 現在不是「沒人用的過渡殼」
- 而是 repo 內多個 view 與 main 仍在吃的穩定入口

---

## 3) 目前哪些 re-export 還是活的

### 3.1 Config / Rules façade
`app.services` 目前仍提供：
- `load_config_json`
- `save_config_json`
- `load_replace_rules`
- `save_replace_rules`

現行 caller：
- `app/views/config_view.py`
- `app/views/rules_view.py`
- `app/views/bundler_view.py`

### 3.2 Cache façade
`app.services` 目前仍 re-export：
- `cache_get_overview_service`
- `cache_reload_service`
- `cache_reload_type_service`
- `cache_save_all_service`
- `cache_search_service`
- `cache_get_entry_service`
- `cache_update_dst_service`
- `cache_rotate_service`
- `cache_rebuild_index_service`

現行 caller：
- `app/views/cache_view.py`
- `main.py`（`cache_rebuild_index_service`）

### 3.3 Pipeline façade
`app.services` 目前仍 re-export：
- `run_bundling_service`
- `run_manual_lookup_service`
- `run_batch_lookup_service`
- `run_lang_extraction_service`
- `run_book_extraction_service`
- `run_lm_translation_service`
- `run_merge_zip_batch_service`
- `run_ftb_translation_service`
- `run_kubejs_tooltip_service`
- `run_md_translation_service`

現行 caller：
- `bundler_view.py`
- `lookup_view.py`
- `extractor_view.py`
- `lm_view.py`
- `merge_view.py`
- `translation_view.py`

### 3.4 QC / checkers 線
`app.services` 目前仍保留：
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`

現行 caller：
- `app/views/qc_view.py`

但這條線前面已定調：
- `qc_view.py` 可能重寫或刪除
- 所以目前不納入這波 split 主線
- PR28 也不建議碰這條線

---

## 4) 為什麼現在不能直接刪 `services.py` 裡的 re-export

### 核心原因
因為現有 caller **還沒搬走**。

例如你點名的這段：

```python
from app.services_impl.cache.cache_services import (
    cache_get_overview_service,
    cache_reload_service,
    cache_reload_type_service,
    cache_save_all_service,
    cache_search_service,
    cache_get_entry_service,
    cache_update_dst_service,
    cache_rotate_service,
    cache_rebuild_index_service,
)
```

表面上看是 `services.py` 裡的「過渡 re-export」，但實際上：
- `cache_view.py` 現在還是 `from app.services import ...`
- `main.py` 現在也還是 `from app.services import cache_rebuild_index_service`

所以如果現在直接刪：
- 不是清 dead code
- 是直接砍 active import 路徑

同樣的情況也存在於：
- `extractor_view.py`
- `lookup_view.py`
- `lm_view.py`
- `merge_view.py`
- `translation_view.py`
- `config_view.py`
- `rules_view.py`
- `bundler_view.py`

---

## 5) 對原始 PR28 內容的修正判斷

### 原始版本不成立的地方
原文大意是：
> 經過檢查確認，目前現有的程式碼已全部完成重構，不再依賴這些舊有的路徑或引入方式。

這句目前**不成立**。

因為目前 repo 的真實狀態是：
- 已完成的是「實作搬移到 `services_impl/*`」
- **未完成的是「caller 搬離 `app.services`」**

也就是說：
- 你已經完成「後端結構抽離」
- 但還沒完成「前端 import migration」

所以 PR28 若要成立，實際內容應該改成：

> 先把現有 caller 全部從 `app.services` 遷移到 canonical modules，
> 再移除 `services.py` 中不再需要的 re-export 與相關註解。

---

## 6) 建議的 PR28 正確做法

### 正確標題建議
比較準的標題應該是：

`Cleanup: Migrate callers off app.services and remove legacy re-exports`

這比單純寫「Remove legacy import shims」更誠實，因為它反映了真正的工作量：
- 不是只刪 shim
- 而是先搬 caller，再刪 shim

---

## 7) PR28 建議拆法

### 7.1 目標
- 將目前 repo 內所有可安全遷移的 `from app.services import ...` 改成直接 import canonical module
- 在 repo 內已無 caller 依賴後，再移除 `app.services` 內對應的 re-export
- 清理 `services.py` 中已經失效的「相容性註解」

### 7.2 不做的事（Out-of-scope）
- 不碰 `qc_view.py` / checkers 線
- 不重構 UI 邏輯
- 不改 `services_impl/*` 內部實作
- 不改 config schema
- 不碰 `app/views/__init__.py` 的 cache alias 機制（那是另一條相容性決策）

---

## 8) 建議遷移清單（caller -> canonical module）

### 8.1 Config / Rules / Bundle
- `app/views/config_view.py`
  - 改從 `app.services_impl.config_service` import
- `app/views/rules_view.py`
  - 改從 `app.services_impl.config_service` import
- `app/views/bundler_view.py`
  - `run_bundling_service` → `app.services_impl.pipelines.bundle_service`
  - `load_config_json` → `app.services_impl.config_service`

### 8.2 Lookup / Extract / LM / Merge
- `app/views/lookup_view.py`
  - 改從 `app.services_impl.pipelines.lookup_service` import
- `app/views/extractor_view.py`
  - 改從 `app.services_impl.pipelines.extract_service` import
- `app/views/lm_view.py`
  - 改從 `app.services_impl.pipelines.lm_service` import
- `app/views/merge_view.py`
  - 改從 `app.services_impl.pipelines.merge_service` import

### 8.3 TranslationView
- `app/views/translation_view.py`
  - `run_ftb_translation_service` → `app.services_impl.pipelines.ftb_service`
  - `run_kubejs_tooltip_service` → `app.services_impl.pipelines.kubejs_service`
  - `run_md_translation_service` → `app.services_impl.pipelines.md_service`

### 8.4 Cache
- `app/views/cache_view.py`
  - 改從 `app.services_impl.cache.cache_services` import
- `main.py`
  - `cache_rebuild_index_service` 改從 `app.services_impl.cache.cache_services` import

### 8.5 暫緩，不碰
- `app/views/qc_view.py`
- `run_*check*` / `run_variant_compare*`

---

## 9) PR28 預期可刪除的項目（在 caller 搬完之後）

### 9.1 `app/services.py` 中可刪的 re-export
在 caller 遷移完成後，理論上可以刪：
- `load_config_json`
- `save_config_json`
- `load_replace_rules`
- `save_replace_rules`
- cache services 全部 re-export
- lookup / bundle / extract / merge / LM / FTB / KubeJS / MD 的 re-export

### 9.2 可刪的註解
例如：
- `注意：services.py 仍 re-export 同名符號，維持 ... import 相容。`
- `façade / export surface` 的過渡說明（如果 `services.py` 最終不再是 public façade）

但前提是：
- caller 真的全部搬走
- 驗證全部通過

---

## 10) 風險判斷

### 高風險點
1. `translation_view.py` 是 lazy import
- 如果改法不一致，容易出現 `None` fallback 誤判

2. `main.py` 仍直接吃 `cache_rebuild_index_service`
- 如果漏改，啟動時可能炸 import

3. `cache_view.py` import 很多 service
- 這類檔案最容易漏一兩個，然後只在某個按鈕路徑才爆

4. 若誤碰 QC/checkers 線
- 會把目前「暫緩」的東西提前鎖死

---

## 11) 建議驗證範圍

## Validation checklist
- [ ] `uv run python -c "from app.views.config_view import ConfigView; print('ok')"`
- [ ] `uv run python -c "from app.views.rules_view import RulesView; print('ok')"`
- [ ] `uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"`
- [ ] `uv run python -c "from app.views.lookup_view import LookupView; print('ok')"`
- [ ] `uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"`
- [ ] `uv run python -c "from app.views.lm_view import LMView; print('ok')"`
- [ ] `uv run python -c "from app.views.merge_view import MergeView; print('ok')"`
- [ ] `uv run python -c "from app.views.translation_view import TranslationView; print('ok')"`
- [ ] `uv run python -c "import main; print('ok')"`
- [ ] `uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py tests/test_cache_view_features.py tests/test_cache_search_orchestration.py`

---

## 12) 我的建議結論

### 建議做
可以做 PR28，但要改成：
- **先搬 caller**
- **再刪 re-export**
- **不碰 QC/checkers**

### 不建議做
不建議直接照你原始描述：
- 直接刪 `services.py` 內的相容性層
- 不搬 caller
- 只靠「手動檢查沒問題」就認定完成

因為這會是破壞性 cleanup，不是安全 cleanup。

---

## 13) 最後結論

這顆 PR28 真正要做的，不是：
- `Remove dead shims`

而是：
- `Migrate active callers off app.services, then remove legacy re-exports`

這兩者差很多。前者像在掃垃圾，後者其實是一次正式 migration。
