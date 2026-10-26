# Minecraft 翻譯工作流程

> 本頁是使用者導覽，不取代各 service、parser、設定 schema 或 View 架構文件。若文件與目前程式碼或測試不一致，以程式碼與測試為準。

## 先分辨三種情境

專案不是只有一條固定的「Extract → Translate → Merge → Writeback」流程：

1. **一般／單頁工具**：使用者可分別執行 JAR 提取、語系合併、機器翻譯或專用格式翻譯，檢查中間輸出後再進入下一階段。
2. **一鍵製作／流水線**：`PipelineActions.one_click_steps()` 固定依序執行「抽取資源 → 語系比對 → 啟動翻譯 → 打包資源」。
3. **Mod 翻譯資料庫**：掃描與查詢是獨立的資料庫工作流；在合併或機器翻譯啟用相應設定與版本快照時，才會參與補譯或寫回。

這三者的輸入、輸出與生命週期不同，下面分開說明。

## 一般／單頁工具流程

### 1. JAR 提取

- **入口**：JAR 提取頁，服務層為 `app/services_impl/pipelines/extract_service.py`，核心 generator 在 `translation_tool/core/jar_processor.py`。
- **輸入**：含有模組 `.jar` 的來源資料夾。
- **輸出**：依模式寫入語言檔、Patchouli／book 檔案，實際資料夾名稱由設定和提取模式決定。
- **模式**：`lang`、`book`、`dual`；`dual` 由 actions 統一管理兩段提取的 session 與進度。
- **預覽**：預覽掃描和 JAR 探索也走 service 提供的 generator，不應把 UI 的資料夾拼接邏輯當成核心規則。

詳細的路徑準備、取消和輸出契約見 [`../developer-guide/jar-pipeline.md`](../developer-guide/jar-pipeline.md) 與
[`../developer-guide/views/EXTRACTOR_VIEW_ARCHITECTURE.md`](../developer-guide/views/EXTRACTOR_VIEW_ARCHITECTURE.md)。

### 2. 語系合併

- **入口**：語系合併頁或流水線的第二步；服務層為 `app/services_impl/pipelines/merge_service.py`，核心在 `translation_tool/core/lang_merger.py`。
- **輸入**：可為資料夾或 ZIP。資料夾走 `run_merge_folder_batch_service`；ZIP 走 `run_merge_zip_batch_service`。
- **處理**：依設定處理 `en_us`、`zh_cn`、`zh_tw`，保留既有繁中，將需要翻譯的內容整理到待翻譯輸出；Patchouli 和其他輸出有各自的子目錄。
- **資料庫參與**：啟用 Mod DB 且有目標版本時，合併時每個條目先查資料庫（鍵值與英文原文都相同才命中），命中的譯文優先於 jar 自帶 zh_tw 與 zh_cn；輸出資料夾裡已有的譯文不被覆蓋。譯文來源順序：輸出既有譯文 → 資料庫 → jar 的 zh_tw → zh_cn 簡轉繁 → 待翻譯。不存在資料庫或沒有版本時會依設定快照略過。

不要把資料夾包成 ZIP 清單傳給 ZIP service；一鍵流程明確以 folder service 處理抽取結果。
輸出與錯誤摘要見 [`../developer-guide/views/MERGE_VIEW_ARCHITECTURE.md`](../developer-guide/views/MERGE_VIEW_ARCHITECTURE.md)。

### 3. 機器翻譯

- **入口**：機器翻譯頁，服務層為 `app/services_impl/pipelines/lm_service.py`，核心入口為 `translation_tool/core/lm_translator.py` 的 `translate_directory_generator()`。
- **輸入**：指定的待翻譯資料夾；批次、profile、模型、token 預算、快取和重試由設定與頁面選項共同決定。
- **輸出**：翻譯結果寫到指定輸出資料夾；已完成內容可透過快取分流，Mod DB 是否查詢或寫回由設定和任務快照決定。
- **取消／錯誤**：service 以 `TaskSession` 回報進度、日誌和 terminal 狀態；取消不等於把已落盤的批次回復成原文。

LMView 的 UI／session 邊界見 [`../developer-guide/views/LM_VIEW_ARCHITECTURE.md`](../developer-guide/views/LM_VIEW_ARCHITECTURE.md)，
快取與續跑契約見 [`../developer-guide/cache.md`](../developer-guide/cache.md) 和
[`../developer-guide/resume-capability.md`](../developer-guide/resume-capability.md)。

### 4. 專用格式翻譯

「任務翻譯」頁不是 JAR 提取後必經的下一步，而是針對既有輸入資料的專用流程：

- FTB Quests：`app/services_impl/pipelines/ftb_service.py` 與 `translation_tool/plugins/ftbquests/`。
- KubeJS Tooltip：`app/services_impl/pipelines/kubejs_service.py` 與 `translation_tool/plugins/kubejs/`。
- Markdown：`app/services_impl/pipelines/md_service.py` 與 `translation_tool/plugins/md/`。

這些服務各自保存步驟選項和續跑資訊；不要用 LMView 或一鍵流水線的輸出路徑推測它們的資料格式。

## 一鍵製作／流水線

`app/views/pipeline/pipeline_actions.py` 的 `one_click_steps()` 建立四個步驟，
`PipelineRunner` 依序執行；任一步驟失敗或取消，就不執行後續步驟。

| 步驟 | 實際邊界 | 重要輸入／輸出 |
|---|---|---|
| 1. 抽取資源 | `extract_lang`／`extract_book` service；`dual` 共用 session | mods 資料夾 → `jar_mod_extract` 下的 lang／book 輸出 |
| 2. 語系比對 | 一律呼叫 folder merge service，分別處理 lang 與 book 來源 | 抽取輸出 → `locale_sort` 的整理輸出 |
| 3. 啟動翻譯 | 依序翻譯有檔案的 lang／Patchouli 待翻譯目錄，共用此步驟 session | 整理輸出 → `lm_translate` 的翻譯輸出 |
| 4. 打包資源 | 先以 `build_bundle_staging()` 合併來源，再呼叫 bundler service | 既有整理譯文 + 新翻譯 → staging → ZIP |

路徑名稱由 `app/views/pipeline/pipeline_config.py` 從 `config.json` 的相關設定推導；打包 staging
會疊加來源、合併同路徑 JSON、移除殘留檔案，不能以「最後一個來源整個覆蓋前面來源」描述。
完整的 worker、取消、session terminal 與 service update 契約見
[`../developer-guide/views/PIPELINE_VIEW_ARCHITECTURE.md`](../developer-guide/views/PIPELINE_VIEW_ARCHITECTURE.md)。

## Mod 翻譯資料庫流程

Mod DB 有自己的掃描、版本、來源優先序、校對、批次翻譯和寫回規則：

1. 在 Mod 資料庫頁選擇資料夾與目標 Minecraft 版本，掃描 JAR／ZIP 並匯入 SQLite。
2. 以模組、版本、格式和條目狀態查詢或校對；寫回遵守資料庫 repository 的只新增／同步規則。
3. 合併或 LM 翻譯任務若啟用 DB 且帶有有效版本快照，才會查詢可用譯文；沒有可用資料時回到一般待翻譯／快取路徑。

使用者操作與資料契約見 [`mod-translation-db.md`](mod-translation-db.md)；設定鍵以
[`../reference/configuration.md`](../reference/configuration.md) 和
`translation_tool/translation_db/settings.py` 為準。

## 簡繁轉換與替換規則

簡中文字先經 OpenCC `s2twp` 轉成繁體，再套用專案根目錄 `replace_rules.json` 的替換規則，換成台灣官方 Minecraft 用語（例如「刷怪蛋」→「生怪蛋」、「臺階」→「半磚」）。
規則以官方 zh_cn／zh_tw 為準，採最長匹配、單次掃描：同一段文字只替換一次，較長的詞優先於較短的詞。
可在「替換規則」頁編輯與測試；規則的撰寫原則與驗證工具見 [`../developer-guide/replace-rules.md`](../developer-guide/replace-rules.md)。
原詞寫成 `^文字$` 是「整段完全相符」規則：只在整段文字剛好等於這段文字時替換，用來處理單獨名稱與複合詞譯法不同的詞
（單獨的「紅樹木」是方塊「紅樹林木塊」，「紅樹木椅子」則是「紅樹林木椅子」）。
規則也會套用在模組自帶的繁中，所以不會改動已經是官方繁中的文字；簡中與繁中同字不同義的詞（例如「紅磚」「蜂巢」「力量」）
不寫規則，這些名稱保持原樣。

### 替換規則頁的操作

- 規則依診斷結果在該列顯示警示圖示（交叉引用、譯詞含自己的原詞、原詞重複、前後空白、原詞不會出現在 `s2twp` 轉換後的文字，例如單字「岩」會先被轉成「巖」），懸停可看原因；警示不阻擋儲存。勾選「只看有問題（N）」只列出有警示的規則，可與搜尋同時使用。
- 搜尋或「只看有問題」啟用時，刪除、新增、排序與重新載入都以全部規則為準：刪除的提示上有「復原」，按下會把規則放回原位；新增會先清除篩選並跳到最後一頁的新列。排序只改變畫面順序，要按「儲存」才寫入檔案。
- 有未儲存的變更時，頁面顯示「有未儲存的變更」；重新載入會覆蓋全部編輯，所以先確認。儲存前會檢查每條規則（空的原詞、無法編譯的正規表示式、重複原詞、錯誤的群組參照），有錯就不寫入並跳到第一個錯誤列。

## 圖示預覽

「圖示預覽」頁掃描模組資料夾（JAR 目錄或已解包資料夾）並列出每個模組的語言條目與繁中譯文。掃描 JAR 目錄時，模組清單會邊掃描邊出現（筆數先顯示、標示「掃描中」），掃描完成後才有未翻譯數；JAR 很多時掃描用行程池平行進行，結果存成磁碟快取，同一個資料夾再次開啟時只讀索引與當頁模組。

圖示以像素銳利的 128 px 圖顯示：動畫貼圖取第一格，灰階植物貼圖會染色，完整方塊顯示等角三面預覽；滑鼠懸停可看貼圖、模型與來源 JAR。item／block 等內容類 key 找不到圖示時顯示共用佔位圖。原版貼圖缺漏時不會借用其他模組的同名貼圖。

## 常見判斷

- **想逐步檢查中間檔案**：使用單頁工具，先確認提取輸出，再執行 folder／ZIP 合併，最後指定翻譯輸入和輸出。
- **想一次產出資源包**：使用一鍵流水線，但應以四個步驟的狀態和輸出檔案共同判斷成功。
- **已有過往譯文**：確認快取或 Mod DB 的設定與版本是否有效；不要只看到原文仍在待翻譯目錄就推論資料庫沒有資料。
- **任務中斷**：依 [`../developer-guide/resume-capability.md`](../developer-guide/resume-capability.md) 判斷該工具的續跑語意；不同工具不是同一個 checkpoint 協定。
