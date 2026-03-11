# PR20（設計）— 抽離 merge pipeline service 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR19 extract split 完成後
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR20 把 `app/services.py` 內的 `run_merge_zip_batch_service()` 抽離到 `app/services_impl/pipelines/merge_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/merge_view.py` 不必改 import；這顆 PR 的重點不是搬檔案數量，而是小心保住 progress 疊加與 nested try/except 行為。

---

## 1) Phase 0 盤點：merge 相關 caller / import / guard test / 依賴點

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_merge_zip_batch_service(zip_paths, output_dir, session, only_process_lang)`

### 1.2 現行 UI caller
- `app/views/merge_view.py`
  - import：`from app.services import run_merge_zip_batch_service`
  - 實際使用：背景 thread 直接以 `target=run_merge_zip_batch_service` 啟動
- `main.py`
  - 直接 import `MergeView`
- `tests/test_ui_refactor_guard.py`
  - 把 `app/views/merge_view.py` 納入 UI refactor guard 清單

### 1.3 核心依賴
- `translation_tool.core.lang_merger.merge_zhcn_to_zhtw_from_zip`
- `update_logger_config()`
- `TaskSession`
- `UI_LOG_HANDLER`
- `Path`
- `traceback`

### 1.4 邏輯敏感點
- ZIP 層級 progress + inner generator progress 疊加
- nested try/except
- `merge_zhcn_to_zhtw_from_zip(...)` 必須真的被 iterate，否則不會執行
- empty ZIP list 時會直接 `session.finish()`
- 每顆 ZIP 完成後還有一次額外 `session.set_progress((idx + 1) / total)`

### 1.5 guard test / 測試盤點
- `tests/test_ui_refactor_guard.py`
  - 只要求 `merge_view.py` 保持 shared `styled_card`
- `tests/test_main_imports.py`
  - 不涉及 `MergeView`
- 目前 repo 內沒有 merge service 專屬測試

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_merge_zip_batch_service|from app\.services import .*run_merge|import app\.services" app tests main.py`
- `rg -n "merge_view|test_main_imports|test_ui_refactor_guard|run_merge_zip_batch_service" tests app main.py`

### 1.7 結論
Merge 表面上只有一顆 function，但風險高於 extract；可以拆，但應明確把這顆當「邏輯敏感 PR」處理，不要順手加 cleanup。

---

## 2) PR20 目標
- 新增 `app/services_impl/pipelines/merge_service.py`
- 將 `run_merge_zip_batch_service()` 自 `app/services.py` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `app/views/merge_view.py` 不改 import，不改 UI 行為

---

## 3) Scope / Out-of-scope

### Scope
- 新增 `merge_service.py`
- 搬入 `run_merge_zip_batch_service()`
- `app/services.py` 改由新模組 re-export
- 移除 `services.py` 中不再需要的 merge 相關直接依賴（限本顆確定無用者）

### Out-of-scope
- 不改 `app/views/merge_view.py`
- 不改 `translation_tool/core/lang_merger.py`
- 不改 merge UI / checkbox / log 樣式
- 不補 merge 專屬新測試（除非驗證時真的缺）

---

## 4) 要保留不變的 contract
- 函式簽名保持不變
- `MergeView` 仍透過 `from app.services import run_merge_zip_batch_service`
- progress 疊加公式保持不變
- error / log 行為保持不變
- `UI_LOG_HANDLER.set_session(None)` 行為保持不變

---

## 5) Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import merge_service"`
- [ ] `uv run python -c "from app.services import run_merge_zip_batch_service"`
- [ ] `uv run python -c "from app.views.merge_view import MergeView; print('ok')"`
- [ ] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 6) 風險、回歸點與 rollback

### 6.1 主要風險
1. progress 疊加算式被改壞
2. inner generator 沒被 iterate
3. nested try/except 邏輯改壞，錯誤資訊丟失
4. `MergeView` import 沒接回 façade

### 6.2 rollback
- 回退：
  - `app/services.py`
  - `app/services_impl/pipelines/merge_service.py`
  - 本次 PR 文件

---

## 7) 預期交付物
- `app/services_impl/pipelines/merge_service.py`
- `app/services.py`（merge 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr20-merge-split.md`

---

## 8) 備註
這顆 PR 不大，但比 bundle / lookup 難搞。實作時優先守住行為，不要急著順手整理格式。