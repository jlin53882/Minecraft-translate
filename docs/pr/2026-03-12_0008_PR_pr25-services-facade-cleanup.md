# PR Title
refactor: clean up app.services facade imports and export layout

# PR Description

## Summary
PR25 不是再拆新的 pipeline，而是整理 `app/services.py` 作為 façade / export surface 的殘留髒污：
- 移除已確認無用的 import
- 重新整理區塊與註解
- 明確標出哪些是 façade exports、哪些是暫緩中的 QC / checkers legacy wrappers

這顆 PR **不碰** QC / checkers 的實作內容，也不改任何 view 或 core pipeline。

---

## Phase 1 完成清單
- [x] 做了：依已批准設計稿完成 PR25（services façade cleanup）
- [x] 做了：修改前先建立可回退備份，且備份放進 `backups/` 的 PR 專屬目錄
- [x] 做了：再次精準驗證候選殘留 import 是否真的無用
- [x] 做了：移除 `app/services.py` 中已確認無用的 import / 殘留符號
- [x] 做了：整理 `app/services.py` 的註解與 export 區塊
- [x] 做了：明確標記 QC / checkers 為暫緩線，但不改其實作
- [x] 做了：完成 Validation checklist 內的 façade import smoke 與測試
- [ ] 未做：QC / checkers 線清理
- [ ] 未做：任何 pipeline 真正實作變更
- [ ] 未做：任何 view / core module 調整

---

## 刪除/移除/替換說明（若有，固定放這裡）

### 刪除項目：`app/services.py` 內未使用的 stdlib import
- **刪除內容**：`os`、`time`、`deque`、`Generator`、`Dict`、`Any`、`Path`
- **為什麼改**：這些 import 是前面多顆 pipeline 逐步抽離後留下的殘留；在目前的 `app/services.py` 內容裡已沒有實際使用。
- **為什麼能刪**：已用精準搜尋再次驗證，`app/services.py` 內無實際引用；目前實際執行邏輯都已搬去 `services_impl/pipelines/` 或 `services_impl/cache/`。
- **目前誰在用 / 沒人在用**：`app/services.py` 已無使用；沒有發現 `app/views` / `tests` / `main.py` 透過 `app.services` 間接依賴這些 stdlib 名稱。
- **替代路徑是什麼**：無需替代；這些只是殘留 import，不是 façade 對外出口。
- **風險是什麼**：風險低，但若誤判有動態使用，可能造成 import-time 錯誤；因此本次有補 façade import smoke 驗證。
- **我是怎麼驗證的**：先以 `rg --fixed-strings` 檢查 `app/services.py` 內是否仍有引用，再跑 `uv run python -c "import app.services; print('ok')"` 與相關 façade smoke test。

### 刪除項目：`app/services.py` 內未使用的 façade / helper import
- **刪除內容**：`LogLimiter`、`UI_LOG_HANDLER`、`PROJECT_ROOT`、`CONFIG_PATH`、`REPLACE_RULES_PATH`、`_save_app_config`、`cache_manager`、`log_warning`、`log_error`、`log_debug`、`log_info`
- **為什麼改**：這些符號在目前的 `app/services.py` 中已無實際使用；它們曾在更早版本由 inline services 或 cache 邏輯使用，但在前面 PR15~PR24 的抽離後已變成殘留。
- **為什麼能刪**：本次有再次用精準搜尋驗證：
  - `app/services.py` 內無實際使用
  - `app/views` / `tests` / `main.py` 沒有從 `app.services` 取用這些符號
- **目前誰在用 / 沒人在用**：
  - `UI_LOG_HANDLER` / `LogLimiter` 的真實使用點在 `app.services_impl.logging_service` 與各 pipeline 子模組，不在 `app.services`
  - `PROJECT_ROOT` / `CONFIG_PATH` / `REPLACE_RULES_PATH` / `_save_app_config` 的真實使用點在 `app.services_impl.config_service`
  - `cache_manager` 與 `log_*` 目前在其他模組直接使用，`app/services.py` 已無需要
- **替代路徑是什麼**：這些符號各自由 `services_impl` 或實際使用模組直接持有；`app/services.py` 不再假裝 re-export 它們。
- **風險是什麼**：若有隱藏 caller 真的從 `app.services` 拿這些符號，cleanup 後會失效；但本次已先做精準搜尋，風險低。
- **我是怎麼驗證的**：用精準搜尋確認 `app/services.py` 內不再引用，並跑 façade import smoke + `tests/test_main_imports.py tests/test_ui_refactor_guard.py`。

### 替換項目：`app/services.py` 頂部說明與區塊註解
- **為什麼改**：原本檔頭仍描述自己像「服務層大雜燴」，與目前已拆成 façade 的現況不完全一致；若繼續保留舊說明，之後讀的人會被誤導。
- **目前誰在用 / 沒人在用**：註解本身沒有 runtime caller，但它會影響維護者理解，因此要跟現況對齊。
- **替代路徑是什麼**：新的說明明確改成：
  - `app/services.py` 是 façade / export surface
  - 新 pipeline 優先放 `app/services_impl/pipelines/`
  - QC / checkers 線目前暫緩
- **風險是什麼**：風險低；但若註解寫錯方向，會讓後續 PR 又把實作塞回 `services.py`。
- **我是怎麼驗證的**：人工比對目前檔案結構與最近 PR17~PR24 的實際拆分結果，確認註解與現況一致。

---

## What was done

### 1. 清掉已驗證無用的 import / 殘留符號
本次再次精準檢查後，從 `app/services.py` 移除了以下目前確定無用的項目：
- stdlib：`os`、`time`、`deque`、`Generator`、`Dict`、`Any`、`Path`
- façade/helper 殘留：`LogLimiter`、`UI_LOG_HANDLER`、`PROJECT_ROOT`、`CONFIG_PATH`、`REPLACE_RULES_PATH`、`_save_app_config`
- 其他殘留：`cache_manager`、`log_warning`、`log_error`、`log_debug`、`log_info`

保留的關鍵出口：
- `_load_app_config`
- `load_config_json` / `save_config_json`
- `load_replace_rules` / `save_replace_rules`
- `GLOBAL_LOG_LIMITER`
- 已拆出的各 pipeline façade exports
- cache services façade exports

### 2. 重整 `app/services.py` 的角色說明
檔頭說明改成更符合現況的版本：
- 明確標出 `app/services.py` 現在主要是 façade / export surface
- 提醒新的 pipeline 實作應優先放進 `app/services_impl/pipelines/`
- 明確標出 QC / checkers 線目前暫緩，不在這顆 cleanup 內處理

### 3. 重整區塊結構
本次把 `app/services.py` 的結構收斂成三塊主要區段：
- `façade helper`
  - 目前只保留 `update_logger_config()` 這個對外入口
- `pipeline façade exports`
  - LM / extract / FTB / KubeJS / MD / merge / lookup / bundle
- `legacy QC / checkers（暫緩線）`
  - 保留現有 wrapper，不動實作
- `cache façade exports`
  - 保留 cache view 仍在使用的出口

### 4. QC / checkers 線刻意只標示、不處理
這顆 PR 沒有去改：
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`
- `qc_view.py`

原因很單純：你前面已經定調這條線可能重寫或刪除，不應在 cleanup PR 先亂動。

### 5. 備份位置
本次修改前已建立可回退備份：
- `backups/pr25-services-facade-cleanup-20260312-0005/app/services.py`

依目前專案備份規則，備份統一放在 `backups/<pr-slug-timestamp>/`，並保留原相對路徑。

---

## Important findings
- `app/services.py` 現在確實已接近 façade，而不是早期那種大雜燴 service 檔。
- 真正不該在這顆 PR 動的，是 QC / checkers 線；把它標成暫緩區，比假裝整理乾淨更誠實。
- 做完這顆後，`services.py` 的閱讀成本已比前面連續 split 前低很多，後續若要重寫 QC 線，邊界也會清楚不少。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有修改任何 pipeline 真正實作
- 沒有修改 `app/views/*`
- 沒有修改 `qc_view.py`
- 沒有刪掉或重寫 checkers wrappers
- 沒有修改 `translation_tool/*` 核心邏輯

---

## Validation checklist
- [x] `uv run python -c "import app.services; print('ok')"`
- [x] `uv run python -c "from app.services import load_config_json, save_config_json, load_replace_rules, save_replace_rules; print('ok')"`
- [x] `uv run python -c "from app.services import run_manual_lookup_service, run_bundling_service, run_lang_extraction_service, run_book_extraction_service, run_merge_zip_batch_service, run_lm_translation_service, run_ftb_translation_service, run_kubejs_tooltip_service, run_md_translation_service; print('ok')"`
- [x] `uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py`

---

## Test result
```text
$ uv run python -c "import app.services; print('ok')"
ok

$ uv run python -c "from app.services import load_config_json, save_config_json, load_replace_rules, save_replace_rules; print('ok')"
ok

$ uv run python -c "from app.services import run_manual_lookup_service, run_bundling_service, run_lang_extraction_service, run_book_extraction_service, run_merge_zip_batch_service, run_lm_translation_service, run_ftb_translation_service, run_kubejs_tooltip_service, run_md_translation_service; print('ok')"
ok

$ uv run pytest -q tests/test_main_imports.py tests/test_ui_refactor_guard.py
.......                                                                  [100%]
7 passed in 0.02s
```
