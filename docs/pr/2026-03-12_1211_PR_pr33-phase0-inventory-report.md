# PR33 Phase 0 盤點報告（停在 Phase 0，未進入 Phase 1）

## 進度狀態
- PR32 已完成並推送：`2353222`
- 目前已進入 PR33，停在 Phase 0（尚未改 PR33 目標程式碼）

---

## Phase 0 結論
1. `_update_logger_config()` 重複定義存在於 6 個 pipeline 檔案：
   - `extract_service.py`
   - `ftb_service.py`
   - `kubejs_service.py`
   - `lm_service.py`
   - `md_service.py`
   - `merge_service.py`

2. 這 6 份定義都等價地呼叫：
   - `apply_logger_config(_load_app_config, logger_name="translation_tool")`

3. 兩個 pipeline 檔案不在這次去重範圍：
   - `bundle_service.py`（無 `_update_logger_config`）
   - `lookup_service.py`（無 `_update_logger_config`）

4. `_update_logger_config()` 目前沒有外部 caller，僅各自 module 內呼叫。

5. logger 初始化時機：
   - 6 個目標 service 都是在 `run_*_service` 一開始就呼叫 `_update_logger_config()`
   - 後續再做 `session.start()` / `UI_LOG_HANDLER.set_session(session)`（或同層級啟動流程）

6. 測試覆蓋缺口：
   - repo 內目前沒有針對 `update_logger_config` / `UI_LOG_HANDLER` / 初始化時機 的專門測試。

7. baseline 測試：
   - `uv run pytest -q --basetemp=.pytest-tmp\pr32-phase0 -o cache_dir=.pytest-cache\pr32-phase0`
   - 結果：`50 passed`
   - （PR32 完成後目前全量為 57 passed，PR33 進場前可再作為新 baseline）

---

## Phase 0 盤點命令摘要
- `rg -n "def _update_logger_config\(|_update_logger_config\(" app/services_impl/pipelines`
- `rg -n "_update_logger_config\(" . --glob "*.py" --glob "!backups/**" --glob "!docs/**"`
- `rg -n "update_logger_config|UI_LOG_HANDLER|GLOBAL_LOG_LIMITER|logger" tests --glob "*.py"`

---

## PR33 Phase 1 前置建議（先確認）
1. 抽出單一 helper，例如：`app/services_impl/pipelines/_pipeline_logging.py`
2. Phase 1 只做「去重 + 等價重導向」，不改 service 對外介面
3. Phase 2 驗證至少要包含：
   - import smoke（6 個 pipeline service）
   - 全量 pytest
   - 1 條「初始化時機」檢查（確保 pipeline 開始前已完成 logger 設定）

---

## 目前停點
- ✅ PR33 Phase 0 完成
- ⛔ 尚未進入 Phase 1（等待你確認放行）
