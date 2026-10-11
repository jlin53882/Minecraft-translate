# 常見排查

## 啟動或設定問題

- 確認使用 Python `>=3.12`、已執行 `uv sync`，且 `config.json` 是由目前的
  `config.example.json` 建立。設定 schema 的單一來源是
  [`translation_tool/utils/config_schema.py`](../../translation_tool/utils/config_schema.py)。
- Gemini 翻譯需要在 `lm_translator.keys` 放入有效 Key，並在 `lm_translator.models` 啟用要使用的模型；
  Key 會以明文存在本機設定檔，不要提交或分享。
- 設定改動不一定立即影響已開啟頁面或已開始的任務；逐項套用時機見
  [`../reference/configuration-apply-timing.md`](../reference/configuration-apply-timing.md)。

## 流程沒有產生預期輸出

- 先確認選取的是正確的來源資料夾、JAR 或 ZIP，並檢查頁面顯示的輸入／輸出路徑。
- 一鍵流程中資料夾型抽取結果會走 folder merge service，不是 ZIP merge service；各階段輸入輸出與失敗判定見
  [`../developer-guide/views/PIPELINE_VIEW_ARCHITECTURE.md`](../developer-guide/views/PIPELINE_VIEW_ARCHITECTURE.md)。
- 若要確認某個檔案是否因語系整理進入待翻譯資料夾，先看語系合併輸出，再檢查
  [`translation-workflow.md`](translation-workflow.md) 描述的資料流。

## 任務中斷、關閉或續跑

任務的取消、落盤與續跑不是所有工具共用同一個 checkpoint。機器翻譯、專用格式翻譯與一鍵流程的界線，
以及哪些情況只能重新執行，見 [`../developer-guide/resume-capability.md`](../developer-guide/resume-capability.md)；
長任務生命週期契約見 [`../developer-guide/operation-lifecycle.md`](../developer-guide/operation-lifecycle.md)。

## Windows 或 Web 模式差異

- Windows 打包版、檔案落盤和強制結束的驗證清單見 [`windows-verification.md`](windows-verification.md)。
- Flet Web 模式的資料夾選擇與主機路徑限制見 [`../reference/web-mode-limitations.md`](../reference/web-mode-limitations.md)。

若以上資訊不足，請保留輸入設定、任務日誌、錯誤 traceback 與產生的輸出路徑；不要把 API Key 放進回報。
