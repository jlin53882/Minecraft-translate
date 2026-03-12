# PR29: cleanup non-QC re-exports in `app.services`

## Summary
本 PR 將 `app/services.py` 收斂為「只服務 QC/checkers 暫緩線」的最小 façade。
在 PR28a/PR28b 已完成 caller migration 的前提下，移除非 QC 所需的 legacy re-export（config/pipeline/cache/update_logger_config），保留 `qc_view.py` 仍在使用的 QC wrappers。

---

## Validation checklist
- [ ] `rg -n --glob "*.py" --glob "!backups/**" --glob "!.venv/**" --glob "!docs/**" --glob "!.agentlens/**" "^from app\.services import" .`
- [ ] `uv run python -c "from app.services import run_untranslated_check_service, run_variant_compare_service, run_variant_compare_tsv_service; print('ok')"`
- [ ] `uv run python -c "from app.views.qc_view import QCView; print('ok')"`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr29 -o cache_dir=.pytest-cache\pr29 tests/test_main_imports.py tests/test_ui_refactor_guard.py`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\full -o cache_dir=.pytest-cache\full`

---

## Phase 1 完成清單
- [x] 建立 rollback 備份：`backups/pr29-services-facade-cleanup-20260312-100210/app/services.py`
- [x] 完成 `app/services.py` 非 QC re-export 清理
- [x] 保留 QC/checkers wrappers（`qc_view.py` 相容）
- [x] 補上 PR 文件（本檔）

---

## 刪除/移除/替換說明（若有，固定放這裡）
### 刪除項目：`app/services.py` 的非 QC re-export 與 `update_logger_config()` façade
- **為什麼改**：PR28 已把非 QC caller 全部改為直接 import `app.services_impl.*`，`app.services` 不再需要維持全域 active façade。
- **為什麼能刪**：repo 掃描後，`from app.services import ...` 只剩 `app/views/qc_view.py`，且該檔只使用 QC wrappers。
- **目前誰在用 / 沒人在用**：
  - 仍在用：`qc_view.py`（`run_untranslated_check_service`、`run_variant_compare_service`、`run_variant_compare_tsv_service`）
  - 未使用：config/pipeline/cache 對外 re-export 與 `update_logger_config()` façade
- **替代路徑是什麼**：非 QC caller 已改用 `app.services_impl.config_service`、`app.services_impl.pipelines.*`、`app.services_impl.cache.cache_services`。
- **風險是什麼**：若 repo 外部腳本仍硬依賴 `from app.services import <非QC符號>`，會出現 ImportError。
- **我是怎麼驗證的**：
  - 用 `rg` 確認 repo 內只剩 `qc_view.py` 直接 import `app.services`
  - 執行 `uv run` import smoke（`app.services`、`QCView`）
  - 執行 guard tests + full pytest（40 passed）

---

## What was done

### 1) `app/services.py` 收斂為 QC façade
保留：
- `run_untranslated_check_service`
- `run_variant_compare_service`
- `run_english_residue_check_service`
- `run_variant_compare_tsv_service`

移除：
- config re-export：`load_config_json`、`save_config_json`、`load_replace_rules`、`save_replace_rules`
- pipeline re-export：LM / extract / FTB / KubeJS / MD / merge / lookup / bundling
- cache re-export：`cache_*_service` 全部
- `update_logger_config()` façade

### 2) 模組描述更新
將 `app/services.py` 頂部說明改為 PR29 後現況：
- 本檔只保留 QC/checkers 暫緩線入口
- 非 QC 流程請走 `app.services_impl.*`

### 3) 對外可見介面明確化
新增 `__all__`，只匯出 QC/checkers 相關 API，避免再被誤用成雜湊入口。

---

## Important findings
- PR29 完成後，repo 內唯一 `from app.services import ...` 使用點仍是：`app/views/qc_view.py`。
- `uv` 在本機需要指定 repo 內 cache/temp 路徑（`UV_CACHE_DIR=.uv-cache`、`TMP/.tmp`）以避開 Windows 使用者目錄權限問題。
- 全量測試通過（`40 passed`），顯示這次 façade 瘦身未造成現有測試回歸。

---

## Rejected approaches
- 試過：直接刪掉整個 `app/services.py`，讓 QC 也立刻改走 `app.services_impl`。
- 為什麼放棄：目前 QC/checkers 線仍屬暫緩區，`qc_view.py` 可能重寫或刪除；在策略未定前強行遷移會把 cleanup PR 變成行為調整 PR，風險過高。
- 最終改採：先做 non-QC re-export cleanup，保留 QC façade，讓 PR29 保持低風險、可驗證。

---

## Not included in this PR
- 沒有修改 `app/views/qc_view.py` 的行為
- 沒有遷移 QC/checkers 線到 `app.services_impl`
- 沒有刪除 `app/services.py` 檔案本體

---

## Next step
- 進行 QC/checkers 線決策：
  - A) 轉移 `qc_view.py` 到 `app.services_impl`（若要延續功能）
  - B) 移除/重寫 `qc_view.py`（若該功能將下線）
- 決策完成後，再開下一顆 PR 處理 `app.services` 的最終去留。

---

## Test result
```text
> rg -n --glob "*.py" --glob "!backups/**" --glob "!.venv/**" --glob "!docs/**" --glob "!.agentlens/**" "^from app\.services import" .
.\app\views\qc_view.py:12:from app.services import (

> uv run python -c "from app.services import run_untranslated_check_service, run_variant_compare_service, run_variant_compare_tsv_service; print('ok')"
ok

> uv run python -c "from app.views.qc_view import QCView; print('ok')"
ok

> uv run pytest -q --basetemp=.pytest-tmp\pr29 -o cache_dir=.pytest-cache\pr29 tests/test_main_imports.py tests/test_ui_refactor_guard.py
.......                                                                  [100%]
7 passed in 0.02s

> uv run pytest -q --basetemp=.pytest-tmp\full -o cache_dir=.pytest-cache\full
........................................                                 [100%]
40 passed in 0.99s
```
