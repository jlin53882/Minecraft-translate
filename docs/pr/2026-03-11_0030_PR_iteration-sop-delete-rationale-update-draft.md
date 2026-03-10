# PR Title
ITERATION_SOP.md v1.1 更新記錄

# Purpose
這份文件記錄本次 `ITERATION_SOP.md` 的實體化與 v1.1 更新內容，作為 SOP 更新的討論與落地紀錄。

## 釐清
- SOP 規則**已存在**：今天的對話已明確建立 `ITERATION_SOP.md v1.0` 的核心規則。
- 先前缺的是**實體檔案**：在本次更新前，repo / workspace 內沒有可驗證的 `ITERATION_SOP.md` 檔案。
- 因此這次不是覆寫既有 repo 檔案，而是：
  1. 在 OpenClaw workspace 建立實體 `ITERATION_SOP.md`
  2. 在 repo 根目錄新增正式版 `ITERATION_SOP.md`
  3. 同步補入新的「刪除/移除/替換說明標準」

## 本次確認結果
- **版本 v1.1**：已確認採用
- **實體同步進 repo**：已確認，放在 **repo 根目錄**
- **刪除說明章節位置**：已確認，若 PR 有刪除項目，必須**固定放在 `## Phase 1 完成清單` 後面**

## 目前實體位置
- OpenClaw workspace：`C:\Users\admin\.openclaw\workspace\ITERATION_SOP.md`
- Repo 根目錄：`C:\Users\admin\Desktop\Minecraft_translator_flet\ITERATION_SOP.md`

---

# 本次更新重點（v1.1）

## 1. 將 SOP 規則落成實體檔案
把原本只存在對話與輸出中的規則，正式落成 `ITERATION_SOP.md`，避免對話變長後遺失上下文。

## 2. 維持既有 v1.0 核心規則
保留以下核心要求：
- 不得自行宣稱完成
- 不得捏造測試或驗證輸出
- 嚴格分成 Phase 1 / Phase 2
- 沒有 `## Validation checklist` 就停下來
- PR / 設計稿 / 設計討論都用 `.md` 檔保存

## 3. 補入刪除/移除/替換說明標準
凡 PR 涉及刪除、移除、停用、替換任何程式碼、檔案、import、wrapper、欄位、函式、模組，PR 文件都必須逐項補上：
1. 為什麼改
2. 為什麼能刪
3. 目前誰在用 / 沒人在用
4. 替代路徑是什麼
5. 風險是什麼
6. 我是怎麼驗證的

並且：
- 若資訊不足，必須標記 `[需確認]`
- 不得用模糊語句帶過
- 若有刪除項目，章節位置固定放在 `## Phase 1 完成清單` 後面

## 4. 更新 PR Template
PR Template 已同步補上：
- `## Phase 1 完成清單`
- `## 刪除/移除/替換說明（若有，固定放這裡）`
- `## Test result`

讓之後照模板寫時，不需要每次靠口頭提醒。

---

# 建議收錄內容（摘要）

## 總原則
- 完成判斷必須有實際指令輸出
- 測試/驗證輸出不得捏造
- Phase 1 / Phase 2 必須分開
- 沒有 Validation checklist 就停下
- 設計稿與 PR 文件一律用 `.md` 保存

## Phase 1
- 先讀設計文件
- 確認範圍與不做事項
- 修改前先建立可回退備份
- 完成後先列 `## Phase 1 完成清單`

## Phase 2
- 逐條實跑 `## Validation checklist`
- 完整貼出實際輸出
- 驗證完再寫 PR 文件
- PR 文件固定放 `docs/pr/YYYY-MM-DD_HHmm_PR_<topic>.md`

## 刪除說明標準（新增）
- 每個刪除項目都要補六欄位說明
- 章節固定放在 `## Phase 1 完成清單` 後面
- 資訊不足必須標 `[需確認]`

---

# 結論
這次更新的重點不是重寫 SOP，而是把原本已經確立的作業規則正式落成實體檔案，並把「刪除/移除/替換說明標準」制度化，避免之後再靠對話記憶維持。
