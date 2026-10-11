# BUNDLER_VIEW_ARCHITECTURE.md

## 定位

BundlerView 位於翻譯流程的**最後一步**：翻譯完成後，將產出資料夾打包成一個 Minecraft 資源包 ZIP（含 pack.mcmeta / pack.png），方便發布使用。

流程圖見 [page_flows/bundler.md](../../page_flows/bundler.md)。

---

## 呼叫鏈

```
BundlerView.start_bundling_clicked()
  └─ 防重入（_bundling_running）→ 驗證 root_dir → 補齊 output_zip（留空 = 重讀 config 後 root_dir + 檔名）
  └─ launch_page_operation(...)（name「資源包打包」，owner `bundler`，worker 以 in_new_task("bundler", ...) 包裝）
       └─ _bundling_worker(root_dir, output_zip, version, description, pack_image)
            ├─ 從 version_data 讀 min_format / max_format
            └─ for update in bundle_outputs_generator(**generator_kwargs)
                 ├─ log → UiBatcher.add_lines → _apply_bundling_ui → log_view.add_many
                 ├─ progress → progress_bar.value
                 └─ error → progress_bar.color = C.RED
```

**注意**：BundlerView **直接**迭代 `bundle_outputs_generator`（實作在 `translation_tool/core/output_bundler.py`，由 `app/services_impl/pipelines/bundle_service.py` 匯入），**不走** `run_bundling_service`。後者是 `app/views/pipeline/pipeline_actions.py` 使用的包裝，會額外經 `GLOBAL_LOG_LIMITER` 過濾並可傳入 `force_rebuild`；BundlerView 不經過該過濾，也未提供強制重建選項。

---

## 畫面結構

`BundlerView`（`ft.Column`）由 `_build_controls` 組裝，其下的建構器為 `_build_bundler_version_section`、`_build_bundler_info_card`、`_build_bundler_paths_card`、`_build_bundler_preview_cards`。版面為頁首 `kit.page_header`，下方左右兩欄，最底為可收合的「打包日誌」卡（預設展開，高度 200）：

- 左欄：「資源包資訊」卡（由上而下：「遊戲版本 (pack_format)」選擇器，預設顯示「選擇遊戲版本」；「檔案敘述」`description_field`；「資源包圖片」`pack_image_field`，右側為選圖按鈕）、「路徑與額外內容」卡（「翻譯專案根目錄 *」、「最終 ZIP 儲存路徑」，各自右側有選擇按鈕；「其他指定資料夾 / 檔案」與「新增資料夾」按鈕）。
- 右欄：「資源包預覽」卡（`preview_image` / `preview_title` / `preview_format`）、「pack.mcmeta」卡（`mcmeta_view`，內容由 `mcmeta_text()` 產生）、執行卡（左側 `status_text` 預設「準備就緒」，其下 `progress_bar` 預設隱藏；右側「開始打包」`start_button`）。預覽卡未輸入描述時顯示「（尚未輸入描述）」，`mcmeta_view` 預設顯示 description 為空、min/max_format 為 "0" 的 JSON。
- 描述、版本或圖片改變時由 `_update_preview` 更新預覽；描述以 `mc_text_spans` 渲染 § 顏色代碼。

## 主要 UI 元件

| 元件 | 說明 |
|------|------|
| `version_search` / `version_list` | 版本搜尋（`resource_pack_version.json`）＋清單；以展開/收合的切換列（`_toggle_version_expand`）呈現，選取（`_select_version`）後提供 min/max_format |
| `description_field` | pack.mcmeta 的 description（支援 § 顏色代碼） |
| `pack_image_field` | pack.png 路徑（可選，FilePicker 選圖） |
| `root_dir_field` | 翻譯專案根目錄（必填） |
| `output_zip_field` | ZIP 儲存路徑（留空 → root_dir + `output_bundler.output_zip_name`，預設 `可使用翻譯.zip`） |
| `extra_folders` / `extra_folders_view` | 額外資料夾/檔案清單。額外資料夾的子資料夾內容與檔案放入 ZIP 根目錄（資料夾本身的名稱不進入 ZIP）；額外檔案放入 ZIP 根目錄 |
| `progress_bar` / `status_text` | 打包進度（執行時顯示、完成後隱藏）與狀態文字 |
| `log_view` | LogView，`mode="tail"`，tail_lines=250 |

---

## 資料來源

- `translation_tool/core/resource_pack_version.json`：`{version: {min_format, max_format}}`，由 `_load_version_data()` 載入，`_refresh_version_list()` 依搜尋關鍵字過濾。
- config `output_bundler.output_zip_name`：`_load_output_zip_from_config()` 讀取，作為 output_zip 留空時的預設檔名；`did_mount` 訂閱 `config_store.subscribe_paths`，設定存檔後即時更新欄位提示（僅在 `output_bundler.output_zip_name` 變更時）。

---

## core 層（output_bundler.py）

| 函式 | 職責 |
|------|------|
| `bundle_outputs_generator(input_root_dir, output_zip_path, description, min_format, max_format, pack_image_path, extra_folders, force_rebuild=False)` | 主流程 generator，yield `{progress, log, error?}` |
| `_collect_sources(input_root_dir, extra_folders, exclude)` | 列出所有可能進入 ZIP 的來源檔（標籤、相對路徑、絕對路徑），並排除打包自身產生的檔案 |
| `_compute_fingerprint(sources, description, min_format, max_format, pack_image_path)` | 計算輸入指紋（來源內容雜湊、相對路徑、pack 參數、壓縮設定）；任一來源讀取失敗時回 None |
| `_load_reusable_state(output_zip_path, fingerprint)` / `_commit_state(output_zip_path, fingerprint)` | 讀取／寫入狀態 sidecar（見下節「增量沿用」） |
| `_iter_add_folder_to_zip(zip_file, folder_path, base_path_in_zip, seen_files, exclude)` | 遞迴寫入資料夾，每寫完一個檔案 yield 一次（供進度回報）；檔案命名衝突自動加 `_1`、`_2` 後綴 |
| `_ProgressTracker` | 將「已處理檔案數 / 總檔案數」轉成 0.15~0.85 之間的單調進度 |
| `_write_pack_mcmeta(zip_file, description, min_format, max_format)` | 寫 pack.mcmeta（UI 設定的 description/min/max_format） |

### 增量沿用（指紋與狀態 sidecar）
- 指紋以檔案**內容**雜湊（BLAKE2）計算，不使用 mtime；涵蓋來源相對路徑、description、min/max_format、pack.png 內容與壓縮設定。
- 狀態檔為輸出 ZIP 旁的 `<輸出 ZIP>.bundle-state.json`（`BUNDLE_STATE_SUFFIX`），記錄指紋與 ZIP 大小、雜湊。輸入未變、狀態有效、且 ZIP 內容未被改動時，直接沿用既有 ZIP 並回報「沿用上次的 ZIP」。
- 重建時先移除舊狀態檔；ZIP 先寫到 `<輸出 ZIP>.tmp`，確認中央目錄可讀後才 `os.replace` 替換正式 ZIP，最後才寫入新狀態。`force_rebuild=True` 時忽略狀態一律重建。
- 打包自身產生的檔案（輸出 ZIP、`.tmp`、狀態檔）不列為來源，也不寫入 ZIP。

### generator 行為細節
- **pack.mcmeta / pack.png 來源優先序**：若 root_dir 或 extra_folders 內已存在實際檔案 → 用檔案、跳過 UI 設定並 warn；否則用 UI 欄位。
  - pack.mcmeta 僅在 `description` 非空**或** `min_format > 0` 時寫入（`_write_pack_mcmeta`，含 `min_format`/`max_format` 字串化）
  - pack.png 僅接受 `.png/.jpg/.jpeg` 副檔名；UI 來源若與已寫入的 `pack.png` 衝突 → 改名 `pack_1.png`
- ZIP 壓縮：`ZIP_DEFLATED` + `compresslevel=ZIP_COMPRESS_LEVEL`（常數值 6）。
- 子資料夾名為 `root`（不分大小寫）時直接放 ZIP 根目錄，其餘以資料夾名為根路徑。
- root_dir 下的散檔（非資料夾）也打包進 ZIP 根目錄（與既有檔名衝突時同樣 `_N` 後綴）。
- 進度：0.0~0.15 為 pack 檔階段，0.15~0.85 為子資料夾/額外項目階段，1.0 完成。
- 失敗時移除暫存 ZIP 與狀態檔；既有的輸出 ZIP 不會被覆寫，下次必定重建。

---

## 維護注意

1. **不要改成走 `run_bundling_service`**：BundlerView 的 worker 直接迭代 generator，不使用 TaskSession，與 pipeline 的 session 流程是刻意分開的兩種設計。
2. 背景 worker（`_bundling_worker`）不直接改控制項或呼叫 `page.update()`：日誌與進度經 `UiBatcher` 排到 event loop 上套用（Flet 1.0 的 worker-thread 契約）。
3. `_on_pack_image_picked` 是 FilePicker 上傳事件的 stub（`pass`）；選檔／選資料夾的實際流程用 `_page.run_task(_async_*)` 完成。

## 檔案結構

- `app/views/bundler_view.py`：`BundlerView` 主體（設定同步、版本選擇、選檔、打包背景執行緒與 log）。
- `app/views/bundler/bundler_widgets.py`：`BundlerWidgetsMixin`，控制項與卡片組裝（`_build_controls`、`_build_bundler_*_card`／`section`）。
- `app/services_impl/pipelines/bundle_service.py`：`run_bundling_service`（pipeline 流程用的包裝）。
- `translation_tool/core/output_bundler.py`：`bundle_outputs_generator` 與指紋、狀態 sidecar、ZIP 寫入等核心邏輯。
