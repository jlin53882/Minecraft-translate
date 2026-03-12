# PR28b: Migrate high-risk callers off `app.services`

## Summary
PR28b 已完成高風險 caller migration，將 `translation_view.py`、`lm_view.py`、`merge_view.py`、`cache_view.py`、`main.py` 從 `app.services` 改為直接 import canonical modules。`translation_view.py` 保留原本的 lazy import + fallback-to-`None` 形式；`cache_view.py` 與 `main.py` 只改 import path，不改 UI、事件或 app startup 邏輯。

---

## Phase 1 完成清單
- [x] 建立 rollback backups：`backups/pr28ab-caller-migration-20260312-022156/...`
- [x] 完成 5 個高風險 caller 的 import migration
- [x] 保留 `translation_view.py` lazy import + fallback-to-`None`
- [x] 保持 `cache_view.py` / `main.py` 只改 import path
- [x] 完成 smoke import 驗證
- [x] 完成指定 pytest 驗證
- [x] 完成最終重複驗證

---

## What was done

### 1. High-risk caller migration
完成以下 caller 的 canonical import 遷移：
- `app/views/translation_view.py`
- `app/views/lm_view.py`
- `app/views/merge_view.py`
- `app/views/cache_view.py`
- `main.py`

### 2. Import path 調整
- `translation_view.py`
  - `run_ftb_translation_service` -> `app.services_impl.pipelines.ftb_service`
  - `run_kubejs_tooltip_service` -> `app.services_impl.pipelines.kubejs_service`
  - `run_md_translation_service` -> `app.services_impl.pipelines.md_service`
- `lm_view.py`
  - `run_lm_translation_service` -> `app.services_impl.pipelines.lm_service`
- `merge_view.py`
  - `run_merge_zip_batch_service` -> `app.services_impl.pipelines.merge_service`
- `cache_view.py`
  - `cache_*_service` -> `app.services_impl.cache.cache_services`
- `main.py`
  - `cache_rebuild_index_service` -> `app.services_impl.cache.cache_services`

### 3. 行為保持不變
- `translation_view.py` 仍使用 `try/except` lazy import，失敗時維持 symbol = `None`
- `cache_view.py` 只改 import block，未改 event / UI / cache orchestration 邏輯
- `main.py` 只改 `cache_rebuild_index_service` import path，未改 bootstrap / app startup flow
- 未刪除 `app/services.py` 任何 re-export
- 未碰 QC / checkers scope

---

## Important findings
- 預設 Windows 使用者層 `uv` cache / pytest temp 路徑先前有 `WinError 5` 權限問題；本次驗證改用 repo 內本地路徑：
  - `UV_CACHE_DIR=.uv-cache`
  - `TMP=.tmp`
  - `TEMP=.tmp`
  - `--basetemp=.pytest-tmp/...`
  - `-o cache_dir=.pytest-cache/...`
- 在上述條件下，PR28b checklist 可用 `uv run` 全數通過。
- 高風險 caller migration 本身沒有造成 import regression；重複驗證下 `TranslationView`、`LMView`、`MergeView`、`CacheView`、`main` 皆可正常 import。
- `qc_view.py` 仍維持 `from app.services import ...`，未被這一輪 migration 觸及。

---

## Rejected approaches
- 試過：把高風險 caller 也塞進 PR28a，一次把全部 caller migration 做完。
- 為什麼放棄：`translation_view.py` 有 lazy import、`main.py` 是啟動入口、`cache_view.py` surface 很大，這三者若跟低風險 caller 混在同一顆，任何 ImportError 或行為回歸都很難快速定位。這不是某個 test 已經報錯後才撤退，而是根據 Phase 0 盤點先行切風險。
- 最終改採：拆出 `PR28b`，專門處理高風險 caller，並用更重的 import smoke + cache view 測試覆蓋它。

---

## Not included in this PR
這個 PR 沒有做以下事情：
- 沒有刪除 `app.services.py` 的 re-export
- 沒有修改 `qc_view.py`
- 沒有修改 checkers wrappers
- 沒有改 `cache_view.py` 的 UI / event logic
- 沒有改 `translation_view.py` 的 lazy import 行為

---

## Next step
- 後續可再盤點 repo 內 `from app.services import ...` 的剩餘使用點
- 若只剩 QC / checkers 暫緩線，才考慮開真正刪除 legacy re-export 的 cleanup PR

---

## Test result
```text
> uv run python -c "from app.views.translation_view import TranslationView; print('ok')"
ok

> uv run python -c "from app.views.lm_view import LMView; print('ok')"
ok

> uv run python -c "from app.views.merge_view import MergeView; print('ok')"
ok

> uv run python -c "from app.views.cache_view import CacheView; print('ok')"
ok

> uv run python -c "import main; print('ok')"
ok

> uv run pytest -q --basetemp=.pytest-tmp\\pr28b -o cache_dir=.pytest-cache\\pr28b tests/test_main_imports.py tests/test_ui_refactor_guard.py tests/test_cache_view_features.py tests/test_cache_search_orchestration.py tests/test_cache_view_monkeypatch_integration.py tests/test_cache_view_state_gate.py
....................                                                     [100%]
20 passed in 0.88s

> uv run python -c "from app.views.config_view import ConfigView; from app.views.rules_view import RulesView; from app.views.bundler_view import BundlerView; from app.views.lookup_view import LookupView; from app.views.extractor_view import ExtractorView; from app.views.translation_view import TranslationView; from app.views.lm_view import LMView; from app.views.merge_view import MergeView; from app.views.cache_view import CacheView; import main; print('all-imports-ok')"
all-imports-ok
```
