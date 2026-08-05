# BUNDLER_VIEW_ARCHITECTURE.md

## 定位

BundlerView 位於翻譯流程的**最後一步**：翻譯完成後，將產出資料夾打包成一個 ZIP 資源包，方便發布或寫回 JAR。

---

## 與 output_bundler 的關係

```
BundlerView.start_bundling_clicked()
  → bundling_worker() [thread]
      → run_bundling_service()   [bundle_service.py]
          → bundle_outputs_generator()  [output_bundler.py]
```

`bundle_service.py` 是 `output_bundler.py` 的封裝層（PR18），將 `bundle_outputs_generator` 包裝成 service interface。

---

## 主要 UI 元件

- `root_dir_textfield`：翻譯專案根目錄（包含各語言產出資料夾）
- `output_zip_textfield`：最終 ZIP 儲存路徑
- `start_button`：觸發打包
- `progress_bar` + `log_view`：進度與日誌顯示

路徑選擇使用 **Flet FilePicker**（`file_picker`），預設 ZIP 檔名由 `config.json` 的 `output_bundler.output_zip_name` 取得（預設：`可使用翻譯.zip`）。

---

## 輸出格式

產出為標準 **ZIP 壓縮檔**（副檔名 `.zip`），包含 `root_dir` 下的所有翻譯產出資料夾結構。
