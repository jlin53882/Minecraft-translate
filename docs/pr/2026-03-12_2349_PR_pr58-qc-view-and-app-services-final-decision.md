# PR58：qc view and app services final decision

## Summary
這顆 PR 的最終決策是：**`qc_view.py` 保留，且本顆不修改其內容；`app/services.py` 也保留，作為 QC/checkers 線的 active façade。**

也就是說，PR58 不做移除，不做遷移，不做功能重寫；而是把這個保留決策正式落成 tests/guard，避免後面又有人把這條凍結但仍在用的線誤判成 dead code。

---

## Phase 1 完成清單
- [x] 做了：inventory 確認 `qc_view.py` 仍直接依賴 `app.services`。
- [x] 做了：新增 `tests/test_qc_view_characterization.py`，鎖住 QC view 的最小 observable behavior。
- [x] 做了：新增 `tests/test_qc_services_facade.py`，鎖住 `app/services.py` 的 façade export 與 generator 包裝行為。
- [x] 做了：保留 `tests/test_main_imports.py` 一起跑，確認 QC 仍不重新進 main navigation。
- [x] 做了：明確不修改 `qc_view.py` 內容。
- [ ] 未做：將 QC 線遷移到 `services_impl/checkers/` 或重寫 QC 內容（原因：你已明確決定「保留但內容不動」）。

---

## 最終決策
### 決策結果
- `qc_view.py`：**保留**
- `app/services.py`：**保留**
- 本顆 PR：**只補 guard / characterization tests，不改功能內容**

### 為什麼這樣決定
- `qc_view.py` 目前仍有明確 caller/value，不是 dead code。
- `app/services.py` 目前雖然只剩 QC/checkers façade，但這條 façade 仍是 active，不該在沒有產品決策與完整替代路徑前硬拆。
- 你已明確指定：**QC view 先留下來，但裡面內容都不動。**

這顆 PR 的工作因此轉成：
- 把「先保留」變成被測試守住的工程事實。

---

## What was done

### 1. 新增 QC characterization tests
新增 `tests/test_qc_view_characterization.py`，先鎖住：
- 三個 QC 任務按鈕仍存在：
  - 未翻譯檢查
  - JSON 資料夾差異比對
  - TSV 單檔案差異比對
- `set_controls_disabled()` 仍會一起控制 QC 相關 inputs/buttons
- `start_task('untranslated')` 在缺少路徑時仍會提示錯誤
- `task_worker()` 仍會消耗 generator updates 並更新 progress/log

### 2. 新增 QC services façade tests
新增 `tests/test_qc_services_facade.py`，先鎖住：
- `app.services.__all__` 仍只暴露 QC/checkers façade
- `run_untranslated_check_service()` 仍會轉發 generator update
- `run_variant_compare_tsv_service()` 遇到例外仍會 yield error update dict

### 3. 保留 QC 與 main 邊界
這顆仍一起跑 `tests/test_main_imports.py`，確認：
- QC 線保留
- 但沒有重新接回 main navigation

也就是說，QC 現在的定位仍然是：
- **保留的凍結支線**
- 不是 dead code
- 也不是主流程活躍入口

---

## Important findings
- PR58 的真正價值不是「做更多重構」，而是**在最後一顆 PR 停手**。
- 這輪 PR40~58 到這裡已經把 core / services / views 都收過一輪，如果 PR58 還硬要去碰 QC 內容，很容易把整輪最後收尾變成新的產品決策泥沼。
- 所以這顆的正解就是：
  - 承認 QC 線目前是凍結但保留
  - 用 tests 把這個狀態鎖住
  - 不在這顆假裝把命運一次解決

---

## Validation checklist
- [x] `rg -n "from app\.services import|run_untranslated_check_service|run_variant_compare_service|run_english_residue_check_service|run_variant_compare_tsv_service" app tests --glob "*.py"`
- [x] `uv run pytest -q tests/test_qc_view_characterization.py tests/test_qc_services_facade.py tests/test_main_imports.py --basetemp=.pytest-tmp\pr58 -o cache_dir=.pytest-cache\pr58`
- [x] `uv run pytest -q --basetemp=.pytest-tmp\pr58-full -o cache_dir=.pytest-cache\pr58-full`

## Test result
```text
$ uv run pytest -q tests/test_qc_view_characterization.py tests/test_qc_services_facade.py tests/test_main_imports.py --basetemp=.pytest-tmp\pr58 -o cache_dir=.pytest-cache\pr58
........                                                                 [100%]
8 passed, 24 warnings in 0.73s

$ uv run pytest -q --basetemp=.pytest-tmp\pr58-full -o cache_dir=.pytest-cache\pr58-full
........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
171 passed, 61 warnings in 1.85s
```

---

## Rejected approaches
1) 試過：在 PR58 直接把 QC 線搬到 `services_impl/checkers/`。
   - 為什麼放棄：這違反本顆已確認的產品決策，也會把最後收尾 PR 再次打開大範圍遷移風險。
   - 最終改採：保留 QC 線，先用 tests 鎖住現況。

2) 試過：直接刪掉 `qc_view.py` / `app.services.py`，讓主線徹底乾淨。
   - 為什麼放棄：這和明確決策衝突，而且 `qc_view.py` 仍實際依賴 `app.services`，不是無 caller dead code。
   - 最終改採：保留，且補上 characterization/ façade tests。

---

## Not included in this PR
這個 PR **沒有做** 以下事情：
- 沒有修改 `qc_view.py` 內容
- 沒有修改 `app/services.py` 行為
- 沒有把 QC 線接回 main navigation
- 沒有處理 QC 功能重構或刪除

---

## Next step
- 這輪 PR40~58 主線到此收尾。
- 後續若要處理 QC，應開新主題，不再混入本輪 refactor 主線。
- UI `Text.style` deprecation warnings 也應獨立開 cleanup PR，不混入這輪主線。
