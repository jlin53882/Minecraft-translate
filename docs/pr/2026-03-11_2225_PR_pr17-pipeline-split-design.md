# PR17 Title
refactor: split lookup pipeline services into app.services_impl.pipelines.lookup_service

# PR Description

## Summary
PR17 將 `app/services.py` 內的 lookup 類 service wrappers 抽離到 `app/services_impl/pipelines/lookup_service.py`，讓 `services.py` 繼續只做 façade / re-export，維持 `lookup_view.py` 既有 import 不變。
本次只處理低風險的 lookup 組，不碰 LM / FTB / KubeJS / MD / Extract / Merge / Bundle / QC 等其他 pipeline。

---

## Phase 1 完成清單
- [x] 做了：補上本 PR 設計稿所需的 `## Validation checklist`
- [x] 做了：完成 Phase 0 盤點，確認 lookup caller、guard test、相關 import 依賴點
- [x] 做了：新增 `app/services_impl/pipelines/lookup_service.py`
- [x] 做了：將 `run_manual_lookup_service()` 自 `app/services.py` 抽離到新模組
- [x] 做了：將 `run_batch_lookup_service()` 自 `app/services.py` 抽離到新模組
- [x] 做了：`app/services.py` 改為 re-export 新模組中的 lookup services，維持 `lookup_view.py` 相容
- [x] 做了：建立可回退備份
- [x] 做了：完成 Validation checklist 內的 import smoke / pytest / view import smoke
- [ ] 未做：其他 pipeline（bundle / checkers / merge / extract / LM / FTB / KubeJS / MD）
- [ ] 超出範圍：調整 lookup 查詢邏輯、UI 行為、節流策略、錯誤訊息內容

---

## 刪除/移除/替換說明（若有，固定放這裡）

### 刪除項目：`app/services.py` 內 inline `run_manual_lookup_service()` 實作
- **為什麼改**：`app/services.py` 在 PR13~PR16 之後仍殘留多組 pipeline wrappers；PR17 的目標是開始依類型逐顆抽離，讓 `services.py` 收斂成 façade，降低後續維護成本。
- **為什麼能刪**：此函式邏輯已原樣搬到 `app/services_impl/pipelines/lookup_service.py`，且 `app/services.py` 仍保留同名 re-export，外部呼叫點不需改動。
- **目前誰在用 / 沒人在用**：實際 caller 為 `app/views/lookup_view.py` 第 7 行 import、以及第 72 行呼叫；沒有發現其他實際 caller。Phase 0 搜尋指令：`rg -n "run_(manual|batch)_lookup_service|from app\.services import .*run_(manual|batch)_lookup_service|import app\.services" app tests main.py`
- **替代路徑是什麼**：實作移至 `app/services_impl/pipelines/lookup_service.py::run_manual_lookup_service`；對外入口仍由 `app/services.py` re-export。
- **風險是什麼**：若 re-export 路徑寫錯，`lookup_view.py` 會在 import 階段失敗；若搬移過程改壞邏輯，單筆學名查詢結果可能異常。
- **我是怎麼驗證的**：執行 `uv run python -c "from app.services import run_manual_lookup_service"` 與 `uv run python -c "from app.views.lookup_view import LookupView; print('ok')"`，均通過。

### 刪除項目：`app/services.py` 內 inline `run_batch_lookup_service()` 實作
- **為什麼改**：與單筆 lookup 相同，將 lookup 類 wrapper 收斂到 `services_impl/pipelines/`，讓 `services.py` 保持薄 façade。
- **為什麼能刪**：此函式邏輯已原樣搬到 `app/services_impl/pipelines/lookup_service.py`，並由 `app/services.py` 持續 re-export；UI caller 不需改 import。
- **目前誰在用 / 沒人在用**：實際 caller 為 `app/views/lookup_view.py` 第 7 行 import、以及第 103 行 iterate generator；沒有發現其他實際 caller。Phase 0 搜尋同上。
- **替代路徑是什麼**：實作移至 `app/services_impl/pipelines/lookup_service.py::run_batch_lookup_service`；對外入口仍由 `app/services.py` re-export。
- **風險是什麼**：若 generator 輸出結構被改壞，批次查詢 UI 可能無法正確顯示進度、結果或錯誤；若 import 路徑錯誤，`lookup_view.py` 會直接 import fail。
- **我是怎麼驗證的**：執行 `uv run python -c "from app.services_impl.pipelines import lookup_service"`、`uv run python -c "from app.views.lookup_view import LookupView; print('ok')"`，並確認 `uv run pytest -q tests/test_main_imports.py` 通過。

---

## What was done

### 1. Phase 0 盤點：先掃 caller / import / guard test
本次屬於 package / module / import 結構調整，先依規範完成盤點，再進入 Phase 1。

盤點結果：
- lookup service 的現行 caller 只有 `app/views/lookup_view.py`
- `tests/test_main_imports.py` 的 guard 只檢查 `main.py` 不應 import 被停用 views，與本次 lookup service 模組抽離沒有直接衝突
- repo 內沒有其他地方直接依賴 `app.services_impl.pipelines.lookup_service`
- `app/services.py` 仍是 lookup UI 的唯一穩定 façade 入口，因此適合採「搬實作、不改 caller」策略

實際使用的 Phase 0 搜尋：
- `rg -n "run_(manual|batch)_lookup_service|from app\.services import .*run_(manual|batch)_lookup_service|import app\.services" app tests main.py`
- `rg -n "lookup_service|services_impl/pipelines|app\.services_impl\.pipelines" app tests main.py`
- `rg -n "lookup_view|qc_view|bundler_view|icon_preview_view|test_main_imports" tests app main.py`

### 2. 新增 pipeline 模組：`app/services_impl/pipelines/lookup_service.py`
新增 lookup 專用模組，內容保留原本 lookup service 的行為：
- `run_manual_lookup_service(name: str) -> str`
- `run_batch_lookup_service(json_text: str)`

這次沒有改：
- `species_cache` 的查詢策略
- 回傳字串內容
- batch generator 的 `log` / `progress` / `result` / `error` 結構
- `GLOBAL_LOG_LIMITER` 的使用方式

### 3. `app/services.py` 改為 façade / re-export
將原本 inline 的 lookup 實作從 `app/services.py` 移除，改為：
- 刪掉 lookup 相關內部實作
- 移除不再需要的 `json` 與 `species_cache` import
- 新增：
  - `from app.services_impl.pipelines.lookup_service import run_manual_lookup_service, run_batch_lookup_service`

這樣做的效果是：
- `lookup_view.py` 仍可繼續 `from app.services import ...`
- 本 PR 不需要同步修改 UI caller
- 後續 PR18 / PR19 若要繼續搬其他 pipeline，可沿用同樣模式

### 4. 備份與 rollback 準備
為了符合可回退要求，先建立下列備份：
- `app/services.py.bak.20260311_pr17`
- `docs/pr/2026-03-11_2225_PR_pr17-pipeline-split-design.md.bak.20260311_pr17`

若 PR17 需要回退，可直接用備份快速還原 `services.py` 與 PR 文件。

---

## Important findings
- 這次最穩的切法確實是 lookup：邊界清楚、依賴少、沒有 TaskSession / `UI_LOG_HANDLER` 綁定。
- `lookup_view.py` 目前只依賴 `app.services` 對外入口，沒有直接耦合到實作位置，因此 façade / re-export 模式適合繼續沿用。
- `tests/test_main_imports.py` 與本次調整不衝突；它只管 `main.py` 不要 import 停用 views，不會阻止 lookup service 模組搬移。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有修改 `lookup_view.py` 的 UI 行為或執行緒處理
- 沒有修改 `species_cache` / 網路查詢策略 / 快取邏輯
- 沒有調整 batch lookup 的 yield 欄位格式
- 沒有搬動 bundle / checkers / merge / extract / LM / FTB / KubeJS / MD 等其他 pipeline
- 沒有新增新的 guard test

---

## Next step

### PR18
- 優先考慮下一顆低風險 pipeline：`bundle` 或 `checkers`
- 若要先追求更穩定的節奏，建議先拆 `bundle`，再拆 `checkers`
- `merge` / `extract` / `LM` / `FTB` / `KubeJS` / `MD` 仍建議延後，因為 session 綁定、長任務與進度邏輯更敏感

---

## Validation checklist
- [x] `uv run python -c "from app.services_impl.pipelines import lookup_service"`
- [x] `uv run python -c "from app.services import run_manual_lookup_service"`
- [x] `uv run pytest -q tests/test_main_imports.py`
- [x] `uv run python -c "from app.views.lookup_view import LookupView; print('ok')"`

---

## Test result
```text
$ uv run python -c "from app.services_impl.pipelines import lookup_service"
(no output)

$ uv run python -c "from app.services import run_manual_lookup_service"
(no output)

$ uv run pytest -q tests/test_main_imports.py
.                                                                        [100%]
1 passed in 0.01s

$ uv run python -c "from app.views.lookup_view import LookupView; print('ok')"
ok
```
