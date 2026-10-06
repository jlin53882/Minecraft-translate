# 橫切改動審查清單

> 適用版本：v0.6.0+  
> 更新日期：2026-10-06  
> 適用範圍：日誌、任務生命週期、執行緒／併發、錯誤處理、輸入驗證等「一改會碰到很多檔案」的變更

---

## 為什麼需要這份清單

PR #169（UI 與後台日誌同步）歷經多輪審查才收斂。回顧後，多數輪次的問題不是需求變大，而是同一類錯誤沒有在第一次發現時就整類掃完。本清單把當時反覆出現的缺陷類型整理成提交前自查項目，**在開 PR 與每次推送前逐項過一遍**。

適用判斷：變更滿足下列任一項，就視為橫切改動並套用本清單。

- 新增或修改「集中機制」（日誌鏡像、去重、任務歸屬、生命週期狀態）。
- 一次改動 5 個以上檔案，且改法相同（批次替換、腳本改寫）。
- 修改有狀態機的物件（`TaskSession`、`TaskManager`、對話框、管線步驟）。
- 新增跨執行緒的資料傳遞（執行緒池、背景執行緒回呼 UI）。

---

## 1. 動手前

- [ ] **先判斷層級**：這是橫切關注點嗎？是 → 在機制層解決，不要逐呼叫點手動補。
- [ ] **寫出不變量**（一兩句話即可，放進 PR 描述）。範例：
  - 每個任務結束時，後台只寫一次結束紀錄。
  - 同一筆事件只寫入 `app.log` 一次。
  - 每個 `app.log` 實體行都能對應到所屬任務。
- [ ] **讀被修改物件的所有消費者與既有註解**。例如 `TaskManager` 的註解已說明「ERROR 尚未結案，服務通常在 `finally` 才 `finish()`」，沒讀就容易誤判狀態。
- [ ] **列出狀態機的合法順序**：`start()` / `set_error()` / `finish()` / 重複呼叫 / 結束後再呼叫，各自預期的結果。
- [ ] **限制或延後的項目要先估成本**，不憑直覺標「Low」或「需大改架構」。同時執行任務的歸屬問題，實際只需 `contextvars` 加一個欄位。

## 2. 找到第一個缺陷時

- [ ] **掃整類，不修單點**：用 AST 或全專案搜尋找出同類寫法，一次修完。
- [ ] **把這一類寫成契約測試**（例如 `tests/test_log_pair_contract.py`、`tests/test_thread_context_contract.py`），讓下一個違規者在 CI 就被擋下。
- [ ] 契約測試的白名單用**穩定識別**（路徑＋限定函式名＋例外類型＋序號），不要用行號。
- [ ] 修完用**變異測試**確認：故意還原修正，測試必須轉紅。注意 `sed` 沒套用會造成假陽性，變異後要先確認檔案真的改了。

## 3. 日誌與去重

- [ ] 同一事件在 UI 與後台是否只出現一次？UI 寫入自動鏡像到後台；後台日誌由 `UISessionLogHandler` 轉進 UI；帶 `ui_mirrored` 標記的記錄不回灌。
- [ ] UI 文字與後台文字是否**逐字相同**（含 `{e}` 與 `{e!r}`）？去重靠完全相同的文字，格式不同就會重複。
- [ ] 呼叫點是否**明確**指定 `dedupe=` / `forwarded=`，而不是依賴預設值？
- [ ] 例外記錄是否帶 `exc_info`（ERROR 在 `except` 內自動補）？
- [ ] 多行訊息：續行是否也帶任務標籤？
- [ ] 修改 `log_format` 相關邏輯時，驗證器（`validate_log_format`）是否認得新欄位（`task_tag`、`task_id`、`task_name`）？
- [ ] 訊息經過遮罩（`redact_secrets`）後再加標籤，順序不可顛倒。

## 4. 任務、執行緒與併發

- [ ] 背景執行緒是否繼承任務歸屬？一律用 `ContextThreadPoolExecutor` / `run_in_context`，**不要**直接用 `ThreadPoolExecutor`（池內執行緒不會繼承 `contextvars`）。
- [ ] 是否假設「UI 執行緒有任務歸屬」？沒有；未知歸屬走「最近綁定的 session」退回行為。
- [ ] 路由用**任務識別**，不用文字內容或全域「目前的 session」。
- [ ] `task_id` 是否每次 `start()` 重新產生（同一個 session 重跑不可沿用舊 ID）？
- [ ] 工作執行緒邊界的 `except` 寫錯誤 log 前，是否用 `session_task_scope(session)` 重新歸屬？服務結束時已 `set_session(None)` 清掉歸屬，沒有歸屬的記錄會退回「最近綁定的 session」，完整堆疊可能送進別的任務畫面。
- [ ] 同時執行兩個任務且訊息文字相同時，去重是否仍各自成立？（要有真正的多任務測試，不是單元 mock。）

## 5. 任務生命週期（狀態機）

- [ ] `finish()` 是否**冪等**？重複呼叫不可重複寫結束紀錄、不可在 `TaskManager` 留下重複完成紀錄。
- [ ] 判斷「已結束」是否用 `is_finished`，**不用** `status in {DONE, ERROR}`（ERROR 不等於已結束）。
- [ ] 結束後才呼叫 `set_error()`：session、`TaskManager`、後台三處的最終狀態是否一致？
- [ ] 任何結束路徑（成功／來源失敗／例外／取消）都只 `finish` 一次，且失敗時先 `set_error()`。
- [ ] 要有交錯情境測試：一個任務已標錯誤但未結束、另一個任務同時結束；結束後才標錯誤。

## 6. 輸入驗證與失敗呈現

- [ ] 輸入不存在、型別錯誤（資料夾 vs 檔案、ZIP vs 資料夾）時，核心產生器是否 `yield error=True`，而不是靜默成功？
- [ ] 一鍵流程可略過缺少的輸入（`skip_missing_input=True`），一般流程不可。
- [ ] 失敗時 UI 是否真的有可見的提示？對話框關閉後要再推一次 `page.update()`，避免遮罩殘留把 SnackBar 蓋住（`pipeline_one_click_dialog.py` 的 `_one_click_dispose_dialogs`）。
- [ ] 完成訊息不可在失敗時仍顯示「已完成」。
- [ ] 包裝呼叫端 handler 時（例如 `SyncTextField`）要「先同步、再呼叫」並保留 sync／async／零參數寫法，不可二選一；測試要模擬 Web 真實情況（`e.data` 是新值、`control.value` 是舊值），不能在測試裡先手動設好值。
- [ ] 清理動作（刪資料夾）成功與否要如實記錄：`ignore_errors=True` 之後不能無條件寫「已清理」。
- [ ] 平台差異：新增檔案／資料夾選擇器一律用 `SafeFilePicker`（Flet Web 不支援 `get_directory_path`），並讓欄位可手動輸入。
- [ ] 輸出資料夾不要求事先存在，用 `ensure_output_dir()` 自動建立；只有「輸入」才要求存在。

## 7. 批次修改與工具

- [ ] 批次替換（`sed`、腳本改寫）前先限定檔案範圍，改完用 `python -m py_compile` 把關。腳本用位元組偏移當字元索引，遇到中文會破壞程式碼。
- [ ] **不要**對整個 repo 跑 `ruff format` 或 `ruff check --fix`；只處理有改動的檔案。
- [ ] 提交前跑 **CI 同版本、同指令**的 lint 與格式檢查。
- [ ] 批次改寫後，逐一檢查會互相配對的程式（UI／後台文字對、`dedupe` 標記），不要只看測試是否通過。

## 8. 測試

- [ ] 不只測 happy path：至少涵蓋狀態機邊界、多任務併發、真實檔案 handler（`app.log` 實際寫入內容）。
- [ ] 新增 UI 行為時，說明驗證方式：自動化測試、手動煙霧測試，或「無法在此環境驗證」及原因。
- [ ] 跑 `pytest` 全量後，再個別跑本次新增的測試檔。

## 9. PR 描述與誠實回報

- [ ] 描述只寫**已驗證**的宣稱，不先寫期望。「每一行」「全部」這類字眼要有對應的檢查支撐。
- [ ] 限制與未驗證項目要明講（例如：沙箱無法連 CanvasKit CDN，無法跑真實 Flet UI → 以 Windows 實測補足）。
- [ ] 被問「整個專案都檢查過了嗎」時，如實說明範圍：grep 掃描、AST 掃描、逐檔審查是不同層級。

---

## 相關文件

- [`TEST_STRATEGY.md`](TEST_STRATEGY.md)：測試策略
- [`PR_WORKFLOW.md`](PR_WORKFLOW.md)：PR 執行彙報格式
- [`EXCEPTION_INVENTORY.md`](EXCEPTION_INVENTORY.md)：例外處理盤點
- [`WORKER_THREAD_AUDIT.md`](WORKER_THREAD_AUDIT.md)：背景執行緒稽核
- [`WEB_MODE_LIMITATIONS.md`](WEB_MODE_LIMITATIONS.md)：Flet Web 模式限制
- [`WINDOWS_VERIFICATION.md`](WINDOWS_VERIFICATION.md)：Windows 實機驗證

## 修訂紀錄

| 日期 | 變更 |
|---|---|
| 2026-10-06 | 初版：整理自 PR #169 的審查回顧與 Windows 煙霧測試結果 |
