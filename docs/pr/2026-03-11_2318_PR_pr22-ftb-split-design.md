# PR22（設計）— 抽離 FTB pipeline service 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR21 完成後
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR22 把 `app/services.py` 內的 `run_ftb_translation_service()` 抽離到 `app/services_impl/pipelines/ftb_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/translation_view.py` 的 lazy import 不必改；這顆 PR 的重點是保住 step flags、`dry_run`、`write_new_cache` 與 `TaskSession` 行為。

---

## 1) Phase 0 盤點

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_ftb_translation_service(directory_path, session, output_dir, dry_run=False, step_export=True, step_clean=True, step_translate=True, step_inject=True, write_new_cache=True)`

### 1.2 現行 UI caller
- `app/views/translation_view.py`
  - 採 `try/except` lazy import：`from app.services import run_ftb_translation_service`
  - 實際使用：`_run_ftb()` 中呼叫
- `main.py`
  - 直接 import `TranslationView`
- `tests/test_ui_refactor_guard.py`
  - 把 `translation_view.py` 納入 UI refactor guard 清單

### 1.3 核心依賴
- `translation_tool.core.ftb_translator.run_ftb_pipeline`
- `update_logger_config()`
- `TaskSession`
- `UI_LOG_HANDLER`

### 1.4 邏輯敏感點
- `TranslationView` 對 service 不可用的情況有 fallback（設成 `None`）
- `run_ftb_translation_service()` 內直接呼叫 `run_ftb_pipeline(...)`
- step flags 多，錯一個就容易變回歸
- error handling 會 `session.add_log()` + `session.set_error()`

### 1.5 guard test / 測試盤點
- `tests/test_ui_refactor_guard.py` 會檢查 `translation_view.py` 仍用 shared `styled_card`
- 目前沒有 FTB service 專屬 import smoke test

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_ftb_translation_service|from app\.services import run_ftb_translation_service|import app\.services" app tests main.py`
- `rg -n "translation_view|test_ui_refactor_guard|run_ftb_translation_service" tests app main.py`

### 1.7 結論
FTB 已經比 extract / merge / LM 更容易因參數或 pipeline 入口錯位出事，所以應獨立一顆，不和 KubeJS / MD 合併。

---

## 2) PR22 目標
- 新增 `app/services_impl/pipelines/ftb_service.py`
- 將 `run_ftb_translation_service()` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `translation_view.py` 不改 import、不改 UI

---

## 3) Scope / Out-of-scope

### Scope
- 新增 `ftb_service.py`
- 搬入 `run_ftb_translation_service()`
- `app/services.py` 改由新模組 re-export

### Out-of-scope
- 不改 `app/views/translation_view.py`
- 不改 `translation_tool/core/ftb_translator.py`
- 不調整 FTB UI 選項、步驟預設值、task runner
- 不順手整理 KubeJS / MD

---

## 4) 要保留不變的 contract
- `TranslationView` 仍透過 `from app.services import run_ftb_translation_service`
- lazy import fallback 行為不變
- 參數簽名與 step flags 順序不變
- error / finish / unbind 行為不變

---

## 5) Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import ftb_service"`
- [ ] `uv run python -c "from app.services import run_ftb_translation_service"`
- [ ] `uv run python -c "from app.views.translation_view import TranslationView; print('ok')"`
- [ ] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 6) 風險與 rollback
- 主要風險：step flags 錯位、lazy import 失效、session 錯誤處理改壞
- rollback：回退 `app/services.py`、`app/services_impl/pipelines/ftb_service.py`、本次 PR 文件

---

## 7) 預期交付物
- `app/services_impl/pipelines/ftb_service.py`
- `app/services.py`（FTB 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr22-ftb-split.md`
