# PR21（設計）— 抽離 LM pipeline service 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR19 / PR20 完成後
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR21 把 `app/services.py` 內的 `run_lm_translation_service()` 抽離到 `app/services_impl/pipelines/lm_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/lm_view.py` 不必改 import；這顆 PR 的關鍵是保住 `dry_run`、`export_lang`、`write_new_cache` 參數行為，以及 `GLOBAL_LOG_LIMITER.flush()` 的尾端處理。

---

## 1) Phase 0 盤點

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_lm_translation_service(input_dir, output_dir, session, dry_run=False, export_lang=False, write_new_cache=True)`

### 1.2 現行 UI caller
- `app/views/lm_view.py`
  - import：`from app.services import run_lm_translation_service`
  - 實際使用：背景 thread 直接以 `target=run_lm_translation_service` 啟動
- `main.py`
  - 直接 import `LMView`
- `tests/test_ui_refactor_guard.py`
  - 把 `app/views/lm_view.py` 納入 UI refactor guard 清單

### 1.3 核心依賴
- `translation_tool.core.lm_translator.translate_directory_generator as lm_translate_gen`
- `update_logger_config()`
- `TaskSession`
- `UI_LOG_HANDLER`
- `GLOBAL_LOG_LIMITER.flush()`

### 1.4 邏輯敏感點
- `dry_run=True` 時要補前後提示 log
- `export_lang` / `write_new_cache` 要原樣傳下去
- `update_dict` 轉 `session` 的流程必須保持
- generator 結束後要做 `GLOBAL_LOG_LIMITER.flush()`

### 1.5 guard test / 測試盤點
- `tests/test_ui_refactor_guard.py` 只 guard UI 結構
- 目前沒有 LM service 專屬 import smoke test

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_lm_translation_service|from app\.services import run_lm_translation_service|import app\.services" app tests main.py`
- `rg -n "lm_view|test_ui_refactor_guard|run_lm_translation_service" tests app main.py`

### 1.7 結論
LM 的 service wrapper 比 extract/merge 更像正式 pipeline service，但 caller 集中、參數邊界清楚，適合單獨一顆 PR。

---

## 2) PR21 目標
- 新增 `app/services_impl/pipelines/lm_service.py`
- 將 `run_lm_translation_service()` 自 `app/services.py` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `app/views/lm_view.py` 不改 import，不改 UI 行為

---

## 3) Scope / Out-of-scope

### Scope
- 新增 `lm_service.py`
- 搬入 `run_lm_translation_service()`
- `app/services.py` 改由新模組 re-export
- 移除 `services.py` 中不再需要的 LM 直接依賴（限本顆確定無用者）

### Out-of-scope
- 不改 `app/views/lm_view.py`
- 不改 `translation_tool/core/lm_translator.py`
- 不改 API / cache / prompt / batch 行為
- 不清理其他無關殘留 import

---

## 4) 要保留不變的 contract
- 函式簽名保持不變
- `LMView` 仍透過 `from app.services import run_lm_translation_service`
- `dry_run` 前後提示 log 保持不變
- `GLOBAL_LOG_LIMITER.flush()` 保持不變
- error / finish / unbind 行為保持不變

---

## 5) Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import lm_service"`
- [ ] `uv run python -c "from app.services import run_lm_translation_service"`
- [ ] `uv run python -c "from app.views.lm_view import LMView; print('ok')"`
- [ ] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 6) 風險與 rollback
- 主要風險：`dry_run` 提示漏掉、參數傳遞錯位、flush 漏掉、session unbind 漏掉
- rollback：回退 `app/services.py`、`app/services_impl/pipelines/lm_service.py`、本次 PR 文件

---

## 7) 預期交付物
- `app/services_impl/pipelines/lm_service.py`
- `app/services.py`（LM 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr21-lm-split.md`
