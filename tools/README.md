# tools/ 目錄說明

本目錄存放專案輔助腳本。**不屬於 App 的一部分**，不會被測試或 CI 執行；用過就丟的一次性腳本已清掉
（`analyze_coverage*.py` 的多個版本、`_dep_scan*.py`、`fix_*.py`、`verify_patchouli_zhTW*_v*.py`、
`__scan_results.json` 等，歷史仍可在 git log 查到）。

| 檔案 | 用途 |
|------|------|
| `analyze_all.py` | 全域覆蓋率分析 |
| `gap_analysis.py` | 測試缺口分析 |
| `__code_scan.py` | 程式碼結構掃描 |
| `verify_patchouli_final.py` | Patchouli 翻譯驗證 |
| `build_icon_index.py` | 為指定的 mods 資料夾預建 Mod 資料庫 icon 索引；JAR 檔案變更後重新執行，例如 `python tools/build_icon_index.py "C:/path/to/mods"` |
| `benchmark_moddb_700k.py` | 70 萬筆以上 Mod 資料庫的讀取基準：每個案例在全新行程量測冷／熱／快取清空後的時間，含條目頁完整快照；方法見 `docs/developer-guide/performance.md` |
| `benchmark_moddb_700k_write.py` | 同一資料庫複本上的寫入路徑基準（手動儲存、批次取代與還原、批量審核、write_back、掃描匯入、衍生資料重建與驗證）；會修改傳入的資料庫 |
| `smart_review_strategy_probe.py` | 讀取既有 Smart Review JSON，離線比較驗證、重試與批次策略；可選擇性執行不寫入資料庫的 live A/B。 |
| `perf_moddb_web.py` | 真實 Flet Web 的 Mod 資料庫頁效能量測：用暫存資料目錄啟動 App、以 Playwright 逐項操作，記錄伺服器端（收到事件 → 最後一個 patch）與瀏覽器端（動作 → 畫面 semantics 變動）延遲，並取樣事件迴圈卡頓堆疊；情境在 `perf_moddb_web_scenarios.py`／`perf_moddb_web_flows.py`。用法：`python tools/perf_moddb_web.py run --db <moddb.db> [--only paging,select] [--hot 5] [--profile] [--out result.json]`（DB 會先複製）。 |
| `report_standalone_term_candidates.py` | 以 fixture 或 Mod DB 產生 standalone term 候選報告。 |
| `test_main.py` / `test_all_features.py` | 手動執行的整合檢查（不在 pytest 的 `testpaths` 內） |

需要新的一次性腳本時，請放在這裡並在上表補一行；用完請刪除，不要留多個版本。
