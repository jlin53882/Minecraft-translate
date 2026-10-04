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

打包流程（Nuitka standalone）不在 repo 內。`tools/build_exe.bat` 是依此結構整理的**草稿，尚未在 Windows 實測**。

### 重新打包：取代程式檔、保留使用者資料

正式資料夾同時是執行資料夾，檔案分兩類：

| 類別 | 內容 | 重新打包時 |
|---|---|---|
| A. packaged files | exe、DLL / PYD、Nuitka 相依樹、assets、`config.example.json` | **完全取代**：新版沒有的舊檔會被刪除，避免已移除的 DLL / PYD / 套件殘留成混合版本 |
| B. 使用者資料 | `config.json`、`replace_rules.json`、`logs/`、`快取資料/`、`學名資料庫/`、`.icon_cache/`、各輸出資料夾 | **永遠保留**；`config.json`、`replace_rules.json` 僅在不存在時才建立 |

流程：

1. Nuitka 先輸出到 `dist\_staging`（只清這個暫存資料夾）。
2. 驗證 staging 內有 exe 才發佈；build 失敗時正式資料夾完全不動。
3. `tools/publish_dist.py` 先複製新檔，再依 `.packaged_manifest.json` 刪除「上次發佈過、這次已不存在」的檔案，最後寫入新 manifest。
   - 使用者資料從不進 manifest，所以不會被刪；另有保護清單作為第二道防線，manifest 被改壞（路徑越界、指向 `config.json` / `logs/` 等）也不會刪到。
   - manifest 損毀時寧可不刪任何東西。
   - 因刪檔而變空的資料夾會被修剪；內含其他檔案（例如使用者放進去的）的資料夾不會被動。

已知限制：

- 從沒有 manifest 的舊安裝首次升級時，無法判斷舊遺留檔是否屬於 packaged files，所以不會刪除；需手動清一次，之後就自動維護。
- 流程是「先複製、再刪除、最後寫 manifest」，不是整個資料夾的原子替換；中途失敗時重新執行即可（冪等）。
- 長期更乾淨的模型是把 packaged 與使用者資料拆成 `app/` 與 `data/` 兩個目錄，可整體替換 `app/`。這需要更大的路徑重構，本 PR 不做。

契約由 `tests/test_publish_dist.py` 保護：移除的 DLL / PYD / 套件會被清掉、結果與乾淨 build 等價、使用者資料不變、失敗的 build 不動正式資料夾、被竄改或損毀的 manifest 不會刪到保護項目或 target 之外的檔案。該測試驗證的是 `publish_dist.py` 的邏輯，**不等於**在 Windows 上實際跑過 `build_exe.bat`。

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
