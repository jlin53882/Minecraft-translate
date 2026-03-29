# PR48 設計稿：`app/services_impl` 共用 task runner / lifecycle 抽離

## Summary
PR48 要處理的是 service wrapper 的重複控制流。`lm_service.py`、`extract_service.py`、`ftb_service.py`、`kubejs_service.py`、`md_service.py` 等檔案都在重複做 logger refresh、session start/finish、UI handler binding、error wrapping。這顆 PR 的目標是把這套 lifecycle 變成一個能被測的共用 helper。

---

## Phase 0 盤點
- `app/services_impl/pipelines/` 目前已有多支 service wrapper，且 `lm_service.py`、`extract_service.py` 已可見重複 lifecycle 片段。
- `app/services_impl/pipelines/_pipeline_logging.py` 目前只負責 logging bootstrap，不處理 session / error flow。
- repo 目前只有 `tests/test_pipeline_logging_bootstrap.py`，缺 lifecycle / error handling 專屬測試。
- 若這層不先統一，PR54~56 的 view 重整會一直撞到 service contract 差異。

---

## 設計範圍
- 新增 `app/services_impl/pipelines/_task_runner.py`，提供兩種共用入口：`run_generator_task(...)` 與 `run_callable_task(...)`。
- 共用 helper 必須統一處理：`ensure_pipeline_logging()`、`session.start()`、`UI_LOG_HANDLER.set_session(session)`、log flush、error wrapping、finally cleanup。
- 各 pipeline service 檔改成薄 wrapper，只保留 domain-specific runner 呼叫與少量參數轉譯。
- 維持外部 service API 名稱不變，避免 UI 層 import / call site 連動。
- 新增 `tests/test_pipeline_services_session_lifecycle.py`、`tests/test_pipeline_services_error_handling.py`。

---

## Validation checklist
- [ ] `rg -n "session\.start\(|UI_LOG_HANDLER\.set_session|GLOBAL_LOG_LIMITER\.flush|ensure_pipeline_logging" app/services_impl/pipelines --glob "*.py"`
- [ ] `uv run pytest -q tests/test_pipeline_logging_bootstrap.py tests/test_pipeline_services_session_lifecycle.py tests/test_pipeline_services_error_handling.py --basetemp=.pytest-tmp\pr48 -o cache_dir=.pytest-cache\pr48`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr48-full -o cache_dir=.pytest-cache\pr48-full`

---

## Rejected approaches
1) 試過：在每支 service wrapper 內各自補註解與小 helper，不抽共用 task runner。
2) 為什麼放棄：看起來保守，但實際上會讓 lifecycle bug 永遠散在各檔，修一次得改五六份。
3) 最終改採：把重複控制流正式抽成共用 helper，讓 wrapper 回到真正的薄層。

---

## Not included in this PR
- 不改 UI `TaskSession` 外部契約。
- 不重寫 logger limiter / handler。
- 不動 QC/checkers 舊線。

---

## Next step
- PR49 接著把 `main.py` 的 view registry 與 startup task 抽出去。
