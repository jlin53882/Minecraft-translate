# PR28a: Migrate low-risk callers off `app.services`

## Summary
PR28a 已完成低風險 caller migration，將 5 個 view 從 `app.services` re-export 改為直接 import canonical service modules。這一批只調整 import path，未修改 UI、事件流程或服務行為，`app/services.py` re-export 也依要求保留。

---

## Phase 1 完成清單
- [x] 建立 rollback backups：`backups/pr28ab-caller-migration-20260312-022156/...`
- [x] 完成 5 個低風險 caller 的 import migration
- [x] 完成 smoke import 驗證
- [x] 完成指定 pytest 驗證

---

## What was done

### 1. Low-risk caller migration
將下列 caller 改為直接 import canonical service modules：
- `app/views/config_view.py`
- `app/views/rules_view.py`
- `app/views/bundler_view.py`
- `app/views/lookup_view.py`
- `app/views/extractor_view.py`

### 2. Import path 調整
- `config_view.py`
  - `app.services` -> `app.services_impl.config_service`
- `rules_view.py`
  - `app.services` -> `app.services_impl.config_service`
- `bundler_view.py`
  - `run_bundling_service` -> `app.services_impl.pipelines.bundle_service`
  - `load_config_json` -> `app.services_impl.config_service`
- `lookup_view.py`
  - `app.services` -> `app.services_impl.pipelines.lookup_service`
- `extractor_view.py`
  - `app.services` -> `app.services_impl.pipelines.extract_service`

### 3. 行為保持不變
- 只改 import path
- 未修改 UI state、event handler、threading、service call 參數
- 未刪除 `app/services.py` 任何 re-export

---

## Important findings
- 預設 Windows 使用者層 `uv` cache / pytest temp 路徑先前有 `WinError 5` 權限問題；本次驗證改用 repo 內本地路徑：
  - `UV_CACHE_DIR=.uv-cache`
  - `TMP=.tmp`
  - `TEMP=.tmp`
  - `--basetemp=.pytest-tmp/...`
  - `-o cache_dir=.pytest-cache/...`
- 在上述條件下，PR28a checklist 可用 `uv run` 全數通過。
- 這批低風險 caller migration 沒有引入 import regression。

---

## Rejected approaches
- 試過：把低風險 caller 與高風險 caller（`translation_view.py` / `cache_view.py` / `main.py`）合併成一顆 PR28 一次做完。
- 為什麼放棄：Phase 0 盤點顯示 caller 橫跨 11 個檔案，且 `translation_view.py` 有 lazy import、`main.py` 有啟動入口、`cache_view.py` surface 很大；若一顆全做，出問題時很難判斷是低風險同步 import，還是高風險 lazy import / main / cache orchestration 造成。這不是 test 已失敗才放棄，而是基於回歸面與 diff 規模先行降風險。
- 最終改採：拆成 `PR28a`（低風險 caller migration）與 `PR28b`（高風險 caller migration）兩顆。

---

## Not included in this PR
這個 PR 沒有做以下事情：
- 沒有刪除 `app.services.py` 的 re-export
- 沒有修改 `translation_view.py`
- 沒有修改 `lm_view.py`
- 沒有修改 `merge_view.py`
- 沒有修改 `cache_view.py`
- 沒有修改 `main.py`
- 沒有碰 `qc_view.py` / checkers scope

---

## Next step
續做 PR28b 高風險 caller migration，範圍包含：
- `translation_view.py`
- `lm_view.py`
- `merge_view.py`
- `cache_view.py`
- `main.py`

---

## Test result
```text
> uv run python -c "from app.views.config_view import ConfigView; print('ok')"
ok

> uv run python -c "from app.views.rules_view import RulesView; print('ok')"
ok

> uv run python -c "from app.views.bundler_view import BundlerView; print('ok')"
ok

> uv run python -c "from app.views.lookup_view import LookupView; print('ok')"
ok

> uv run python -c "from app.views.extractor_view import ExtractorView; print('ok')"
ok

> uv run pytest -q --basetemp=.pytest-tmp\\pr28a -o cache_dir=.pytest-cache\\pr28a tests/test_main_imports.py tests/test_ui_refactor_guard.py
.......                                                                  [100%]
7 passed in 0.02s
```
