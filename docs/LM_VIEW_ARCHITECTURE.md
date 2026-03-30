# LM_VIEW_ARCHITECTURE.md

## 定位

LMView 是 **LM（Large Model）翻譯執行頁**，用於對已提取的 assets 資料夾進行批量 AI 翻譯。屬於翻譯管線的翻譯環節。

---

## 主要功能

| 選項 | 說明 |
|------|------|
| `input_path` | 要翻譯的 assets 資料夾（必填） |
| `output_path` | 輸出資料夾（留空則用預設名稱） |
| `dry_run_switch` | Dry-run 模式，只分析不發 API |
| `export_lang_checkbox` | 輸出 `.lang` 檔而非 `.json` |
| `write_new_cache_switch` | 每次 API 回傳單獨寫入一筆快取 |

---

## 與 cache_manager 的關係

LMView 本身**不直接操作 cache_manager**，但透過 `write_new_cache_switch` 控制是否寫入快取。實際快取讀寫由 `lm_translator.translate_directory_generator`（`translation_tool.core.lm_translator`）處理。

---

## 呼叫鏈

```
start_clicked()
  → TaskSession.start()
  → run_lm_translation_service()    [lm_service.py]
      → lm_translate_gen()          [lm_translator.py]
          → API 翻譯 + 快取讀寫
```

UI Poller（`loop()` 執行緒）每 0.1 秒輪詢 `session.snapshot()`，以 **tail 模式**（最多 250 行）渲染日誌，防止 ListView 膨脹凍住。
