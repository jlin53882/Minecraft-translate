# PR33 Phase 1 執行 + Validation 回報（停在 commit 前）

> 狀態：Phase 1 已完成，Validation 已跑完，**尚未 commit/push**。

## 本次實作內容（non-UI）

### 新增檔案
- `app/services_impl/pipelines/_pipeline_logging.py`
- `tests/test_pipeline_logging_bootstrap.py`

### 修改檔案
- `app/services_impl/pipelines/extract_service.py`
- `app/services_impl/pipelines/ftb_service.py`
- `app/services_impl/pipelines/kubejs_service.py`
- `app/services_impl/pipelines/lm_service.py`
- `app/services_impl/pipelines/md_service.py`
- `app/services_impl/pipelines/merge_service.py`

### 變更重點
- 6 個 pipeline 重複 `_update_logger_config()` 已去重。
- 改為統一呼叫 `ensure_pipeline_logging()`（集中於 `_pipeline_logging.py`）。
- 依你要求，PR33 已直接補「初始化時機」測試（不用等 PR34）：
  - 以 `monkeypatch apply_logger_config` 驗證 logger bootstrap 在 pipeline first-step 前執行。
  - 覆蓋 FTB / KubeJS / MD / LM / Extract(lang,book) / Merge。

---

## Validation checklist 實際輸出

### 1) 重複函式清理檢查
```text
> rg -n "def _update_logger_config" app/services_impl/pipelines --glob "*.py"
(no output, exit code 1)
```

### 2) import smoke
```text
> uv run python -c "from app.services_impl.pipelines.lm_service import run_lm_translation_service; print('lm-import-ok')"
lm-import-ok

> uv run python -c "from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service; print('merge-import-ok')"
merge-import-ok
```

### 3) 初始化時機測試（PR33 新增）
```text
> uv run pytest -q tests/test_pipeline_logging_bootstrap.py --basetemp=.pytest-tmp\pr33-newtests -o cache_dir=.pytest-cache\pr33-newtests
.......                                                                  [100%]
7 passed in 0.33s
```

### 4) 全量測試
```text
> uv run pytest -q --basetemp=.pytest-tmp\pr33-phase1 -o cache_dir=.pytest-cache\pr33-phase1
................................................................         [100%]
64 passed in 1.08s
```

---

## 數字對照
- PR32 完成後 baseline：`57 passed`
- PR33 Phase 1 後：`64 passed`
- 差異：`+7`（新增的 pipeline logging bootstrap 時機測試）

---

## 目前停點
- ✅ PR33 Phase 1 與 Validation 完成
- ⛔ 尚未 commit/push（等你確認放行）
