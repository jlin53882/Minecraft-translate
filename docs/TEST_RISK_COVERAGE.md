# 引擎風險 × 行為測試盤點

本文件記錄主線目前高風險工作流由哪些測試保護、測試證據的層級，以及仍需補強的邊界。它是**風險 inventory，不是 coverage 百分比報表**；不得用 import 測試數或全套測試總數推論行為保護已完整。

## 方法與判讀

- 依「失敗會不會遺失／覆寫使用者輸出、錯誤回報成功、額外呼叫模型，或讓任務無法取消／恢復」排序。
- 優先確認 assertion 是否檢查輸出、狀態、事件或檔案內容；只檢查 import、原始碼字串或 mock 被呼叫，列為結構線索，不等於 end-to-end 行為證據。
- 此 repo 的 `pyproject.toml`／`uv.lock` 沒有 `pytest-cov`；本輪不新增 coverage 相依，也不設全專案百分比門檻。
- 測試均應使用 `tmp_path`、固定 fixture、fake API／clock；UI smoke 的 runtime 與產物留在可拋棄目錄或 `.artifacts/`。

## 風險 × 現有保護 × 尚缺邊界

| 風險路徑 | 現有行為證據 | 尚缺／證據限制 | 優先級 |
|---|---|---|---|
| 快取持久化、pending 回復、lazy read／失敗拒寫 | `test_cache_manager.py`（save failure 後 pending 可重試）、`test_cache_store.py`（CRUD／執行緒競爭）、`test_cache_lazy_reads.py`（磁碟首讀、partial init、single/batch write、cooldown、索引拒絕）、`test_cache_search_orchestration.py`（build-then-swap） | `CacheSearchFacade` 內部吞掉重建錯誤時，上層成功訊號是否仍準確，需另行驗收；shared translation loop 目前忽略 `add_to_cache()` 的 bool，cache write 被拒時任務結果未必反映資料未寫入 | P1 |
| Token batching、ordered result、LM retry／key rotation | `test_lm_batch_budget.py`、`test_lm_token_budget_integration.py`、`test_lm_translator_shared_loop.py`、`test_key_health_integration.py`、`test_api_key_cycle.py`、`test_api_key_rotation.py` | shared loop 對 partial／cache write failure 的端到端結果傳遞仍弱；`test_lm_translator_main.py` 的部分舊 assertion 接受多種 status，不能單獨證明精確錯誤分類 | P1 |
| checkpoint 與取消 | `test_lm_checkpoint_fingerprint.py`、`test_cancellation.py`、`test_task_session.py`、`test_shell_scheduling.py` | fingerprint 測試證明同資料可讀 checkpoint，不等於程序崩潰後整個流程可恢復；FakePage teardown 測試不等於真 Flet 關閉；TaskSession／TaskManager 沒有獨立終止 `CANCELLED` 狀態 | P1 |
| JAR 掃描／提取與封存安全 | `test_scan_jars_explicit_list.py`、`test_jar_processor_extract.py`、`test_extraction_failure_contract.py`、`test_zip_safety.py`、`test_zip_safety_hardening.py`、`test_zip_bomb_protection.py` | 高風險尺寸／路徑防護有行為測試；大型真實模組包與 UI progress／取消串接仍需人工或整合驗收 | P1 |
| Lang merge／輸出與 pending | `test_lang_merger_folder_baseline.py`、`test_lang_merger_zip_baseline.py`、`test_lang_merger_budget_exhausted.py`、`test_lang_merge_pending_export.py`、`test_merge_pipeline_e2e.py`、`test_translation_path_writer.py` | 有資料流程測試；跨階段意外中止後所有中間檔案／checkpoint 的恢復邊界仍需另外列明，不能從成功路徑推定 | P1 |
| FTB Quests／KubeJS／Markdown 注入 | `test_ftbquests_snbt_inject.py`、`test_kubejs_tooltip_inject.py`、`test_md_inject_qa.py` 均有輸出內容斷言；KubeJS 另保護平衡括號的 `event.add(..., Text.of(...))`、nested Text、escaping、no-op 與 patched count invariant | FTB Quests 完整 extraction→translate→inject 多階段工作流仍未由單一端到端 fixture 鎖定；KubeJS 其他未列出的 JavaScript 語法形態未全覆蓋 | P1 |
| 資源包 bundler | `test_output_bundler.py`（實際 ZIP 檔案、額外目錄、重複檔名與完整流程）、`test_bundler_view_characterization.py`、`test_duplicate_run_guards.py` | 核心產物有行為保護；UI busy/cancel 與 ZIP 失敗後可恢復狀態仍主要由 View 測試覆蓋 | P2 |
| Dashboard／13 頁真實渲染與主要 Dialog | `test_dashboard_data.py`、各 View characterization、`tools/ui_smoke.py` 真 Flet／Playwright | FakePage／控制項斷言無法證明 canvas 排版；原 smoke 未覆蓋主要 pipeline Dialog、窄版／直向尺寸、空／有資料／錯誤／取消／loading 矩陣與回訪；新的 harness 和人工檢查清單補上邊界 | P1 |
| callback／UI teardown | `test_shell_scheduling.py` 驗證 worker callback 只排程及 dispose；smoke 現新增真 Flet session dispose 後 late TaskSession probe | 需在真實 Windows 桌面 CLOSE／destroy 流程確認的部分不屬本次測試工具 PR；CanvasKit smoke 不能代替桌面實機 | P2 |

## 本輪第一批行為補強與修復

- `tests/test_ftbquests_snbt_inject.py::test_patch_lang_snbt_file_writes_only_template_keys`：實際解析輸出 SNBT，確認既有值更新、其他欄位保留、模板外 key 不新增。
- `tests/test_kubejs_tooltip_inject.py::test_inject_basic_flow`：不再只斷言計數非負；檢查實際輸出的翻譯內容與來源 JS 未改動。
- `tests/test_md_inject_qa.py::test_main_writes_translated_markdown_without_changing_tokens`：走 CLI 主流程及暫存檔案，確認 en_us→zh_tw 輸出、格式 token 保留、多餘文字行清空且來源檔未修改。

這些是 characterization／行為保護；KubeJS 的平衡括號 writeback 修復也已納入本 PR，避免測試只保護一個計數器而沒有驗證輸出內容。

## 本輪已解決與保留邊界

KubeJS `event.add(..., Text.of(...))` 的 root cause 是原先以非平衡括號方式切割呼叫參數，巢狀 `Text.of()` 的右括號會提前結束 match；修復改用字串／括號感知的 call scanner，並在實際輸出內容變更後才增加 patched count。回歸測試涵蓋 plain string、nested Text、escaping、already-translated/no-op、scene.text 與 ItemEvents writeback。

其他需獨立驗收的錯誤傳遞：

- `CacheSearchFacade` 內部重建例外被 catch 後只記 log，呼叫端可能無法區分索引實際失敗。
- shared LM 翻譯迴圈目前忽略 `add_to_cache()` 的回傳值；寫入被拒絕時，應由後續安全／錯誤處理工作包決定如何反映在任務結果。
- 目前 smoke 的 `cancelled` case 表示「已送出取消要求、worker 尚未結束」；它不代表專案已定義或驗證終止的 CANCELLED UI 狀態。

上述 cache error propagation 與 terminal CANCELLED status 仍是明確邊界；本 PR 未擴張到那些 subsystem，也未引入 coverage gate。
