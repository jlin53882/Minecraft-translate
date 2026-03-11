# PR25（設計）— `app/services.py` façade cleanup（非 QC 線）

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR19 ~ PR24 完成後再做
> QC / checkers：目前暫緩，不納入本顆 cleanup
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR25 不是再拆新 pipeline，而是整理 `app/services.py` 作為 façade 的殘留髒污：移除已無使用的 import、收斂註解、按區塊整理 re-export；但**不碰** QC / checkers 線，也不碰任何你目前預計重寫 / 刪除的頁面。

---

## 1) Phase 0 盤點：目前已確認的殘留項目

### 1.1 `app/services.py` 已確認可疑殘留
目前已查到下列符號在 `app/services.py` 中存在，但在 `app/views` / `tests` / `main.py` 沒看到直接 caller，且 `services.py` 內也沒有實際使用：
- `PROJECT_ROOT`
- `CONFIG_PATH`
- `REPLACE_RULES_PATH`
- `_save_app_config`
- `cache_manager`
- `log_warning`
- `log_error`
- `log_debug`
- `log_info`

### 1.2 目前**不能**動的 façade 出口
以下目前仍有 caller，不能在 cleanup PR 亂砍：
- `_load_app_config`（`update_logger_config()` 還在用）
- `load_config_json` / `save_config_json`
- `load_replace_rules` / `save_replace_rules`
- cache services re-exports
- 已拆出的 lookup / bundle / extract / merge / LM / FTB / KubeJS / MD re-exports

### 1.3 目前暫緩、不納入本顆 cleanup 的項目
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`
- `qc_view.py`

原因：這條線目前方向未定，預計可能重寫或刪除；cleanup PR 不應搶先動它。

### 1.4 Phase 0 實際使用的盤點指令
- `rg -n "PROJECT_ROOT|CONFIG_PATH|REPLACE_RULES_PATH|_save_app_config|cache_manager|log_warning|log_error|log_debug|log_info|run_untranslated_check_service|run_variant_compare_service|run_english_residue_check_service|run_variant_compare_tsv_service" app/services.py app tests main.py`

---

## 2) PR25 目標
- 讓 `app/services.py` 更像單純 façade / export surface
- 移除已確認無用的 import
- 收斂註解與區塊順序，讓後續閱讀成本下降
- 不動功能、不改 caller、不碰 QC 線

---

## 3) Scope / Out-of-scope

### Scope
- 移除已確認無用的 import / 殘留符號
- 依責任重新整理 `app/services.py` 內的 export 區塊
- 視需要調整註解，明確標出：
  - 仍屬 façade 的 export
  - 已暫緩的 QC 線
- 若 cleanup 涉及刪除項目，PR 文件必須逐項說明 caller / 替代路徑 / 風險

### Out-of-scope
- 不改任何 pipeline 真正實作
- 不改 `app/views/*`
- 不動 `qc_view.py` / checkers 線
- 不碰 `translation_tool/*` 核心邏輯
- 不把 cleanup 和新一輪 split 混在一起

---

## 4) 建議 cleanup 順序
1. 先用精準搜尋再次確認每個候選 import 是否真的無 caller
2. 只刪已確認無用的 import
3. 再整理 export 區塊與註解
4. 最後跑 import smoke，確認 façade 沒斷

---

## 5) Validation checklist
- [ ] `uv run python -c "import app.services; print('ok')"`
- [ ] `uv run python -c "from app.services import load_config_json, save_config_json, load_replace_rules, save_replace_rules; print('ok')"`
- [ ] `uv run python -c "from app.services import run_manual_lookup_service, run_bundling_service, run_lang_extraction_service, run_book_extraction_service, run_merge_zip_batch_service, run_lm_translation_service, run_ftb_translation_service, run_kubejs_tooltip_service, run_md_translation_service; print('ok')"`
- [ ] `uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py`

---

## 6) 風險與 rollback

### 6.1 主要風險
1. 把 façade 出口誤判成無用後刪掉
2. cleanup 順手碰到 QC 線，導致未定方向被提前鎖死
3. 註解整理時把「暫緩區 / 已拆區」界線搞混

### 6.2 rollback
- 回退 `app/services.py`
- 回退本次 cleanup PR 文件
- cleanup PR 不應同時改別的檔，讓 rollback 保持單純

---

## 7) 預期交付物
- `app/services.py`（cleanup 後的 façade）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr25-services-facade-cleanup.md`

---

## 8) 備註
這顆 PR 的價值不是加功能，而是把前面連續 split 後留下的邊角料收乾淨。它應該最後做，不要提早做。