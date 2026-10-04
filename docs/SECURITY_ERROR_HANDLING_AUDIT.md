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

### #135 例外與 print 盤點（`ruff --select BLE001,S110,S112,T201`）

分類：**修** = 補 log；**保留** = 刻意保留並加說明；**待處理** = 尚未處理。

| 位置 | 類別 | 處理 |
|---|---|---|
| `ftbquests_lmtranslator.py`、`md_lmtranslator.py`：unshield 失敗 | 修 | 會讓輸出殘留保護標記，改為 `log_warning` |
| `ftbquests_lmtranslator.py`：recorder 記錄失敗（2 處）、預先統計失敗 | 修 | 補 `log_warning` |
| `lang_merge_pending.py`：無法讀取的待翻譯檔 | 修 | 補 `log_warning` 後略過 |
| `cache_overview.py`：讀取作用中分片失敗 | 修 | 補 `log.warning` |
| `jar_processor_preview.py`：讀取 worker 數設定失敗 | 修 | 補 `log.debug` 後使用預設值 |
| `lang_merge_extracted_assets.py`：`session.add_log` 保護（7 處） | 保留 | 集中為 `_safe_session_log`，失敗只留 debug |
| `ftbquests_lmtranslator.py`、`md_translation_progress.py`：`set_progress` 保護 | 保留 | UI 進度失敗不可中斷翻譯，加 noqa 說明 |
| `md_translation_stats.py`、`safe_json_loader.py`：逐檔 / 逐編碼嘗試 | 保留 | 外部不可信輸入，壞檔略過，加 noqa 說明 |
| 其餘 `except Exception`（已有 log 的邊界，約 22 處） | 保留 | 失敗已記錄，加 `# noqa: BLE001 - 原因` |
| `utils/log_unit.py`：logging 內部 `try/except/pass`（4 處） | 待處理 | logging 自身失敗不可遞迴記錄，需另行確認寫法 |
| `plugins/md/md_extract_qa.py`、`md_inject_qa.py`：`print`（約 36 處） | 待處理 | 看起來是命令列 QA 工具的輸出，需確認是否仍被使用 |
| `main.py`、`core/icon_preview_cache.py`、`utils/exceptions.py`：`print` | 待處理 | 各 1 處 |
| 其他檔案的 BLE001 | 待處理 | 本 PR 只處理高風險路徑上的檔案 |

本表只涵蓋 PR-2 動到的檔案與上述 ruff 規則；`except Exception` 的完整盤點尚未完成。
