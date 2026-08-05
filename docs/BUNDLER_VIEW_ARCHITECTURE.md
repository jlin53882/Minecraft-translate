# BUNDLER_VIEW_ARCHITECTURE.md

## 定位

BundlerView 位於翻譯流程的**最後一步**：翻譯完成後，將產出資料夾打包成一個 Minecraft 資源包 ZIP（含 pack.mcmeta / pack.png），方便發布或寫回 JAR。

---

## 呼叫鏈

```
BundlerView.start_bundling_clicked()
  └─ 驗證 root_dir → 補齊 output_zip（留空 = root_dir + config 檔名）
  └─ threading.Thread(_bundling_worker).start()
       └─ _bundling_worker(root_dir, output_zip, version, description, pack_image)
            ├─ 從 version_data 讀 min_format / max_format
            └─ for update in bundle_outputs_generator(**kwargs)
                 ├─ log → log_view.add()（逐行）
                 ├─ progress → progress_bar.value
                 └─ error → progress_bar.color = theme.ERROR
```

**注意**：BundlerView **直接**呼叫 `translation_tool/core/output_bundler.py` 的 `bundle_outputs_generator`，**不走** `run_bundling_service`（`bundle_service.py` 是給 `pipeline_view` 的 TaskSession 流程用的，兩者不相通）。

---

## 主要 UI 元件

| 元件 | 說明 |
|------|------|
| `version_search` / `version_list` | 版本搜尋（`resource_pack_version.json`）＋下拉清單，選取後提供 min/max_format |
| `description_field` | pack.mcmeta 的 description（支援 § 顏色代碼） |
| `pack_image_field` | pack.png 路徑（可選，FilePicker 選圖） |
| `root_dir_field` | 翻譯專案根目錄（必填） |
| `output_zip_field` | ZIP 儲存路徑（留空 → root_dir + `output_bundler.output_zip_name`，預設 `可使用翻譯.zip`） |
| `extra_folders` / `extra_folders_view` | 額外資料夾/檔案清單（合併進 ZIP 根目錄） |
| `progress_bar` | 打包進度（隱藏/顯示切換） |
| `log_view` | LogView，`mode="tail"`，tail_lines=250 |

---

## 資料來源

- `translation_tool/core/resource_pack_version.json`：`{version: {min_format, max_format}}`，由 `_load_version_data()` 載入，`_refresh_version_list()` 依搜尋關鍵字過濾。
- config `output_bundler.output_zip_name`：`_load_output_zip_from_config()` 讀取，作為 output_zip 留空時的預設檔名。

---

## core 層（output_bundler.py）

| 函式 | 職責 |
|------|------|
| `bundle_outputs_generator(input_root_dir, output_zip_path, description, min_format, max_format, pack_image_path, extra_folders)` | 主流程 generator，yield `{progress, log, error?}` |
| `_add_folder_to_zip(zip_file, folder_path, base_path_in_zip, seen_files)` | 遞迴寫入資料夾；檔案命名衝突自動加 `_1`、`_2` 後綴 |
| `_write_pack_mcmeta(zip_file, description, min_format, max_format)` | 寫 pack.mcmeta（UI 設定的 description/min/max_format） |

### generator 行為細節
- **pack.mcmeta / pack.png 來源優先序**：若 root_dir 或 extra_folders 內已存在實際檔案 → 用檔案、跳過 UI 設定並 warn；否則用 UI 欄位。
  - pack.mcmeta 僅在 `description` 非空**或** `min_format > 0` 時寫入（`_write_pack_mcmeta`，含 `min_format`/`max_format` 字串化）
  - pack.png 僅接受 `.png/.jpg/.jpeg` 副檔名；UI 來源若與已寫入的 `pack.png` 衝突 → 改名 `pack_1.png`
- ZIP 壓縮：`ZIP_DEFLATED` + `compresslevel=9`。
- 子資料夾名為 `root` 時直接放 ZIP 根目錄，其餘以資料夾名為根路徑。
- root_dir 下的散檔（非資料夾）也打包進 ZIP 根目錄（與既有檔名衝突時同樣 `_N` 後綴）。
- 進度：0.0~0.15 為 pack 檔階段，0.15~0.85 為子資料夾/額外項目階段，1.0 完成。
- 失敗時移除半成品 ZIP。

---

## 維護注意

1. **不要改成走 `run_bundling_service`**：BundlerView 的 worker 直接迭代 generator（無 TaskSession），與 pipeline 的 session 流程是刻意不同的兩種設計。
2. 背景 thread 直接呼叫 `self._page.update()`（打包更新量大但無 modal 鎖定），加上 `_scroll_log()` 自動捲動日誌。
3. `_on_pack_image_picked` / `_on_output_zip_picked` / `_on_extra_folder_picked` 是 FilePicker 事件 stub（`pass`），實際流程用 `_page.run_task(_async_*)` 完成。
