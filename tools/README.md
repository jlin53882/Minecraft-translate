# tools/ 目錄說明

本目錄存放專案輔助腳本與工具程式。

## 分析工具
| 檔案 | 用途 |
|------|------|
| `analyze_all.py` | 全域覆蓋率分析 |
| `analyze_coverage*.py` | 各模組覆蓋率分析（v1-v3, final） |
| `_dep_scan*.py` | 依賴掃描工具 |
| `__code_scan.py` | 程式碼結構掃描 |
| `__scan_results.json` | 掃描結果資料 |

## 驗證工具
| 檔案 | 用途 |
|------|------|
| `verify_patchouli_zhTW*.py` | Patchouli 翻譯驗證腳本（多版本） |
| `verify_patchouli_final.py` | Patchouli 最終驗證 |

## 修復工具
| 檔案 | 用途 |
|------|------|
| `fix_merge_ui.py` | UI Merge 修復工具 |
| `fix_test.py` | 測試修復工具 |
| `gap_analysis.py` | 測試缺口分析 |

## 整合測試
| 檔案 | 用途 |
|------|------|
| `test_main.py` | 全域測試入口 |
| `test_all_features.py` | 全功能整合測試 |

---

*最後更新：2026-03-21*
