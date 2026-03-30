# QC_VIEW_ARCHITECTURE.md

## 定位

QCView 是 **Quality Check（品質檢查）頁**，用於在翻譯完成後進行多種品質驗證。並非翻譯管線的必要環節，而是獨立輔助工具。

---

## QCBase 提供的基底類別

`QCBase`（`qc_base.py`）封裝所有 QC 任務共用的執行緒邏輯：

```
task_worker(service_func, args_tuple, on_complete, controls_to_disable)
  → run() [thread]
      → for update in service_func(*args_tuple): yield update
```

特點：
- 統一的 ProgressBar + LogView 更新
- `controls_to_disable` 列表：任務期間自動禁用指定的 UI 控制項
- `on_complete` 回調：任務結束後恢復控制項

QCView 及子元件（如 `UntranslatedChecker`）都透過 `task_runner.task_worker()` 執行任務。

---

## 主要檢查項目

| 檢查類型 | 服務函式 | 說明 |
|----------|----------|------|
| Key 缺失檢查 | `run_untranslated_check_service` | 比對 en_us 與 zh_tw，找出翻譯 key 缺失 |
| 簡繁差異比較（JSON） | `run_variant_compare_service` | 比對 zh_cn 與 zh_tw JSON 資料夾差異，輸出 JSON 報告 |
| 簡繁差異比較（TSV） | `run_variant_compare_tsv_service` | 比對 TSV 檔中 zh_cn/zh_tw 欄位差異，輸出 CSV |

---

## UI 架構

三個 `ft.Card` 各自獨立，各自擁有路徑輸入 + 啟動按鈕，共用同一組 `progress_bar` + `log_view`。

`UntranslatedChecker` 已拆分為獨立元件（PR1），其實例由 QCView持有並佈局在第一張卡片中。
