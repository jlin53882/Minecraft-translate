# Windows 實機驗證清單（交給 Codex／測試者執行）

> 目的：整理「**只能在 Windows 驗證**」的項目。Linux／網頁版（`tools/ui_smoke.py` + Playwright）可以驗證的畫面項目**不在這裡**，由開發環境另外處理（見最後一節）。
> 每一項請照「步驟」執行，把結果（通過／失敗＋截圖或日誌）貼回對應 issue；全部通過後才關閉該 issue。
> 最後更新：PR #153 合併後（2026-10-04）。

## 0. 共同前提

- Windows 10/11，已安裝 [uv](https://docs.astral.sh/uv/) 與 Nuitka 需要的 C 編譯器（MSVC Build Tools 或 Nuitka 自動下載的 MinGW）。
- 取得 `main` 最新程式碼；`git log -1` 應包含 PR #153（`Merge pull request #153`）。
- 準備一把可用的 Gemini API 金鑰（只在需要實際翻譯的項目使用）；**不要把真實金鑰貼進 issue 或截圖**。
- 回報格式：每項寫「通過 / 失敗」，失敗附 `data\logs\` 內的日誌（先檢查沒有金鑰）與重現步驟。

## 1. 打包與資料目錄（issue #137）

契約說明：`docs/DISTRIBUTION_AND_DATA_DIR.md`；打包腳本：`tools/build_exe.bat`（草稿，**尚未在 Windows 實測**，失敗請記錄並回報要修的地方）。

預期輸出結構：

```
dist\MinecraftTranslator\
  MinecraftTranslator.bat        ← 啟動器
  app\MinecraftTranslator.exe    ← 程式（每次更新整個換掉）
  data\                          ← 使用者資料（更新時完全不碰）
```

| # | 項目 | 步驟 | 預期結果 |
|---|---|---|---|
| 1.1 | 打包成功 | 在專案根目錄執行 `tools\build_exe.bat`（保持 `CONSOLE_MODE=force`） | 結束時印出「完成」；`dist\MinecraftTranslator\app\MinecraftTranslator.exe` 存在；`dist\_staging` 可有可無 |
| 1.2 | 視窗能啟動 | 雙擊 `MinecraftTranslator.bat` | Flet 桌面視窗出現，不閃退；console 沒有 Traceback |
| 1.3 | 路徑判定 | 在 console 或日誌確認資料根目錄 | 資料根目錄為 `dist\MinecraftTranslator\data`（不是 `app\`）。若不符，記錄 `sys.argv[0]`、`__compiled__`、`sys.frozen` 與 `get_data_root()` 的實際值 |
| 1.4 | 第一次啟動 | 刪除 `data\` 後啟動 | 自動建立 `data\config.json`（內容等同 `config.example.json`，**欄位完整**）、`data\logs\` 等；沒有錯誤提示 |
| 1.5 | 存設定、重開 | 設定頁改一個值（例如 `log_level`）→ 存檔 → 關閉 → 重開 | 值保留；`data\config.json` 是合法 JSON |
| 1.6 | 唯讀資源 | 檢查程式能讀到 `assets`、`config.example.json`、`pyproject.toml`、`resource_pack_version.json` | 圖示與版本資訊正常；`resource_pack_version.json` 有 3 處以 `__file__` 讀取，確認打包版都能讀到（若找不到檔案請回報是哪一處） |
| 1.7 | 舊安裝遷移 | 模擬舊版平面式安裝：把 `config.json`、`replace_rules.json`、`logs\`、`快取資料\` 放在 `dist\MinecraftTranslator\`（與 `app\` 同層），其中 `config.json` 填入可辨識的自訂值；再啟動新版 | 首次啟動後這些項目被搬進 `data\`，自訂值仍在，沒有被預設值覆蓋；`data\` 已有同名檔時**不覆蓋**、舊位置檔案保留 |
| 1.8 | 更新（發佈腳本） | 在 `data\` 放一個標記檔，再次執行 `tools\build_exe.bat` | `app\` 被換成新版；`data\` 內容（含標記檔、`config.json`）完全不變 |
| 1.9 | 更新時程式正在執行 | 保持 exe 執行中，執行 `build_exe.bat` 的發佈步驟 | 發佈失敗並提示先關閉程式；`app\` 被還原、仍可正常啟動 |
| 1.10 | 環境變數覆蓋 | 設 `MCT_DATA_DIR=D:\mct-data` 後啟動 | 資料寫進 `D:\mct-data`，且不觸發遷移 |
| 1.11 | 啟動器 `.bat` | 從不同工作目錄（例如桌面捷徑）啟動 `.bat` | 仍能啟動，資料仍在 `data\` |
| 1.12 | 圖示快取 | 開「圖示預覽」頁並載入一個模組包 | 快取寫入 `data\.icon_cache\`，不寫在 `app\` |

完成後把結果貼到 **#137**。若 `build_exe.bat` 需要修正，請另開 PR 並在 issue 說明。

## 2. 視窗關閉流程（issue #126、#122）

背景：桌面視窗關閉不會觸發 `page.on_close`；PR #149 實作了「有任務時確認 → 取消 → drain → flush → `dispose()` → `destroy()`」。**自動測試與假 page 不能證明視窗事件**，必須在真實桌面版驗證（建議用 1.1 打包出的 exe；也可用 `uv run main.py`）。

| # | 項目 | 步驟 | 預期結果 |
|---|---|---|---|
| 2.1 | 無任務關閉 | 啟動後直接按視窗 ✕ | 視窗立即關閉、行程消失（工作管理員確認沒有殘留 `MinecraftTranslator.exe`） |
| 2.2 | 有任務時出現確認 | 啟動一個長任務（例如機器翻譯、對假資料夾或小模組包），期間按 ✕ | 出現確認對話框，說明目前有哪些任務、關閉會中斷；視窗沒有直接消失 |
| 2.3 | 選「繼續執行」 | 在 2.2 的對話框選繼續 | 對話框關閉、任務繼續跑、視窗仍可操作 |
| 2.4 | 選「仍要關閉」 | 再次按 ✕ → 選仍要關閉 | 任務被取消、快取與 checkpoint 落盤，行程正常結束（時間內完成，沒有被作業系統判定無回應） |
| 2.5 | 落盤正確 | 2.4 之後重開，檢查 `data\` 的快取與 checkpoint | 檔案完整可讀（不是半份 JSON）；已完成批次的翻譯仍在快取 |
| 2.6 | 取消＋flush 耗時 | 記錄 2.4 從按下到行程結束的秒數 | 記錄數字；若超過數秒，回報（handler 太慢的風險） |
| 2.7 | `dispose()` 只執行一次 | 觀察日誌（log_level 設 DEBUG） | 桌面關閉路徑只有一次 `dispose`；沒有重複或例外 |
| 2.8 | 工作台刷新 | 任務進行中停在工作台頁 | 快取筆數、規則數會在合理延遲內更新；沒有任務時沒有持續輪詢（CPU 閒置） |

結果貼到 **#126**（2.1–2.8 都貼）與 **#122**（2.1、2.4、2.7）。

## 3. 重開後續跑（issue #151，尚未實作，列出供實作後驗證）

等 #151 實作完成後，在 Windows 打包版驗證：

| # | 項目 | 預期結果 |
|---|---|---|
| 3.1 | 機器翻譯進行到一半，用工作管理員強制結束行程，再開啟 | **不會自動續跑**；顯示「上次被中斷的任務」確認對話框 |
| 3.2 | 對話框選「續跑」 | 以同一批輸入續跑，結果不漏翻、不重翻、不錯位（與不中斷的結果比對） |
| 3.3 | 對話框選「放棄」 | checkpoint 被清除，不續跑，不消耗 API 額度 |
| 3.4 | 輸入檔改過（指紋不符） | 提示「無法續跑」，不靜默忽略 |
| 3.5 | 使用者未選擇前 | 不得呼叫 API（可用假 API 或封包監看確認） |
| 3.6 | 中斷點涵蓋「切批中、寫快取中、剛開始」 | 三種都能正確處理 |

## 4. 其他只能在 Windows 驗證的項目

| Issue | 項目 | 說明 |
|---|---|---|
| #154 | `keyring`（Windows Credential Manager）在 Nuitka standalone 下是否可用 | 調查性質：以最小範例打包後測試讀寫憑證、後端動態載入是否缺檔。決策前不寫實作 |
| #139 | Snackbar 與底部狀態列重疊 | 原觀察來自 Windows 11 / Flet 1.0.1 / Chrome 的截圖。開發環境會用網頁版重現；若網頁版與桌面版行為不同，請在 Windows 桌面版再確認 |
| #147 | oauthlib CVE | 不需要 Windows；需要確認 Flet 是否會進入 PKCE Authorization Code 流程（程式／依賴分析） |

## 5. 不需要 Windows（開發環境以網頁版處理，列出供對照）

這些項目會以 `tools/ui_smoke.py`（真實 Flet 網頁版＋Playwright）驗證，不需要 Codex 在 Windows 做：

- #134：設定頁 8 頁的排版、欄寬、補齊欄位的顯示與儲存。
- #117：設定頁套用時機說明、存檔提示、快取資料夾存檔後的重載與頁面刷新、打包頁 `output_zip_name` 即時更新。
- #114／#121／#120：View 拆分、舊色名稱與 `components.py` 清除、文件核對（前後截圖對照）。

> 注意：網頁版驗證**不能**取代第 1、2 節；視窗事件、打包與檔案鎖只存在於 Windows 桌面版。

## 6. 回報後的收尾

- 全部通過：在對應 issue 留言附結果並關閉（#137、#126、#122；#117、#134 在網頁版驗證完成、且本清單無相關項目後關閉）。
- 失敗：在 issue 留言附日誌與重現步驟，並**不要**關閉；需要改程式時另開 PR（不要直接推到 `main`）。
