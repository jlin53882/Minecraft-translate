# Flet UI 重新設計稿（僅設計，尚未實作）

「MC 繁化工坊」全部畫面的視覺設計稿。本目錄只有圖片，**不含任何程式碼變更**；
後續是否實作、如何拆 PR 另行決定。

- 設計方向：Deepslate & Emerald（深板岩底色、祖母綠主色），深色為主、淺色完整對應
- 字型：Noto Sans TC（內文）、JetBrains Mono（key / 路徑 / 日誌）
- 視窗基準：1440 × 920（圖檔為 1.5× 輸出）
- 畫面中的模組名稱、數字、日誌皆為示意資料

## 總覽

| 檔案 | 內容 |
|---|---|
| `16_設計規範_DesignSystem.png` | 色彩、字型、按鈕、表單、進度、像素圖示、版面 |
| `18_資訊架構與使用流程.png` | 12 個平鋪選單 → 4 大區分組；全域元件；使用旅程 |

## 頁面

| 檔案 | 對應現有 View |
|---|---|
| `01_工作台_Dashboard.png` | （新增）工作台首頁 |
| `02_一鍵流水線_Pipeline.png` | `pipeline/pipeline_view.py` |
| `03_JAR提取_Extractor.png` | `extractor_view.py` |
| `04_語系合併_Merge.png` | `merge_view.py` |
| `05_機器翻譯_LM.png` | `lm_view.py` |
| `06_任務翻譯_Task.png` | `translation_view.py` |
| `07_QC檢驗.png` | `qc_view.py` |
| `08_翻譯校對_Review.png` | `icon_preview_view.py` |
| `09a_快取管理_總覽.png` / `09b_快取管理_查詢.png` | `cache_view.py` |
| `10_替換規則_Rules.png` | `rules_view.py` |
| `11_學名查詢_Lookup.png` | `lookup_view.py` |
| `12_資源包打包_Bundler.png` | `bundler_view.py` |
| `13_設定_Config.png` | `config_view.py` |

## 共用畫面

| 檔案 | 內容 |
|---|---|
| `14_快速跳轉_CommandPalette.png` | Ctrl+P 快速跳轉面板 |
| `15_淺色主題_工作台.png` | 淺色主題範例 |
| `17_對話框與回饋狀態.png` | 完成 / 確認 / 錯誤對話框、空狀態、載入、Toast、快捷鍵 |
