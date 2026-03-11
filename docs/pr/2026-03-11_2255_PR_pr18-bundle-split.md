# PR Title
refactor: split bundle pipeline service into app.services_impl.pipelines.bundle_service

# PR Description

## Summary
PR18 將 `app/services.py` 內的 `run_bundling_service()` 抽離到 `app/services_impl/pipelines/bundle_service.py`，讓 `services.py` 持續只做 façade / re-export，維持 `bundler_view.py` 既有 import 不變。
本次只處理 bundle 這一組低風險 generator wrapper，不碰 `bundler_view.py`、`translation_tool/core/output_bundler.py`、config schema 與其他 pipeline。

---

## Phase 1 完成清單
- [x] 做了：沿用已批准的 PR18 設計稿與 `Validation checklist`
- [x] 做了：依 Phase 0 盤點結果確認 bundle caller、guard test、import 依賴與 UI payload 假設
- [x] 做了：建立 `app/services.py` 可回退備份
- [x] 做了：新增 `app/services_impl/pipelines/bundle_service.py`
- [x] 做了：將 `run_bundling_service()` 自 `app/services.py` 抽離到新模組
- [x] 做了：`app/services.py` 改為 re-export 新模組中的 `run_bundling_service`
- [x] 做了：移除 `app/services.py` 中不再需要的 `bundle_outputs_generator` import
- [x] 做了：完成 Validation checklist 內的 import smoke / pytest / bundler view import smoke
- [ ] 未做：其他 pipeline（checkers / merge / extract / LM / FTB / KubeJS / MD）
- [ ] 超出範圍：修改 `bundler_view.py` UI 行為、`output_bundler.py` 核心邏輯、bundle 設定結構

---

## 刪除/移除/替換說明（若有，固定放這裡）

### 刪除項目：`app/services.py` 內 inline `run_bundling_service()` 實作
- **為什麼改**：`app/services.py` 目前仍承載多組 pipeline wrappers；PR17 已先拆 lookup，PR18 延續同樣策略，將低風險的 bundle wrapper 抽離到 `services_impl/pipelines/`，讓 `services.py` 逐步收斂成 façade 層。
- **為什麼能刪**：原本的 generator wrapper 已原樣搬到 `app/services_impl/pipelines/bundle_service.py`，且 `app/services.py` 仍保留同名 re-export，因此外部呼叫點不需改動。
- **目前誰在用 / 沒人在用**：實際 caller 為 `app/views/bundler_view.py` 第 5 行 import，以及第 138 行 `for update in run_bundling_service(root_dir, output_zip):`。Phase 0 搜尋指令：`rg -n "run_bundling_service|bundle_outputs_generator|from app\.services import .*run_bundling_service|import app\.services" app tests main.py`。未發現其他直接 caller。
- **替代路徑是什麼**：實作移至 `app/services_impl/pipelines/bundle_service.py::run_bundling_service`；對外入口仍由 `app/services.py` re-export。
- **風險是什麼**：若 `app/services.py` 忘記 re-export，`bundler_view.py` 會 import fail；若搬移時漏掉 `GLOBAL_LOG_LIMITER`、`traceback` 或 logger，UI 的 log/progress/error 行為會變掉；若 yield 欄位格式改壞，progress bar 與 log 顯示可能異常。
- **我是怎麼驗證的**：執行 `uv run python -c "from app.services_impl.pipelines import bundle_service"`、`uv run python -c "from app.services import run_bundling_service"`、`uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"`，並確認 `uv run pytest -q tests/test_main_imports.py` 通過。

### 刪除項目：`app/services.py` 內 `bundle_outputs_generator` 直接 import
- **為什麼改**：bundle 的實際 wrapper 已移至 `app/services_impl/pipelines/bundle_service.py`，`app/services.py` 不再直接使用 `bundle_outputs_generator`。
- **為什麼能刪**：直接使用該 import 的程式碼已從 `app/services.py` 移除，新的唯一使用點在 `app/services_impl/pipelines/bundle_service.py`。
- **目前誰在用 / 沒人在用**：`app/services.py` 已無使用；新的使用點為 `app/services_impl/pipelines/bundle_service.py`。Phase 0 與修改後均已以實際檔案檢查確認。
- **替代路徑是什麼**：不再由 façade 層直接依賴，改由 `app/services_impl/pipelines/bundle_service.py` 匯入並呼叫。
- **風險是什麼**：若新模組沒有正確匯入 `bundle_outputs_generator`，bundle service import 會失敗。
- **我是怎麼驗證的**：執行 `uv run python -c "from app.services_impl.pipelines import bundle_service"` 與 `uv run python -c "from app.services import run_bundling_service"`，均成功。

---

## What was done

### 1. Phase 0 盤點結果被落實到這次實作範圍
本次屬於 package / module / import 結構調整，因此先依規範做 Phase 0 盤點，再開始 Phase 1。

盤點結論實際反映到實作策略如下：
- `bundler_view.py` 是主要 caller，因此採「只搬 service 實作、不改 caller」
- `bundler_view.py` 對 update payload 的假設不能變，所以保留原本 generator wrapper 的 `log` / `progress` / `error` 結構
- `tests/test_main_imports.py` 不直接測 bundle service，但可當作本次 import path 回歸的最小 smoke test
- `translation_tool/core/output_bundler.py` 屬核心打包邏輯，本次不碰，避免把 wrapper 抽離與核心邏輯調整混在一起

### 2. 新增 `app/services_impl/pipelines/bundle_service.py`
新增 bundle 專用 pipeline service 模組，將原本 `app/services.py` 中的 wrapper 原樣搬入。

保留不變的點：
- `run_bundling_service(input_root_dir, output_zip_path)` 仍回傳 generator
- 仍透過 `bundle_outputs_generator(...)` 執行核心打包流程
- 仍透過 `GLOBAL_LOG_LIMITER.filter(...)` 做節流
- 發生例外時仍回傳：
  - `{"log": "[致命錯誤] ...", "error": True, "progress": 0}`

### 3. `app/services.py` 收斂為 façade / re-export
`app/services.py` 的改動只有 façade 層該有的內容：
- 移除 inline `run_bundling_service()` 實作
- 移除不再需要的 `bundle_outputs_generator` import
- 新增：`from app.services_impl.pipelines.bundle_service import run_bundling_service`

這樣做的效果：
- `app/views/bundler_view.py` 不需要修改 import
- UI 行為維持不變
- `services.py` 繼續往薄 façade 方向收斂
- 後續 PR19 若繼續拆 `checkers`，可直接沿用這個模式

### 4. 備份與回退準備
為了符合修改前先可回退的要求，本次先建立：
- `app/services.py.bak.20260311_pr18`

若 PR18 後續要回退，可直接拿此備份還原 façade 層。

---

## Important findings
- bundle 確實很適合接在 lookup 後面拆：沒有 `TaskSession` / `UI_LOG_HANDLER` 綁定，風險比 merge / extract / LM / FTB / KubeJS / MD 都低。
- `bundler_view.py` 目前只吃 `app.services` 的對外入口，沒有直接依賴 impl 路徑，代表 façade / re-export 這條策略目前還是健康的。
- 本次不需要新增 guard test 才能安全落地；先用 import smoke + 既有 `test_main_imports.py` 已足夠驗證這顆小 PR 的 import 面。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有修改 `app/views/bundler_view.py`
- 沒有修改 `translation_tool/core/output_bundler.py`
- 沒有新增 bundle 專屬測試
- 沒有調整 bundle config 的 schema 或 `source_folders` 內容
- 沒有搬動 `checkers`、`merge`、`extract`、`LM`、`FTB`、`KubeJS`、`MD`

---

## Next step

### PR19
- 優先拆 `checkers` 系列 wrappers
- 建議仍維持「一組一顆 PR」的節奏，不要把 merge 或 extract 一起混進來
- merge / extract 之後再處理，因為 progress 與長任務風險更高

---

## Validation checklist
- [x] `uv run python -c "from app.services_impl.pipelines import bundle_service"`
- [x] `uv run python -c "from app.services import run_bundling_service"`
- [x] `uv run pytest -q tests/test_main_imports.py`
- [x] `uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"`

---

## Test result
```text
$ uv run python -c "from app.services_impl.pipelines import bundle_service"
(no output)

$ uv run python -c "from app.services import run_bundling_service"
(no output)

$ uv run pytest -q tests/test_main_imports.py
.                                                                        [100%]
1 passed in 0.01s

$ uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"
ok
```
