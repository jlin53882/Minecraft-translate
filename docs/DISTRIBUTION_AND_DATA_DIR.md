# 發佈形式與資料目錄契約（#137）

> 本文件記錄發佈形式與可寫資料的位置。結論來自讀 repo 的靜態盤點；
> 打包後的實測尚未進行（需 Windows 環境），見最後一節。

## 發佈形式

實際發佈給使用者的是**打包的 exe**，可寫資料放在 exe 旁邊：

```
<資料夾>/
 ├─ <程式>.exe（與打包產物）
 ├─ config.json、config.example.json、replace_rules.json
 ├─ 快取資料/、學名資料庫/、logs/、.icon_cache/
 └─ 各種輸出資料夾
```

打包流程（Nuitka standalone）不在 repo 內。`tools/build_exe.bat` 是依此結構整理的**草稿，尚未實測**。
原始碼執行（`uv run python main.py`）仍然支援，行為不變。

> 先前版本的本文件曾寫「只支援原始碼」，是錯的：當時只看了 repo 內的 README 與 `release.yml`。

## 資料根目錄的決定規則

單一決定點：`translation_tool/utils/app_paths.py::get_data_root()`，依序：

1. 環境變數 `MCT_DATA_DIR`（明確覆蓋）。
2. 打包後（PyInstaller `sys.frozen` 或 Nuitka `__compiled__`）：exe 所在資料夾。
3. 原始碼執行：專案根目錄。

不要用 `Path(__file__)` 推算可寫資料位置：打包後它可能指向 `_internal/` 或暫存解壓目錄。

## 資料目錄契約（目前）

可寫資料一律相對於「資料根目錄」（`PROJECT_ROOT` 即 `get_data_root()` 的結果）：

| 資料 | 預設位置 | 來源 |
|---|---|---|
| 使用者設定 | `config.json` | `CONFIG_PATH` |
| 預設設定範本（唯讀） | `config.example.json` | `EXAMPLE_PATH` |
| 替換規則 | `replace_rules.json`（`translator.replace_rules_path`） | `config_manager` |
| 翻譯快取 | `快取資料/`（`cache_manager._CACHE_DIR_NAME`，設定 `translator.cache_directory`） | `utils/cache_manager.py` |
| 學名資料庫 | `學名資料庫/`（`species_cache.cache_directory`） | `utils/species_cache.py` |
| 日誌 | `logs/<日期>/app.log`（`logging.log_dir`，經 `resolve_project_path`） | `config_manager.setup_logging` |
| 錯誤記錄 | `logs/errors_<日期>.log` | `utils/exceptions.py` |
| 翻譯 checkpoint | `logs/translation_checkpoint.json` | `core/lm_translator.CHECKPOINT_FILE` |
| 圖示快取 | `.icon_cache/` | `app/icon_index.py`、`app/views/icon_preview_view.py` |
| 輸出資料夾 | 各 `*_dir_name` / `*_folder_name` 設定，多數經 `resolve_project_path` | `config_manager.DEFAULT_CONFIG` |

### 已處理的不一致

以下三項原本相對於工作目錄或 `Path(__file__)`，現在都走 `get_data_root()`：

- `logs/errors_*.log`（`utils/exceptions.py`）
- `logs/translation_checkpoint.json`（`core/lm_translator.CHECKPOINT_FILE`）
- `.icon_cache/`（`app/icon_index.py`、`app/views/icon_preview_view.py`）

`config_manager.PROJECT_ROOT` 與 `config_service.PROJECT_ROOT` 也都改由同一函式取得。

### 仍使用 `__file__` 的唯讀資源（不是可寫資料）

- `translation_tool/core/resource_pack_version.json`（`pipeline_*_dialog.py`、`bundler_view.py`）
- `pyproject.toml`（`app/shell/app_shell.py` 讀版本號；讀不到只是不顯示版本）
- `assets/`（`main.py`）

這些必須隨打包一併帶入（見 `tools/build_exe.bat` 的 `--include-data-*`）。

## 尚未驗證（需 Windows 實機）

- Nuitka standalone 能否啟動 Flet 桌面視窗（`flet_desktop` 客戶端是否需要額外 `--include-package-data`）。
- 打包後設定、快取、`logs/` 實際落在 exe 旁邊；關閉重開後仍在。
- `__compiled__` 與 `sys.argv[0]` 在 Nuitka standalone / onefile 下的實際值。
- `resource_pack_version.json`、`pyproject.toml` 打包後的讀取行為。
- 上述測試待其他功能完成後再進行。
