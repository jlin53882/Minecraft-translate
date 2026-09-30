# 重構審查與計畫（為串接新 UI 做準備）

> 狀態：**審查結論，尚未開始處理。**
> 快照基準：`main` @ `53f4e8b`（PR #104 合併後）。
> 撰寫時程式碼仍有其他變更在進行，**動手前請先重新量測**（見文末「重現指令」），
> 下列數字與檔案位置可能已經改變。

## 目的

新的 UI 設計稿（`docs/design/ui-redesign/`）要接到現有專案上。
本文整理「哪些地方先重構，後續接內容才不會卡住」，並說明原因，方便日後依序處理。

## 健康狀況摘要

- 測試：`2100 passed`（約 18 秒），基礎穩固，重構時可當安全網。
- 分層：`translation_tool/`（引擎）與 `app/`（UI）大致分離。
- 主要問題集中在 **UI 層**：色彩系統未接通、巨型 View、重複與殘留模組、
  長任務執行方式不統一、View 直接碰設定與作業系統。

---

## P0：先清理（低風險，讓後續工作變簡單）

### P0-1　TaskSession / 日誌模組有三層轉接

- **現況**：`app/task_session.py` → `app/logging/task_session.py` → `app/views/_log/task_session.py`。
  `app/logging/` 六個檔案全是轉接（shim）；`app/logging/__init__.py` 又反向從 `app/views/_log` re-export。
  業務層 `app/services_impl/pipelines/{extract,ftb,kubejs}_service.py` 也 import `app.logging.task_session`。
- **原因**：業務層依賴 UI 套件，方向反了；三層轉接讓人分不清該改哪個檔。
- **建議**：把 `TaskSession`、`LogEntry`、`LogLevel` 這類純資料模型移到中立位置
  （例如 `app/tasks/`），`LogView` 等控制項留在 UI。刪除轉接，統一改 import（約 17 個檔案）。
- **驗收**：`services_impl/` 不再 import `app.views.*`；全專案只剩一個 `TaskSession` 定義。

### P0-2　殘留與死碼

| 項目 | 位置 | 說明 |
|---|---|---|
| 整個套件無法 import | `app/views/cache/` | `__init__.py` 匯入不存在的 `cache_modal_base / cache_modal_query / cache_modal_shard`；`PROJECT_STRUCTURE.md` 也標為「未接線，待清理」 |
| 占位元件 | `app/views/cache_manager/panels/*`（約 169 行） | 只被 `tests/test_pr1_to_pr6.py` 驗證「可 import」；實際使用的是 `app/views/cache_query_panel.py`（633 行）與 `cache_shard_panel.py`（581 行） |
| 被覆蓋的重複函式 | `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py` 約 L496 與 L605 | `on_translated_item` / `on_batch_flushed` 先內嵌定義，後又被工廠函式覆蓋（ruff F811） |
| 未使用的 import / 變數 | 全專案 | F401 65 個、F841 6 個（例如 `pipeline_one_click_dialog.py` 的 `wizard_content`、`translate_input_field`） |
| 過期的註冊 key | `app/view_registry.py` | `VIEW_WINDOW_SIZES` 內有 `"arnold"`，導覽中沒有；`"pipeline"` 卻沒有對應尺寸 |
| 一次性腳本 | `tools/`（約 11.6k 行） | 含 `__scan_results.json`（8641 行）、`fix_test.py`、`verify_patchouli_*_v2/v3` 等；確認無人使用後移除或歸檔 |
| 舊 PR 內文 | `.github/PR1_body.md` ~ `PR6_body.md` | 已是歷史資料 |

- **原因**：新舊並存時，很難判斷該改哪一份；也會拖慢搜尋與 review。
- **建議**：逐項確認沒有引用後刪除；保留行為測試，刪掉只驗證 import 的占位測試。
- **驗收**：`ruff check --select F401,F811,F841` 為 0；`import app.views.cache` 不再存在。

### P0-3　文件數字過期

- README 寫 11 個 View；`docs/PROJECT_STRUCTURE.md` 寫「176 檔、1905 tests」。
  實際為 12 個 View、`tests/` 202 個檔案、2100 個測試。
- **原因**：之後依文件串接會被誤導。
- **建議**：不要手寫會變的數字，或改由 CI / 腳本產出。

---

## P1：串接新 UI 前必須先做

### P1-4　主題系統沒有接通

- **現況**：`app/ui/theme.py` 有 light / dark 兩組 token，但：
  - views 使用的是綁定淺色的模組層常數（如 `theme.RED_600`），原始色階引用約 **262** 處；
  - 直接寫 `ft.Colors.*` 約 **107** 處（15 個檔案）、寫死 hex 約 **28** 處；
  - `main.py` 切換主題只呼叫 `theme.manager.set_mode()`，不會重繪已建立的控制項；
  - 日誌色（`BG_LOG_PANEL` 等）綁死 DARK（`theme.py` 內已有待辦註解）。
- **原因**：新設計靠「語意色」（底色層級 + 五種強調色）在深淺主題間切換。
  不先接通，每個頁面都得逐一手改顏色，且深色 / 淺色無法同時成立。
- **建議**：
  1. 依設計稿擴充語意 token（`bg / side / panel / panel2 / raised / line`、`em / gold / dia / ench / red` 與對應 `*bg`）。
  2. 用 token 組出 `ft.Theme` / `ft.ColorScheme`，設定到 `page.theme` 與 `page.dark_theme`，讓 Flet 原生處理深淺切換。
  3. 提供相容別名，再用批次替換把 `RED_600` 這類原始色階換成語意名稱。
- **驗收**：views 內不再出現 `ft.Colors.*` 與 hex（除 theme 本身）；切換主題後畫面即時更新。

### P1-5　巨型 View 與巨型函式

| 位置 | 規模 |
|---|---|
| `app/views/cache_view.py` | 3671 行、`CacheView` 120 個方法；`_build_shard_widgets` 754 行、`_build_query_widgets` 605 行 |
| `app/views/icon_preview_view.py` | 2069 行 |
| `app/views/extractor/extractor_dialog.py` | 1207 行；`open_extractor_dialog` 703 行、`open_preview_dialog` 433 行 |
| `app/views/pipeline/*_dialog.py` | `open_one_click_dialog` 495 行、`open_merge_dialog` 365 行、`open_extract_dialog` 349 行 |
| `app/views/merge_view.py` | `MergeView.__init__` 478 行 |
| `main.py` | `main()` 235 行（含多個巢狀 closure） |
| 全專案 | 77 個函式超過 100 行、143 個超過 60 行 |

- **原因**：重畫版面等於重寫這些建構函式；版面與行為混在一起，改外觀容易動到邏輯，也難寫測試。
- **建議**：View 只做「組裝」，拆成 `build_xxx` 函式 / 小元件 + presenter / state。
  `app/views/cache_manager/`（actions / state / controller / presenter）已有 MVC 雛形，可當範本，
  但目前只接了一半，`cache_view.py` 仍內含大部分邏輯。

### P1-6　共用 UI 元件層太薄

- **現況**：`app/ui/components.py` 中，`styled_card`（50 處）、`primary_button`（15）、
  `secondary_button`（11）、`empty_state`（4）有人用；
  `section_header`、`create_snackbar`、`loading_state`、`error_state` **0 處使用**。
- **原因**：設計稿反覆出現的元件（路徑欄位 + 瀏覽、進度 + 日誌 + 取消面板、開關列、統計卡、
  分頁器、表格、步驟列）目前每頁各自拼裝，重設計時會重複改十幾次。
- **建議**：抽成 `app/ui/kit/`，優先做 **執行面板**（進度 + 日誌 + 取消）與 **路徑欄位**，
  其次是統計卡、分頁器、開關列。

### P1-7　長任務執行方式不統一

- **現況**：
  - `lm_view`、`merge_view`、`extractor_dialog`、`translation_actions`：TaskSession + UI 輪詢；
  - `qc_view`：`UiBatcher`；
  - `lookup_view`：背景執行緒直接更新控制項並 `page.update()`。
  - 全 `app/views`：`threading.Thread` 18 處、`page.update()` 167 處、`page.run_task` 等 41 處。
- **原因**：設計稿的「全域任務膠囊」與工作台需要單一任務事件來源。
- **建議**：做 `TaskManager`：統一註冊 `TaskSession`、訂閱進度 / 狀態、提供取消；
  所有流水線都走同一條，頂部膠囊與工作台直接訂閱。

### P1-8　View 直接碰設定與作業系統

- **現況**：`app/views` 內 `load_config / config_manager` 共 44 處（13 個檔案，集中在
  `config_actions.py`、`config_view.py`、`merge_view.py`、各 pipeline dialog）；
  `os.startfile / subprocess` 10 處；`open(` 7 處。合併頁與設定頁的雙向同步是手動處理。
  `extract_service.py` 已寫明「UI 不應自行 load_config」，但只有提取頁做到。
- **原因**：設定頁與多個頁面互相同步，且設定頁版面要重做；沒有統一的讀寫與通知就容易不同步。
- **建議**：`ConfigStore`（讀 / 寫 / 變更通知），開資料夾等動作收斂到 service。

### P1-9　導覽資料分散，View 建構簽名不一致

- **現況**：`_VIEW_NAV`（圖示、名稱）、`_VIEW_IMPORT_MAP`（模組、類別）、`VIEW_WINDOW_SIZES`
  是三張平行表；`LazyViewItem` 繼承 `dict` 並覆寫 `__getitem__` 做延遲建立。
  View 建構簽名混用 `(page)` 與 `(page, file_picker)`。
- **原因**：新設計需要導覽分組、徽章、工作台與任務膠囊，現有資料模型放不下。
- **建議**：單一 `ViewSpec` dataclass（key、label、group、icon、module、class、
  window_size、badge 來源…），導覽由資料驅動；View 統一成 `View(ctx)`，
  `ctx` 內含 `page / file_picker / config / tasks / theme`。

---

## P2：引擎與工程品質

| # | 問題 | 說明 |
|---|---|---|
| P2-10 | `translation_tool/core/lang_item_row.py` 使用 flet 並 import `app.icon_reader` | 「引擎不含 UI」唯一的例外；移到 `app/ui/` |
| P2-11 | 核心翻譯函式過長 | `lm_translator.translate_directory_generator` 679 行；`lm_translator_main.translate_batch_smart_old` 728 行（名稱叫 old，但 `_execute_translation` 仍轉呼叫它，實際是現役）；FTB / KubeJS / MD 三個 `*_lmtranslator.py` 各 370~580 行，流程相似（checkpoint、cache loop、批次 flush、進度），可抽共用骨架 |
| P2-12 | 設定為全域單例 | `translation_tool/core` 有 16 個檔案各自 `load_config`；測試需清快取。設定頁約 60 個欄位逐一硬寫，可改成由設定結構描述（schema）驅動 |
| P2-13 | 例外與除錯輸出 | `except Exception` 289 處；非工具程式碼的 `print(` 36 處；TODO / FIXME 19 處；標註 legacy / 相容的註解 86 處，值得定期盤點 |
| P2-14 | 工具鏈缺口 | 無 ruff 設定檔（CI 只對變更檔 lint）；無型別檢查；`pyproject.toml` 的 `description` 仍是預設文字、`pytest` 同時在主依賴與 dev group、`httpx` 與 `tqdm` 沒有任何 import、`flet` 與三個 `flet-*` 套件可能重複宣告（需確認） |
| P2-15 | 測試 | 整體健康；但 UI 測試多為 mock page 的結構測試，重構 View 時要補「行為層」測試，避免只靠 import 測試 |

---

## 進行中工作：Issue #108 / #109 / #111

> 這三項是引擎層的功能與修正，與上面的 UI 重構清單互相獨立，已先行處理。
> 基準：`main` @ `634b4f1`（含 #105、#107）。每個 issue 獨立 commit，方便日後拆分。
> **狀態：三項皆已完成並有測試**（#109 → #111 → #108）。#108 的設計說明見 `docs/LM_TOKEN_BUDGET.md`。

| 順序 | Issue | 階段 | 內容 | 驗收 |
|---|---|---|---|---|
| 1 | #109 語言合併預算用盡 | A | `lang_merger.py`：executor 結束後檢查 `zip_budget.exhausted`；用盡時 `log_error("預算用盡，輸出不完整")`，並 `yield {"error": True}`，取代「全部處理完成」 | log 有 pack 層級錯誤；不再只顯示成功訊息 |
| | | B | 測試：小預算 pack ZIP 走 `merge_zhcn_to_zhtw_from_zip`，確認回報錯誤旗標；預算充足時行為不變 | 新測試通過、既有測試不變 |
| 2 | #111 預掃描與 JAR 清單不一致 | A | `jar_browser.scan_jars` 新增可選 `jar_files`（預設維持 `glob("*.jar")`）；`run_extraction_process_impl` 傳入 `find_jar_files` 的清單 | 預掃描清單與實際清單一致 |
| | | B | 修正日誌數字（已掃描數 / 含可提取內容數）；測試：巢狀 JAR 被預掃描且共用同一份預算 | `scan_jars` 既有測試與 `icon_preview_view` 行為不變 |
| 3 | #108 token 預算切批 | 0 | `call_gemini_requests` 以 `meta_out` 回報 `finishReason` 與 `usageMetadata`（含思考 token），維持回傳字串；截斷時 log 原因（`MAX_TOKENS` 或 `STOP` 但 JSON 壞） | 截斷時可從 log 看出原因與 token 用量 |
| | | 1a | 新增 `lm_batch_budget.py`：本地 token 估算、依預算取前綴、依 profile 保存預算（撞牆減半、連續成功後緩慢回升）、輸出係數以實際用量校正 | 單元測試涵蓋估算、切批、保存、回升 |
| | | 1b | 明確設定 `maxOutputTokens`；`lm_translator_main`、`lm_translator_shared_loop`、`lm_translator` 三處切批共用同一個估算與預算；維持「回傳為輸入前綴、依位置對應」契約 | 不重翻、不漏翻；截斷後預算保留到後續批次 |
| | | 1c | `lm_translator` 區段新增設定（預設值、`config.example.json`、`config_manager` 驗證）與測試 | 新設定有預設、有驗證、有測試 |
| 4 | 收尾 | | 全部測試與 ruff；更新 PR #106 說明；回報 | pytest 全過 |

### 決策紀錄

- **#109 採建議做法 (a)**：不改例外架構、不取消尚未開始的 futures（(b) 另議）。
- **#111 採可選參數**：`scan_jars(jar_dir, patterns, ..., jar_files=None)`；巢狀 JAR 的預掃描內容會在提取期間留在記憶體，這是 issue 已說明的取捨。
- **#108 不用 `countTokens` API**，也不動 TPM 限流；先做階段 0 觀測，階段 1 的係數預設保守，並由實際用量校正。
- **#108 補充**：輸出校正把思考 token 一併計入；`maxOutputTokens` 超過模型上限時給出明確錯誤；停用開關 `token_budget_enabled` 可回到舊行為。
- **#108 終止保證**：截斷時先縮小學到的預算；若縮小後實際批次沒有變小，才走既有的項目數縮小流程，因此每輪都會嚴格變小或放棄該批。

## 建議順序與 PR 切法

1. **P0 清理**：拆 2~3 個小 PR（TaskSession 轉接、死碼與殘留、文件）。行為不變，測試當安全網。
2. **基礎建設**：主題 token + `ft.Theme` → `app/ui/kit/` → `ViewSpec` + 導覽 → `TaskManager` → `ConfigStore`。
   每個一個 PR，先不改頁面外觀。
3. **逐頁套新設計**：先小頁（學名查詢、替換規則、打包、QC），再中型頁（流水線、提取、合併、機器翻譯、任務翻譯、設定），
   最後大頁（快取、翻譯校對，需先完成 P1-5 拆分）。
4. **P2** 穿插進行，不阻擋 UI 串接。

## 重現指令（動手前先重新量測）

```bash
# 環境（本機建議用 uv；專案需要 Python 3.12）
uv sync --python 3.12 --group dev

# 測試
uv run python -m pytest -q

# 死碼 / 未使用
uv run ruff check app translation_tool main.py --select F401,F811,F841 --statistics

# 巨型檔案與函式
git ls-files '*.py' | grep -v '^tests/' | xargs wc -l | sort -rn | head -30

# 顏色硬編碼
grep -rhoE "ft\.Colors\.[A-Z_0-9]+" app/views app/ui | wc -l
grep -rhoE "theme\.(RED|GREEN|BLUE|GREY|AMBER|ORANGE|YELLOW|BLUE_GREY|TEAL|PURPLE|CYAN)[A-Z_0-9]*" app/views | wc -l

# View 內直接碰設定 / OS
grep -rn "load_config\|config_manager" --include=*.py app/views | wc -l
grep -rn "os.startfile\|subprocess" --include=*.py app/views | wc -l
```
