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
