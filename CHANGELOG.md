# Changelog

本專案 Changelog 採用「Keep a Changelog」風格，版本號採 Semantic Versioning。

- Keep a Changelog: https://keepachangelog.com/
- SemVer: https://semver.org/

---

## [Unreleased]

> 主要內容：UI 全面重新設計（PR #106）。版本號尚未決定。

### Features
- **Mod 翻譯資料庫**：分版本的翻譯記憶庫（SQLite）。掃描 mods 資料夾的 jar（含 `META-INF/jarjar` 內嵌 jar、Patchouli 書籍）建立；機器翻譯的查詢順序為「資料庫 → 快取 → AI」，翻譯結果自動寫回（只新增、不覆蓋，其他版本相同原文的空白一併補上）；在「Mod 資料庫」頁可手動校對，儲存時同步所有版本中原文相同的條目（可還原）。可匯入翻譯 ZIP（zh_tw 不判讀、直接匯入），並可選擇是否判斷清理英文內容；譯文逐字保存（換行、格式碼、前後空白）。詳見 `docs/MOD_TRANSLATION_DB.md`。
- **UI 全面重新設計（Deepslate & Emerald）**：依 `docs/design/ui-redesign/` 設計稿重做全部頁面；深色 / 淺色主題（偏好存於 `ui.theme_mode`），切換不需重建畫面。
- **新外殼**：分組側欄（工作流程 / 品管與校對 / 資料庫 / 輸出 / 系統）、頂列（任務膠囊、API Key 狀態）、狀態列、`Ctrl+P` 快速跳轉、`Ctrl+1…0` 切頁。
- **工作台**：新增 Dashboard 頁面（流程進度、近期活動、快取與規則統計、Key 健康度）。
- **任務追蹤**：`TaskManager` 統一收集 `TaskSession`，任務執行中切換頁面不中斷，頂列 / 側欄 / 狀態列同步顯示。
- **共用 UI kit**：`app/ui/kit/`（按鈕、Chip、卡片、進度、空 / 載入 / 錯誤狀態等）與語意色主題 `app/ui/design.py`。
- **ConfigStore**：設定的讀 / 寫 / 變更通知單一入口，存檔後外殼（Key 狀態、模型、主題）立即更新。
- **API Key 健康度（#113）**：RPD 耗盡 / 403 的 Key 冷卻一段時間不再被請求（以 Key 指紋識別，不是 index）；全部冷卻時每個 cycle 只試探一把。
- **token 預算切批（#108）**：依 token 預算切批、學習式預算、`maxOutputTokens`、`finishReason` / 用量診斷；可用 `token_budget_enabled` 關閉。

### Improvements
- 設定存檔的訂閱者通知一律在寫入鎖釋放後執行，並統一所有 app 層寫入的鎖（避免巢狀寫入死鎖與並行寫出壞檔）。
- AppShell 的 UI 更新改為排程回 Flet event loop 並節流；新增 `AppShell.dispose()` 與 `page.on_close` teardown。
- 合併頁單欄位寫入改走 ConfigStore（只改被修改的欄位，也會通知外殼）。

### Bug Fixes
- **語言合併（#109）**：ZIP 累計讀取預算用盡時回報不完整輸出，不再顯示「全部處理完成」。
- **JAR 提取（#111）**：`scan_jars` 支援明確的 `jar_files`，預掃描清單與實際提取清單一致。
- 全部 API Key 冷卻時，429 RPM 重試不再改領其他冷卻中的 Key。
- 字型註冊改為合併而非覆蓋，避免罕用字變方塊。

### Refactoring
- `TaskSession` / `LogEntry` 移到中立的 `app/tasks/`，services 不再 import `app.views`；刪除 `app/logging/`、`app/task_session.py` 轉接。
- 以單一 `ViewSpec`（`app/view_registry.py`）取代三張平行的導覽表。
- 刪除 `views/cache/`、`cache_manager/panels/` 死碼與一次性 `tools/` 腳本。

### Tests
- 新增 UI kit、外殼、TaskManager、ConfigStore、Dashboard 資料、Key 健康度、token 預算等測試與 AppShell 排程 / teardown 的回歸測試。

### Docs
- 新增 `docs/UI_DESIGN_SYSTEM.md`、`docs/LM_TOKEN_BUDGET.md`、`docs/LM_KEY_HEALTH.md`、`docs/CONFIG_APPLY_TIMING.md`、`docs/REFACTOR_PLAN.md`、`docs/REFACTOR_AUDIT.md`。
- 重寫 `README.md`：更新功能、導覽表、設定、架構與開發說明。

### Known issues
- 關閉視窗會中斷進行中的任務（#126、#127）。
- 設定頁尚無 token 預算與 Key 冷卻欄位（#134）。
- `translator.replace_rules_path` 在語言合併被忽略（#118）。
- 後續工作清單見 GitHub issues #114 ~ #137。

---

## [0.8.0] - 2026-04-02

> 範圍：PR40 ~ PR63

### Features
- **KubeJS reverse_index**：實作雙軌 reverse_index 去重（#40）。
- **顏色字元校驗**：新增 `color_char_checker` 工具（#41）。
- **Rich Text Shield**：新增 `rich_text_shield` 脫殼模組（#42）。
- **Type annotations**：分批補齊 `icon_classifier` / `ui_logging_handler` / `merge_view` / pipeline services（#46 / #48 / #49）。
- **Icon Preview 重構**：JAR 目錄模式、雙軌 zh_tw 讀取、SnackBar feedback（#51）。
- **JAR 掃描**：新增多執行緒 `jar_browser` 工具（#53），`jar_processor` 內部改用之（#54）。
- **Icon 快取**：L2 磁碟快取（#55 / #56）、Icon Model 解析重構 + 即時搜尋 UI（#58）。
- **ZIP Reader 重構 + 效能**：PR59 ZIP Reader 重構；PR60 ThreadPoolExecutor + 預建立 icon 索引（5 分 → 1-2 分）。

### Bug Fixes
- Code Review 全量修復（28 issues，#43）。
- Icon Preview 覆蓋 bug + Phase4 進度條（#57）；模組搜尋分頁修復（#61）。
- Audit：LRU ZipFile fd leak、KubeJS O(N²) 優化、py.typed（#62）；L2-L9 LOW/P2 修復（#63）。

### Tests
- Icon Preview 單元測試補寫 30 tests（#52）。

---

## [0.7.0] - 2026-03-23

> 範圍：PR10 ~ PR39

### Features
- FTB Quest 翻譯進度條驗證報告（`step6 progress bar validation report`），新增 FTB Quest 抽取格式完整性驗證。

### Improvements
- **翻譯視圖日誌區**：深色 Container 背景替代 styled_card；ListView 移除無效 bgcolor 參數；初始化提示文字；日誌自動滾到底。
- **KubeJS 翻譯 Pipeline**：OpenCC 簡→繁轉換延伸至 `client_scripts` 來源值；跳過 ASCII 藝術字（█▓▒░）；`skip_chinese=False` 邏輯修正；新增 `item.kubejs.*` 翻譯記憶匹配。
- **翻譯進度**：FTB 進度改以檔案數均勻推進（非 chunk 數）。

### Bug Fixes
- `scroll_to(end=True)` → `scroll_to(offset=1.0)`（Flet 0.28.3 API 相容）。
- `log_presenter._entry_color` 移除錯誤 hex 前綴拼接，正確使用 `Colors` enum 的 `.value`。
- `kubejs_translator_clean.py` 補回 `import json`（`jq` 依賴）。
- CI：修復 lint F401（`threading` import 移除又恢復）；Ruff format 6 個檔案。

### Tests
- 3 個 `test_translation_view_characterization.py` characterization tests 新增（翻譯視圖行為迴歸保護）。

---

## [0.6.0] - 2026-03-12

> 範圍：PR1 ~ PR39（以你目前的 PR 編號切版）

### Features
- Flet 桌面 GUI：提供設定、規則、快取管理、翻譯任務（FTB/KubeJS/Markdown）、JAR 提取、機器翻譯、檔案合併等頁面入口。
- JAR 提取：從模組 JAR 中提取語言檔與 Patchouli 手冊內容（含預覽與報表）。
- 語言合併：支援 `en_us.json` / `zh_cn.json` / `zh_tw.json` 的保守合併策略，並提供 pending export / quarantine 機制。
- AI 翻譯（Gemini）：支援批次翻譯、自動重試、縮批、換 key、節流等策略（以不改行為為原則持續重構）。
- 快取管理：快取分片儲存、全文搜尋索引、歷史版本檢視與套用。
- 品管/檢查：未翻譯條目、簡繁差異、英文殘留、TSV 版簡繁比較等檢查工具（目前 UI 功能線有凍結/暫緩策略）。

### Improvements
- 設定與路徑解析：以 project root 為基準，降低 cwd 漂移造成的找不到 config/資源/快取路徑問題。
- logging：集中化並讓 pipeline 在每次任務啟動時重新同步 logging 設定（維持舊行為、降低 UI 卡頓）。
- 文件：repo 內已有 `ITERATION_SOP.md`、`docs/`、`docs/pr/` 與長期測試風格筆記（例如 `docs/testing-style-note.md`）。

### Refactoring
- Service 分層：主線 canonical services 逐步收斂到 `app/services_impl/*`，並讓 `app/services.py` 退為 QC/checkers 暫緩線 façade（避免主線被歷史包袱綁死）。
- Plugin shared helpers：引入/強化 `translation_tool/plugins/shared/*`，降低多條 pipeline 重複 helper 的風險。

### Tests
- 建立一批 pytest 測試與 guard tests，用於保護：import contract、path resolution、cache/search 契約、以及 refactor 的關鍵回歸點。
