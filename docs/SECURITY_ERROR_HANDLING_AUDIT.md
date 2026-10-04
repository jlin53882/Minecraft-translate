# PR-4 安全與例外處理稽核

## 已落地的契約

- API key 只放在 `x-goog-api-key` header；URL、HTTP 例外文字、遠端 response body 與非 JSON 回應診斷都經過 redaction。
- `redact_text()` 會遮蔽 Google API key、Bearer token，以及常見 `api_key` / `authorization` / `password` / `secret` / `token` 欄位；`redact_mapping()` 適合保留診斷結構的 JSON。
- 快取搜尋索引重建現在回傳成功／失敗；呼叫端不可把「已記錄錯誤」誤當成成功。
- 翻譯共用迴圈檢查快取寫入與落盤結果。快取未能 durable 時，任務回報 `FAILED`，不再宣稱完成。
- `config.json` 仍是明文檔案，依既有設計由使用者自行保護且不應提交版本庫；本 PR 沒有假裝已提供 OS credential store。

## 高風險路徑檢查結果

| 路徑 | 處理 | 備註 |
|---|---|---|
| LM API request / response | 已修 | response body 限長且遮蔽 secret |
| translator model fallback | 已修 | HTTP error 判斷使用已遮蔽訊息 |
| cache write / search rebuild | 已修 | failure 以明確布林值往上傳 |
| config read/write | 保留邊界 | 原子寫入與驗證已存在；錯誤仍需避免把設定內容寫入 log |
| extractor / merge / plugin exceptions | 分類保留 | 不機械刪除 broad catch；各流程需維持 partial-output 與 cancellation 契約 |

## 明確未納入本 PR 的債務

Ruff 報出的全部 BLE001、S110、S112、print 與 legacy comment 不等於同一種安全漏洞。剩餘項目需依 #135 的路徑、資料敏感度、恢復語意逐項處理；本 PR 不以「全數消除 lint」冒充完成例外稽核。

## 維護規則

1. 新增錯誤訊息前先通過 `redact_text()`；若需要結構化診斷，先 `redact_mapping()` 再序列化。
2. 任何「回傳空值但記 log」的 failure path 都必須說明呼叫端如何知道失敗；能回傳 bool 就不要只吞例外。
3. broad catch 只有在能保留取消、partial output 或 fallback 契約時才保留，並加入測試／註解說明原因。

## 機密輸出出口稽核（#125）

原則：不再逐一檢查每個呼叫點，而是在**所有會輸出文字的出口**統一遮蔽，並以測試證明。

遮蔽的內容（`translation_tool/utils/redaction.py`）：
- **已知機密**：`config_manager` 載入設定時，把使用者的 `lm_translator.keys` 登錄為已知機密（佔位字串 `YOUR_…` 與長度 < 8 的值不登錄）。任何格式的金鑰，只要原樣出現在文字中就會被遮蔽；舊金鑰保留在登錄表中，因為舊金鑰仍可能出現在殘留的例外訊息裡。
- **格式遮蔽**：Google API key（`AIza…`）與 `Bearer` token。
- 全域出口使用 `redact_secrets()`（高精確度，不套用欄位名稱規則，避免把一般訊息裡的 `token: 123` 誤遮蔽）；HTTP 錯誤內容等診斷訊息另用 `redact_text()`（含欄位名稱規則）。

| 出口 | 狀態 | 做法 | 測試 |
|---|---|---|---|
| 日誌檔、終端機（`setup_logging` 的 handler） | 已遮蔽 | `RedactingFormatter`：格式化後（含 traceback）再遮蔽 | `test_log_file_written_by_setup_logging_is_redacted` |
| UI 日誌（`UI_LOG_HANDLER` → `TaskSession`） | 已遮蔽 | handler 內遮蔽；formatter 也是 `RedactingFormatter` | `test_ui_log_handler_masks_before_reaching_the_session` |
| `TaskSession.add_log()`（不經 logger 的直接呼叫） | 已遮蔽 | 總出口處遮蔽 | `test_task_session_add_log_masks_direct_calls` |
| 提示訊息 `show_snack()` | 已遮蔽 | 總出口處遮蔽 | `test_snackbar_message_is_masked` |
| 錯誤記錄 `errors_*.log` | 已遮蔽 | 訊息、context、traceback 寫入前遮蔽 | `test_error_log_file_is_masked` |
| 金鑰格式錯誤訊息（`validate_api_keys*`） | 已修 | 9 處原本輸出完整原始金鑰，改用 `mask_key()` | `test_api_key_redaction_in_errors.py` |
| 儲存設定失敗的 traceback | 已修 | 經 `redact_text()` 後寫入 log | 同上 |
| LM API 的 HTTP 錯誤內容 | 已遮蔽 | 擲出前 `redact_text()`（PR #143） | `test_pr45_safety_and_config_contracts.py` |
| 金鑰在請求中的位置 | 無問題 | 只放在 `x-goog-api-key` header，不在 URL | — |
| 翻譯紀錄 `translation_map.json/csv`、checkpoint、快取分片 | 無機密欄位 | 紀錄只含原文／譯文／路徑／指紋；checkpoint 只含進度與指紋 | 讀碼確認 |
| `print` 輸出 | 無機密 | 以 grep 確認沒有 print 金鑰／token／授權資料（QA 命令列工具只印檔案路徑與統計） | 讀碼確認 |
| 剪貼簿、診斷匯出 | 不存在 | 專案內沒有；日後新增必須先經 `redact_secrets()` | — |
| UI 金鑰欄位 | 設計如此 | `password=True` 預設遮蔽，可手動顯示 | — |
| `config.json`、`config.json.pre-sync.bak` | **明文（設計取捨）** | 見下方 | — |

### config.json 明文儲存（已定位為本 issue 範圍外）

`config.json` 是使用者自己的檔案，使用者可以直接編輯；金鑰以明文存放，`.gitignore` 已排除，文件與設定頁都提醒不要分享。設定同步（`sync_missing_config_keys`）會留下 `config.json.pre-sync.bak`，敏感度與 `config.json` 相同。**導入 OS 憑證庫（Windows Credential Manager 等）屬於獨立的設計決策**（新增依賴、影響 Nuitka 打包與舊設定遷移），不在 #125 的第一階段範圍，應另開 issue。

### 仍存在的限制

- 遮蔽只能處理「已登錄的金鑰」與「已知格式」；來源不明、格式不同又沒登錄的機密（例如使用者貼在別處的字串）不會被遮蔽。
- 第三方程式（Flet 客戶端、Python 直譯器本身）直接寫到 stderr 的內容不經過這些出口。
- 靜態與測試涵蓋專案自己的輸出出口；未對每個第三方例外的 `repr` 逐一驗證，但它們最終都會經過上述 logging／UI 出口。

## 例外與 print 盤點（#135）

### 如何盤點與強制

量測：`ruff check app translation_tool main.py --select BLE001,S110,S112,T201`。由 `tests/test_exception_inventory.py` 強制：

1. **沒有說明的寬鬆例外、無聲例外、非工具程式的 `print` 都會讓測試失敗**；唯一的例外清單是兩個命令列 QA 工具（`md_extract_qa.py`、`md_inject_qa.py`，stdout 即產品介面）。
2. **逐項盤點**：`docs/EXCEPTION_INVENTORY.md` 由 `tools/gen_exception_inventory.py` 產生，列出全部 258 個寬鬆例外豁免（BLE001／S110／S112）的位置、函式、規則、分類與原因／處理；`test_inventory_document_matches_the_code` 檢查整份文件與 `render(collect())` 逐字一致（每列的檔案、函式、規則、分類、原因都會比對；文件不含行號，避免普通修改造成漂移）。分類：已記錄／回報 211、UI／畫面保護 32、盡力而為（靜默）15。
3. **棘輪**：258 個中 113 個已在程式碼內寫明原因，其餘 145 個（處理本身已有紀錄／回報，或屬 UI／icon 保護、進度回報、復原失敗時以原始例外為準）記錄在測試的 `UNEXPLAINED_NOQA_BASELINE`（逐檔數量），**只能減少、不能增加**；補上原因後請調降基準並重新產生盤點文件。沒有資料相關的無聲路徑留在這 145 個之中——那些已在前面補了日誌。

### 分類與處理

| 類別 | 處理 |
|---|---|
| **資料相關的無聲失敗**（會讓輸出殘缺卻沒有紀錄） | **已補 log**：unshield 失敗（FTB／MD）、recorder 記錄失敗、批次刷新 fallback、預先統計、待翻譯檔無法讀取、快取分片與分片序號讀取、checkpoint 損毀、隔離檔寫入失敗、強制輪替分片、KubeJS 翻譯檔與統計讀取、色碼檢查、worker 數設定、icon 預覽失敗、開啟資料夾失敗 |
| **UI 保護**（`session.add_log`、進度、狀態回報、畫面更新） | 保留並說明原因：失敗不可中斷任務；`session.add_log` 集中為 `_safe_session_log` |
| **logging 自身失敗** | 保留並說明原因：不可再記錄（會遞迴）也不可中斷呼叫端 |
| **外部不可信輸入的逐檔／逐編碼嘗試** | 保留並說明原因：壞檔略過，並視情況留 debug 紀錄 |
| **已有紀錄／回報的邊界**（多數 service／UI 層） | 保留；已寫原因者標在原處，其餘列入棘輪 |
| **`print`** | `main.py`、`icon_preview_cache.py`、`exceptions.py`、`config_manager.py` 改為 logging；QA 命令列工具刻意保留 |

### TODO、legacy 與相容註解

| 項目 | 分類 | 處理 |
|---|---|---|
| `lm_translator_main.py` 兩個 `TODO` 加被註解掉的死碼 | 過期 TODO | 已刪除 |
| `lm_config_rules.py` 的 `rotate_key_index()`、`is_api_keys_exhausted()` | 舊 API，**專案內沒有任何呼叫端** | 保留（公開介面變更不在本 PR 範圍）；列為可刪除候選，註解已說明「請勿用於翻譯請求流程」 |
| `get_current_key_index()`、`reset_key_index()` | 舊 API，仍有呼叫端 | 保留 |
| `app/services.py`、`services_impl/*`、`lang_merge_content`、`md_translation_assembly`、`lm_translator_shared`、`jar_processor` 的「相容 façade／re-export」 | 刻意的相容層 | 保留；移除屬 #114 / #121 / #136 的重構範圍 |
| `app/views/__init__.py` 的舊 alias | 已有追蹤 | #121（`theme.py` 與 `components.py` 已移除） |
| `extractor.target_language`、`translator.cjk_ratio_threshold` | 歷史相容欄位 | 保留讀取相容，設定頁不提供；schema 內標示原因 |
| 其餘「相容舊測試／舊呼叫」的註解（`cache_view`、`config_actions`、`task_session` 等） | 仍有測試或呼叫端依賴 | 保留 |

本表不是逐行清單：約 60 處相容／legacy 註解以類別歸納，個別項目是否可刪需要確認呼叫端，不在本 PR 範圍。
