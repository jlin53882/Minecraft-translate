# 發佈形式與資料目錄契約（#137）

> 本文件記錄發佈形式與可寫資料的位置。結論來自讀 repo 的靜態盤點；
> 打包後的實測尚未進行（需 Windows 環境），見最後一節。

## 發佈形式

實際發佈給使用者的是**打包的 exe**。建議結構是 **`app/` 與 `data/` 分離**：

```
<安裝資料夾>/
 ├─ MinecraftTranslator.bat     ← 啟動器（每次更新覆蓋）
 ├─ app/                        ← 程式：每次更新整個換掉
 │   ├─ MinecraftTranslator.exe
 │   ├─ assets/、config.example.json、pyproject.toml、DLL/PYD …   （唯讀資源）
 └─ data/                       ← 使用者資料：更新時完全不碰
     ├─ config.json、replace_rules.json
     └─ logs/、快取資料/、學名資料庫/、.icon_cache/、custom_translators/
```

舊版平面式（exe 與資料放同一資料夾）仍然支援，行為與過去相同：只有「exe 所在資料夾名稱為 `app`」才會啟用分離式。
原始碼執行（`uv run python main.py`）不受影響。

打包流程（Nuitka standalone）不在 repo 內。`tools/build_exe.bat` 是依此結構整理的**草稿，尚未在 Windows 實測**。

> 先前版本的本文件曾寫「只支援原始碼」，是錯的：當時只看了 repo 內的 README 與 `release.yml`。

## 兩種根目錄

單一決定點：`translation_tool/utils/app_paths.py`。

| 函式 | 用途 | 原始碼模式 | 打包（分離式） | 打包（平面式） |
|---|---|---|---|---|
| `get_resource_root()` | 隨程式附帶的**唯讀**檔案：`assets/`、`config.example.json`、`pyproject.toml`、`resource_pack_version.json` | 專案根目錄 | `app/` | exe 所在資料夾 |
| `get_data_root()` | 使用者**可寫**資料：設定、規則、快取、日誌、checkpoint、icon 快取 | 專案根目錄 | `<根>/data` | exe 所在資料夾 |

`get_data_root()` 的決定順序：

1. 環境變數 `MCT_DATA_DIR`（明確覆蓋；不觸發遷移）。
2. 打包後且 exe 位於名為 `app` 的資料夾：`<app 的上一層>/data`。
3. 打包後但 exe 不在 `app` 資料夾：exe 所在資料夾（平面式）。
4. 原始碼執行：專案根目錄。

不要用 `Path(__file__)` 推算位置：打包後它可能指向 `_internal/` 或暫存解壓目錄。

### 舊版資料遷移（分離式首次啟動）

舊使用者把新版放進同一個安裝資料夾後，使用者資料仍在 `<根>/`（`app/` 的上一層）。第一次呼叫 `get_data_root()` 時會一次性搬進 `data/`：

- 只搬已知項目：`config.json`、`replace_rules.json`、`logs/`、`快取資料/`、`學名資料庫/`、`.icon_cache/`、`custom_translators/`。其他檔案不碰。
- `data/` 內已有同名項目時**不覆蓋、不合併**，舊位置的檔案保留。
- 單一項目搬移失敗只記錄，不影響啟動，下次啟動再試。
- 每個行程只執行一次，重複執行是冪等的。
- 舊版遺留的程式檔（舊 exe 等）不會被刪，確認新版正常後可手動刪除。

### 重新打包：換掉 `app/`、保留 `data/`

流程：

1. Nuitka 先輸出到 `dist\_staging`（只清這個暫存資料夾）。
2. 驗證 staging 內有 exe 才發佈；build 失敗時正式資料夾完全不動。
3. `tools/publish_dist.py` 先複製到 `app.new`，再 `app → app.old`、`app.new → app`，最後刪 `app.old`。
   - 新版沒有的舊 DLL / PYD / 套件不會殘留，結果等價於乾淨 build。
   - 任何一步失敗都會還原 `app/`；程式正在執行（檔案被鎖）時會失敗並提示先關閉。
   - `data/` 從不被刪除或覆蓋；`config.json`、`replace_rules.json` 僅在 `data/` 內不存在，**且舊版平面式的根目錄也沒有同名檔**時才建立。舊使用者的檔案優先，由新版首次啟動搬進 `data/`；若發佈時先放預設檔，migration 會因 `data/` 已有同名檔而跳過，使用者設定看起來就被重置。
   - 上次中斷遺留的 `app.new` / `app.old` 會在下次發佈時清掉。

已知限制：

- 替換不是單一原子操作（Windows 沒有「目錄交換」），但每一步失敗都會還原，且 `data/` 不在任何一步的範圍內。
- 如果防毒軟體正掃描舊的 `app.old`，刪不掉只會留下殘留資料夾，不影響使用。

契約由 `tests/test_publish_dist.py`、`tests/test_app_paths.py` 保護，其中包含完整升級生命週期的整合測試（舊版平面式安裝 → publish → 新版首次啟動 migration → 使用者設定與規則進入 `data/`）。這些測試驗證的是路徑與發佈邏輯，**不等於**在 Windows 上實際跑過 `build_exe.bat` 與打包後的 exe。

## 資料目錄契約（目前）

可寫資料一律相對於「資料根目錄」（`PROJECT_ROOT` 即 `get_data_root()` 的結果）。
唯讀資源（`config.example.json` 等）改走「資源根目錄」，見上節。

| 資料 | 預設位置 | 來源 |
|---|---|---|
| 使用者設定 | `config.json` | `CONFIG_PATH` |
| 預設設定範本（唯讀，在資源根目錄） | `config.example.json` | `EXAMPLE_PATH` |
| 替換規則 | `replace_rules.json`（`translator.replace_rules_path`） | `config_manager` |
| 翻譯快取 | `快取資料/`（`cache_manager._CACHE_DIR_NAME`，設定 `translator.cache_directory`） | `utils/cache_manager.py` |
| 學名資料庫 | `學名資料庫/`（`species_cache.cache_directory`） | `utils/species_cache.py` |
| 日誌 | `logs/<日期>/app.log`（`logging.log_dir`，經 `resolve_project_path`） | `config_manager.setup_logging` |
| 錯誤記錄 | `logs/errors_<日期>.log` | `utils/exceptions.py` |
| 翻譯 checkpoint | `logs/translation_checkpoint.json` | `core/lm_translator.CHECKPOINT_FILE` |
| 圖示快取 | `.icon_cache/` | `app/icon_index.py`、`app/views/icon_preview_view.py` |
| 輸出資料夾 | **相對於使用者選的輸入資料夾**（例如 FTB 輸出在輸入資料夾的上一層、提取輸出在 mods 資料夾旁邊），不在資料根目錄內；僅確認 FTB 與提取兩處，其他流程未逐一查證 | `ftb_translator.py`、`extractor_view.py` |

### 已處理的不一致

以下三項原本相對於工作目錄或 `Path(__file__)`，現在都走 `get_data_root()`：

- `logs/errors_*.log`（`utils/exceptions.py`）
- `logs/translation_checkpoint.json`（`core/lm_translator.CHECKPOINT_FILE`）
- `.icon_cache/`（`app/icon_index.py`、`app/views/icon_preview_view.py`）

`config_manager.PROJECT_ROOT` 與 `config_service.PROJECT_ROOT` 也都改由同一函式取得。

### 仍使用 `__file__` 的唯讀資源

`resource_pack_version.json` 在 3 處（`pipeline_bundle_dialog.py`、`pipeline_one_click_dialog.py`、`bundler_view.py`）仍以 `os.path.dirname(__file__)` 往上推算，**沒有改成 `get_resource_root()`**：這三個檔案有大量既有 ruff 問題，改動會讓 CI 的 changed-files lint 失敗，而清理它們超出本 PR 範圍。
它們依賴 Nuitka 把模組的 `__file__` 對應到 `app/` 內的虛擬路徑，並由 `--include-data-files` 把該 JSON 放在 `app/translation_tool/core/`。這是推論，**需 Windows 實機確認**；有 `tests/test_app_paths.py::test_source_mode_resource_root_is_repo_root` 確保原始碼模式下檔案位置一致。

## 尚未驗證（需 Windows 實機）

- Nuitka standalone 能否啟動 Flet 桌面視窗（`flet_desktop` 客戶端是否需要額外 `--include-package-data`）。
- 打包後設定、快取、`logs/` 實際落在 exe 旁邊；關閉重開後仍在。
- `__compiled__` 與 `sys.argv[0]` 在 Nuitka standalone / onefile 下的實際值。
- `resource_pack_version.json`、`pyproject.toml` 打包後的讀取行為。
- 分離式首次啟動的舊資料搬移（用真實舊安裝，含 Windows 檔案被占用的情況）。
- 啟動器 `MinecraftTranslator.bat` 啟動 `app\\MinecraftTranslator.exe` 的行為。
- 上述測試待其他功能完成後再進行。
