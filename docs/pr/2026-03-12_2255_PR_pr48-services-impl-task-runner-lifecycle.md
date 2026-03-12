# PR48：services impl task runner lifecycle

## Summary
這顆 PR 把 service wrapper 內重複的 lifecycle 控制流正式抽成共用 task runner。目標不是重寫 UI contract，而是讓 logger refresh、session start/finish、UI handler binding、error wrapping 這些重複邏輯有唯一實作點。

---

## Phase 1 完成清單
- [x] 做了：新增 `app/services_impl/pipelines/_task_runner.py`，提供 `run_callable_task()` / `run_generator_task()`。
- [x] 做了：把 `ftb_service.py`、`kubejs_service.py`、`md_service.py` 改成薄 wrapper，只留 domain-specific 參數轉譯。
- [x] 做了：保留各 service 模組的 `UI_LOG_HANDLER` monkeypatch seam，相容既有測試/外部 patch 點。
- [x] 做了：新增 focused tests：`test_pipeline_services_session_lifecycle.py`、`test_pipeline_services_error_handling.py`。
- [ ] 未做：`extract_service.py` / `lm_service.py` / `merge_service.py` 全數改吃 `_task_runner.py`（原因：這顆先收斂 FTB/KubeJS/MD，避免 scope 膨脹）。

---

## What was done

### 1. 抽出共用 task runner
新增 `app/services_impl/pipelines/_task_runner.py`：
- `run_callable_task()`
- `run_generator_task()`

統一處理：
- `ensure_pipeline_logging()`
- `session.start()` / `session.finish()`
- `UI_LOG_HANDLER.set_session(...)`
- error wrapping / traceback log
- finally cleanup

### 2. 把三支 pipeline service 瘦成薄 wrapper
調整：
- `ftb_service.py`
- `kubejs_service.py`
- `md_service.py`

這些 wrapper 現在只負責：
- import 真正的 pipeline 入口
- 準備 kwargs
- 指定錯誤時是否寫回 `session.add_log()`
- 把本模組的 `UI_LOG_HANDLER` 傳給 `_task_runner`

### 3. 保留 monkeypatch seam，相容既有測試
這次實作一開始踩到一個相容坑：
- 既有 `test_pipeline_logging_bootstrap.py` 會 monkeypatch `ftb_service.UI_LOG_HANDLER` / `kubejs_service.UI_LOG_HANDLER` / `md_service.UI_LOG_HANDLER`
- 如果 wrapper 不再暴露這個符號，測試會直接炸

修正後：
- 各 wrapper 模組重新保留 `UI_LOG_HANDLER`
- `_task_runner` 接受注入的 `ui_log_handler`

這樣共用 lifecycle 與既有 monkeypatch seam 兩邊都保住。

---

## Important findings
- PR48 真正的坑不是 task runner 抽不抽得出來，而是**相容 seam 不能順手抹掉**。
- 這次我有先踩到 `UI_LOG_HANDLER` seam 被抹掉，導致 bootstrap order tests 失敗；已在本顆內自行修正，不需要你決策。
- 目前 extract/lm/merge 仍保留舊 lifecycle 形式，但現在已經有可重用的 task runner，後續要收斂會容易很多。

---

## Validation checklist
- [x] `rg -n "session\.start\(|UI_LOG_HANDLER\.set_session|GLOBAL_LOG_LIMITER\.flush|ensure_pipeline_logging" app/services_impl/pipelines --glob "*.py"`
- [x] `uv run pytest -q tests/test_pipeline_logging_bootstrap.py tests/test_pipeline_services_session_lifecycle.py tests/test_pipeline_services_error_handling.py --basetemp=.pytest-tmp\pr48 -o cache_dir=.pytest-cache\pr48`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr48-full -o cache_dir=.pytest-cache\pr48-full`

## Test result
```text
$ rg -n "session\.start\(|UI_LOG_HANDLER\.set_session|GLOBAL_LOG_LIMITER\.flush|ensure_pipeline_logging" app/services_impl/pipelines --glob "*.py"
...（顯示 lifecycle 已集中到 _task_runner，舊 wrapper 與其餘 service 仍可盤點）

$ uv run pytest -q tests/test_pipeline_logging_bootstrap.py tests/test_pipeline_services_session_lifecycle.py tests/test_pipeline_services_error_handling.py --basetemp=.pytest-tmp\pr48 -o cache_dir=.pytest-cache\pr48
.........                                                                [100%]
9 passed in 0.29s

$ uv run pytest -q --basetemp=.pytest-tmp\pr48-full -o cache_dir=.pytest-cache\pr48-full
........................................................................ [ 58%]
....................................................                     [100%]
124 passed in 1.49s
```

---

## Rejected approaches
1) 試過：在每支 service wrapper 內各自補註解與小 helper，不抽共用 task runner。
   - 為什麼放棄：這看起來保守，實際上只是讓 lifecycle bug 永遠散在各檔。
   - 最終改採：把重複控制流正式抽成共用 helper。

2) 試過：抽 task runner 時順手拿掉 wrapper 的 `UI_LOG_HANDLER` 暴露。
   - 為什麼放棄：這直接弄壞既有 monkeypatch seam，bootstrap tests 當場爆。
   - 最終改採：保留 wrapper module-level handler，task runner 改接受注入 handler。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有改 UI `TaskSession` 外部契約
- 沒有重寫 logger limiter / handler
- 沒有動 QC/checkers 舊線
- 沒有一次把所有 service wrapper 全部搬進 `_task_runner`

---

## Next step

### PR49
- 把 `main.py` 的 view registry 與 startup task 抽出去。
- 現在 service lifecycle 比較穩了，後面 view 層整理比較不會一直撞 service 差異。
