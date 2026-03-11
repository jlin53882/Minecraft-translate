# PR Title
refactor: split extract pipeline services into app.services_impl.pipelines.extract_service

# PR Description

## Summary
PR19 將 `app/services.py` 內的 extract 類 service wrappers 抽離到 `app/services_impl/pipelines/extract_service.py`，讓 `app/services.py` 持續只做 façade / re-export，維持 `app/views/extractor_view.py` 既有 import 不變。
本次只處理正式提取流程使用的兩顆 extract services：
- `run_lang_extraction_service()`
- `run_book_extraction_service()`

這顆 PR **沒有**動 preview 流程，也**沒有**改 `ExtractorView` UI。

---

## Phase 1 完成清單
- [x] 做了：依已批准設計稿完成 PR19（extract split）
- [x] 做了：修改前先建立可回退備份，且備份放進 `backups/` 的 PR 專屬目錄
- [x] 做了：新增 `app/services_impl/pipelines/extract_service.py`
- [x] 做了：將 `run_lang_extraction_service()` 自 `app/services.py` 抽離到新模組
- [x] 做了：將 `run_book_extraction_service()` 自 `app/services.py` 抽離到新模組
- [x] 做了：`app/services.py` 改為 re-export 新模組中的 extract services
- [x] 做了：移除 `app/services.py` 中不再需要的 `extract_lang_files_generator` / `extract_book_files_generator` 直接 import
- [x] 做了：完成 Validation checklist 內的 import smoke / view import smoke / UI guard test
- [ ] 未做：preview 流程重構
- [ ] 未做：`translation_tool/core/jar_processor.py` 調整
- [ ] 未做：`ExtractorView` UI 改版

---

## 刪除/移除/替換說明（若有，固定放這裡）

### 刪除項目：`app/services.py` 內 inline `run_lang_extraction_service()` 實作
- **為什麼改**：PR19 的目標是讓 `app/services.py` 繼續往 façade 層收斂，把 extract 這組 service wrapper 抽到 `services_impl/pipelines/`，降低 `services.py` 的維護負擔。
- **為什麼能刪**：原本的實作已原樣搬到 `app/services_impl/pipelines/extract_service.py`，而且 `app/services.py` 仍保留同名 re-export，所以外部 caller 不需要改。
- **目前誰在用 / 沒人在用**：實際 caller 為 `app/views/extractor_view.py` 第 23 行 import，以及 `start_extraction()` 內的 `target = run_lang_extraction_service if mode == "lang" else run_book_extraction_service`。沒有在 `main.py` / `tests` 看到其他直接 caller。
- **替代路徑是什麼**：實作改由 `app/services_impl/pipelines/extract_service.py::run_lang_extraction_service` 提供；對外入口仍由 `app/services.py` re-export。
- **風險是什麼**：若 re-export 漏掉，`ExtractorView` 會 import fail；若搬移時漏掉 `UI_LOG_HANDLER.set_session(None)`，後續任務可能吃到舊 session；若漏掉 `GLOBAL_LOG_LIMITER.flush()`，lang 提取尾端 log 行為可能改變。
- **我是怎麼驗證的**：執行 `uv run python -c "from app.services import run_lang_extraction_service, run_book_extraction_service"`、`uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"`、`uv run pytest -q tests/test_ui_refactor_guard.py`，均通過。

### 刪除項目：`app/services.py` 內 inline `run_book_extraction_service()` 實作
- **為什麼改**：與 lang extraction 相同，將 extract family 的 wrapper 收斂到 `services_impl/pipelines/`，避免 `services.py` 繼續膨脹。
- **為什麼能刪**：原本實作已搬到 `app/services_impl/pipelines/extract_service.py`，且 façade 仍保留同名 re-export，既有 caller 不需改 import。
- **目前誰在用 / 沒人在用**：實際 caller 仍是 `app/views/extractor_view.py`，沒有發現其他直接 caller。
- **替代路徑是什麼**：實作改由 `app/services_impl/pipelines/extract_service.py::run_book_extraction_service` 提供；對外入口仍由 `app/services.py` re-export。
- **風險是什麼**：若搬移時把 session start/finish/error 行為改壞，Book 提取模式會回歸；若 import 路徑接錯，`ExtractorView` 會直接 import fail。
- **我是怎麼驗證的**：同上，並確認 `uv run python -c "from app.services_impl.pipelines import extract_service"` 可正常 import。

### 刪除項目：`app/services.py` 內 `extract_lang_files_generator` / `extract_book_files_generator` 直接 import
- **為什麼改**：extract generator 的直接使用點已從 `app/services.py` 轉移到 `app/services_impl/pipelines/extract_service.py`，façade 層不應再直接依賴這兩個 core generator。
- **為什麼能刪**：`app/services.py` 已無直接使用；新的唯一使用點在 `extract_service.py`。
- **目前誰在用 / 沒人在用**：`app/services.py` 已無使用；`app/services_impl/pipelines/extract_service.py` 仍使用中。
- **替代路徑是什麼**：改由 `extract_service.py` 匯入並呼叫。
- **風險是什麼**：如果新模組沒正確匯入 generator，extract service import 會失敗。
- **我是怎麼驗證的**：`uv run python -c "from app.services_impl.pipelines import extract_service"` 通過。

---

## What was done

### 1. 新增 `app/services_impl/pipelines/extract_service.py`
新增 extract 專用 pipeline service 模組，將原本的兩顆 extract wrapper 搬入：
- `run_lang_extraction_service()`
- `run_book_extraction_service()`

這次保留不變的點：
- 函式簽名不變
- `session.start()` / `session.finish()` / `session.set_error()` 行為不變
- `UI_LOG_HANDLER.set_session(session)` / `set_session(None)` 行為不變
- lang extraction 仍保留 `GLOBAL_LOG_LIMITER.flush()`
- book extraction 仍維持目前沒有額外 `flush()` 的行為

### 2. 新模組內自行接回 logger config 更新責任
原本 extract wrapper 直接呼叫 `services.py::update_logger_config()`。
搬移後，新的 `extract_service.py` 透過：
- `app.services_impl.config_service._load_app_config`
- `app.services_impl.logging_service.update_logger_config(...)`

在模組內建立 `_update_logger_config()`，維持每次任務開始前都重新套用 logger config 的行為。

這樣做的理由：
- 避免 `extract_service.py` 反向 import `app.services` 造成 circular import
- 保住原本 runtime 行為，不改 caller

### 3. `app/services.py` 改為 façade / re-export
`app/services.py` 的 extract 區塊已改成：
- 刪除 inline `run_lang_extraction_service()` / `run_book_extraction_service()`
- 刪除不再需要的 `extract_lang_files_generator` / `extract_book_files_generator` import
- 新增：
  - `from app.services_impl.pipelines.extract_service import run_lang_extraction_service, run_book_extraction_service`

效果是：
- `ExtractorView` 不需要改 import
- 本顆 PR 只改 service 實作位置，不動 UI
- `services.py` 繼續往薄 façade 收斂

### 4. Preview 流程刻意不碰
這顆 PR **沒有**動 `ExtractorView.show_preview()` 相關流程。
原因：
- preview 目前直接依賴 `translation_tool.core.jar_processor.preview_extraction_generator`
- 它不是透過 `app.services` 這條 extract façade 走
- 若這顆 PR 順手改 preview，scope 會立刻膨脹，而且風險不成比例

換句話說，PR19 只處理「正式提取流程的 service split」，不處理 preview orchestration。

### 5. 備份位置
本次修改前已建立可回退備份：
- `backups/pr19-extract-split-20260311-2325/app/services.py`

依目前專案備份規則，備份統一放在 `backups/<pr-slug-timestamp>/`，並保留原相對路徑。

---

## Important findings
- Extract family 很適合一起拆：兩顆函式結構高度相似，caller 集中，而且 scope 容易守。
- 真正需要特別防的是 `UI_LOG_HANDLER` 的 bind / unbind，以及 logger config 更新責任；這兩點如果搬移時漏掉，回歸會很難追。
- `ExtractorView` 的 preview 流程與正式提取 service 是兩條不同責任線，這次刻意不混在一起是對的。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有修改 `app/views/extractor_view.py`
- 沒有修改 preview 對話框 / preview poller / preview report 流程
- 沒有修改 `translation_tool/core/jar_processor.py`
- 沒有新增 extractor 專屬新功能
- 沒有清理其他無關的 `services.py` 殘留 import
- 沒有碰 QC / checkers 線

---

## Next step
- PR20：merge split
- PR21：LM split
- PR22：FTB split
- PR23：KubeJS split
- PR24：MD split
- PR25：services façade cleanup（排除 QC / checkers）

---

## Validation checklist
- [x] `uv run python -c "from app.services_impl.pipelines import extract_service"`
- [x] `uv run python -c "from app.services import run_lang_extraction_service, run_book_extraction_service"`
- [x] `uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"`
- [x] `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## Test result
```text
$ uv run python -c "from app.services_impl.pipelines import extract_service"
(no output)

$ uv run python -c "from app.services import run_lang_extraction_service, run_book_extraction_service"
(no output)

$ uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"
ok

$ uv run pytest -q tests/test_ui_refactor_guard.py
......                                                                   [100%]
6 passed in 0.01s
```
