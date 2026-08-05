# UNTRANSLATED_CHECKER_ARCHITECTURE.md

## 定位與功能

**定位**：翻譯品質把關工具，檢測 en_us 與 zh_tw 之間未翻譯的翻譯鍵（Key），屬 QC 環節（翻譯完成後、打包前）。

**功能**：比較 en_us 與 zh_tw JSON 語言檔 → 找出 zh_tw 中缺失的翻譯鍵 → 輸出未翻譯報告至指定資料夾。

## 檔案結構

- `app/views/untranslated_checker.py` — `UntranslatedChecker` 元件（約 172 行，`ft.Container`）
- `app/views/qc_base.py` — 依賴 `QCBase.task_worker()` 執行執行緒任務
- `app/services.py` — `run_untranslated_check_service(en_dir, tw_dir, out_dir)`（generator）

## 與 QCView 的關係

- `UntranslatedChecker(page, file_picker, task_runner)` 由 QCView 建立並佈局在**第一張 Card**（PR1 拆分）
- QCView 的 `start_task("untranslated")` 仍保留 `run_untranslated_check_service` 作為備用分派（讀 `self.untranslated_checker.en_dir/tw_dir/out_dir`）

## 主要 UI 元件

| 元件 | 說明 |
|------|------|
| `en_dir` (TextField) | 英文來源資料夾路徑 |
| `tw_dir` (TextField) | 繁中來源資料夾路徑 |
| `out_dir` (TextField) | 報告輸出資料夾路徑 |
| `start_button` (ft.Button) | 開始檢查（SEARCH_OFF icon） |

每列路徑欄配 `_create_pick_button(folder_mode=True)`（FOLDER_OPEN icon）。

## 任務執行（_on_start）

```
_on_start(e)
  ├─ 驗證三路徑非空（缺漏 → snack「錯誤：請填寫所有路徑！」）
  └─ task_runner.task_worker(
         run_untranslated_check_service, (en_dir, tw_dir, out_dir),
         controls_to_disable=[start_button, en_dir, tw_dir, out_dir],
     )
```
`controls_to_disable` 任務期間禁用，`finally` 自動恢復（由 QCBase 處理）。

## FilePicker 流程

`_pick_file_or_directory` → `_page.run_task(_async_pick_file_or_directory, ...)`（async 包裝）：
- folder_mode → `get_directory_path()`；否則 `pick_files()`
- 結果寫回 target_textfield + update；取消 → snack「您已取消選擇」

## 檢查邏輯（check_untranslated_generator）

1. 掃描 `en_us_dir` 所有 `.json`（`os.walk`），無任何 en_us 檔 → error 結束
2. 對每個 en_us 檔找 `zh_tw_dir/<相同相對路徑>`（**不替換檔名**，路徑直接對應）：
   - **找不到對應繁中檔案** → 整個 en_us 檔內容視為未翻譯，**原樣寫入 out_dir**（`total_untranslated_keys += len(en_data)`）
   - 找到 → `untranslated_keys = en_data.keys() - tw_data.keys()`（**只比 key 集合**，不做值比對）
3. 有未翻譯 key → 寫報告（僅含未翻譯 key 的 en value）至 out_dir 同相對路徑
4. 輸出統計：`files_with_missing`（有缺漏的檔案數）+ `total_untranslated_keys`

**注意**：此檢查只偵測「key 缺失」，不偵測「值為空/仍為英文」——那是 `english_residue_checker` 的職責。

## 與 Translation Workflow 的關係

```
Translation Workflow（翻譯執行） → UntranslatedChecker（QC 把關）→ 修補漏譯 Key → Output Bundler（打包）
```

## 維護注意

1. 此元件**不自己開執行緒**；一律透過注入的 `task_runner`（QCBase）執行，沿用 QC 共用 progress_bar + log_view。
2. service 必須是 generator；逐筆 yield 的 log 會寫入 QCView 的 LogView（level="info"）。
