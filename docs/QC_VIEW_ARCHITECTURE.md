# QC_VIEW_ARCHITECTURE.md

## 定位

QCView 是 **Quality Check（品質檢查）頁**，翻譯完成後的品質驗證工具，非翻譯管線必要環節。頁面先以三張模式卡（`kit.ChoiceCard`，由 `QC_MODES` 定義）選擇檢驗模式，只顯示對應的設定面板；三種模式共用同一組 `progress_bar` + `log_view`。

## 檔案結構

- `app/views/qc_view.py` — 主視圖
- `app/views/qc_base.py` — `QCBase` 共用背景任務執行器（`task_worker`、`_guess_level`）
- `app/ui/ui_batcher.py` — UiBatcher（背景執行緒 → event loop 的批次 UI 更新）
- `app/views/untranslated_checker.py` — `UntranslatedChecker` 元件（Key 缺失檢查模式的面板內容）
- `app/services.py` — `run_untranslated_check_service` / `run_variant_compare_service` / `run_variant_compare_tsv_service`（皆為 generator 包裝）；另有 `run_english_residue_check_service`，目前 QCView 沒有使用
- `translation_tool/checkers/` — 實際比對邏輯（`untranslated_checker.py`、`variant_comparator.py`、`variant_comparator_tsv.py`）

## QCBase（task_worker）

```
start_task()（event loop）
  → 清空 log、重置 progress_bar、停用控制項、page.update()
  → task_worker(service_func, args, on_complete, controls_to_disable)
       → threading.Thread(run)                         [worker thread]
            for update in service_func(*args):
                log 逐行 → batcher.add_lines([(line, level)])
                progress → batcher.set_state(progress=...)
                error    → batcher.set_state(error=True)
                batcher.flush()                        ← 依節流 / 背壓決定是否排程
            except → 錯誤行 + set_state(error=True)
            finally → set_state(done=True)；flush(force=True)
       → UiBatcher 以 page.run_task 在 event loop 上呼叫 apply_ui(lines, state)
            log_view.add_many(lines)；progress_bar.value
            error → progress_bar.color = C.RED
            done  → progress_bar.value = 0、color = C.EM、恢復控制項、呼叫 on_complete
            page.update()
```

- **worker thread 不直接修改 Flet control，也不呼叫 `page.update()`**；它只把資料交給 `UiBatcher`，所有 UI 變更都在 `apply_ui`（event loop）上執行。
- **UiBatcher 行為**：
  - 節流：兩次刷新至少間隔 `interval`（QC 為 0.2 秒）。
  - 背壓：上一次刷新還在 event loop 上執行時不排新的刷新，資料繼續累積，避免 `run_task` 佇列堆積。
  - 自適應：下一次間隔至少是上次套用耗時的兩倍，讓 event loop 保留時間處理使用者操作。
  - 狀態同名 key 只保留最新值；`flush(force=True)` 保證最後一批（含 `done`）一定送出。
- **完成 / 錯誤回到 event loop**：任務結束一定經 `set_state(done=True)` + `flush(force=True)`；錯誤以 `error` 狀態呈現（進度條變色），日誌等級由 `_guess_level` 依文字推測。
- `service_func` 須為 **generator**（逐步 yield update dict）。

## 主要檢查項目（start_task 分派表）

| task_type | 服務函式 | 輸入 | 輸出 |
|-----------|----------|------|------|
| `untranslated`（備用） | `run_untranslated_check_service` | en_dir / tw_dir / out_dir | Key 缺失檢查報告 |
| `compare_json` | `run_variant_compare_service` | cn_dir / tw_dir / out_dir | JSON 資料夾簡繁差異報告 |
| `compare_tsv` | `run_variant_compare_tsv_service` | tsv_path / out_csv_path | TSV 簡繁差異 CSV |

`start_task(task_type)` 流程：
1. 清空 log、重置 progress_bar 並設為可見、`set_controls_disabled(True)`（只含 JSON / TSV 兩組欄位與按鈕；Key 缺失檢查的控制項由 `UntranslatedChecker._on_start` 傳入 `controls_to_disable`），並以 `page.run_task(_scroll_to_log)` 捲到日誌
2. 依 task_type 收集路徑（缺漏 → snack 錯誤 + 復原控制項）
3. `task_runner.task_worker(target_func, args, on_complete=復原控制項)`

## UI 架構

建構子拆成 `_init_qc_state_and_progress`（狀態、共用進度列／日誌、`QCBase`、各欄位與按鈕）、`_build_qc_mode_cards`（`mode_cards`）、`_build_qc_json_panel`、`_build_qc_tsv_and_untranslated_panels`（組出 `mode_panels` 並呼叫 `_apply_mode`）。`select_mode(mode)` 切換 `self.mode`（預設為 `QC_MODES` 第一項 `untranslated`），只顯示對應面板。

版面由上而下：`kit.page_header`「QC 品質檢驗」（副標「檢查缺漏、簡繁不一致與英文殘留；報告輸出到你指定的資料夾」）→ 一列三張模式卡（標題／副標：「Key 缺失檢查」比對 en_us 與 zh_tw 的 key；「簡繁差異（JSON 資料夾）」OpenCC 轉換後逐檔比對；「簡繁差異（TSV 單檔）」輸出含轉換欄位的 CSV；預設選中第一張並顯示勾選標記）→ 目前模式的設定面板（預設只顯示「Key 缺失檢查」）→ 「處理日誌」卡片（預設為空，進度條隱藏）。

- **模式 `untranslated`**：`untranslated_checker` 元件（Key 缺失檢查，`UntranslatedChecker(page, file_picker, task_runner)`）
- **模式 `compare_json`**：簡繁差異比較 JSON 資料夾（`cn_dir_textfield` / `tw_dir_textfield_2` / `compare_out_dir_textfield` / `compare_start_button`）
- **模式 `compare_tsv`**：簡繁差異比較 TSV 單檔（`tsv_file_textfield` / `tsv_out_file_textfield` / `compare_tsv_start_button`）
- 共用：「處理日誌」`kit.section_card`，內含 `progress_bar`（預設隱藏，`start_task` 時顯示）+ `log_view`（`LogView`，`mode="append"`、`max_lines=2000`、`height=360`）

## FilePicker 流程

`_create_pick_button(target_textfield, title, folder_mode, file_filter)` → `_pick_file_or_directory()`：
- 把目標寫入 `self._pending_pick` → `_page.run_task(_async_pick_file_or_directory)`
- folder_mode → `file_picker.get_directory_path()`；否則 `file_picker.pick_files(allow_multiple=False)`
- 結果寫回 target_textfield；取消 → snack「您已取消選擇」

## 三種檢查服務的比較邏輯

### 簡繁差異比較（JSON 資料夾模式，`translation_tool/checkers/variant_comparator.py` 的 `compare_variants_generator`）
- 初始化 `OpenCC('s2twp')` + `load_replace_rules`（規則路徑讀 config 的 `translator.replace_rules_path`，與 ftb_translator 相同的轉換鏈）
- 掃描 `zh_cn_dir` 所有 `.json`，對應 `zh_tw_dir` 中 `zh_cn.json → zh_tw.json` 相對路徑；**找不到對應繁中檔 → 跳過**；找不到任何 `.json` → error 結束
- 逐 key：只比較**兩側都有的 key**，且**兩側皆為 str**；`converter.convert(cn_value)` → `apply_replace_rules()` 後與 `tw_value` 比對
- 有差異 → 寫報告 JSON（`key` / `zh_cn_original` / `zh_cn_converted_to_tw` / `zh_tw_actual`）至 output_dir，沿用 zh_cn 檔的相對路徑（檔名不改成 zh_tw）

### TSV 簡繁差異（`translation_tool/checkers/variant_comparator_tsv.py` 的 `compare_variants_tsv_generator`）
- 單檔模式：讀 TSV（需有 `key` / `zh_cn` / `zh_tw` 欄，否則 error），用 `OpenCC('s2twp')` 轉換 `zh_cn` 欄後與 `zh_tw` 比對（不套用 replace rules），差異輸出 CSV（欄位 `key` / `zh_cn_original` / `zh_cn_converted_by_opencc` / `zh_tw_original`，`utf-8-sig`）

### 未翻譯檢查（`translation_tool/checkers/untranslated_checker.py` 的 `check_untranslated_generator`）
- 掃 `en_dir` 的所有 `.json`，找 `tw_dir` 下相同相對路徑的檔案；**找不到對應繁中檔案 → 整檔標記未翻譯**
- 以 key 集合差（`en.keys() - tw.keys()`）判斷，**只看 key 是否缺失**，不看值是否為空或仍為英文；報告（缺失 key 的英文值）寫入 out_dir 同相對路徑

各 service（`app/services.py`）都包一層 `GLOBAL_LOG_LIMITER.filter()` 節流高頻日誌，例外時 yield `{log, error: True, progress: 0}`。

`filter()` 只節流 `log` / `progress`；`error` 等其他欄位一律保留，帶有這些欄位的 update 會立即輸出、不被節流吞掉。因此 checker 回報的 `error` 一定會到達 `task_worker`（契約詳見 PIPELINE_VIEW_ARCHITECTURE.md 的「Service 層契約」）。

## 維護注意

1. 新增檢查類型：在 `QC_MODES` 加一項、`mode_panels` 加對應面板、`start_task` 加分派分支，並把新欄位加進 `set_controls_disabled` 清單。
2. `task_worker` 的 service 必須是 generator；若回傳 list 會 `TypeError: 'list' object is not iterable`。
3. 背景執行緒中不可直接操作 `log_view` / `progress_bar` 或呼叫 `page.update()`；新增的 UI 回饋一律放進 `apply_ui`，經 `UiBatcher` 送到 event loop。
4. 開始任務時的捲動（`_scroll_to_log`）是 event loop 上的 coroutine，由 `page.run_task` 排程。
