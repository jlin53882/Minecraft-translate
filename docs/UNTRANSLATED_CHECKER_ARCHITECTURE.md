# UntranslatedChecker 架構文件

## 定位與功能

**定位**：翻譯品質把關工具，檢測 en_us 與 zh_tw 之间未翻譯的翻譯鍵（Key）。

**功能**：
- 比較 en_us 與 zh_tw 兩組 JSON 語言檔
- 找出 zh_tw 中缺失的翻譯鍵
- 輸出未翻譯報告至指定資料夾

**使用時機**：翻譯完成後執行翻譯結果審查，確認沒有漏譯的 Key。

---

## 與 Translation Workflow 的關係

```
Translation Workflow（翻譯執行）
        │
        ▼
UntranslatedChecker（QC 把關）──→ 發現漏譯 Key ──→ 修補後重新翻譯
        │
        ▼
Output Bundler（打包成品）
```

UntranslatedChecker 屬於 Quality Control（QC）環節，位於翻譯完成後、打包前，用於確保翻譯完整性。

---

## 主要 UI 元件

| 元件 | 說明 |
|------|------|
| `en_dir` (TextField) | 英文來源資料夾路徑 |
| `tw_dir` (TextField) | 繁中來源資料夾路徑 |
| `out_dir` (TextField) | 報告輸出資料夾路徑 |
| `start_button` (ElevatedButton) | 開始檢查 |
| `FilePicker` (on page.overlay) | 資料夾選擇器 |

**任務執行**：依賴 `QCBase.task_worker()`，傳入 `run_untranslated_check_service` 函式與資料夾參數。
