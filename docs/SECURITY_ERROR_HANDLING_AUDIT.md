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

## PR-2 補充稽核（#125 / #135）

### #125 行為層稽核

| 面向 | 發現 | 處理 |
|---|---|---|
| 金鑰格式錯誤訊息 | `validate_api_keys` / `validate_api_keys_from_ui` 的 9 處訊息與 `log_error` 以 `{k!r}` 輸出**完整原始金鑰**（使用者貼錯欄位或貼殘缺金鑰時，會顯示在 UI 提示並寫入 log） | 全部改用 `mask_key()`，並加測試 |
| 儲存設定失敗 | `config_actions.save_config_from_view` 以 `traceback.print_exc()` 輸出到 stderr，訊息可能帶有使用者輸入 | 改為 `logger.error` 並經 `redact_text()` |
| 設定讀取失敗 | `config_manager` 以 `print` 輸出錯誤 | 改為 `log.error`（訊息只含路徑與解析錯誤） |
| 剪貼簿 / diagnostics 匯出 | 專案內沒有找到（grep `clipboard`、`export.*log`、`diagnostic`） | 不適用；日後若新增，須先通過 `redact_text()` / `redact_mapping()` |
| UI 金鑰欄位 | 設定頁以 `password=True` 預設遮蔽，可手動顯示 | 維持 |
| 明文 `config.json` | 沒有改變。OS 憑證庫遷移屬後續項目，另開 issue | 未處理（已知取捨） |

稽核方式為靜態 grep，未涵蓋所有第三方例外的 `repr`。未處理的項目不代表沒有風險。

### #135 例外與 print 盤點

量測指令：`ruff check app translation_tool main.py --select BLE001,S110,S112,T201`。分類：**修** = 補 log；**保留** = 刻意保留並加 `# noqa: BLE001 - 原因`；**待 #150** = 在 PR #150 會重寫的檔案，等它合併後再處理，避免衝突。

| 位置 | 類別 | 處理 |
|---|---|---|
| `lang_merge_pending.py`：無法讀取的待翻譯檔 | 修 | 補 `log_warning` 後略過 |
| `cache_overview.py`：讀取作用中分片失敗 | 修 | 補 `log.warning` |
| `jar_processor_preview.py`：worker 數設定讀取失敗 | 修 | 補 `log.debug` 後使用預設值 |
| `kubejs_translator_io.py`：讀取失敗視為空檔 | 修 | 補 `log.warning`（原本完全無聲） |
| `color_char_checker.py`：略過無法讀取的檔案 | 修 | 補 `log.warning`（原本完全無聲） |
| `lang_merge_extracted_assets.py`：`session.add_log` 保護（7 處） | 保留 | 集中為 `_safe_session_log`，失敗只留 debug |
| `md_translation_progress.py`、`log_unit.py`（`progress`）：UI 進度回報 | 保留 | 進度失敗不可中斷任務，加說明 |
| `log_unit.py`：logging 自身失敗（3 處） | 保留 | 失敗不可再記錄（會遞迴）也不可中斷呼叫端，加說明 |
| `md_translation_stats.py`、`safe_json_loader.py`：逐檔／逐編碼嘗試 | 保留 | 外部不可信輸入，壞檔略過，加說明 |
| `app/services.py`、`lookup_service.py`、各 checker、`variant_comparator*.py`、`jar_processor.py`、`output_bundler.py`、`lang_merge_content_patchers.py`、`lm_translator_scan.py`、`ftbquests_snbt_*.py`、兩個 pipeline dialog：已記錄或已回報給呼叫端的邊界 | 保留 | 加 `# noqa: BLE001 - 原因` |
| `main.py`、`icon_preview_cache.py`、`utils/exceptions.py`、`config_manager.py`：`print` | 修 | 改為 logging（含對應測試更新） |
| `md_extract_qa.py`、`md_inject_qa.py`：約 36 個 `print` | 保留 | 命令列 QA 工具的輸出；不經過 logging 是刻意的 |
| `ftbquests_lmtranslator.py`、`md_lmtranslator.py`、`kubejs_tooltip_lmtranslator.py`：約 25 處 `except Exception`（含 unshield 失敗、recorder 記錄失敗、統計失敗等無聲路徑） | **待 #150** | PR #150 大幅重寫這三個檔案；先前在這裡做的修改已移出本 PR，等 #150 合併後再補一個小 PR |

自動修正（`ruff --fix`）曾移除 `output_bundler.py` 內被測試 monkeypatch 的 `load_config` 匯入而使 7 個測試失敗，已以 `# noqa: F401` 保留並說明。動到的每個檔案都已清掉其既有 ruff 問題（CI 對變更檔案是全檔檢查）。
