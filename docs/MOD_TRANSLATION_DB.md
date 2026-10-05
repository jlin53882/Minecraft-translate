# Mod 翻譯資料庫（Translation DB）

分版本的翻譯記憶庫（SQLite）：掃描 mods 資料夾的 jar 建立、機器翻譯時優先查詢並把結果寫回、可在介面手動校對。
與「翻譯快取」並存、互不取代：快取記錄「AI 翻過這句」，資料庫記錄「這個模組的這個鍵值在各版本的譯文」。

## 資料模型

譯文跟**內容**綁定，版本只是標示內容出現在哪個遊戲版本。「相同內容」＝ `(類型, 模組, 鍵值, 原文)`。

| 資料表 | 說明 |
|---|---|
| `entry` | 某遊戲版本中的一個可翻譯項目：`(kind, mc_version, mod_id, key) → en_us`。`kind` 為 `lang` 或 `patchouli` |
| `translation` | 掛在 entry 上的譯文，每個來源最多一筆（唯一：`entry_id + source`） |
| `effective` | 每個 entry 依來源優先序選出的生效譯文；寫入時維護，讀取不必重算（優先序變更時開啟資料庫會自動重建） |
| `history` | 手動更新的異動記錄（可還原，同一次同步的版本共用 `batch`） |
| `src_change` | 「鍵值相同但原文已變動」而略過的記錄 |
| `scan_run` | 掃描記錄 |

來源代碼：0 AI 機翻、1 模組自帶繁中、2 簡中轉繁（OpenCC `s2twp`）、3 町宮字幕組、4 i18n 轉換、5 自訂補充、6 人工。
預設優先序：人工 > 町宮 > 自訂 > 自帶繁中 > i18n > 簡中轉繁 > AI；已校驗（`checker` 不為空）者永遠最優先。

### 身分（`translation_db/identity.py`）

- **lang**：`assets/<模組>/lang/en_us.json` 的頂層鍵值。
- **patchouli**：`(assets|data)/<模組>/<書籍目錄>/…/<語言>/…/檔案.json` 內的欄位，鍵值為
  「去掉 assets/data 與語言資料夾的相對路徑 + `#` + JSON 路徑」。書籍放在 `assets` 或 `data`、不同語言資料夾，都得到相同身分。
- 1.12 以前的 `.lang` 格式**不處理**。

## 寫入規則

| 動作 | 規則 |
|---|---|
| **掃描 jar** | 只新增、不覆蓋。條目已存在就跳過；既有條目缺少的譯文來源會補入；原文已變動（鍵值相同、原文不同）只記錄到 `src_change`，不改動 |
| **AI 翻譯寫回** | 只新增、不覆蓋（已有 AI 來源就不動）。目標版本沒有條目時直接建立；**其他版本中原文相同、完全沒有譯文的空白也一併補上**。快取命中的譯文也會補進資料庫 |
| **手動儲存** | 寫入「人工」來源（最高優先），並（可選）同步所有版本中**原文相同**的條目；原本的譯文來源保留，可從記錄還原 |
| **審核** | 不改文字，把目前譯文確認為人工來源（同樣會同步原文相同的版本） |

## 與翻譯流程的整合

`translate_directory_generator`（機器翻譯與一鍵流水線）的查詢順序為 **資料庫 → 翻譯快取 → AI**：

1. 資料庫：目標版本自己的譯文（原文須與資料庫 `en_us` 完全一致）；沒有時，沿用其他版本中原文相同的譯文（版本最接近者；可關閉）。
2. 快取、AI 與先前相同。
3. 翻譯完成（含取消前已完成的批次）才寫回資料庫；`dry-run` 不寫入；沒有翻成功而以原文回填的項目不寫入。

資料庫檔案不存在、設定關閉、或沒有指定目標版本時，整個功能等同不存在（翻譯流程不會建立資料庫檔案）。
Patchouli 與 lang 走同一個流程，只是身分不同。FTB Quests / KubeJS / Markdown 不使用資料庫。

## 設定（`translation_db`）

| 設定 | 預設 | 說明 |
|---|---|---|
| `enabled` | `true` | 翻譯時使用資料庫（機器翻譯頁可個別覆寫） |
| `path` | `mod_translation.db` | 相對路徑以資料目錄為基準 |
| `version` | 空 | 預設目標版本；**沒有指定版本就不會查詢或寫回**（機器翻譯頁可個別填寫） |
| `cross_version` | `true` | 允許沿用其他版本、原文相同的譯文 |
| `write_back` | `true` | 翻譯結果寫入資料庫 |
| `sync_manual` | `true` | 手動儲存時同步其他版本（頁面上也可個別關閉） |
| `priority` | 見上 | 來源優先序（每行一個名稱） |

## 程式結構

```
translation_tool/translation_db/
  schema.py      資料表、來源代碼、連線（WAL）
  models.py      對外資料結構
  identity.py    路徑 → (類型, 模組, 鍵值)；get_by_path
  repository.py  所有 SQL：掃描寫入、手動同步、還原、寫回、查詢
  scanner.py     讀 jar（含 META-INF/jarjar 內嵌 jar、Patchouli 書籍）並寫入
  resolver.py    翻譯流程用的查詢（TranslationResolver）與寫回緩衝（WriteBackBuffer）
  settings.py    讀取 translation_db 設定、開啟資料庫
translation_tool/core/lm_translator_db.py   目錄翻譯的銜接（開啟、分流、寫回）
app/services_impl/moddb_service.py          View 取用資料庫的服務層（View 不直接 import 引擎）
app/views/moddb_view.py + app/views/moddb/  總覽 / 條目校對 / 掃描匯入
```

入口：側欄「資料庫」群組的「Mod 資料庫」、`Ctrl+P` 快速跳轉、工作台卡片、機器翻譯頁與設定頁的選項。
掃描使用 `zip_safety` 的讀取預算（防 ZIP bomb），內嵌 jar 最多遞迴 3 層。

## 測試

`tests/test_translation_db_core.py`（資料庫、掃描、規則）、`tests/test_lm_translator_db.py`（與目錄翻譯整合）、
`tests/test_moddb_view.py`（各頁籤與服務層）。
