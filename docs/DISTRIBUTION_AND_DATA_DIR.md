# 發佈形式與資料目錄契約（#137）

> 本文件記錄**目前實際支援的發佈形式**與可寫資料的位置。結論來自讀 repo 的靜態盤點；
> 「built app 實測」尚未進行（需 Windows 環境），見最後一節。

## 支援的發佈形式

**僅支援原始碼執行**：

- README 的安裝流程是 `git clone` → `uv sync` → `cp config.example.json config.json` → `uv run python main.py`。
- `.github/workflows/release.yml` 只執行測試並建立 GitHub Release 草稿；沒有 `flet build` / `flet pack` /
  PyInstaller 等打包步驟，也沒有產出可執行檔。
- 因此 Flet 官方文件所說的「built app 的唯讀 bundle」目前**不適用**本專案。

若未來改成打包發佈，必須先完成本文件「資料目錄契約」的遷移，再發佈（見下）。

## 資料目錄契約（目前）

可寫資料一律相對於 `PROJECT_ROOT`（`translation_tool/utils/config_manager.py` 的
`Path(__file__).resolve().parents[2]`，`app/services_impl/config_service.py` 另有一份相同定義）：

| 資料 | 預設位置 | 來源 |
|---|---|---|
| 使用者設定 | `config.json` | `CONFIG_PATH` |
| 預設設定範本（唯讀） | `config.example.json` | `EXAMPLE_PATH` |
| 替換規則 | `replace_rules.json`（`translator.replace_rules_path`） | `config_manager` |
| 翻譯快取 | `快取資料/`（`cache_manager._CACHE_DIR_NAME`，設定 `translator.cache_directory`） | `utils/cache_manager.py` |
| 學名資料庫 | `學名資料庫/`（`species_cache.cache_directory`） | `utils/species_cache.py` |
| 日誌 | `logs/<日期>/app.log`（`logging.log_dir`，經 `resolve_project_path`） | `config_manager.setup_logging` |
| 錯誤記錄 | `logs/errors_<日期>.log` | `utils/exceptions.py`（**相對於目前工作目錄**，不是 `PROJECT_ROOT`） |
| 翻譯 checkpoint | `logs/translation_checkpoint.json`（**相對於目前工作目錄**） | `core/lm_translator.CHECKPOINT_FILE` |
| 圖示快取 | `.icon_cache/`（`Path(__file__)` 往上推算） | `app/icon_index.py`、`app/views/icon_preview_view.py` |
| 輸出資料夾 | 各 `*_dir_name` / `*_folder_name` 設定，多數經 `resolve_project_path` | `config_manager.DEFAULT_CONFIG` |

### 已發現的不一致（待後續處理，本 PR 不改行為）

1. `logs/errors_*.log` 與 `logs/translation_checkpoint.json` 使用**相對路徑**，實際位置取決於啟動時的工作目錄；
   從專案根目錄以外的位置啟動會寫到別處。這也會影響 #127 的「重開後續跑」（checkpoint 找不到）。
2. `PROJECT_ROOT` 在兩處各自定義，未來若要集中成「資料根目錄」需同時處理。
3. `.icon_cache/` 沒有走設定也沒有走 `PROJECT_ROOT`。

## 若未來要支援 built app（尚未決定）

依 Flet 官方文件（built app 的 app 檔案為唯讀 bundle，工作目錄改為 app-private 可寫目錄）：

- 把「可寫資料根目錄」集中成單一函式，預設維持 `PROJECT_ROOT`（原始碼模式行為不變），並可由環境變數
  或 `FLET_APP_STORAGE_DATA` 覆蓋。
- 唯讀資源（`config.example.json`、`assets`）以 `__file__` 讀取。
- 上表三個不一致項目需一併改為走同一個資料根目錄。
- 既有使用者資料遷移需明確處理。
- 原始碼模式下可在啟動時偵測資料目錄不可寫並給出清楚錯誤。

## 尚未驗證

- built app 在 Windows 上的實際行為（需實機）。
- `flet run` 開發模式把工作目錄設到 `<project>/.flet/storage/data` 時，上述相對路徑項目的實際落點。
