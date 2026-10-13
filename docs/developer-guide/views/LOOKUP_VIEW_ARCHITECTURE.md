# LOOKUP_VIEW_ARCHITECTURE.md

## 定位

LookupView 是**學名（物種名稱）翻譯的快速查詢工具**，屬於翻譯前置準備工具。支援單筆與批次（JSON 陣列）兩種查詢，結果來自本地快取與線上查詢（`translation_tool.utils.species_cache`）。

流程圖見 [page_flows/lookup.md](../../page_flows/lookup.md)。

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

**佈局**：頁首 `kit.page_header` + 左右兩欄（`LookupView` 本身為 `scroll=ADAPTIVE`）。左欄為「單筆查詢」卡（輸入 + 查詢按鈕同列，下接最近查詢）與「查詢結果」卡（標題右側「複製結果」），右欄為「批次查詢」卡（由上而下：JSON 輸入、「批次查詢」按鈕列、批次結果）。

## 呼叫鏈

### 單筆查詢
```
single_lookup_clicked(e)
  ├─ 驗證輸入非空（否則顯示錯誤）
  ├─ 鎖 UI：button/input disabled + ProgressRing 顯示 + 「查詢中...」，並 _remember(名稱)
  ├─ launch_page_operation(...)（name「單筆翻譯查詢」，owner `lookup`，於背景執行下列 worker）
  └─ single_lookup_worker(name)
       └─ run_manual_lookup_service(name) → 結果與復原 UI 的動作經 _run_on_ui 排到 event loop 套用
```

### 批次查詢
```
batch_lookup_clicked(e)
  ├─ 驗證 JSON 非空（空值時結果區顯示錯誤）
  ├─ 鎖 UI：button disabled + ProgressBar 顯示（indeterminate）
  ├─ launch_page_operation(...)（name「批次翻譯查詢」，owner `lookup`，於背景執行下列 worker）
  └─ batch_lookup_worker(json_text)
       └─ for update in run_batch_lookup_service(json_text):  # generator
            ├─ update.error → 記下 log 文字、break
            ├─ update.result → 記下結果文字
            └─ update.progress → 記下進度值
        finally: 經 _run_on_ui 一次套用結果文字 / 進度，並復原 UI（button + ProgressBar）
```

## Service 層（app/services_impl/pipelines/lookup_service.py）

- `run_manual_lookup_service(name) -> str`
  - `is_potential_species_name(name)` 前置格式檢查（不合格式回錯誤訊息）
  - `lookup_species(name)` 交給 `describe_lookup`：找到回傳名稱；查無回「在本地快取和線上查詢中均未找到結果。」（種以下名稱另附上一層物種參考）；限流回「維基百科回應請求過於頻繁，請稍後再試（…）。」；其他錯誤回「查詢失敗，請稍後再試（…）。」
- `run_batch_lookup_service(json_text)`（generator，逐步 yield update dict）
  - `json.loads` 解析；非 list → yield `{log, error: True}`
  - 逐筆：`is_potential_species_name` 判斷（`"格式錯誤"`）→ `lookup_species`（查無 → `"未找到"`，暫時失敗 → `"查詢失敗：…"`）
  - `SpeciesLookup.rate_limited`（含上一層物種）時停止：yield 一筆停止訊息，其餘名稱記為 `"未查詢（已因限流停止）"`，不再送出請求
  - 每筆 yield `{log: "(i/total) 已查詢: ...", progress}`，經 `GLOBAL_LOG_LIMITER.filter()` 過濾高頻日誌
  - 完成 yield `{log: "--- 批次查詢完成 ---", result: json.dumps(...)}`
  - JSONDecodeError / 其他例外 → yield `{log, error: True}`

## species_cache 查詢邏輯（species_cache.py）

### 格式判定
- 查詢前會去除前後空白並合併重複空白。
- `SPECIES_NAME_REGEX`（`translation_tool/utils/species_names.py`）接受二名法、三名法、常見種下階級與雜交符號（例如 `Felis catus`、`Canis lupus familiaris`、`Quercus robur subsp. broteroana`、`Acer × freemanii`）。
- `is_potential_species_name(name)`：非 str 或格式不符 → False。

### lookup_species_name(name) 決策鏈
1. 未初始化 → `initialize_species_cache()`（失敗回 None）
2. **快取命中**（`name in _species_cache_data`）→ 回傳快取值；空值代表已確定查無，回 None 且不再連線
3. 未命中且格式合法 → `query_wikipedia_and_update_cache(name)` 線上查詢
4. 格式不合法 → None（不發請求）

### 線上查詢（MediaWiki Action API）
- 查詢前 `time.sleep(_RATE_LIMIT_DELAY)`（config `species_cache.wikipedia_rate_limit_delay`，預設 0.5s）做 rate limit；語言取自 `species_cache.wikipedia_language`（預設 `zh`）
- 使用官方 `action=query`、`titles` 與 `redirects=1` 解析學名條目和轉址，取最終條目標題（去括號註記）為俗名；預設 `zh` 語言會再以 OpenCC `s2twp` 正規化為台灣繁體。
- 請求包含專案 User-Agent、15 秒逾時；HTTP 429/5xx、連線、逾時與非 JSON 暫時回應最多重試 3 次。
- **確定結果才寫檔**：記憶體快取 + 快取檔 append（config `species_cache.cache_directory` / `cache_filename`，預設 `學名資料庫/species_cache.tsv`；TSV：`學名\t俗名`）。API 明確標示頁面 `missing`／`invalid` 時寫入空的俗名代表查無；回應缺少頁面資料或標題屬於不完整回應，當成錯誤、不寫入快取；同一學名有多列時，載入時以最後一列為準。
- 暫時性 API 錯誤（限流、逾時、連線失敗）回 None 且不寫入快取，之後查詢會重新連線。
- `lookup_species(name, refresh=True)` 略過快取重新連線，用於「再查一次」。
- 種以下的名稱（例如 `Fagus sylvatica purpurea`）查無時，`lookup_species` 以 `parent_species_name` 取出上一層物種並查詢，把結果放在 `SpeciesLookup.parent_name`／`parent_value` 當參考；`value` 仍是 None。上一層查詢遇到限流或錯誤時，`status` 仍是查無，失敗記在 `parent_status`／`parent_detail`；`SpeciesLookup.rate_limited` 與 `SpeciesLookup.failed` 會把上一層的失敗算進去。

## 檔案結構

- `app/views/lookup_view.py` — UI
- `app/services_impl/pipelines/lookup_service.py` — service 封裝
- `translation_tool/utils/species_cache.py` — `lookup_species_name`（本地快取 + 線上查詢）；`translation_tool/utils/species_names.py` — `is_potential_species_name` 等不碰網路的判斷規則

## 維護注意

1. 兩個 worker 皆由 `launch_page_operation` 啟動，不直接改控制項：結果經 `_run_on_ui`（`page.run_task`）排到 event loop 套用。
2. `page` 屬性必須維持 `@property` 並回傳 Page 實例；`show_snack(self.page, ...)` 與 `run_task` 依賴此行為。
3. 批次結果以 `ensure_ascii=False` dump 中文，結果區顯示原始 JSON。
