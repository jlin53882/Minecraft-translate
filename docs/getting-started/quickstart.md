# 最短成功流程

## 使用一鍵流水線

1. 啟動 `uv run python main.py`。
2. 在「一鍵流水線」選擇含有模組 JAR 的來源資料夾與輸出資料夾。
3. 依需求設定語言代碼、合併選項、翻譯選項與輸出 ZIP 設定。
4. 執行後依序確認「抽取資源 → 語系比對 → 啟動翻譯 → 打包資源」四個步驟的狀態。

一鍵流程的實際輸入、輸出與 session 邊界以
[`developer-guide/views/PIPELINE_VIEW_ARCHITECTURE.md`](../developer-guide/views/PIPELINE_VIEW_ARCHITECTURE.md)
及其使用的 [`app/views/pipeline/pipeline_config.py`](../../app/views/pipeline/pipeline_config.py) 為準。

## 分開執行單頁工具

若要檢查中間產物或只執行一個階段，可在側欄分別使用：

- **JAR 提取**：把模組 JAR 內容提取成語言檔或 Patchouli 輸出。
- **語系合併**：將資料夾或 ZIP 的 `en_us`／`zh_cn`／`zh_tw` 內容整理成待翻譯資料與既有譯文。
- **機器翻譯**：翻譯指定目錄並寫入輸出；翻譯快取和 Mod DB 是否參與由目前設定與該頁選項決定。
- **任務翻譯**：處理 FTB Quests、KubeJS Tooltip 或 Markdown 的專用流程。
- **QC 檢驗**：在打包前檢查未翻譯條目、英文殘留或格式問題。

這些頁面不是一條固定的「Extract → Translate → Merge → Writeback」順序；完整差異與資料流見
[`../user-guide/translation-workflow.md`](../user-guide/translation-workflow.md)。

## 成功判斷與安全界線

- 以頁面進度、錯誤提示、輸出檔案和任務摘要共同判斷，不要只看單一日誌文字。
- 翻譯任務關閉視窗前會要求確認；中斷後是否能續跑、哪些內容已落盤見
  [`../developer-guide/resume-capability.md`](../developer-guide/resume-capability.md)。
- `config.json` 含敏感設定；輸出 ZIP 與中間產物請放在自己的工作目錄，避免誤提交。
