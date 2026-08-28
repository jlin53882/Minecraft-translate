# Extractor View 架構

## 定位

ExtractorView 位於翻譯流程的**輸入端**：從 mods 資料夾的 JAR 檔案中，提取出語言檔（lang）與手冊檔（Patchouli book），作為後續翻譯流程的來源。提供兩種執行方式：**直接提取** 與 **先預覽再提取**。

- 直接提取：`open_extractor_dialog()`（點擊「提取 Lang / Book / Lang+Book」）
- 預覽：`open_preview_dialog()`（點擊「預覽 Lang / Book / Lang+Book」）

> 核心提取邏輯（JAR 掃描 / 正則比對 / 寫檔）屬於 `translation_tool/core/`，UI 層不直接操作 JAR。兩個層級之間的橋接是 `app/services_impl/pipelines/extract_service.py`。

---

## 模組地圖

```
app/views/extractor_view.py                 ← 主視圖（按鈕 / 路徑欄位 / 入口分派）
app/views/extractor/
  ├─ extractor_panels.py                    ← 左側面板組合（路徑設定 + 動作區）
  ├─ extractor_dialog.py                    ← 提取 / 預覽 對話框（進度+日誌+統計）
  ├─ extractor_state.py                     ← PreviewState / ExtractionState 資料類別
  └─ extractor_dialog_helpers.py            ← format_size() 大小格式化
app/services_impl/pipelines/extract_service.py  ← Service 層（路徑準備 / generator 驅動）
translation_tool/core/
  ├─ jar_processor.py                       ← 相容入口 + 三種模式 generator + regex 建構
  ├─ jar_processor_discovery.py             ← find_jar_files()
  ├─ jar_processor_extract.py               ← 實際提取（單 JAR + 批量並行）
  └─ jar_processor_preview.py               ← 預覽掃描 + 報告
```

---

## 呼叫流程（文字版架構圖）

### 直接提取（lang / book / dual 共用）
```
ExtractorView._handle_extract_*_click()
  ├─ _check_mods_dir_or_snack()          ← 前置驗證（留空 / 不存在 → SnackBar，不進 dialog）
  ├─ _auto_fill_output_path()            ← 輸出路徑留空時自動推算
  └─ open_extractor_dialog()
      ├─ prepare_extraction_paths()      ← Service：算出最終輸出資料夾
      ├─ get_lang_codes()                ← Service：讀取語系清單
      ├─ page.show_dialog(dialog)
      └─ on_start_click()                ← 點「開始提取」→ 背景 thread 跑 run_extraction()
          └─ run_extraction()            ← [background thread]
              ├─ 依 mode 選 generator：
              │    lang  → extract_lang_files_generator()
              │    book  → extract_book_files_generator()
              │    dual  → extract_dual_files_generator()
              ├─ run_extraction_loop(gen, cancelled_flag, on_update)
              │     ← Service：逐 update 呼叫 on_update 更新 UI + 累計 stats
              └─ 完成 → ui_done()（顯示統計 / 開啟輸出資料夾按鈕）
```

### 預覽（先掃描，再確認提取）
```
ExtractorView._handle_preview_*_click()
  └─ open_preview_dialog()
      ├─ start_scan()                    ← 點「開始預覽」
      │   ├─ prepare_preview_paths()     ← 輸出留空時推算（_預覽_*_輸出）
      │   ├─ do_scan()  [thread]         ← 跑 preview_extraction_generator()
      │   │     └─ 逐 update 寫入 preview_state (PreviewState)
      │   └─ ui_poller()  [thread]       ← 每 0.1s 讀 preview_state 更新 UI
      └─ show_result_dialog()            ← 掃描完成 → 結果清單 + 「確認執行」
          └─ start_extraction()          ← pop dialog → open_extractor_dialog(auto_start=True)
```

---

## 狀態管理

| 狀態 | 位置 | 用途 |
|------|------|------|
| `state` dict | `extractor_dialog.py` closure | running / done / cancelled / stats / progress / current_file |
| `extraction_cancel_flag` (list) | `extractor_dialog.py` closure | 跨執行緒取消信號（thread-safe：list 內容共享） |
| `preview_state` | `PreviewState` (`extractor_state.py`) | 預覽掃描的 progress / current / total / done / result / error |
| `ExtractionState` | `extractor_state.py` | 目前未在 extractor 流程使用（保留型別） |

**取消機制要點**：`on_cancel_click` 同時設 `extraction_cancel_flag[0] = True`（給 `run_extraction_loop` 偵測）與 `state["cancelled"] = True`（UI 顯示）。兩份必須同步，否則背景 thread 不會真的停。dialog 被強制 dismiss（ESC 等）時 `on_dialog_dismiss` 也會設 cancel flag 防止背景 thread 空跑。

---

## Service 層（extract_service.py）

設計原則：UI 層不應自行 `load_config()`、拼接路徑或呼叫 `os.startfile`，全部收斂到 Service。

| 函式 | 職責 |
|------|------|
| `prepare_extraction_paths(mods_dir, mode, output_path)` | 計算提取輸出路徑（mode 對應子資料夾名，取自 config `extractor.output_folder_names`） |
| `prepare_preview_paths(mods_dir, mode)` | 計算預覽輸出路徑（`mods_dir + _預覽_*_輸出`） |
| `get_output_folder_names()` | 讀取所有子資料夾命名（含預設值） |
| `get_lang_codes()` | 讀取 `jar_extractor.lang_codes`（預設 en_us/zh_cn/zh_tw） |
| `get_target_language()` | 讀取目標語系（預設 zh_tw） |
| `_select_extraction_generator(mode, ...)` | 依 mode 回傳對應 generator |
| `_run_extraction_with_session(generator, session, mode_label)` | 透過 TaskSession 驅動 generator（GLOBAL_LOG_LIMITER 過濾 + add_log/set_progress/set_error） |
| `run_lang/book/dual_extraction_service(...)` | 封裝 `_run_extraction_with_session`，用於 **pipeline_view** 的 TaskSession 流程 |
| `run_extraction_loop(generator, cancelled_flag, on_update)` | 供 **dialog** 用：逐 update 回呼 + 累計 stats，不依賴 TaskSession |
| `open_output_folder(path)` | 跨平台開啟資料夾（取代 UI 直接 os.startfile） |

**兩條驅動路徑**：
1. **TaskSession 路徑**（pipeline_view / pipeline_extract_dialog 用）：`run_*_extraction_service()` → `_run_extraction_with_session()`。輸出寫入 session，UI 端靠 poller 讀 snapshot。
2. **Dialog 路徑**（extractor_view 用）：`run_extraction_loop()` → on_update callback 直接更新 dialog 元件。立即反應，不需 poller。

`run_extraction_loop` 回傳的 stats 含 `lang` / `book` sub-dict（dual 模式時由 generator 的 `phase` 拆解填入），供 dialog 顯示 LANG/BOOK 分區統計。

---

## 核心 generator 層（translation_tool/core/）

### jar_processor.py（相容入口 + 組合）
| 函式 | 職責 |
|------|------|
| `get_lang_codes(*, skip_zh_cn)` | 從 config 讀語系，可過濾 zh_cn |
| `build_lang_file_regex(*, codes, skip_zh_cn)` | 動態建 lang 檔 regex：`(?:assets/([^/]+)/)?lang/(codes)\.(json\|lang)$` |
| `build_book_path_regex(*, codes, skip_zh_cn)` | 動態建 book 檔 regex（patchouli_books/book/manual/guidebook） |
| `extract_lang_files_generator(...)` | lang 提取 generator（`_run_extraction_process` + lang regex） |
| `extract_book_files_generator(...)` | book 提取 generator |
| `extract_dual_files_generator(...)` | **依序** lang → book，yield 帶 `phase`；最後 yield combined stats |
| `preview_extraction_generator(...)` | 委派到 `preview_extraction_generator_impl` |
| `BOOK_PATH_REGEX_DUAL_STRUCTURE` | 常數 regex（dual 模式的 book 路徑） |

### jar_processor_extract.py（實際提取）
- `extract_from_jar_impl(jar_path, output_root, target_regex, scan_results)`：單 JAR。以 ZIP 讀取比對 regex；`assets/` 開頭輸出到 `output_root/<path>`，非 assets 輸出到 `output_root/<modid>_extracted/<path>`。SHA-256 相同則跳過（增量更新）。
- `run_extraction_process_impl(mods_dir, output_dir, target_regex, process_name, ...)`：批量。背景 thread 預掃描 JAR（`scan_jars`），再用 `ThreadPoolExecutor`（worker = config `parallel_execution_workers`，上限 32，否則 `cpu_count//2`）並行提取，逐 JAR yield `progress/current/total/log`，最後 yield 帶 `stats`。

### jar_processor_preview.py（預覽）
- `_scan_single_jar_for_preview(...)`：單 JAR 掃描（純函式、執行緒安全），回傳 matched 清單與 `size_mb`。
- `preview_extraction_generator_impl(...)`：`ThreadPoolExecutor` + `as_completed` 並行掃描，逐 JAR yield 進度；全部完成後在主執行緒合併，yield 最終 `result`（total_jars / preview_results / total_files / total_size_mb / failed_jars）。
- `generate_preview_report(result, mode, output_path)`：把預覽結果寫成 Markdown 報告（每 JAR 檔案清單超過 50 筆截斷）。

### jar_processor_discovery.py
- `find_jar_files(folder_path)`：遞迴找出所有 `.jar` 絕對路徑。

---

## Regex 規則

- **Lang**：`(?:assets/([^/]+)/)?lang/({lang_codes})\.(json|lang)$` — lang_codes 由 config 動態組出（`en_us|zh_tw|zh_cn`）。
- **Book**：`(?:assets|data)/([^/]+)/(?:patchouli_books|book|manual|guidebook)/(?:[^/]+/)?_?(?:{lang_codes})(?:/.*)?$`
  - 2026-07-14 user review：移除了 `|book\.json` fallback — 必須匹配 lang code，避免 `zh_cn/book.json` 仍被算入 book。
- **skip_zh_cn 行為**：`skip_zh_cn=True` 時從 codes 移除 `zh_cn`。**注意**：此過濾對 lang 模式與 dual 的 lang phase 生效；book 模式的 regex 也支援（build_book_path_regex 與 build_lang_file_regex 對稱）。

---

## Dual 模式統計（Phase 3，2026-07-13）

`extract_dual_files_generator` 的 yield 設計，避免 UI 統計被誤導：
- lang phase 的 update 帶 `phase: "lang"`，book phase 帶 `phase: "book"`。
- **book phase 只 yield 純 book stats**（不把 lang+book 加總）— 原本的 bug 是 combined stats 被寫進 `stats["book"]` sub-dict，讓 user 看到「BOOK 成功 13」其實是 lang+book 合計。
- 最後補一個 `phase: "book_final"` 的 combined yield（含 `lang` / `book` sub-dict），`run_extraction_loop` 只認 `("lang", "book")` 兩個 phase 拆 sub-dict，所以 combined 只更新頂層 stats。
- Dialog 端 `update_dual_stats(result_stats)` 透過 `key` 屬性找到 `lang_success` / `lang_warnings` / `book_success` / `book_warnings` 四個 Text 元件填入。

---

## Mod 檔名清理與增量更新（jar_processor_extract.py）

- **`_normalize_jar_base_name(jar_filename)`**：從 JAR 檔名提取乾淨 Mod ID
  - 去除 `.jar` 副檔名後，依序：(1) 移除 `-_(neoforge|forge|fabric|quilt|build|release|alpha|beta)_?` 前綴/標記 → 取代為 `-`；(2) `VERSION_REGEX`（`[-_](?:[a-zA-Z]+-)?\d+(?:\.\d+)+(?:[-_.][a-zA-Z0-9]+)*$`）匹配到版本號時截斷其前段；(3) 去除前後 `-_`，結果為空則回退原始檔名
  - 例：`Botania-1.20.1-443-Forge.jar` → `Botania`
- **`get_file_hash(data)`**：SHA-256 hexdigest。`extract_from_jar_impl` 用 ZIP entry 的 hash 與既有輸出檔比較，**相同則跳過**（增量更新，重跑不重寫）
- **`scan_results` 快取**：`run_extraction_process_impl` 先在背景 thread 用 `scan_jars` 預掃所有 JAR 的 zip entry 清單，再傳入 `extract_from_jar_impl` 避免每個 JAR 重複掃描目錄（效能優化）

## 常見陷阱（維護注意）

1. **不要在 on_update 裡判斷「整段完成」**：generator 會逐 JAR yield 帶 `stats` 的 update，舊邏輯 `pct >= 1.0 or "stats" in update` 會逐 JAR 誤觸發「[完成] 0/0/0」。真正的完成只能等 `run_extraction_loop` 返回後用回傳的累計 stats。
2. **背景 thread 更新 UI 必須走 `page.run_task`**：`dialog.modal` 等屬性直接呼叫 `page.update()` 不會確實同步到前端（2026-07-13 回歸 bug）。
3. **cancel flag 用 outer-scope list**：`on_cancel_click` 與 worker thread 要共享同一份 reference，否則取消只改 UI 不改執行。
4. **`LogView.add()` 的 level 必須在白名單**（debug/info/warning/error/system）：傳顏色字串（如 `theme.ORANGE_700`）會 silent return，整行 log 不顯示。
5. **預覽 dialog 用「單一 dialog mutate」**：不要把 result dialog 疊在 preview dialog 上（modal=False 可能被提前 dismiss 打破疊層假設）。掃描完成直接把 preview_dialog 的 title/content/actions 換成結果畫面。
6. **`preview_state.done` 每次掃描前要重設 False**：否則上次的 `done=True` 讓新一輪 ui_poller 立刻退出，user 看到「按了沒反應」。
7. **循環引入**：`jar_processor_preview.py` 內使用 lazy import 讀 `build_lang_file_regex` / `build_book_path_regex`（避免反向 import jar_processor）。
8. **新增提取模式時**：若走 dialog 流程，沿用 `run_extraction_loop` + on_update 模式；若走 TaskSession 流程，沿用 `_run_extraction_with_session`。不要讓 UI 直接迭代 generator。

---

## 測試對應

- `tests/test_extract_service.py` — Service 層（run_extraction_loop / run_*_extraction_service）
- `tests/test_extractor_dual_mode.py` — dual phase / combined stats
- `tests/test_extractor_dialog_no_false_completion.py` — 防止逐 JAR 誤報完成
- `tests/test_extraction_cancel_flag.py` — 取消旗標生效
- `tests/test_skip_zh_cn.py` — skip_zh_cn 過濾（lang/book/dual）
- `tests/test_jar_processor.py` — 核心 generator
