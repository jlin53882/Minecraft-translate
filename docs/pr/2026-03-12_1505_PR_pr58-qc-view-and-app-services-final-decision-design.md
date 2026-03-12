# PR58 設計稿：`qc_view.py` / `app/services.py` 最終命運決策

## Summary
PR58 是整輪最後一題，也是最不該提早碰的一題。因為 `qc_view.py` + `app/services.py` 不只是結構問題，還牽涉產品命運：這條 QC/checkers 線到底保不保留、保留到什麼程度、是不是要正式收編到新的 services_impl/view 結構。沒有這個決策，技術上做再漂亮都只是半套。

---

## Phase 0 盤點
- 目前 `app/services.py` 約 73 行，已收斂成只服務 QC/checkers 的殘留 façade；`qc_view.py` 約 214 行，仍直接依賴 `app.services`。
- `main.py` 目前不再 import `qc_view.py`，也有 `tests/test_main_imports.py` 在守這條邊界。
- 這代表 QC 線已被明確凍結：沒有死，但也沒有正式融入主流程。
- 這題若太早處理，會把整輪 refactor 從『穩定切邊界』變成『邊重構邊做產品決策』。

---

## 設計範圍
- 先做產品路線二選一，文件內必須明寫：A. 保留功能；B. 標記停用並移除。沒有決策就不進 Phase 1。
- 若選 A（保留功能）：新增 `app/services_impl/checkers/` 或同級正式服務模組，讓 `qc_view.py` 改依賴新 service；必要時補 `tests/test_qc_view_characterization.py`。
- 若選 B（不保留）：先補最小 characterization tests 鎖住現在行為，再把 `qc_view.py` 標記停用、移出 main 可見路徑，最後才移除 `app/services.py` façade。
- 不論選 A 或 B，`app/services.py` 都不該再維持半永久的歷史包袱狀態；PR58 必須讓它要嘛正式併入新架構，要嘛完成退場。

---

## 刪除/移除/替換說明
- 若最終路線是移除 `qc_view.py` / `app/services.py`，PR 文件必須逐項列出 caller、替代路徑、風險與驗證方式。
- 若最終路線是保留功能，也要說清楚為什麼保留、正式入口在哪、舊 façade 何時退場。

---

## Validation checklist
- [ ] `rg -n "from app\.services import|run_untranslated_check_service|run_variant_compare_service|run_english_residue_check_service|run_variant_compare_tsv_service" app tests --glob "*.py"`
- [ ] `uv run pytest -q tests/test_main_imports.py tests/test_qc_view_characterization.py --basetemp=.pytest-tmp\pr58 -o cache_dir=.pytest-cache\pr58`
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr58-full -o cache_dir=.pytest-cache\pr58-full`

---

## Rejected approaches
1) 試過：先技術上把 `qc_view.py` 搬進新架構，產品命運之後再說。
2) 為什麼放棄：這是最容易白做工的路；如果最後決定不保留，前面那波結構整理幾乎都變沉沒成本。
3) 最終改採：先明確做產品決策，再走保留或退場其中一條完整路徑。

---

## Not included in this PR
- 不在這顆 PR 同時做其他 view 重構。
- 不重新打開被凍結的功能到 main navigation。
- 不混入 unrelated checker bug fix。

---

## Next step
- PR58 做完，這輪 PR40~58 的主線才算真正收乾淨。
