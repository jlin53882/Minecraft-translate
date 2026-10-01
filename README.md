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
- **快取管理**：翻譯快取分片儲存、全文搜尋（SQLite FTS5）、版本歷史
- **品管檢查**：偵測未翻譯條目、簡繁不一致、英文殘留
- **學名查詢**：生物學名的 Wikipedia 查詢與快取
- **輸出打包**：產出可直接使用的資源包 ZIP
- **工作台與任務追蹤**：頂列任務膠囊、側欄執行中標記、狀態列；任務執行中切換頁面不會中斷
- **深色 / 淺色主題**，設計稿見 [`docs/design/ui-redesign/`](docs/design/ui-redesign/)

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
| 資料庫 | 快取管理 | 快取總覽、查詢、分片 | `Ctrl+9` |
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

> ⚠️ 目前關閉視窗會中斷進行中的任務，尚未寫入磁碟的快取與進度可能遺失。
> 長時間任務請保持視窗開啟。相關改善追蹤於 [#126](https://github.com/jlin53882/Minecraft-translate/issues/126)、[#127](https://github.com/jlin53882/Minecraft-translate/issues/127)。

## 設定說明

`config.json` 主要區塊（完整預設值見 [`config.example.json`](config.example.json)）：

| 區塊 | 說明 |
|---|---|
| `ui` | 介面偏好（`theme_mode`：`dark` / `light`） |
| `logging` | 日誌等級與輸出目錄 |
| `translator` | 輸出資料夾、快取目錄、平行處理 worker 數、替換規則檔路徑 |
| `ftb_translator` | FTB 任務翻譯輸出資料夾 |
| `species_cache` | 生物學名快取（Wikipedia 查詢） |
| `lm_translator` | Gemini API Keys、模型清單、溫度、批次大小、token 預算、Key 冷卻、System Prompt |
| `output_bundler` | 最終 ZIP 打包名稱 |
| `lang_merger` | 待翻譯與隔離資料夾命名、簡中處理選項 |
| `jar_extractor` / `extractor` | 提取的語言代碼與各類輸出資料夾名稱 |

重點：

- **API Key**：填在 `lm_translator.keys`（陣列，可多把輪替）。設定頁的 Key 欄位預設遮蔽。
- **模型**：`lm_translator.models` 中把要使用的模型設為 `enabled: true`。
- **token 預算與 Key 冷卻**（`token_budget_*`、`max_output_tokens`、`key_failure_cooldown_sec` 等）：**目前設定頁尚未提供欄位**，需要直接編輯 `config.json`；說明見 [`docs/LM_TOKEN_BUDGET.md`](docs/LM_TOKEN_BUDGET.md)、[`docs/LM_KEY_HEALTH.md`](docs/LM_KEY_HEALTH.md)（補齊欄位追蹤於 [#134](https://github.com/jlin53882/Minecraft-translate/issues/134)）。
- **何時生效**：多數設定在「下一次任務」才套用，少數需要重新啟動；逐項說明見 [`docs/CONFIG_APPLY_TIMING.md`](docs/CONFIG_APPLY_TIMING.md)。

> ⚠️ `config.json` 含 API Key，**以明文儲存**，已加入 `.gitignore`，請勿上傳或分享給他人。
> 關於 Key 的儲存與日誌遮蔽，審查項目追蹤於 [#125](https://github.com/jlin53882/Minecraft-translate/issues/125)。

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
docs/                   架構、設計與計畫文件
```

設計重點：

- **單一資料來源**：頁面清單、導覽分組、快捷鍵都在 `app/view_registry.py` 的 `ViewSpec`，新增頁面只要加一筆。
- **語意色主題**：顏色一律用語意字串（`C.PANEL`、`C.EM`…），切換深 / 淺色不需重建畫面。
- **任務事件單一來源**：頁面照原本方式使用 `TaskSession`，`TaskManager` 會自動收集，頂列與工作台直接訂閱。
- **Flet 1.0 單執行緒模型**：背景執行緒不得直接改 Control；UI 更新要排程回 page 的 event loop（`page.run_task`）。

完整說明見 [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md)、[`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)。

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
- 目前尚無 ruff 設定檔與型別檢查（追蹤於 [#128](https://github.com/jlin53882/Minecraft-translate/issues/128)）。

Windows 若遇到 `WinError 5`（使用者目錄快取/暫存權限），建議改用 repo 內路徑：

```powershell
$env:UV_CACHE_DIR = ".uv-cache"
$env:TMP = ".tmp"
$env:TEMP = ".tmp"
uv run pytest -q --basetemp=.pytest-tmp\full -o cache_dir=.pytest-cache\full
```

## 已知限制與後續規劃

後續工作都以 GitHub issue 追蹤，主要方向：

- **穩定性**：設定檔 atomic write（[#129](https://github.com/jlin53882/Minecraft-translate/issues/129)）、設定存檔後的套用時機（[#117](https://github.com/jlin53882/Minecraft-translate/issues/117)）、`replace_rules_path` 讀取位置的 bug（[#118](https://github.com/jlin53882/Minecraft-translate/issues/118)）。
- **任務體驗**：工作台統計即時刷新與關閉視窗確認（[#126](https://github.com/jlin53882/Minecraft-translate/issues/126)）、關閉視窗後繼續執行的架構決策（[#127](https://github.com/jlin53882/Minecraft-translate/issues/127)）。
- **品質**：視覺與行為驗收（[#123](https://github.com/jlin53882/Minecraft-translate/issues/123)）、效能 baseline（[#124](https://github.com/jlin53882/Minecraft-translate/issues/124)）、安全審查（[#125](https://github.com/jlin53882/Minecraft-translate/issues/125)）。
- **重構**：巨型 View 與引擎函式拆分（[#114](https://github.com/jlin53882/Minecraft-translate/issues/114)、[#115](https://github.com/jlin53882/Minecraft-translate/issues/115)）。

完整清單請看 [Issues](https://github.com/jlin53882/Minecraft-translate/issues)。

## 文件索引

| 文件 | 內容 |
|---|---|
| [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) | 設計 token、UI kit、外殼、新增頁面步驟 |
| [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)、[`docs/PROJECT_INDEX.md`](docs/PROJECT_INDEX.md) | 專案結構與索引 |
| [`docs/LM_TOKEN_BUDGET.md`](docs/LM_TOKEN_BUDGET.md)、[`docs/LM_KEY_HEALTH.md`](docs/LM_KEY_HEALTH.md) | token 預算切批、API Key 健康度 |
| [`docs/CONFIG_APPLY_TIMING.md`](docs/CONFIG_APPLY_TIMING.md) | 設定「何時生效」逐項盤點 |
| [`docs/CACHE_SYSTEM.md`](docs/CACHE_SYSTEM.md)、[`docs/cache_search_optimization.md`](docs/cache_search_optimization.md) | 快取系統與搜尋索引 |
| [`docs/JAR_PIPELINE.md`](docs/JAR_PIPELINE.md)、[`docs/TRANSLATION_WORKFLOW.md`](docs/TRANSLATION_WORKFLOW.md) | JAR 提取與翻譯流程 |
| [`docs/REFACTOR_PLAN.md`](docs/REFACTOR_PLAN.md)、[`docs/REFACTOR_AUDIT.md`](docs/REFACTOR_AUDIT.md) | 重構計畫與審查 |
| [`docs/TEST_STRATEGY.md`](docs/TEST_STRATEGY.md)、[`docs/PR_WORKFLOW.md`](docs/PR_WORKFLOW.md)、[`docs/RELEASE_WORKFLOW.md`](docs/RELEASE_WORKFLOW.md) | 測試策略、PR 與發佈流程 |

> 各頁面的 `*_VIEW_ARCHITECTURE.md` 寫於 UI 重新設計之前，部分內容可能已過期（追蹤於 [#120](https://github.com/jlin53882/Minecraft-translate/issues/120)）。

## 授權

MIT License，詳見 [`LICENSE`](LICENSE)。
