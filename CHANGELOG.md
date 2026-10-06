# Changelog

本專案 Changelog 採用「Keep a Changelog」風格，版本號採 Semantic Versioning。

- Keep a Changelog: https://keepachangelog.com/
- SemVer: https://semver.org/

---

## [Unreleased]

> 主要內容：UI 全面重新設計（PR #106）。版本號尚未決定。

### Docs
- 新增 `docs/WEB_MODE_LIMITATIONS.md`：Flet Web 模式限制（不能選資料夾、只能輸入主機路徑）與未來擴充方向。
- 新增 `docs/CROSS_CUTTING_REVIEW_CHECKLIST.md`：橫切改動（日誌／任務生命週期／併發）提交前自查清單。

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
- **UI 與後台日誌自動同步**：寫進畫面的訊息（`TaskSession.add_log`、`LogView.add`、批次推畫面的提取／打包／QC 路徑）會自動鏡像到後台 log，不必每個呼叫點自己配對 `log_info`。核心流程已經自己 log 過的訊息會去重（`translation_tool/utils/ui_mirror.py`，相同文字每筆後台記錄只抵銷一次），鏡像記錄帶 `ui_mirrored` 標記，UI log handler 會略過，不會在畫面重複。任務開始／結束（狀態、耗時、摘要）也寫入後台。
- **`app.log` 每一行標示所屬任務**：安裝 LogRecord 工廠，在寫 log 的執行緒把任務（`contextvars`，執行緒池也繼承）帶進記錄；`RedactingFormatter` 自動在 `%(message)s` 前加上 `[task=<任務名稱>/<識別>] `（沒有任務時不加）。同時執行多個任務時，檔案裡交錯的每一行都看得出是哪個任務——不論是核心流程直接寫的、執行緒池內寫的，還是 UI 替任務補寫的訊息。多行訊息與 traceback 的**每個實體行**也帶任務標籤（續行補 `[task=…]`，空行保留標籤）；`log_format` 驗證器與執行期使用同一個 `RedactingFormatter`／記錄欄位契約，自訂格式可以使用 `%(task_tag)s`／`%(task_name)s`／`%(task_id)s`（沒有任務時是空字串），`%(message)-80s` 這類帶寬度／精度的寫法也會自動插入標籤，未知欄位仍被拒絕。
- **任務終止狀態三邊一致**：`TaskSession.is_finished` 以真正的 lifecycle 判斷（`ERROR` 但尚未 `finish()` 不算結束），`UISessionLogHandler` 清理 routing 時不再誤刪另一個「已標記失敗、仍在收尾」的任務；結束後才 `set_error()`（流水線安全網）會更正 `TaskManager` 最近完成清單的結果，並在後台另寫一筆「任務結果更正」（不重複寫「任務結束」）；`TaskSession.task_id` 每次 `start()` 重新產生，識別的是「這一次執行」。
- **後台記錄依任務送進各自的畫面**：`UISessionLogHandler` 從單一全域 session 改為 `{任務: session}` 路由——同時執行多個任務時，後台記錄不再全部送進最後綁定的那個任務畫面；沒有任務歸屬的記錄仍退回「最近綁定的 session」，已知任務但未登記的記錄不送。預覽 poller（UI 執行緒）轉送掃描內容時明確帶掃描工作的任務識別；`LogView.add(dedupe=…)`／`ctx.add_log(forwarded=…)` 每個呼叫點必須明確表態（AST 契約測試把關）。
- **同時執行的任務不再互相抵銷去重**：服務入口（`UISessionLogHandler.set_session`）以 `contextvars` 把目前執行緒歸屬到任務，追蹤器只讓「同一任務或歸屬未知」的後台記錄抵銷畫面訊息；新增 `tests/test_log_pair_contract.py`，用 AST 掃描「同一個 except 區塊同時有後台記錄與會被鏡像的畫面訊息」，兩邊文字不同就讓測試失敗。執行緒池與背景執行緒也帶著任務歸屬：`ContextThreadPoolExecutor` 取代 12 個檔案內的 `ThreadPoolExecutor`、核心流程自己開的 Thread 用 `run_in_context`、直接消費 generator 的 UI 工作執行緒（提取／預覽／打包／QC）以 `in_new_task` 取得自己的任務歸屬；`LogView.add` 預設視為 UI 自己的事件（無條件寫後台），只有轉送核心流程內容的呼叫點才去重。契約測試禁止日後直接使用 `ThreadPoolExecutor`。
- **任務結束只記錄一次**：`TaskSession.finish()` 冪等——流水線步驟自己 finish 之後，安全網再呼叫（含 `set_error()`）不會重複寫後台「任務結束」或重複通知觀察者；`start()` 會重新啟用。
- **日誌詳細度**：在 `except` 區塊內記 ERROR 會自動附上 traceback；翻譯頁、合併、流水線的失敗訊息補上例外類型、輸入／輸出路徑與「完整堆疊已寫入後台 log」；任務開始訊息列出輸入、輸出與步驟設定（翻譯、合併、QC、打包）；圖示索引、MD 統計、快取歷史、版本對照檔等原本靜默略過的錯誤改為留下警告。；全專案例外處理經 AST 掃描逐項檢視（547 個 `except`），補上例外類型（`{e}`→`{e!r}`）、堆疊與是哪個檔案／JAR；`show_snack` 的後台等級依顏色對應（紅=錯誤、金=警告）；服務載入失敗、既有輸出無法讀取而改寫、批次翻譯例外等原本只在畫面或完全無紀錄的情況都會寫入後台
- 設定存檔的訂閱者通知一律在寫入鎖釋放後執行，並統一所有 app 層寫入的鎖（避免巢狀寫入死鎖與並行寫出壞檔）。
- AppShell 的 UI 更新改為排程回 Flet event loop 並節流；新增 `AppShell.dispose()` 與 `page.on_close` teardown。
- 合併頁單欄位寫入改走 ConfigStore（只改被修改的欄位，也會通知外殼）。

### Bug Fixes
- **打包時任意殘留的 `*.zip.tmp`／`*.bundle-state.json` 被加入 ZIP**：排除清單原本只認「這次輸出 ZIP」自己的暫存與狀態檔，以前用別的檔名輸出、失敗殘留的暫存檔仍會被打包。現在凡是 `.zip.tmp`、`.bundle-state.json`、`.bundle-state.json.tmp` 結尾的檔案一律不是來源。
- **語言合併可在單一 update 內取消**（#170）：`lang_merger.py` 在檔名掃描（每 256 筆）、任務提交、完成迴圈加入取消檢查點；取消或關閉 generator 時，佇列中還沒開始的任務直接丟棄，只等正在執行的少數任務，不再把整個佇列跑完才停止。
- **一鍵流程對話框在 Flet Web 殘留／按鈕無反應**：Flet 0.85+ 改用 `page.show_dialog()` / `page.pop_dialog()` 管理對話框生命週期（沒有這組 API 的頁面仍走 overlay 相容流程），精靈按鈕改用標準 `ft.Button`，避免 Web 的 `TextButton` 事件沒送達。生命週期函式拆到 `pipeline_one_click_lifecycle.py`。
- **輸出 ZIP 放在來源資料夾內時打包卡住**：預設輸出路徑就在來源資料夾內，失敗留下的暫存 ZIP（`.zip.tmp`）、輸出 ZIP 與狀態檔會被當成來源掃描，甚至把自己再壓一次。現在打包自己的產物（ZIP、暫存、狀態檔）一律排除在指紋與壓縮之外，也讓「來源未變動就沿用」在預設路徑下能成立。
- **Flet Web 手動輸入的路徑沒同步到後端**：新增 `SyncTextField`（`app/ui/sync_text_field.py`），單行欄位預設掛空的 `on_change`，讓輸入值即時回到 `.value`（一鍵流程曾收到 `input=[], output=[]`、打包對話框的輸出 ZIP 欄位仍用預設路徑）。`app/` 內全部 `ft.TextField` 改用它（含各流程對話框），並由 AST 契約測試強制。
- **Flet Web 不支援資料夾選擇器**：新增 `SafeFilePicker`（`app/ui/safe_file_picker.py`），`get_directory_path`／`pick_files`／`save_file` 在不支援的平台改為顯示提示並視為取消，不再拋出未捕捉的 `FletUnsupportedPlatformException`；欄位保持可手動輸入（Web 模式輸入的是執行程式那台電腦的路徑）。
- **輸出資料夾不必事先存在**：一鍵製作、提取、合併的輸出目錄改為自動建立（路徑是檔案或無法建立時才提示）。
- **一鍵製作輸入驗證失敗時對話框遮罩殘留**：關閉對話框時，移除 overlay 後再推一次更新，避免緊接著的 SnackBar 被殘留遮罩蓋住（Windows 煙霧測試發現）。
- **資料夾合併遇到不存在的輸入資料夾不再顯示「翻譯已完成」**：核心改回報 `error=True` 並帶出路徑；服務層記為資料夾失敗（階段 1 失敗、略過階段 2、任務狀態 ERROR）。一鍵流程對提取沒有產生的來源（例如沒有 Patchouli 書籍）以 `skip_missing_input=True` 明確略過，不會整個流程失敗。ZIP 合併同理：缺檔的 ZIP 現在記為該 ZIP 失敗（帶出路徑），其餘 ZIP 照常處理。路徑存在但型別不對（資料夾模式收到檔案／ZIP、ZIP 模式收到資料夾）同樣判為失敗；一鍵流程的 `skip_missing_input` 只略過「路徑不存在」。
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
