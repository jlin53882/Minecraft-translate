# PR23（設計）— 抽離 KubeJS pipeline service 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR22 完成後
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR23 把 `app/services.py` 內的 `run_kubejs_tooltip_service()` 抽離到 `app/services_impl/pipelines/kubejs_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/translation_view.py` 的 lazy import 不必改；這顆 PR 的關鍵是保住 `step_extract / step_translate / step_inject / write_new_cache` 的參數傳遞，以及目前「logger.error 進 UI、不額外 session.add_log」這個行為。

---

## 1) Phase 0 盤點

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_kubejs_tooltip_service(input_dir, session, output_dir, dry_run=False, step_extract=True, step_translate=True, step_inject=True, write_new_cache=True)`

### 1.2 現行 UI caller
- `app/views/translation_view.py`
  - lazy import：`from app.services import run_kubejs_tooltip_service`
  - 實際使用：`_run_kjs()` 中呼叫
- `main.py`
  - 直接 import `TranslationView`
- `tests/test_ui_refactor_guard.py`
  - 把 `translation_view.py` 納入 UI refactor guard 清單

### 1.3 核心依賴
- `translation_tool.core.kubejs_translator.run_kubejs_pipeline`
- `update_logger_config()`
- `TaskSession`
- `UI_LOG_HANDLER`

### 1.4 邏輯敏感點
- 與 FTB 類似，但 error handling 不完全相同
- 現況是：`logger.error(...)` 後只 `session.set_error()`，**不**再額外 `session.add_log()`
- 如果搬移時把這點改掉，UI log 會變化

### 1.5 guard test / 測試盤點
- `tests/test_ui_refactor_guard.py` 會 cover `translation_view.py`
- 目前沒有 KubeJS service 專屬 import smoke test

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_kubejs_tooltip_service|from app\.services import run_kubejs_tooltip_service|import app\.services" app tests main.py`
- `rg -n "translation_view|test_ui_refactor_guard|run_kubejs_tooltip_service" tests app main.py`

### 1.7 結論
KubeJS 應獨立一顆，因為它和 FTB / MD 雖同在 `TranslationView`，但 error 行為與參數邊界各自不同。

---

## 2) PR23 目標
- 新增 `app/services_impl/pipelines/kubejs_service.py`
- 將 `run_kubejs_tooltip_service()` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `translation_view.py` 不改 import、不改 UI

---

## 3) Scope / Out-of-scope

### Scope
- 新增 `kubejs_service.py`
- 搬入 `run_kubejs_tooltip_service()`
- `app/services.py` 改由新模組 re-export

### Out-of-scope
- 不改 `translation_view.py`
- 不改 `translation_tool/core/kubejs_translator.py`
- 不補新的 KubeJS UI 功能
- 不順手調整 FTB / MD

---

## 4) 要保留不變的 contract
- lazy import fallback 行為不變
- 參數簽名與順序不變
- `logger.error` + `session.set_error()` 行為不變
- `UI_LOG_HANDLER.set_session(None)` 行為不變

---

## 5) Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import kubejs_service"`
- [ ] `uv run python -c "from app.services import run_kubejs_tooltip_service"`
- [ ] `uv run python -c "from app.views.translation_view import TranslationView; print('ok')"`
- [ ] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 6) 風險與 rollback
- 主要風險：error 行為被改掉、lazy import 失效、參數傳遞錯位
- rollback：回退 `app/services.py`、`app/services_impl/pipelines/kubejs_service.py`、本次 PR 文件

---

## 7) 預期交付物
- `app/services_impl/pipelines/kubejs_service.py`
- `app/services.py`（KubeJS 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr23-kubejs-split.md`
