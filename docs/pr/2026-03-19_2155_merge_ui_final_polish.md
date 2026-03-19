# Merge UI 最後收尾調整清單｜定稿前最後一輪

> 日期：2026-03-19 21:55
> 目的：在目前已接近可交付的 UI 基礎上，做最後一輪 UX / 文案 / 視覺收尾
> 適用畫面：Merge 頁面目前新版（Files / Processing Options / Output and Action）

---

## 一、目前判斷

目前這版 UI 已經比前幾版好很多，方向正確，已接近可交付。

### 現階段狀態
- 架構：✅ 對
- 區塊分層：✅ 對
- 拖拉上傳區：✅ 對
- Processing Options 雙欄結構：✅ 可接受
- Output and Action：✅ 清楚

### 目前還差的不是功能，而是最後的 UI 收尾

---

## 二、最後 5 點必修收尾

# 1. 文案改成中文主介面

## 問題
目前畫面仍偏英文工程介面，像：
- `General Processing`
- `Patchouli Advanced`
- `Only process Patchouli 'zh_cn' Lang`
- `En_us Skipping Threshold`

這會讓整體產品感不一致，且閱讀成本偏高。

## 修改要求
主介面文案統一改成中文，英文若需要可保留在 tooltip 或次說明。

### 建議定稿
- `Files` → `檔案清單`
- `Processing Options` → `處理選項`
- `General Processing` → `一般處理`
- `Patchouli Advanced` → `Patchouli 進階`
- `Output and Action` → `輸出與執行`
- `Only process Lang files` → `只處理 Lang 檔案`
- `Process zh_cn files` → `處理 zh_cn 檔案`
- `Only process Patchouli zh_cn` → `只處理 Patchouli 的 zh_cn`
- `En_us Skipping Threshold` → `en_us 跳過門檻`
- `Start Process` → `開始處理`

---

# 2. Patchouli 區塊再拆成兩個子區塊

## 問題
目前右側 Patchouli 區塊雖然已比前面好，但仍然稍重：
- 開關 + 說明 + threshold 混在同一塊
- 視覺上還是像一大坨設定

## 修改要求
把右側區塊拆成兩段：

### A. Patchouli 處理模式
- `只處理 Patchouli 的 zh_cn`
- 一句短說明

### B. 跳過判斷
- `允許 zh_cn 觸發跳過 en_us`
- `en_us 跳過門檻`
- disabled note（必要時）

### 目標
讓使用者一眼分得出：
- 左邊是「處理哪些檔」
- 右邊是「怎麼判定跳過」

---

# 3. 所有說明文字再縮短 30~50%

## 問題
目前說明文字仍偏長，畫面有「規格說明書感」。

## 修改原則
每個設定只保留一句短說明，不超過 1 行半。

### 建議範例

#### `只處理 Lang 檔案`
改為：
`只處理語言檔，其他內容檔案略過。`

#### `處理 zh_cn 檔案`
改為：
`關閉後，所有 zh_cn 檔案會略過。`

#### `只處理 Patchouli 的 zh_cn`
改為：
`僅處理 Patchouli 的 zh_cn 語言檔。`

#### `允許 zh_cn 觸發跳過 en_us`
改為：
`zh_cn 達門檻時，跳過對應 en_us。`

#### `en_us 跳過門檻`
改為：
`預設 0.5，範圍 0.0 ~ 1.0。`

---

# 4. 若 `Process all files / Only process Lang files` 為互斥，請改控件型態

## 問題
如果目前畫面中的選項邏輯是互斥，但 UI 用兩個 switch / checkbox 表達，使用者會誤以為可以同時成立。

## 修改要求
如果這兩個模式是互斥的，請改成：
- radio group
- segmented control
- dropdown

### 建議優先
若 Flet 好做，優先用 `RadioGroup` 或 `SegmentedButton`。

### 若不是互斥
則需補明確說明，避免使用者混淆。

---

# 5. 底部 Output and Action 區塊做最後對齊收尾

## 問題
目前底部雖然已經清楚，但：
- Start button 視覺上略貼角落
- 輸出路徑欄位與操作按鈕仍可再更協調

## 修改要求
- Start button 與輸出欄位區塊保持更明確的對齊
- 保留明顯主按鈕感，但不要像孤立在角落
- Progress / status / action 的層次更一致

---

## 三、額外建議（非必修，但加分）

### 1. ZIP 清單空狀態提示
當沒有選擇任何 ZIP 時，可顯示簡短 placeholder：
- `尚未加入任何 ZIP 檔案`
- `可拖拉檔案進來，或按「新增 ZIP」選取`

### 2. Disabled note 改為更短版
目前建議統一用：
`需先開啟「處理 zh_cn 檔案」`

而不是一整句過長說明。

### 3. Threshold 欄位視覺再加強
- 輸入框稍大一點
- 與標題距離縮短
- 不要讓這塊看起來像 debug 欄位

---

## 四、驗收標準（最後一輪）

- [ ] 主介面改為中文文案
- [ ] Patchouli 區塊拆成兩個子區塊
- [ ] 所有說明文字縮短
- [ ] 若模式互斥，已改用互斥型控件
- [ ] Output and Action 區塊已做對齊收尾
- [ ] 整體看起來像正式設定頁，而不是工程後台表單

---

## 五、最終一句話（給工程師）

現在這版已經接近可交付，最後一輪請不要再大改架構，只要專注於：

> **中文化、減字、拆清 Patchouli 區塊、處理互斥控件、把底部收尾做好。**

完成後，這頁就可以視為定稿候選。