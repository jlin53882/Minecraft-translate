# PR33 設計稿：pipeline logging bootstrap 去重（non-UI）

## Summary
`app/services_impl/pipelines/*` 目前有多份 `_update_logger_config()`；本 PR 抽成單一共用 bootstrap helper，維持既有 service API。

---

## Phase 0 盤點（必做）
- [ ] 盤點各 pipeline 的 logger 初始化時機（在哪一行、在哪個流程節點）
- [ ] 盤點 `update_logger_config` 依賴與 side-effect
- [ ] 確認 caller 範圍（只在 pipeline 內，或有外部直接調用）
- [ ] 明確定義「初始化完成」判準（handler 綁定、logger 名稱、level）

---

## Phase 1 設計範圍
### 新增
- `app/services_impl/pipelines/_pipeline_logging.py`
  - `ensure_pipeline_logging()`（命名可微調）

### 修改
- `extract_service.py`
- `ftb_service.py`
- `kubejs_service.py`
- `lm_service.py`
- `md_service.py`
- `merge_service.py`

### 移除
- 各檔本地 `_update_logger_config()` 重複定義

---

## Out of scope
- 不改 pipeline 輸入輸出參數
- 不改 UI

---

## 刪除/移除/替換說明
- **刪除/替換項目**：各 pipeline 檔中的 `_update_logger_config()`
- **為什麼改**：初始化規則分散，後續難維護
- **現況 caller**：各檔內部呼叫
- **替代路徑**：`_pipeline_logging.ensure_pipeline_logging()`
- **風險**：初始化時機偏移可能導致前段 log 掉失或 handler 綁錯
- **驗證依據**：時機驗證 + import smoke + pytest

---

## Validation checklist
- [ ] `rg -n "def _update_logger_config" app/services_impl/pipelines`
- [ ] `uv run python -c "from app.services_impl.pipelines.lm_service import run_lm_translation_service; print('ok')"`
- [ ] `uv run python -c "from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service; print('ok')"`
- [ ] **時機驗證**：在每條 pipeline 啟動第一個可觀測步驟前，logger handler 已綁定（新增單元測試或 monkeypatch 驗證）
- [ ] `uv run pytest -q --basetemp=.pytest-tmp\pr33 -o cache_dir=.pytest-cache\pr33`

---

## Rejected approaches
1) **方案**：維持現狀，僅補註解說明。  
   **放棄原因**：重複初始化邏輯仍會分叉。  
2) **方案**：連 service 層與 UI logging 一次全改。  
   **放棄原因**：範圍過大且跨層，風險高。  
3) **最終採用**：只做 pipeline 內初始化去重 + 時機驗證。

---

## Next
PR34：先補完整 non-UI guard tests 安全網。
