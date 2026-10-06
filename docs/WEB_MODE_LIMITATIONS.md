# Flet Web 模式的限制與擴充方向

> 適用版本：v0.6.0+（Flet 1.0.1）  
> 更新日期：2026-10-06

本專案的主要使用方式是 **Windows 桌面版**。Flet Web 模式（`tools/ui_smoke.py`、瀏覽器連線）主要用於煙霧測試與開發驗證，有下列限制。

---

## 1. 目前行為

| 項目 | 桌面版 | Web 模式 |
|---|---|---|
| 資料夾選擇器（`get_directory_path`） | ✅ 原生對話框 | ❌ Flet 不支援，按下按鈕顯示提示 |
| 檔案選擇器（`pick_files`／`save_file`） | ✅ | 依 Flet 支援而定；不支援時同樣顯示提示 |
| 路徑輸入欄位 | ✅ | ✅ 手動輸入 |
| 輸出資料夾 | 不存在時自動建立 | 同左 |

Web 模式**只能手動輸入路徑**，而且路徑指的是「**執行程式那台電腦**」上的路徑，不是瀏覽器所在電腦的路徑：

- 在同一台 Windows 上開瀏覽器連本機：輸入的就是這台電腦的路徑，可正常使用。
- 從另一台電腦的瀏覽器連線：輸入的仍是主機（執行程式那台）上的路徑。

## 2. 為什麼不能選資料夾

Flet 1.0.1 的 `FilePicker.get_directory_path()` 在 Web 模式會拋出 `FletUnsupportedPlatformException`；瀏覽器本身也不會把本機資料夾的絕對路徑交給網頁。

## 3. 目前的處理方式（PR #169）

- `app/ui/safe_file_picker.py` 的 `SafeFilePicker`：不支援時記一筆警告、顯示「Web 模式不支援選擇資料夾／檔案對話框，請直接輸入「執行程式那台電腦」上的路徑」，並視為使用者取消。其他例外不吞。
- `app/` 內**不得直接建立 `ft.FilePicker(...)`**，一律用 `SafeFilePicker`（`tests/test_safe_file_picker.py` 的 AST 契約測試強制）。
- `ensure_output_dir()`：輸出資料夾不必事先存在，自動建立。
- `kit.text_field` 的單行欄位預設掛 `on_change`，讓 Web 手動輸入的值即時同步回後端（沒有事件處理函式時，Flet 不會即時同步，按「執行」會讀到空字串）。

## 4. 未來若需要在 Web 模式選擇資料夾（尚未實作）

沒有實際需求前不做。需要時可考慮：

| 方案 | 做法 | 優點 | 代價 |
|---|---|---|---|
| 伺服器端資料夾瀏覽器 | 自製對話框，列出主機磁碟機與資料夾供點選，回填路徑 | 純 Flet 即可；適合本機／內網；不需傳檔 | 中等工作量；要處理權限、隱藏資料夾、遠端暴露檔案系統的**安全風險**（限制可瀏覽的根目錄） |
| 瀏覽器上傳 | 用 `pick_files` 加上傳，把檔案傳到主機暫存區再處理 | 遠端瀏覽器也能用自己電腦的檔案 | 工作量大；Mod 目錄動輒數百 MB 的 JAR；上傳的是檔案而不是資料夾；需要清理暫存 |
| 維持手動輸入 | 現狀 | 零成本 | 使用者要自己輸入完整路徑 |

實作伺服器端資料夾瀏覽器時的注意事項：

1. 只在 Web 模式啟用，桌面版繼續用原生選擇器。
2. 以 `SafeFilePicker` 為入口（在 `get_directory_path` 的 Web 分支改為開啟自製對話框），呼叫端不必改。
3. 限制可瀏覽的根目錄，避免把整個檔案系統暴露給任何連得上的瀏覽器。
4. 補上 `docs/WINDOWS_VERIFICATION.md` 的驗證項目。

## 5. 測試時的注意事項

- **Playwright 的 `fill()` 不可靠**：Flet Web 的畫面由 Flutter 繪製，欄位文字顯示在畫面上，不代表 Flutter 的輸入控制器收到了值。`fill()` 直接改 DOM 值、不經過真實的鍵盤事件，所以「畫面看得到路徑、後端卻是空字串或被截斷」是這種輸入方式的典型現象，不一定是應用程式的同步問題。
- 驗證欄位同步請用**真實按鍵事件**：先點擊欄位，再用 `page.keyboard.type(文字, delay=20)` 逐字輸入，等約 300 ms（或按 Tab 失焦）後再按按鈕。
- 判斷根因看後台 log：合併頁按下「開始合併」會記一行 `[合併] 開始按鈕：mode=…, folder=…, zips=…, output=…`，是後端當下實際收到的欄位值；一鍵流程有 `Pipeline one_click: input=[…], output=[…]`。畫面有值而 log 是空的才是同步問題。
- Web 模式驗證**不能取代**桌面版（視窗事件、原生選擇器、打包與檔案鎖只存在於桌面版），見 `WINDOWS_VERIFICATION.md`。

## 相關文件

- [`WINDOWS_VERIFICATION.md`](WINDOWS_VERIFICATION.md)：Windows 實機驗證清單
- [`CROSS_CUTTING_REVIEW_CHECKLIST.md`](CROSS_CUTTING_REVIEW_CHECKLIST.md)：橫切改動審查清單（平台差異）
- [`UI_VISUAL_ACCEPTANCE.md`](UI_VISUAL_ACCEPTANCE.md)：UI 視覺驗收
