# LOOKUP_VIEW_ARCHITECTURE.md

## 定位

LookupView 是**學名（物種名稱）翻譯的快速查詢工具**，屬於翻譯前置準備工具。支援單筆與批次（JSON 陣列）兩種查詢，結果來自本地快取與線上查詢（`translation_tool.utils.species_cache`）。

## 主要 UI 元件

`LookupView`（`ft.Column`）的控制項由 `_init_lookup_inputs` 建立，`__init__` 以 `kit` 的卡片組裝版面。

| 元件 | 類型 | 說明 |
|------|------|------|
| `single_input` | `kit.text_field` | 單筆學名輸入（placeholder「輸入單一學名，例如：Felis catus」），Enter 也觸發查詢（`on_submit`） |
| `single_button` | `kit.button` | 「查詢」（primary，搜尋圖示），與輸入框同一列、位於其右側 |
| `recent_row` | Row | 「最近查詢」晶片列（預設隱藏，首次查詢後才顯示；`_remember` 記錄於 `_recent`，最多 `RECENT_LIMIT` 筆，只存在本次執行）；點晶片經 `_lookup_recent` 重新查詢 |
| `single_result_text` | Text | 單筆查詢結果（`selectable=True`；預設顯示「查詢結果將顯示在這裡。」） |
| `single_progress_ring` | ProgressRing | 查詢中旋轉指示器（預設隱藏） |
| `copy_button` | `kit.button` | 「複製結果」（ghost），位於「查詢結果」卡右上角，`copy_result_clicked` 複製結果到剪貼簿 |
| `batch_input` | `kit.text_field` | JSON 陣列輸入（multiline；placeholder「輸入 JSON 格式的學名列表，例如：["Felis catus", "Canis lupus familiaris"]」） |
| `batch_result_textfield` | `kit.text_field` | 批次結果（read_only, multiline；標籤「批次查詢結果 (JSON)」，位於按鈕下方） |
| `batch_button` | `kit.button` | 「批次查詢」（primary），位於 JSON 輸入框下方，與 `batch_progress_bar` 同一列 |
| `batch_progress_bar` | `kit.progress_bar` | 批次進度（不確定進度時 `value=None`，預設隱藏） |

**佈局**：頁首 `kit.page_header` + 左右兩欄（`ft.Column(scroll=ADAPTIVE)`）。左欄為「單筆查詢」卡（輸入 + 查詢按鈕同列，下接最近查詢）與「查詢結果」卡（標題右側「複製結果」），右欄為「批次查詢」卡（由上而下：JSON 輸入、「批次查詢」按鈕列、批次結果）。

## 呼叫鏈

### 單筆查詢
```
single_lookup_clicked(e)
  ├─ 驗證輸入非空（否則顯示錯誤）
  ├─ 鎖 UI：button/input disabled + ProgressRing 顯示 + 「查詢中...」，並 _remember(名稱)
  ├─ threading.Thread(single_lookup_worker, daemon=True).start()
  └─ single_lookup_worker(name)
       └─ run_manual_lookup_service(name) → 結果與復原 UI 的動作經 _run_on_ui 排到 event loop 套用
```

### 批次查詢
```
batch_lookup_clicked(e)
  ├─ 驗證 JSON 非空
  ├─ 鎖 UI：button disabled + ProgressBar 顯示（indeterminate）
  ├─ threading.Thread(batch_lookup_worker, daemon=True).start()
  └─ batch_lookup_worker(json_text)
       └─ for update in run_batch_lookup_service(json_text):  # generator
            ├─ update.error → 記下 log 文字、break
            ├─ update.result → 記下結果文字
            └─ update.progress → 記下進度值
        finally: 經 _run_on_ui 一次套用結果文字 / 進度，並復原 UI（button + ProgressBar）
```

## Service 層（app/services_impl/pipelines/lookup_service.py，PR17 抽離）

- `run_manual_lookup_service(name) -> str`
  - `is_potential_species_name(name)` 前置格式檢查（不合格式回錯誤訊息）
  - `lookup_species_name(name)` → 有結果回傳，否則「在本地快取和線上查詢中均未找到結果。」
- `run_batch_lookup_service(json_text)`（generator，逐步 yield update dict）
  - `json.loads` 解析；非 list → yield `{log, error: True}`
  - 逐筆：`is_potential_species_name` 判斷（`"格式錯誤"`）→ `lookup_species_name`（未找到 → `"未找到"`）
  - 每筆 yield `{log: "(i/total) 已查詢: ...", progress}`，經 `GLOBAL_LOG_LIMITER.filter()` 過濾高頻日誌
  - 完成 yield `{log: "--- 批次查詢完成 ---", result: json.dumps(...)}`
  - JSONDecodeError / 其他例外 → yield `{log, error: True}`

## species_cache 查詢邏輯（species_cache.py）

### 格式判定
- `_SPECIES_NAME_REGEX = ^[A-Z][a-z]+ [a-z]+$`：大寫開頭 + 空格 + 小寫（如 `Felis catus`）
- `is_potential_species_name(name)`：非 str 或格式不符 → False

### lookup_species_name(name) 決策鏈
1. 未初始化 → `initialize_species_cache()`（失敗回 None）
2. **快取命中**（`name in _species_cache_data`）→ 回傳快取值；**空值快取**（`""`，先前查詢失敗標記）→ 回 None（避免重複查詢）
3. 未命中且格式合法 → `query_wikipedia_and_update_cache(name)` 線上查詢
4. 格式不合法 → None（不發請求）

### 線上查詢（wikipedia，`_WIKIPEDIA_AVAILABLE`）
- 查詢前 `time.sleep(_RATE_LIMIT_DELAY)`（config `species_cache.wikipedia_rate_limit_delay`，預設 0.5s）做 rate limit；語言取自 `species_cache.wikipedia_language`（預設 `zh`）
- `wikipedia.page(name, auto_suggest=False)`，取 `page.title.split("(")[0]`（去括號註記）為俗名
- **成功才寫檔**：記憶體快取 + 快取檔 append（config `species_cache.cache_directory` / `cache_filename`，預設 `學名資料庫/species_cache.tsv`；TSV：`學名\t俗名`）；失敗僅更新記憶體為 `""`（不重複查詢、不寫檔）
- `PageError` → 回 None；`DisambiguationError` → 原名稱記為空值快取，取第一個選項遞迴查詢；其他例外 → 回 None（原名稱記為空值快取）

## 檔案結構

- `app/views/lookup_view.py` — UI
- `app/services_impl/pipelines/lookup_service.py` — service 封裝
- `translation_tool/utils/species_cache.py` — `is_potential_species_name` / `lookup_species_name`（本地快取 + 線上查詢）

## 維護注意

1. 兩個 worker 都是一次性背景執行緒，不直接改控制項：結果經 `_run_on_ui`（`page.run_task`）排到 event loop 套用；與 Extractor/LM 頁的 TaskSession + UI timer 模式不同。
2. `page` 屬性為 `@property`：必須是 Page 實例，`show_snack(self.page, ...)` 才收得到。
3. 批次結果以 `ensure_ascii=False` dump 中文，結果區顯示原始 JSON。
