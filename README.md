# MC 繁化工坊（Minecraft-translate）

[![CI](https://github.com/jlin53882/Minecraft-translate/actions/workflows/ci.yml/badge.svg)](https://github.com/jlin53882/Minecraft-translate/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

基於 [Flet](https://flet.dev/) 的桌面應用程式，把 Minecraft 模組包從簡體中文／英文批量翻譯成繁體中文（台灣用語）。
從 JAR 提取、語言合併、AI 機器翻譯、品管檢查到資源包打包，一個介面完成整條流程。

> 目前版本以 [`pyproject.toml`](pyproject.toml) 為準；變更記錄見 [`CHANGELOG.md`](CHANGELOG.md)。

## 功能

- **JAR 提取**：從模組 JAR 檔提取語言檔與 Patchouli 手冊
- **語言合併**：智慧合併 `en_us.json`、`zh_cn.json`、`zh_tw.json`，保留已翻譯內容
- **簡繁轉換**：基於 OpenCC S2TW，含自訂替換規則
- **AI 機器翻譯**：串接 Gemini API
  - 多把 API Key 輪替；額度用盡（RPD）或無權限（403）的 Key 會暫時冷卻，不再被重複請求
  - 依 token 預算切批，避免批次被截斷後才縮小而浪費額度
  - 依錯誤類型自動縮批、重試、換 Key、節流
- **Mod 翻譯資料庫**：分版本的 SQLite 翻譯記憶庫；掃描 JAR／資源包匯入、條目校對、批次機翻、AI 修復既有譯文與逐筆審查、批次取代
- **快取管理**：翻譯快取分片儲存、全文搜尋（SQLite FTS5）、版本歷史
- **品管檢查**：偵測未翻譯條目、簡繁不一致、英文殘留
- **學名查詢**：生物學名的 Wikipedia 查詢與快取
- **輸出打包**：產出可直接使用的資源包 ZIP
- **工作台與任務追蹤**：頂列任務膠囊、側欄執行中標記、狀態列；任務執行中切換頁面不會中斷
- **深色 / 淺色主題**

## 支援的翻譯來源格式

| 格式 | 說明 |
|---|---|
| `lang/*.json` | Minecraft 標準語言檔 |
| `patchouli_books/` | Patchouli 手冊 JSON |
| `ftbquests/*.snbt` | FTB Quests 任務檔 |
| `kubejs/*.js` | KubeJS Tooltip 腳本 |
| `*.md` | Markdown 文件 |

## 畫面與導覽

左側是分組導覽，頂列顯示目前任務與 API Key 狀態，底部是狀態列。

| 分組 | 頁面 | 用途 | 快捷鍵 |
|---|---|---|---|
| 工作流程 | 工作台 | 流程進度、近期活動、快取與規則統計、Key 健康度 | `Ctrl+1` |
| | 一鍵流水線 | 提取 → 合併 → 翻譯 → 打包一次完成 | `Ctrl+2` |
| | JAR 提取 | 提取語言檔與 Patchouli 手冊 | `Ctrl+3` |
| | 語系合併 | 合併 `en_us` / `zh_cn` / `zh_tw` | `Ctrl+4` |
| | 機器翻譯 | Gemini 批次翻譯 | `Ctrl+5` |
| | 任務翻譯 | FTB Quests / KubeJS / Markdown 翻譯 | `Ctrl+6` |
| 品管與校對 | QC 檢驗 | 未翻譯、簡繁不一致、英文殘留 | `Ctrl+7` |
| | 翻譯校對 | 圖示預覽與逐項校對 | `Ctrl+8` |
| 資料庫 | Mod 資料庫 | 總覽、掃描匯入、條目校對、批次機翻與 AI 修復 | — |
| | 快取管理 | 快取總覽、查詢、分片 | `Ctrl+9` |
| | 替換規則 | 簡繁替換規則（支援純文字與正規表示式） | `Ctrl+0` |
| | 學名查詢 | 生物學名查詢 | — |
| 輸出 | 資源包打包 | 產出資源包 ZIP | — |
| 系統 | 設定 | 編輯 `config.json` | — |

- `Ctrl+P`：快速跳轉（搜尋頁面與動作）。
- 側欄底部可切換深色 / 淺色主題，偏好會記在 `config.json` 的 `ui.theme_mode`。

## 安裝

需求：Python `>=3.12`、[uv](https://github.com/astral-sh/uv)

```bash
# 1) Clone
git clone https://github.com/jlin53882/Minecraft-translate.git
cd Minecraft-translate

# 2) 安裝依賴
uv sync

# 3) 建立設定檔
cp config.example.json config.json
# 編輯 config.json，在 lm_translator.keys 填入 Gemini API Key
```

## 使用

```bash
uv run python main.py
```

啟動後會開啟桌面 GUI。

> ⚠️ 任務執行中關閉視窗會先跳出確認；確定關閉會中斷任務，尚未落盤的資料可能遺失。
> 被中斷的機器翻譯在下次啟動時會詢問是否續跑（不會自動開始），行為見 [`docs/developer-guide/resume-capability.md`](docs/developer-guide/resume-capability.md)。

## 設定說明

`config.json` 主要區塊（完整預設值見 [`config.example.json`](config.example.json)）：

| 區塊 | 說明 |
|---|---|
| `ui` | 介面偏好（`theme_mode`：`dark` / `light`） |
| `logging` | 日誌等級與輸出目錄 |
| `translator` | 輸出資料夾、快取目錄、平行處理 worker 數、替換規則檔路徑 |
| `ftb_translator` | FTB 任務翻譯輸出資料夾 |
| `species_cache` | 生物學名快取（Wikipedia 查詢） |
| `translation_db` | Mod 翻譯資料庫路徑、使用時機、版本與來源優先序 |
| `lm_translator` | Gemini API Keys、模型清單、溫度、批次大小、token 預算、Key 冷卻、System Prompt |
| `output_bundler` | 最終 ZIP 打包名稱 |
| `lang_merger` | 待翻譯與隔離資料夾命名、簡中處理選項 |
| `jar_extractor` / `extractor` | 提取的語言代碼與各類輸出資料夾名稱 |

重點：

- **API Key**：填在 `lm_translator.keys`（陣列，可多把輪替）。設定頁的 Key 欄位預設遮蔽。
- **模型**：`lm_translator.models` 中把要使用的模型設為 `enabled: true`。
- **token 預算與 Key 冷卻**：設定頁的「Token 預算」與「批次與限制」分類可直接編輯；說明見 [`docs/developer-guide/lm-token-budget.md`](docs/developer-guide/lm-token-budget.md)、[`docs/developer-guide/lm-key-health.md`](docs/developer-guide/lm-key-health.md)。
- **何時生效**：多數設定在「下一次任務」才套用，少數需要重新啟動；逐項說明見 [`docs/reference/configuration-apply-timing.md`](docs/reference/configuration-apply-timing.md)。

> ⚠️ `config.json` 含 API Key，**以明文儲存**，已加入 `.gitignore`，請勿上傳或分享給他人。
> Key 在日誌與錯誤訊息中的遮蔽規則見 [`docs/developer-guide/security-error-handling-audit.md`](docs/developer-guide/security-error-handling-audit.md)。

## 架構概覽

```text
main.py                 入口（初始化 runtime 並啟動 Flet）
app/                    UI 層
  shell/                應用外殼：側欄、頂列、狀態列、快速跳轉、TaskManager
  ui/                   design.py（主題 token）、kit/（共用元件）、keyboard_shortcuts.py…
  views/                各功能頁面（含子模組）
  tasks/                TaskSession 與結構化日誌（與 UI 無關）
  services_impl/        pipeline 業務邏輯（提取、合併、翻譯、打包…）
  config_store.py       設定的讀 / 寫 / 變更通知
  view_registry.py      頁面登錄（導覽分組、快捷鍵、視窗尺寸的單一來源）
translation_tool/       核心翻譯引擎（不含 UI）
  core/                 語言合併、JAR 處理、Gemini 翻譯、Key 輪替與健康度、token 預算
  plugins/              格式插件：ftbquests、kubejs、md、shared
  checkers/             品管檢查器
  utils/                設定管理、快取（SQLite FTS5）、文字處理…
tests/                  單元與整合測試
docs/                   長期維護文件（架構、契約、操作說明、ADR）
```

設計重點：

- **單一資料來源**：頁面清單、導覽分組、快捷鍵都在 `app/view_registry.py` 的 `ViewSpec`，新增頁面只要加一筆。
- **語意色主題**：顏色一律用語意字串（`C.PANEL`、`C.EM`…），切換深 / 淺色不需重建畫面。
- **任務事件單一來源**：頁面照原本方式使用 `TaskSession`，`TaskManager` 會自動收集，頂列與工作台直接訂閱。
- **Flet 1.0 單執行緒模型**：背景執行緒不得直接改 Control；UI 更新要排程回 page 的 event loop（`page.run_task`）。

完整說明見 [`docs/developer-guide/ui-design-system.md`](docs/developer-guide/ui-design-system.md)、[`docs/developer-guide/codebase-ownership.md`](docs/developer-guide/codebase-ownership.md)。

## 開發與測試

```bash
# 全量測試
uv run pytest -q

# lint 與格式檢查（CI 對 PR 變更的 .py 檔強制執行）
uv run ruff check <變更的檔案>
uv run ruff format --check <變更的檔案>
```

- CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）：`test` 跑完整測試；`lint` 只對 PR 變更的 Python 檔跑 `ruff check` 與 `ruff format --check`。
- 新增或修改的程式請搭配測試；翻譯引擎的行為變更需有「修正前會失敗」的回歸測試。
- ruff 規則設定在 `pyproject.toml` 的 `[tool.ruff]`；目前沒有型別檢查。

Windows 若遇到 `WinError 5`（使用者目錄快取/暫存權限），建議改用 repo 內路徑：

```powershell
$env:UV_CACHE_DIR = ".uv-cache"
$env:TMP = ".tmp"
$env:TEMP = ".tmp"
uv run pytest -q --basetemp=.pytest-tmp\full -o cache_dir=.pytest-cache\full
```

## 已知限制與後續規劃

後續工作以 [GitHub Issues](https://github.com/jlin53882/Minecraft-translate/issues) 追蹤；Windows 才能驗證的項目（打包版、檔案落盤、強制結束）由維護者在 Windows 實機確認。

## 文件索引

完整的依讀者導覽從 [`docs/index.md`](docs/index.md) 開始；下表保留常用文件的直接入口。

| 文件 | 內容 |
|---|---|
| [`docs/page_flows/`](docs/page_flows/README.md) | 每個頁面的處理流程圖（操作 → 背景工作 → 結果回到畫面） |
| [`docs/developer-guide/views/`](docs/developer-guide/views/) | 各頁面的元件、呼叫鏈與 service 層關係 |
| [`docs/developer-guide/ui-design-system.md`](docs/developer-guide/ui-design-system.md) | 設計 token、UI kit、外殼、新增頁面步驟 |
| [`docs/developer-guide/codebase-ownership.md`](docs/developer-guide/codebase-ownership.md)、[`docs/developer-guide/architecture.md`](docs/developer-guide/architecture.md) | 專案結構、系統地圖與 ownership |
| [`docs/developer-guide/operation-lifecycle.md`](docs/developer-guide/operation-lifecycle.md)、[`docs/developer-guide/worker-thread-audit.md`](docs/developer-guide/worker-thread-audit.md) | 長任務生命週期、worker thread 與 UI 更新契約 |
| [`docs/user-guide/mod-translation-db.md`](docs/user-guide/mod-translation-db.md) | Mod 翻譯資料庫 |
| [`docs/developer-guide/lm-token-budget.md`](docs/developer-guide/lm-token-budget.md)、[`docs/developer-guide/lm-key-health.md`](docs/developer-guide/lm-key-health.md) | token 預算切批、API Key 健康度 |
| [`docs/reference/configuration-apply-timing.md`](docs/reference/configuration-apply-timing.md)、[`docs/reference/configuration.md`](docs/reference/configuration.md) | 設定「何時生效」與設定頁 schema |
| [`docs/developer-guide/cache.md`](docs/developer-guide/cache.md) | 快取系統與搜尋索引 |
| [`docs/developer-guide/jar-pipeline.md`](docs/developer-guide/jar-pipeline.md)、[`docs/user-guide/translation-workflow.md`](docs/user-guide/translation-workflow.md)、[`docs/developer-guide/resume-capability.md`](docs/developer-guide/resume-capability.md) | JAR 提取、翻譯流程與重開後續跑 |
| [`docs/contributing/testing.md`](docs/contributing/testing.md)、[`docs/contributing/review-checklist.md`](docs/contributing/review-checklist.md) | 測試策略與審查清單 |
| [`docs/contributing/pr-workflow.md`](docs/contributing/pr-workflow.md)、[`docs/contributing/release.md`](docs/contributing/release.md)、[`RELEASE_STRATEGY.md`](RELEASE_STRATEGY.md) | GitHub、發佈流程與版本策略 |

## 授權

MIT License，詳見 [`LICENSE`](LICENSE)。
