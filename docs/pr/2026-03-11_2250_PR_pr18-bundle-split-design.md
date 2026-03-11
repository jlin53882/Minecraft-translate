# PR18（設計）— 抽離 bundle pipeline service 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：
> - PR13：`services_impl/` 骨架
> - PR14：`services_impl/logging_service.py`
> - PR15：`services_impl/config_service.py`
> - PR16：`services_impl/cache/cache_services.py`
> - PR17：`services_impl/pipelines/lookup_service.py`
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR18 建議接著把 `app/services.py` 內低風險的 `run_bundling_service()` 抽離到 `app/services_impl/pipelines/bundle_service.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/bundler_view.py` 不必改 import，並延續 PR17 的拆分模式。

---

## 1) Phase 0 盤點：bundle 相關 caller / import / guard test / 依賴點

> 依規範：凡涉及 package / module / import 結構變更，Phase 1 前必須先做 Phase 0 盤點。

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_bundling_service(input_root_dir: str, output_zip_path: str)`
- 目前實作型態：generator wrapper
- 現行職責：
  - 呼叫 `translation_tool.core.output_bundler.bundle_outputs_generator(...)`
  - 經過 `GLOBAL_LOG_LIMITER.filter(...)` 做 log/progress 節流
  - 例外時轉成 UI 可消化的 `{"log", "error", "progress"}` 字典

### 1.2 現行 UI caller
- `app/views/bundler_view.py`
  - 第 5 行：`from app.services import run_bundling_service, load_config_json`
  - 第 138 行：`for update in run_bundling_service(root_dir, output_zip):`

### 1.3 核心依賴
- `translation_tool.core.output_bundler.bundle_outputs_generator`
  - 真正執行 ZIP 打包
  - 內部依 `load_config().get("output_bundler", {})` 讀取 source folders map
- `app.services_impl.logging_service.GLOBAL_LOG_LIMITER`
  - service 層節流責任仍要保留
- `traceback.format_exc()` + module logger
  - 發生非預期錯誤時，仍需維持目前對 UI 的錯誤回報格式

### 1.4 guard test / 相關測試盤點
- `tests/test_main_imports.py`
  - 只檢查 `main.py` 不應 import 被停用 views
  - 雖然裡面列到 `app.views.bundler_view`，但它不是測 bundler service 的 import path；本次搬移 bundle service 不會直接衝突
- `tests/test_ui_refactor_guard.py`
  - 目前沒有 bundler service / bundler view 專屬 guard
- 目前 repo 內**沒有看到**直接驗 bundler service import path 的專屬測試

### 1.5 硬編碼路徑 / UI 風險盤點
- `bundler_view.py` 仍用 tkinter file dialog 做選路徑
- `bundler_view.py` 同時依賴：
  - `run_bundling_service`
  - `load_config_json`
- 本 PR18 若只搬 `run_bundling_service`，不應順手碰 `load_config_json`、tkinter、UI 控制 disable/enable 邏輯
- `bundling_worker()` 對 update payload 的假設很明確：
  - `log` 可 split lines 顯示
  - `progress` 可直接餵給 `ProgressBar`
  - `error` 會決定 progress bar 顏色
  所以 generator wrapper 的欄位格式必須維持不變

### 1.6 Phase 0 實際使用的盤點指令
- `rg -n "run_bundling_service|bundle_outputs_generator|from app\.services import .*run_bundling_service|import app\.services" app tests main.py`
- `rg -n "bundler_view|test_main_imports|test_ui_refactor_guard|run_bundling_service" tests app main.py`

### 1.7 結論
bundle 是目前剩餘 pipeline 裡面相對低風險的一顆：
- 沒有 `TaskSession` / `UI_LOG_HANDLER` 綁定
- 沒有 lookup 以外的複雜 session 生命週期
- 沒有 merge 那種 progress 疊加
- 沒有 LM / FTB / KubeJS / MD / Extract 那種長流程與多 step flags

因此 PR18 很適合接在 PR17 後面，延續同樣的拆法。

---

## 2) PR18 要拆哪一組？

### 結論：先拆 Bundle

### 為什麼現在拆 bundle 最合適
- 與 PR17 lookup 一樣，屬於單純 wrapper 類型
- 主要責任集中在：
  - 呼叫 core generator
  - 節流
  - 例外轉換
- UI caller 單純，只有 `bundler_view.py`
- 拆完之後能繼續驗證「`services.py` 逐步收斂成 façade」這條主線沒有走歪

### 這輪不該一起做的事
- 不要把 `bundler_view.py` 一起重構
- 不要順便動 `translation_tool/core/output_bundler.py`
- 不要順便修改 config schema / `output_bundler.source_folders`
- 不要順便把 checker / merge 一起塞進同一顆 PR

---

## 3) PR18 設計（總則）

### 3.1 目標
- 新增 `app/services_impl/pipelines/bundle_service.py`
- 將 `run_bundling_service()` 自 `app/services.py` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `app/views/bundler_view.py` 不改 import，不改 UI 行為

### 3.2 Scope
- 新增：`app/services_impl/pipelines/bundle_service.py`
  - 搬入 `run_bundling_service(input_root_dir, output_zip_path)`
- 修改：`app/services.py`
  - 改成 `from app.services_impl.pipelines.bundle_service import run_bundling_service`
  - 移除不再需要的 bundle 相關 inline 實作
- 文件：
  - 新增本 PR 的實作/驗證文件（沿用 `docs/pr/YYYY-MM-DD_HHmm_PR_<topic>.md`）

### 3.3 Out-of-scope
- 不改 `translation_tool/core/output_bundler.py`
- 不改 `app/views/bundler_view.py`
- 不改 `load_config_json` 的 export 位置
- 不改 `GLOBAL_LOG_LIMITER` 行為
- 不新增 UI 互動功能或 bundle progress 顯示邏輯

---

## 4) 預計搬移的具體內容

### 4.1 要搬的函式
- `app/services.py::run_bundling_service(input_root_dir: str, output_zip_path: str)`

### 4.2 搬移後預期結構

```python
# app/services_impl/pipelines/bundle_service.py
from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.core.output_bundler import bundle_outputs_generator
...

def run_bundling_service(input_root_dir: str, output_zip_path: str):
    ...
```

```python
# app/services.py
from app.services_impl.pipelines.bundle_service import run_bundling_service
```

### 4.3 要保留不變的 contract
- `run_bundling_service(...)` 仍回傳 generator
- `yield` 結構維持：
  - `log`
  - `progress`
  - `error`
- 發生例外時仍回傳：
  - `{"log": "[致命錯誤] ...", "error": True, "progress": 0}`
- `bundler_view.py` 仍透過 `from app.services import run_bundling_service` 使用，不直接 import impl 模組

---

## 5) 驗證設計

### 5.1 最小驗證目標
確認三件事：
1. 新 module import 正常
2. façade import 正常
3. bundler view 的既有 import 不會因 service 抽離而炸掉

### 5.2 Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import bundle_service"`
- [ ] `uv run python -c "from app.services import run_bundling_service"`
- [ ] `uv run pytest -q tests/test_main_imports.py`
- [ ] `uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"`

### 5.3 若要加強驗證，可選但非必做
- [ ] 寫一個最小 smoke test，monkeypatch `bundle_outputs_generator` 後驗證 `run_bundling_service()` 仍會透過 `GLOBAL_LOG_LIMITER.filter(...)` 產生相同欄位

> 這條是加分項，不是 PR18 必做；若要加，建議獨立成小測試，不要在本顆 PR 同時動太多。

---

## 6) 風險、回歸點與 rollback

### 6.1 主要風險
1. `app/services.py` 忘了 re-export
   - 後果：`bundler_view.py` import fail
2. 搬移時漏帶 `traceback` / `logger` / `GLOBAL_LOG_LIMITER`
   - 後果：錯誤處理格式變掉，UI 顯示異常
3. wrapper 欄位格式不一致
   - 後果：`bundler_view.py` 的 progress bar / log 顯示出問題

### 6.2 rollback 策略
- 若 PR18 出問題，可單獨回退：
  - `app/services.py`
  - `app/services_impl/pipelines/bundle_service.py`
  - 本次 PR 文件
- 因 `bundler_view.py` 不改，回退成本相對低

---

## 7) 預期交付物
- `app/services_impl/pipelines/bundle_service.py`
- `app/services.py`（bundle 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr18-bundle-split.md`（實作完成版）

---

## 8) 本輪新增文件
- `docs/pr/2026-03-11_2250_PR_pr18-bundle-split-design.md`

---

## 9) 後續建議順序
- PR18：bundle
- PR19：checkers
- PR20 之後再考慮 merge / extract
- LM / FTB / KubeJS / MD 仍建議最後處理

理由很簡單：先把低風險 wrapper 類別拆乾淨，再去碰有 session 綁定與長流程的高風險組，才不會自己給自己找麻煩。
