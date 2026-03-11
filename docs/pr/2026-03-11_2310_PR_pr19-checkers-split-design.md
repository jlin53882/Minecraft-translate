# PR19（設計）— 抽離 checkers pipeline services 到 `services_impl/pipelines`

> 專案：`C:\Users\admin\Desktop\minecraft_translator_flet`
> 前置：
> - PR13：`services_impl/` 骨架
> - PR14：`services_impl/logging_service.py`
> - PR15：`services_impl/config_service.py`
> - PR16：`services_impl/cache/cache_services.py`
> - PR17：`services_impl/pipelines/lookup_service.py`
> - PR18：`services_impl/pipelines/bundle_service.py`
> 本輪狀態：已盤點 / 已設計，**不改 code**。

---

## 一句話總結

PR19 建議接著把 `app/services.py` 內的 checkers 類 generator wrappers 抽離到 `app/services_impl/pipelines/checker_services.py`，維持 `app/services.py` 作 façade / re-export，讓 `app/views/qc_view.py` 不必改 import，同時把目前沒有 UI caller 的 `run_english_residue_check_service()` 一起收編進同一顆 PR，避免 checker 類邏輯繼續散在 façade 層。

---

## 1) Phase 0 盤點：checkers 相關 caller / import / guard test / 依賴點

> 依規範：凡涉及 package / module / import 結構變更，Phase 1 前必須先做 Phase 0 盤點。

### 1.1 現行 service 實作位置
- `app/services.py`
  - `run_untranslated_check_service(en_dir: str, tw_dir: str, out_dir: str)`
  - `run_variant_compare_service(cn_dir: str, tw_dir: str, out_dir: str)`
  - `run_english_residue_check_service(input_dir: str, out_dir: str)`
  - `run_variant_compare_tsv_service(tsv_path: str, output_csv_path: str)`

### 1.2 現行職責（共通）
這四顆目前都是同一種模式：
- 呼叫 `translation_tool.checkers.*` 的 core generator
- 經過 `GLOBAL_LOG_LIMITER.filter(...)` 做 log/progress 節流
- 發生例外時轉成 UI 可消化的 `{"log", "error", "progress"}` 字典

### 1.3 現行 UI caller
- `app/views/qc_view.py`
  - import：
    - `run_untranslated_check_service`
    - `run_variant_compare_service`
    - `run_variant_compare_tsv_service`
  - 實際使用位置：
    - `run_untranslated_check_service` → `start_task('untranslated')`
    - `run_variant_compare_service` → `start_task('compare_json')`
    - `run_variant_compare_tsv_service` → `start_task('compare_tsv')`
- **注意：** `run_english_residue_check_service` 目前沒有在 `qc_view.py`、`app/views`、`tests`、`main.py` 看到顯性 caller

### 1.4 核心依賴
- `translation_tool.checkers.untranslated_checker.check_untranslated_generator`
- `translation_tool.checkers.variant_comparator.compare_variants_generator`
- `translation_tool.checkers.english_residue_checker.check_english_residue_generator`
- `translation_tool.checkers.variant_comparator_tsv.compare_variants_tsv_generator`
- `app.services_impl.logging_service.GLOBAL_LOG_LIMITER`
- `traceback.format_exc()` + module logger

### 1.5 guard test / 測試盤點
- `tests/test_main_imports.py`
  - 只檢查 `main.py` 不應 import 被停用 views
  - 裡面列到 `app.views.qc_view`，但不是在驗 checker service 的 import path；本次抽離 service 不會直接衝突
- `tests/test_ui_refactor_guard.py`
  - 目前沒有 checker service / qc_view 專屬 guard
- repo 內目前**沒有看到**直接驗 `run_*check*` / `run_variant_compare*` façade import path 的專屬測試

### 1.6 硬編碼路徑 / UI 風險盤點
- `qc_view.py` 仍用 tkinter file dialog 做資料夾 / 檔案選擇
- `qc_view.py` 的 `task_worker()` 對 update payload 的假設很明確：
  - `log` 會被拆行顯示
  - `progress` 直接餵給 `ProgressBar`
  - `error` 會決定 bar 顏色
- 所以這次只能搬 wrapper 位置，不能動 yield 欄位格式
- `qc_view.py` 目前沒有接 `run_english_residue_check_service`；若 PR19 只做抽離，不應順手把該功能接進 UI，否則 scope 會膨脹

### 1.7 Phase 0 實際使用的盤點指令
- `rg -n "run_untranslated_check_service|run_variant_compare_service|run_english_residue_check_service|run_variant_compare_tsv_service|from app\.services import .*run_.*check|from app\.services import .*variant|import app\.services" app tests main.py`
- `rg -n "qc_view|test_main_imports|test_ui_refactor_guard|run_untranslated_check_service|run_variant_compare_service|run_english_residue_check_service|run_variant_compare_tsv_service" tests app main.py`
- `rg -n "run_english_residue_check_service|check_english_residue_generator|english_residue" app tests main.py`

### 1.8 結論
PR19 適合把 checker wrappers 作為「同一組」一起拆掉，理由：
- 四顆函式模式一致，都是 generator wrapper
- 都屬於 `translation_tool.checkers.*` 這個 concern
- 風險仍低於 merge / extract / LM / FTB / KubeJS / MD
- 即使 `run_english_residue_check_service` 目前沒有 UI caller，仍屬 checker family；如果不一起收，之後還得補一顆零碎 PR

---

## 2) PR19 要拆哪一組？

### 結論：拆 Checkers（一組四顆）

建議 PR19 一次處理：
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`

### 為什麼這輪可以一起拆
- 它們的 wrapper 結構高度一致
- 共用同一組節流 / 例外轉換模式
- caller 面仍然相對集中（`qc_view.py` 為主）
- 比拆 merge / extract 這種帶更多狀態與流程的東西安全很多

### 這輪不該一起做的事
- 不要把 `qc_view.py` 一起改版或補 UI
- 不要順手把 `english residue` 功能接進 `qc_view.py`
- 不要修改 `translation_tool/checkers/*` 核心邏輯
- 不要順手清 `services.py` 其他殘留 import，避免把 cleanup 跟 pipeline split 混在一起

---

## 3) PR19 設計（總則）

### 3.1 目標
- 新增 `app/services_impl/pipelines/checker_services.py`
- 將四顆 checker wrappers 自 `app/services.py` 抽離到新模組
- `app/services.py` 保持 façade / re-export
- `app/views/qc_view.py` 不改 import，不改 UI 行為

### 3.2 Scope
- 新增：`app/services_impl/pipelines/checker_services.py`
  - 搬入四顆 checker wrappers
- 修改：`app/services.py`
  - 改成由 `app.services_impl.pipelines.checker_services` re-export 這四顆函式
  - 移除不再需要的 inline 實作
  - **只移除本次搬走後確定無用的 checker generator import**
- 文件：
  - 新增本 PR 的實作/驗證文件（沿用 `docs/pr/YYYY-MM-DD_HHmm_PR_<topic>.md`）

### 3.3 Out-of-scope
- 不改 `app/views/qc_view.py`
- 不改 `translation_tool/checkers/untranslated_checker.py`
- 不改 `translation_tool/checkers/variant_comparator.py`
- 不改 `translation_tool/checkers/english_residue_checker.py`
- 不改 `translation_tool/checkers/variant_comparator_tsv.py`
- 不新增 `english residue` 的 UI 入口
- 不清理無關的 `services.py` 殘留 import

---

## 4) 預計搬移的具體內容

### 4.1 要搬的函式
- `app/services.py::run_untranslated_check_service(...)`
- `app/services.py::run_variant_compare_service(...)`
- `app/services.py::run_english_residue_check_service(...)`
- `app/services.py::run_variant_compare_tsv_service(...)`

### 4.2 搬移後預期結構

```python
# app/services_impl/pipelines/checker_services.py
from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.checkers.untranslated_checker import check_untranslated_generator
from translation_tool.checkers.variant_comparator import compare_variants_generator
from translation_tool.checkers.english_residue_checker import check_english_residue_generator
from translation_tool.checkers.variant_comparator_tsv import compare_variants_tsv_generator
...
```

```python
# app/services.py
from app.services_impl.pipelines.checker_services import (
    run_untranslated_check_service,
    run_variant_compare_service,
    run_english_residue_check_service,
    run_variant_compare_tsv_service,
)
```

### 4.3 要保留不變的 contract
- 四顆函式都仍回傳 generator
- `yield` 結構維持：
  - `log`
  - `progress`
  - `error`
- 發生例外時仍回傳：
  - `{"log": "[致命錯誤] ...", "error": True, "progress": 0}`
- `qc_view.py` 仍透過 `from app.services import ...` 使用，不直接 import impl 模組

---

## 5) 驗證設計

### 5.1 最小驗證目標
確認三件事：
1. 新 module import 正常
2. façade import 正常
3. `qc_view.py` 的既有 import 不會因 service 抽離而炸掉

### 5.2 Validation checklist
- [ ] `uv run python -c "from app.services_impl.pipelines import checker_services"`
- [ ] `uv run python -c "from app.services import run_untranslated_check_service, run_variant_compare_service, run_english_residue_check_service, run_variant_compare_tsv_service"`
- [ ] `uv run pytest -q tests/test_main_imports.py`
- [ ] `uv run python -c "from app.views.qc_view import QCView; print('ok')"`

### 5.3 可選加強驗證
- [ ] 補一個最小 smoke test，monkeypatch 某顆 checker generator，驗證 wrapper 仍保留相同欄位與錯誤格式

> 這是加分項，不是 PR19 必做。PR19 先以 import path 與 view smoke 穩定落地為主。

---

## 6) 風險、回歸點與 rollback

### 6.1 主要風險
1. `app/services.py` 忘了 re-export
   - 後果：`qc_view.py` import fail
2. 搬移時漏掉某顆 checker generator import
   - 後果：對應 service import fail 或 runtime fail
3. wrapper 欄位格式不一致
   - 後果：`qc_view.py` 的 progress / log 顯示異常
4. 把沒有 UI caller 的 `run_english_residue_check_service` 遺漏
   - 後果：checker family 仍殘留半套在 façade 層，之後還要補 cleanup

### 6.2 rollback 策略
- 若 PR19 出問題，可單獨回退：
  - `app/services.py`
  - `app/services_impl/pipelines/checker_services.py`
  - 本次 PR 文件
- 因 `qc_view.py` 不改，回退成本相對低

---

## 7) 預期交付物
- `app/services_impl/pipelines/checker_services.py`
- `app/services.py`（四顆 checker 改為 re-export）
- `docs/pr/YYYY-MM-DD_HHmm_PR_pr19-checkers-split.md`（實作完成版）

---

## 8) 本輪新增文件
- `docs/pr/2026-03-11_2310_PR_pr19-checkers-split-design.md`

---

## 9) 後續建議順序
- PR19：checkers
- PR20：merge 或 extract（二選一，不要一起）
- LM / FTB / KubeJS / MD 仍建議最後處理

我比較傾向 PR20 先看 merge 還是 extract 要挑哪個，取決於你想先拆「progress 疊加敏感」還是「長任務/session 綁定」。兩個都比 checkers 難搞不少。