# RULES_VIEW_ARCHITECTURE.md

## 定位

RulesView 是翻譯前置工具，`replace_rules.json`（From/To 替換對）的管理介面。翻譯引擎讀取這些規則套用在譯文上。

```
RulesView（維護替換規則）→ ConfigView（翻譯參數）→ Translation Workflow（執行翻譯）
```

## 檔案結構

- `app/views/rules_view.py` — 主視圖（`RulesView`）
- `app/views/rules/rules_actions.py` — 載入/儲存執行緒與分頁計算等操作
- `app/views/rules/rules_state.py` — `RulesTableState` dataclass（**page_size=50**、current_page、total_pages、rid_seq）
- `app/views/rules/rules_table.py` — `create_rule_row(view, from_text, to_text, rid, display_no)` 建立 DataRow（`RulesView.create_rule_row` 轉呼叫它）
- `app/services_impl/config_service.py` — `load_replace_rules()` / `save_replace_rules()`（包裝 `load_rules_core` / `save_rules_core`，路徑 `REPLACE_RULES_PATH`）

## RulesTable 結構（ft.DataTable）

| 欄位 | 說明 |
|------|------|
| `#` | 流水號（灰色，唯讀） |
| `原文 (簡體)` | `kit.text_field`（mono、multiline，`on_change=on_text_change` 即時驗證） |
| `替換為 (繁體)` | 同上 |
| `操作` | IconButton（DELETE_OUTLINE，`data=rid`，`on_click=delete_row_clicked`） |

- 每列 `DataRow(data=rid)`，`_rid` 為 UI 專用穩定 ID（`_new_rid()` 分配）
- `from_field.data = {'rid': rid, 'field': 'from'}` 供 `on_text_change` 定位
- 每頁 50 條（`RulesTableState.page_size`）
- 換頁／搜尋／排序時**重用既有列**（`_fill_rule_row` 改寫內容，多出的列只設 `visible=False`），不重建 DataRow

## 頁面佈局

`__init__` 先 `_init_controls`（`_init_rules_table_controls` + `_init_rules_sort_and_test_controls`），再組出：`_build_header`（`kit.page_header`，標題「替換規則」、副標「機器翻譯後自動套用的用語統一規則，支援純文字與正規表達式」，右側 `loading_indicator`）、`_build_toolbar`（由左至右：`search_box`（placeholder「搜尋 from / to / 備註 / 分類　(/正則/ 以斜線包起來)」）/ `sort_box`（placeholder「排序方式」）/ 重新載入 / 新增規則 / 全部儲存）、左側 `_build_rules_table_area`（`rules_table` + `_build_footer` 分頁列：`total_count_text`、`prev_button`、`page_jump_field`、`total_pages_text_label`、`next_button`）、右側 `_build_test_panel`（「即時測試」卡：`test_input`（標籤「輸入文字」，placeholder「貼上一段簡體文字，立即看到套用結果」）/ `test_result`（標籤「套用結果」）/ `test_info`；其下為「使用說明」卡）。最後 `_initial_load()` 啟動背景載入。

即時測試（`on_test_change`）以目前尚未儲存的 `all_rules_data`（略過 from 為空或無法編譯者）呼叫 `apply_replace_rules`。

## RulesActions 提供的操作

| 函式 | 職責 |
|------|------|
| `start_reload_thread(view)` | 背景執行緒重載（顯示 loading_indicator + snack） |
| `perform_reload(view)` | `_load_rules_core()` → 經 `_run_on_ui_thread` 呼叫 `_handle_reload_success/failure` |
| `start_save_thread(view, clean_rules)` | 背景執行緒 `save_replace_rules(clean_rules)` → snack 成功/失敗 |
| `calc_total_pages(total, page_size)` | `math.ceil` 總頁數（0 條時回 1） |
| `translate_regex_error(err)` | Python re.error → 中文提示（`RulesView.translate_regex_error` 轉呼叫它） |

另有模組層級 `validate_rule(view, src, dst, all_rules, current_index)`，但 `RulesView` 實際使用的是自己的 `validate_rule` 方法（多了 `from_index` 參數），模組層級版本目前沒有被 view 呼叫。

## 搜尋與排序

- **搜尋**：`on_search` 以 `Debouncer`（`app/ui/debounce.py`，300ms，在 event loop 上執行）延遲 → `_do_search(keyword)`；清空關鍵字立即清除搜尋
  - 以 `/` 開頭與結尾（長度>2）→ **Regex 模式**（`re.search(..., IGNORECASE)`）
  - 搜尋欄位：`from` / `to` / `comment` / `category`（`_rule_matches`；預設不分大小寫；Regex 編譯失敗時退回一般搜尋）
  - 結果存 `search_results`，`_render_current_page` 依此分頁（不影響 `all_rules_data`）
- **排序**：`sort_box`（`from_asc` 依 From 字典序 / `from_len` 依 From 長度由短到長）→ 直接排序 `all_rules_data` + 回到第 1 頁

## 資料載入/渲染流程

```
_initial_load() [thread]
  → _load_rules_core() = load_replace_rules()
  → _run_on_ui_thread(_handle_reload_success, rules_data)
      → all_rules_data 初始化（補 _rid）→ _render_current_page()
```

`_run_on_ui_thread` 以 `page.loop.call_soon_threadsafe` 切回 event loop；頁面尚未掛載時先暫存，`did_mount` 再執行。

```
_render_current_page()
  ├─ 資料來源：search_results（搜尋模式）或 all_rules_data（全部）
  ├─ 計算 start/end 分頁切片 → 重用／補足列（create_rule_row + _fill_rule_row）
  ├─ 更新 page_info / total_count_text / total_pages_text_label / prev/next disabled
  └─ self.update()（尚未掛載時退回 page.update()）
```

## 編輯驗證

`on_text_change` → 更新 `all_rules_data[index][field]` → `validate_row_ui(rid)`：
- 合法 → 清除紅框與 error_text
- 不合法 → `from`/`to` 欄位紅框 + `error_text`（如「正則表達式缺少結尾括號「)」。」）

## 驗證決策鏈（`RulesView.validate_rule`）

依序檢查，任一失敗即回 `(False, 錯誤訊息)`：
1. `src.strip()` 為空 → `'from 欄位不可為空'`
2. `re.compile(src)` 拋 `re.error` → `translate_regex_error`（missing `)` / bad escape / multiple repeat / unterminated char set / unknown extension 對應中文提示）
3. **重複檢查**：與其他列（`idx != current_index`）的 `from` 相同 → `'⚠ 與第 N 條規則重複'`（批次驗證時用 `_build_from_index` 建立索引）
4. **群組引用**：`dst` 中 `\N` / `$N` 引用數 > `compiled.groups` → `'引用群組 \N 超出群組數 M'`
5. **無效跳脫**：`dst` 含連續兩個反斜線且其後非數字 → `'可能存在無效跳脫…'`

## 儲存流程（save_rules_clicked）

1. 複製 `all_rules_data` 快照，先註冊「規則批次驗證」operation，再以背景 worker 執行 `_validate_all`（逐條 `validate_rule`）；`_saving` 旗標防止重複觸發
2. 有錯 → snack「第 N 條規則錯誤」並跳到該條所在頁
3. 通過 → 移除 `_rid`、略過 from 為空的列，交給 `start_save_thread`

## 資料載入/儲存核心（config_service.py）

- `load_replace_rules()` / `save_replace_rules()`：包裝 `load_rules_core` / `save_rules_core`，路徑 `REPLACE_RULES_PATH = PROJECT_ROOT / "replace_rules.json"`
- 載入失敗或檔案不存在時回傳空 list（UI 顯示空表格）；載入時固定字串規則依 from 長度由長到短排序，正則規則（from 含 `.?*[]()\\` 任一字元者）排在其後，所以畫面順序可能與檔案順序不同
- 儲存由 `start_save_thread` 背景執行緒呼叫
- 替換規則最終流向翻譯引擎：`text_processor.load_replace_rules` / `convert_text` / `recursive_translate` 在翻譯時套用

## 與 lm_config_rules.py 的關係

- RulesView **不直接依賴** `lm_config_rules.py`；後者屬翻譯引擎層（API Key 輪替與提示詞管理）
- 替換規則是**翻譯引擎的輸入資料**，兩者在不同層次

## 維護注意

1. `_run_on_ui_thread` 用於把背景執行緒結果切回 UI 執行緒（Flet 無內建 thread-safe 更新）。
2. `delete_row_clicked` / `add_row_clicked` 操作 `all_rules_data` 後需 `_render_current_page()`。

## 檔案結構（拆分後）

- `app/views/rules_view.py`：`RulesView` 主體（載入、搜尋、驗證、儲存、新增／刪除）。
- `app/views/rules/rules_widgets.py`：`RulesWidgetsMixin`，控制項與版面組裝（`_init_*`、`_build_*`）。
- `app/views/rules/rules_actions.py`、`rules_state.py`、`rules_table.py`：重新載入與儲存動作、狀態、表格列。
