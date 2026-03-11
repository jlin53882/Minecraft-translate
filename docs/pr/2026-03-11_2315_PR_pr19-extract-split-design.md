# PR19（設計）— 抽離 extract pipeline services 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：
> - PR17：lookup split
> - PR18：bundle split
> - QC / checkers 線：目前暫緩，不納入這波 pipeline split 主線
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR19 建議先把 `app/services.py` 內的 extract 類 service wrappers 抽離到 `app/services_impl/pipelines/extract_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/extractor_view.py` 不必改 import；預覽功能維持直接走 `jar_processor.preview_extraction_generator`，不納入本顆 PR。

---

## 1) Phase 0 盤點：extract 相關 caller / import / guard test / 依賴點

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_lang_extraction_service(mods_dir, output_dir, session)`
  - `run_book_extraction_service(mods_dir, output_dir, session)`

### 1.2 現行 UI caller
- `app/views/extractor_view.py`
  - import：`from app.services import run_lang_extraction_service, run_book_extraction_service`
  - 實際使用：`target = run_lang_extraction_service if mode == "lang" else run_book_extraction_service`
- `main.py`
  - 直接 import `ExtractorView`
- `tests/test_ui_refactor_guard.py`
  - 把 `app/views/extractor_view.py` 納入 UI refactor guard 清單

### 1.3 核心依賴
- `translation_tool.core.jar_processor.extract_lang_files_generator`
- `translation_tool.core.jar_processor.extract_book_files_generator`
- `update_logger_config()`
- `TaskSession`
- `UI_LOG_HANDLER`
- `GLOBAL_LOG_LIMITER`

### 1.4 風險與邊界
- 這兩顆不是單純 generator wrapper，而是帶有：
  - `session.start()` / `session.finish()` / `session.set_error()`
  - `UI_LOG_HANDLER.set_session(session)` / `set_session(None)`
  - `GLOBAL_LOG_LIMITER.flush()`（lang 有、book 目前沒有）
- `ExtractorView` 的正式提取流程走 `app.services`
- 但 `show_preview()` 是直接呼叫 `translation_tool.core.jar_processor.preview_extraction_generator`
  - 所以 PR19 **不能**順手改 preview 流程

### 1.5 guard test / 測試盤點
- `tests/test_ui_refactor_guard.py`
  - 只要求 `extractor_view.py` 保持用 shared `styled_card`
  - 不直接檢查 extract service import path
- `tests/test_main_imports.py`
  - 不涉及 `ExtractorView`
- 目前 repo 內沒有看到 extract service 的專屬 import smoke test

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_lang_extraction_service|run_book_extraction_service|from app\.services import .*run_lang_extraction|from app\.services import .*run_book_extraction|import app\.services" app tests main.py`
- `rg -n "extractor_view|test_main_imports|test_ui_refactor_guard|run_lang_extraction_service|run_book_extraction_service" tests app main.py`

### 1.7 結論
Extract 雖然有兩顆 function，但 wrapper 結構規律、caller 集中、scope 容易守，比 merge 安全，適合先拆。

---

## 2) PR19 目標
- 新增 `app/services_impl/pipelines/extract_service.py`
- 將 `run_lang_extraction_service()` / `run_book_extraction_service()` 自 `app/services.py` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `app/views/extractor_view.py` 不改 import，不改 UI / preview 行為

---

## 3) Scope / Out-of-scope

### Scope
- 新增 `app/services_impl/pipelines/extract_service.py`
- 搬入：
  - `run_lang_extraction_service()`
  - `run_book_extraction_service()`
- `app/services.py` 改由新模組 re-export
- 移除 `services.py` 中不再需要的 extract generator 直接 import

### Out-of-scope
- 不改 `app/views/extractor_view.py`
- 不改 preview 相關函式與對話框流程
- 不改 `translation_tool/core/jar_processor.py`
- 不補新的 extractor UI 功能
- 不順手清其他殘留 import

---

## 4) 要保留不變的 contract
- 函式簽名保持不變
- `ExtractorView` 仍然用 `from app.services import ...`
- `session` 生命週期行為保持不變
- `UI_LOG_HANDLER` bind / unbind 行為保持不變
- `GLOBAL_LOG_LIMITER.filter(...)` 與 `flush()` 行為保持不變

---

## 5) Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import extract_service"`
- [ ] `uv run python -c "from app.services import run_lang_extraction_service, run_book_extraction_service"`
- [ ] `uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"`
- [ ] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 6) 風險、回歸點與 rollback

### 6.1 主要風險
1. 漏掉 `UI_LOG_HANDLER.set_session(None)`
   - 後果：後續任務可能吃到舊 session
2. lang / book 兩顆搬移時行為不一致
   - 後果：其中一種提取模式會回歸
3. `ExtractorView` import 路徑沒接回 façade
   - 後果：頁面 import fail
4. 順手碰到 preview 流程
   - 後果：scope 失控

### 6.2 rollback
- 回退：
  - `app/services.py`
  - `app/services_impl/pipelines/extract_service.py`
  - 本次 PR 文件
- 不需回退 `ExtractorView`，因為本次不動它

---

## 7) 預期交付物
- `app/services_impl/pipelines/extract_service.py`
- `app/services.py`（extract 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr19-extract-split.md`

---

## 8) 後續建議
- PR20：merge
- PR21：LM
- PR22：FTB
- PR23：KubeJS
- PR24：MD
- PR25：cleanup
