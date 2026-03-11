# PR14（設計）— 抽離 logging / 節流 / handler 綁定到 `services_impl/logging_service.py`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：PR13 已建立 `app/services_impl/` 骨架（implementation package），`app/services.py` 仍為唯一行為來源。
> 本輪狀態：已實作 / 已驗證（PR14 落地：抽離 shared logging 元件；不改 pipeline/service 流程）。

---

## 一句話總結

`app/services.py` 目前同時承擔「UI 任務協調」與「logging/節流/handler 綁定」兩種 concern；PR14 建議先把 logging 相關元件抽離到 `app/services_impl/logging_service.py`，並由 `app/services.py` 持續 re-export 以維持 UI caller 的 import 相容。

---

## 1) Phase 0 盤點：logging / handler / 節流目前的分布

### 1.1 相關元件（在 `app/services.py`）
- `class LogLimiter`：UI log 節流器（合併多筆 log、限制刷新頻率）
- `GLOBAL_LOG_LIMITER = LogLimiter(...)`：全域節流器實例
- `UI_LOG_HANDLER = UISessionLogHandler()`：將核心 logger 轉送到 UI TaskSession
- `update_logger_config()`：
  - 讀取 config（log_level / log_format）
  - 確保 `UI_LOG_HANDLER` 掛到 root logger（避免重複 handler）
  - 設定 root logger / translation_tool logger / handler 的 level 與 formatter

### 1.2 attach/detach / session 綁定的使用點（在 `app/services.py`）
盤點結果：大量 service 入口會做以下流程：
- 任務開始：
  - `update_logger_config()`
  - `session.start()`
  - `UI_LOG_HANDLER.set_session(session)`
- 任務中：
  - 以 `GLOBAL_LOG_LIMITER.filter(update_dict)` 節流 generator 的 update
  - 或直接 `session.add_log(...) / session.set_progress(...)`
- 任務結束：
  - `final = GLOBAL_LOG_LIMITER.flush()`（部分流程）
  - `session.finish()` 或 `session.set_error()`
- finally：
  - `UI_LOG_HANDLER.set_session(None)`（避免 handler 留著舊 session）

### 1.3 哪些 service 入口會碰到 handler / logger config（摘要）
- translation pipelines：
  - `run_lm_translation_service`
  - `run_lang_extraction_service` / `run_book_extraction_service`
  - `run_ftb_translation_service`
  - `run_kubejs_tooltip_service`
  - `run_md_translation_service`
  - `run_merge_zip_batch_service`
  - `run_bundling_service`
  - checkers：`run_untranslated_check_service` / `run_variant_compare_service` / `run_english_residue_check_service` / `run_variant_compare_tsv_service`
- lookup：`run_batch_lookup_service` 等

Cache UI services 本身多為同步流程，但其依賴的 search / rebuild 可能會產生日誌（間接受影響）。

### 1.4 UI caller 依賴現況（`from app.services import ...`）
- main.py：`cache_rebuild_index_service`
- views：bundler/config/extractor/lm/lookup/merge/qc/rules/translation/cache_view 等多處直接 import

結論：PR14 必須維持 `app.services` 的 import path 不變（以 façade/re-export 方式）。

---

## 2) PR14 設計

### 2.1 目標
- 把 logging/節流/handler 綁定責任從 `app/services.py` 抽離至：
  - `app/services_impl/logging_service.py`
- `app/services.py` 保持相容 API：
  - 仍可被既有 UI views 直接 import
  - 對外函式名稱與行為不變

### 2.2 Scope（In scope）
在 PR14 中，預計抽離以下內容到 `services_impl/logging_service.py`：
- `LogLimiter`
- `GLOBAL_LOG_LIMITER`
- `UI_LOG_HANDLER`
- `update_logger_config()`
- （可選，但建議）補一組薄 helper，讓 service 入口更一致：
  - `bind_session(session)` / `unbind_session()` 或 contextmanager（仍需評估是否會超出 PR14 範圍）

> 注意：PR14 仍可維持現狀（service 入口自己 set_session / finally 清理），本輪只要把 shared 元件抽離即可。

### 2.3 Out-of-scope（本 PR 不做）
- 不搬移任何 pipeline wrapper（LM/抽取/翻譯/檢查/打包/合併）
- 不改任何 service 入口的邏輯流程（只調整 import 來源）
- 不更動 `UISessionLogHandler` 的實作

### 2.4 建議新增/修改的檔案
- 新增：`app/services_impl/logging_service.py`
- 修改：
  - `app/services.py`
    - 將上述元件改為 `from app.services_impl.logging_service import ...`
    - 其餘函式仍留在本檔（行為不變）
  - （可選）`app/services_impl/__init__.py`
    - 若希望提供統一入口，可 re-export `logging_service`（但非必要）

### 2.5 搬移順序（建議）
1. 新增 `logging_service.py`：把 `LogLimiter` / `GLOBAL_LOG_LIMITER` / `UI_LOG_HANDLER` / `update_logger_config()` 原樣搬入
2. 在 `app/services.py` 用 import 取代原本定義（維持符號名稱不變）
3. 逐一執行 smoke / pytest，確保：
   - handler 仍會被掛到 root logger
   - session 綁定/解除仍在 finally 中正確執行

### 2.6 façade / re-export 相容策略
- 既有 UI caller 使用：`from app.services import ...`
- PR14 後仍維持：
  - `app/services.py` 匯出 `UI_LOG_HANDLER/GLOBAL_LOG_LIMITER/update_logger_config/LogLimiter`
  - 只是其實作來源改到 `services_impl/logging_service.py`
- 相容性補充：
  - 若 caller 使用的是 `import app.services as s` 再透過 `s.GLOBAL_LOG_LIMITER` / `s.UI_LOG_HANDLER` 取 module attribute，這條路徑也必須保持成立。
  - 做法：`app/services.py` 需明確 re-export 同一個物件，而不是重新建立新實例。

---

## 3) 驗證（PR14 預計要做的 smoke / pytest）

### 3.1 import / smoke
- `uv run python -c "import app.services as s; print('ok', hasattr(s,'update_logger_config'))"`
- `uv run python -c "from app.services_impl import logging_service as ls; print('ok', ls.UI_LOG_HANDLER is not None)"`

### 3.2 pytest（最低集合）
- `uv run pytest -q tests/test_main_imports.py tests/test_cache_view_features.py`

> 若 PR14 動到 services.py import 結構，建議再加：
- `uv run pytest -q tests/test_ui_refactor_guard.py`

---

## 4) 風險與 rollback

### 4.1 主要風險
- **handler 變成重複掛載或沒掛載**：`update_logger_config()` 必須維持「檢查 root_logger.handlers」的 idempotent 行為。
- **UI_LOG_HANDLER 的 session 綁定遺漏**：service 入口若沒正確 set_session/unset_session，UI 可能收不到 logger 轉送。
- **singleton 路徑不一致**：`GLOBAL_LOG_LIMITER` 與 `UI_LOG_HANDLER` 都是 module-level 單例；PR14 必須確保 `app.services` 與 `services_impl.logging_service` 指向的是同一個物件，而不是複製或重建。
- **circular import**：`services_impl/logging_service.py` 不應 import views 或 pipelines；只應依賴 `translation_tool.utils.*` 與標準庫。

補充盤點：目前 repo 內 `UISessionLogHandler()` 的實例化位置只有 `app/services.py` 一處（另有 `translation_tool/utils/ui_logging_handler.py` 類別定義），未發現第二個 handler 實例來源；PR14 實作時仍需維持這個單例前提。

### 4.2 rollback 策略
- 若出現回歸：回退 PR14（或把 import 改回 services.py 內部定義）即可，因為對外 API 不變。

---

## 5) 本輪產出文件
- `docs/pr/2026-03-11_2123_PR_pr14-logging-service-split-design.md`

---

## 6) 本輪驗證（盤點/設計階段：可重現的確認方式）

> 目的：證明 PR14 設計盤點的依據可被他人重現（避免只靠口頭描述）。

- 盤點 services.py 內 logging 相關符號位置：
  - `rg -n "UI_LOG_HANDLER|GLOBAL_LOG_LIMITER|LogLimiter\\b|update_logger_config\\b|set_session\\(" app/services.py`

- 盤點 UI caller 對 `app.services` 的依賴：
  - `rg -n "from app\\.services import" -S app/views main.py`

- 盤點 handler 單例來源（確認沒有第二個 `UISessionLogHandler()` 實例）：
  - `rg -n "UISessionLogHandler\(" -S app translation_tool`

- import 骨架確認（PR13 產物）：
  - `uv run python -c "import app.services_impl as si; import app.services_impl.pipelines as p; import app.services_impl.cache as c; print('ok')"`

---

## 7) 需要家豪先決策的點
1. **PR14 是否允許新增 helper API（例如 bind/unbind/contextmanager）**
   - 建議：PR14 先不新增，避免 scope 膨脹；待 PR14 落地後再視需要做 PR15（或 PR14.1）。

2. **PR14 是否要順手統一各 service 入口的 finally 清理模式**
   - 建議：先不做（那會變成行為調整 + 大量 diff），留到後續重構 PR。
