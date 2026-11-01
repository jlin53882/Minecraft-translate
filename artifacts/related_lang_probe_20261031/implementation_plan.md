# Implementation plan（僅在 precision 與 70k 性能門檻通過後才執行）

本計畫不是現在要執行的工作；目前判定為 MORE VALIDATION REQUIRED。

## 前置條件

1. 人工標註 ≥60 筆完成，precision@5 ≥ 80%（初期最低門檻）。
2. 正式 DB 上的 70k 性能實測 < 60 秒（preparation）與 UI 相關操作延遲目標 90 ms，分開量測。
3. 未審核候選永遠不成為權威；跨版本 high-confidence = 0；格式損壞 = 0。

## 整合範圍（同一個 PR，避免只改一半）

- **Builder**：在現有 Context 組裝後呼叫檢索，最多 5 筆新增 evidence，與既有 evidence ID 去重；既有 48 上限與高信心 evidence 優先保留。
- **Evidence validation**：新增 relation_type、confidence、limitations 欄位；unreviewed 強制降級；格式不一致降級。
- **Planner／快取**：快取鍵納入參考譯文的 revision 與 DB identity；參考譯文變更、刪除時失效；同 run 的 deterministic replay。
- **JSON sidecar**：新欄位需向後相容（舊 sidecar 缺欄位視為無 related evidence）。
- **Resume**：續跑時 related evidence 必須以同一 snapshot 重建，不得因資料變動而混用舊結果。
- **UI**：Related evidence 只顯示為參考，不得自動套用；標示 unreviewed。
- **Docs**：更新 Smart Review context 契約與 `docs/developer-guide` 相關頁面。
- **測試**：本實驗的 11 項邊界測試 + 整合測試（sidecar、resume、快取失效、48 上限）。

## 不做的事

- 不做 Embedding、向量 DB、全表 `%keyword%` 掃描。
- 不以 AI 自評取代人工金標。
- 不改正式 schema；若索引必要，需另外評估並寫入 migration 計畫。
