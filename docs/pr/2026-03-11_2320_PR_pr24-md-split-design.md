# PR24（設計）— 抽離 Markdown pipeline service 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR23 完成後
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR24 把 `app/services.py` 內的 `run_md_translation_service()` 抽離到 `app/services_impl/pipelines/md_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/translation_view.py` 的 lazy import 不必改；這顆 PR 的關鍵是保住 `lang_mode`、step flags、`dry_run`、`write_new_cache` 與目前 error handling 的細節。

---

## 1) Phase 0 盤點

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_md_translation_service(input_dir, session, output_dir=None, dry_run=False, step_extract=True, step_translate=True, step_inject=True, write_new_cache=True, lang_mode="non_cjk_only")`

### 1.2 現行 UI caller
- `app/views/translation_view.py`
  - lazy import：`from app.services import run_md_translation_service`
  - 實際使用：`_run_md()` 中呼叫
- `main.py`
  - 直接 import `TranslationView`
- `tests/test_ui_refactor_guard.py`
  - 把 `translation_view.py` 納入 UI refactor guard 清單

### 1.3 核心依賴
- `translation_tool.core.md_translation_assembly.run_md_pipeline`
- `update_logger_config()`
- `TaskSession`
- `UI_LOG_HANDLER`

### 1.4 邏輯敏感點
- 參數比 KubeJS 更多一個 `lang_mode`
- error handling 和 KubeJS / FTB 又不同：這顆會 `logger.error(...)` + `session.add_log(...)` + `session.set_error()`
- 如果搬移時不小心把錯誤格式統一成別種，UI log 會變

### 1.5 guard test / 測試盤點
- `tests/test_ui_refactor_guard.py` 會 cover `translation_view.py`
- 目前沒有 MD service 專屬 import smoke test

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_md_translation_service|from app\.services import run_md_translation_service|import app\.services" app tests main.py`
- `rg -n "translation_view|test_ui_refactor_guard|run_md_translation_service" tests app main.py`

### 1.7 結論
MD 雖然也掛在 `TranslationView`，但參數與 error 行為都和 FTB / KubeJS 不完全相同，單獨一顆最穩。

---

## 2) PR24 目標
- 新增 `app/services_impl/pipelines/md_service.py`
- 將 `run_md_translation_service()` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `translation_view.py` 不改 import、不改 UI

---

## 3) Scope / Out-of-scope

### Scope
- 新增 `md_service.py`
- 搬入 `run_md_translation_service()`
- `app/services.py` 改由新模組 re-export

### Out-of-scope
- 不改 `translation_view.py`
- 不改 `translation_tool/core/md_translation_assembly.py`
- 不補新的 Markdown UI 功能
- 不順手調整 FTB / KubeJS

---

## 4) 要保留不變的 contract
- lazy import fallback 行為不變
- 參數簽名、順序、預設值不變
- `lang_mode` 原樣傳下去
- error handling 的 `logger + session.add_log + session.set_error` 保持不變

---

## 5) Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import md_service"`
- [ ] `uv run python -c "from app.services import run_md_translation_service"`
- [ ] `uv run python -c "from app.views.translation_view import TranslationView; print('ok')"`
- [ ] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 6) 風險與 rollback
- 主要風險：`lang_mode` 遺漏、error 行為改壞、lazy import 失效
- rollback：回退 `app/services.py`、`app/services_impl/pipelines/md_service.py`、本次 PR 文件

---

## 7) 預期交付物
- `app/services_impl/pipelines/md_service.py`
- `app/services.py`（MD 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr24-md-split.md`
