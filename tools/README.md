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
| `test_main.py` / `test_all_features.py` | 手動執行的整合檢查（不在 pytest 的 `testpaths` 內） |

需要新的一次性腳本時，請放在這裡並在上表補一行；用完請刪除，不要留多個版本。
