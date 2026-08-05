# LOOKUP_VIEW_ARCHITECTURE.md

## 定位

LookupView 是**學名（物種名稱）翻譯的快速查詢工具**，屬於翻譯前置準備工具。支援單筆與批次（JSON 陣列）兩種查詢，結果來自本地快取與線上查詢（`translation_tool.utils.species_cache`）。

## 主要 UI 元件

| 元件 | 類型 | 說明 |
|------|------|------|
| `single_input` | TextField | 單筆學名輸入（如 `Felis catus`） |
| `single_button` | ft.Button | 觸發單筆查詢 |
| `single_result_text` | Text | 單筆查詢結果（`selectable=True`） |
| `single_progress_ring` | ProgressRing | 查詢中旋轉指示器（預設隱藏） |
| `batch_input` | TextField | JSON 陣列輸入（multiline, min_lines=5） |
| `batch_result_textfield` | TextField | 批次結果（read_only, multiline） |
| `batch_button` | ft.Button | 觸發批次查詢 |
| `batch_progress_bar` | ProgressBar | 批次進度（不確定進度時 `value=None`） |

**佈局**：雙 Card（單筆在上、批次在下），`ft.Column(scroll=ADAPTIVE)`。

## 呼叫鏈

### 單筆查詢
```
single_lookup_clicked(e)
  ├─ 驗證輸入非空（否則顯示錯誤）
  ├─ 鎖 UI：button/input disabled + ProgressRing 顯示 + 「查詢中...」
  ├─ threading.Thread(single_lookup_worker).start()
  └─ single_lookup_worker(name)
       └─ run_manual_lookup_service(name) → 更新結果 + 復原 UI（finally）
```

### 批次查詢
```
batch_lookup_clicked(e)
  ├─ 驗證 JSON 非空
  ├─ 鎖 UI：button disabled + ProgressBar 顯示（indeterminate）
  ├─ threading.Thread(batch_lookup_worker).start()
  └─ batch_lookup_worker(json_text)
       └─ for update in run_batch_lookup_service(json_text):  # generator
            ├─ update.error → 顯示 log、break
            ├─ update.result → 寫入 batch_result_textfield
            └─ update.progress → batch_progress_bar.value
        finally: 復原 UI（button + ProgressBar）
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

## 檔案結構

- `app/views/lookup_view.py` — UI（約 195 行）
- `app/services_impl/pipelines/lookup_service.py` — service 封裝
- `translation_tool/utils/species_cache.py` — `is_potential_species_name` / `lookup_species_name`（本地快取 + 線上查詢）

## 維護注意

1. 兩個 worker 都在背景執行緒更新 Flet 控制項並呼叫 `page.update()`；與 Extractor/LM 頁的 TaskSession + UI timer 模式不同，此頁為一次性 worker 執行緒。
2. `page` 屬性為 `@property`（2026-08-01 PR #85 修正）：先前是 bound method，導致 `show_snack(self.page, ...)` 收不到 Page 實例。
3. 批次結果以 `ensure_ascii=False` dump 中文，結果區顯示原始 JSON。
