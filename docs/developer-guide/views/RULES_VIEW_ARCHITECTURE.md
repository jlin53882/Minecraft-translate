# RULES_VIEW_ARCHITECTURE.md

## 定位

RulesView 是翻譯前置工具，`replace_rules.json`（From/To 替換對）的管理介面。翻譯引擎讀取這些規則套用在譯文上。

```
RulesView（維護替換規則）→ ConfigView（翻譯參數）→ Translation Workflow（執行翻譯）
```

流程圖見 [page_flows/rules.md](../../page_flows/rules.md)。

## 檔案結構

- `app/views/rules_view.py` — 主視圖（`RulesView`）
- `app/views/rules/rules_widgets.py` — `RulesWidgetsMixin`，控制項建立與版面組裝（`_init_*`、`_build_*`）
- `app/views/rules/rules_actions.py` — 重新載入執行緒、放棄變更確認對話框（`confirm_discard_changes`）、驗證與儲存 worker（`launch_validate_and_save`、`to_saved_rules`）與分頁計算
- `app/views/rules/rules_filter.py` — 搜尋比對（`parse_query`、`rule_matches`、`filter_rules`，純邏輯）
- `app/views/rules/rules_validation.py` — 逐條驗證（`check_rule`、`validate_rule`、`build_from_index`、`translate_regex_error`）與非阻擋提醒（`rule_hint`）
- `translation_tool/utils/replace_rules_diagnostics.py` — 規則診斷（`diagnose_rules`：交叉引用、自我引用、重複、前後空白、原詞不會出現在 s2twp 轉換後的文字），非 UI，可重用
- `app/views/rules/rules_state.py` — `RulesTableState` dataclass（**page_size=50**、current_page、total_pages、rid_seq）
- `app/views/rules/rules_table.py` — `create_rule_row(view, from_text, to_text, rid, display_no)` 建立 DataRow（`RulesView.create_rule_row` 轉呼叫它）
- `app/services_impl/config_service.py` — `load_replace_rules()` / `save_replace_rules()`（委派給 `translation_tool/utils/text_processor.py` 的同名函式，路徑 `REPLACE_RULES_PATH = str(PROJECT_ROOT / "replace_rules.json")`）

## RulesTable 結構（ft.DataTable）

| 欄位 | 說明 |
|------|------|
| `#` | 流水號（灰色，唯讀） |
| `原文 (簡體)` | `kit.text_field`（mono、multiline，`on_change=on_text_change` 即時驗證） |
| `替換為 (繁體)` | 同上 |
| `操作` | `Row[警示圖示, IconButton]`：警示圖示在規則有診斷問題時顯示，懸停列出全部原因；IconButton（DELETE_OUTLINE，`data=rid`，`on_click=delete_row_clicked`） |

- 每列 `DataRow(data=rid)`，`_rid` 為 UI 專用穩定 ID（`_new_rid()` 分配）
- `from_field.data = {'rid': rid, 'field': 'from'}` 供 `on_text_change` 定位
- 每頁 50 條（`RulesTableState.page_size`）
- 換頁／搜尋／排序時**重用既有列**（`_fill_rule_row` 改寫內容，多出的列只設 `visible=False`），不重建 DataRow

## 頁面佈局

`__init__` 先 `_init_controls`（`_init_rules_table_controls` + `_init_rules_sort_and_test_controls`），再組出：`_build_header`（`kit.page_header`，標題「替換規則」、副標「機器翻譯後自動套用的用語統一規則，支援純文字與正規表達式」，右側 `loading_indicator`）、`_build_toolbar`（由左至右：`search_box`（placeholder「搜尋 from / to / 備註 / 分類　（/正則/ 以斜線包起來）」）/ `sort_box`（placeholder「排序方式」）/ 重新載入 / 新增規則 / 全部儲存）、左側 `_build_rules_table_area`（`rules_table` + `_build_footer` 分頁列：`total_count_text`、`prev_button`、`page_jump_field`、`total_pages_text_label`、`next_button`）、右側 `_build_test_panel`（「即時測試」卡：`test_input`（標籤「輸入文字」，placeholder「貼上一段簡體文字，立即看到套用結果」）/ `test_result`（標籤「套用結果」）/ `test_info`；其下為「使用說明」卡）。最後 `_initial_load()` 啟動背景載入。

即時測試（`on_test_change`）以目前尚未儲存的 `all_rules_data`（略過 from 為空或無法編譯者）呼叫 `apply_replace_rules`。

## RulesActions 提供的操作

| 函式 | 職責 |
|------|------|
| `start_reload_thread(view)` | 背景執行緒重載（顯示 loading_indicator + snack） |
| `perform_reload(view)` | `_load_rules_core()` → 經 `_run_on_ui_thread` 呼叫 `_handle_reload_success/failure` |
| `confirm_discard_changes(page, on_confirm)` | 有未儲存變更時的確認對話框，按「放棄變更並重新載入」才執行 `on_confirm` |
| `launch_validate_and_save(view, snapshot, finish, run_task)` | 同一個 operation 內驗證並寫檔，結束後呼叫 `finish(result)` |
| `to_saved_rules(snapshot)` | 寫檔內容：移除 `_rid`、略過 from 為空的列，`comment` 等其他欄位保留 |
| `calc_total_pages(total, page_size)` | `math.ceil` 總頁數（0 條時回 1） |

## 搜尋與排序

- **搜尋**：`on_search` 以 `Debouncer`（`app/ui/debounce.py`，300ms，在 event loop 上執行）延遲 → `_do_search(keyword)`；清空關鍵字立即清除搜尋
  - 以 `/` 開頭與結尾（長度>2）→ **Regex 模式**（`rules_filter.parse_query`）；語法錯誤時退回一般搜尋並提示
  - 搜尋欄位：`from` / `to` / `comment` / `category`（`rule_matches`，一律不分大小寫）
  - 目前條件存在 `_query` 與 `_only_issues`；`search_results` 一律由 `_apply_filter()` 依它們重算。新增以外的資料異動（刪除、復原、排序、重載）之後都要重算，否則畫面會顯示已刪除或過期的規則。
  - 搜尋中「#」欄顯示規則在全部清單中的位置，與「第 N 條規則重複／錯誤」的編號一致
- **只看有問題的規則**：`only_issues_box`（Checkbox，標籤帶問題數）→ `_only_issues`，與搜尋條件取交集
- **排序**：`sort_box`（`from_asc` 依 From 字典序 / `from_len` 依 From 長度由短到長）→ 直接排序 `all_rules_data`、重跑診斷與過濾、回到第 1 頁

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

`on_text_change` → 更新 `all_rules_data[index][field]`、`_mark_changed()` → `_refresh_row_status()`：一次掃描建立 `{from: [索引]}` 與 `{rid: 索引}`，重算畫面上各列的 `(出錯欄位, 錯誤, 提醒)`，只有結果變了的列才改樣式並以 `update_regions` 送出：
- 通過 → 清除紅框與錯誤
- 不通過 → 只有出錯的欄位紅框並以 `error` 顯示原因（from 的問題在 from 欄，群組引用與跳脫的問題在 to 欄）
- 通過但 from 含正則符號 → from 欄下方以 `helper` 提醒「將視為正規表達式」（不阻擋）

換頁／搜尋／排序時 `_render_current_page` 也會對畫面上的列做同樣的驗證，所以載入、儲存失敗跳頁後錯誤樣式都會顯示。

## 規則診斷（非阻擋）

`diagnose_rules`（`translation_tool/utils/replace_rules_diagnostics.py`）在載入成功時同步執行（`_run_diagnostics`；s2twp 轉換結果以 `lru_cache` 記住）。編輯、刪除、復原、排序則由 `_diag_debouncer` 在停頓 0.6 秒後執行 `_rediagnose_after_edit`，重算並刷新畫面上的警示圖示（不重新過濾，避免列在輸入中消失），避免大量規則時拖慢熱操作。結果存在 `_issues`（`{rid: [RuleIssue]}`）。偵測項目見該模組 docstring。

## 驗證決策鏈（`RulesView.validate_rule`）

依序檢查，任一失敗即回 `(False, 錯誤訊息)`：
驗證實作在 `rules_validation.check_rule`（回傳出錯欄位與訊息），`validate_rule` 只取訊息。
1. `src.strip()` 為空 → `'from 欄位不可為空'`
2. `re.compile(src)` 拋 `re.error` → `translate_regex_error`（missing `)` / bad escape / multiple repeat / unterminated char set / unknown extension 對應中文提示）
3. **重複檢查**：與其他列（`idx != current_index`）的 `from` 相同 → `'⚠ 與第 N 條規則重複'`（批次驗證時用 `_build_from_index` 建立索引）
4. **群組引用**：`dst` 中 `\N` / `$N` 引用數 > `compiled.groups` → `'引用群組 \N 超出群組數 M'`
5. **無效跳脫**：`dst` 含連續兩個反斜線且其後非數字 → `'可能存在無效跳脫…'`

## 儲存流程（save_rules_clicked）

1. 複製 `all_rules_data` 快照，記下 `_edit_seq`，以 `launch_validate_and_save` 啟動「規則驗證與儲存」operation（owner `rules-save`，`DurabilityPolicy.USER_ACTION`，`ShutdownPolicy.DRAIN_ONLY`）；背景 worker 依序執行 `_validate_all` 及通過後的寫檔；`_saving` 旗標防止重複觸發
2. 有錯 → snack「第 N 條規則錯誤」，`_reveal_rule` 跳到該條所在頁（該條不在搜尋結果內時先清除搜尋），渲染後該列顯示紅框與原因
3. 通過 → `to_saved_rules(snapshot)` 寫檔（移除 `_rid`、略過 from 為空的列，其他欄位保留），由同一 operation 呼叫 `save_replace_rules(..., raise_on_error=True)`；成功後若儲存期間沒有新的編輯（`_edit_seq` 未變）就清除「未儲存」標示

## 未儲存變更與復原

- `_has_unsaved`：新增、刪除、編輯、復原時由 `_mark_changed()` 設定，footer 的 `unsaved_text` 顯示「● 有未儲存的變更」；儲存成功或重新載入成功後清除。排序不算變更（載入器會依長度重排，順序不是語意）。
- 重新載入：有未儲存變更時先彈 `confirm_discard_changes`，按「放棄變更並重新載入」才執行。頁面物件在切頁時不會被銷毀，所以編輯在切頁後仍保留（只有重新載入與關閉程式會遺失）。
- 刪除：`delete_row_clicked` 依 rid 刪除，提示帶「復原」動作（`_undo_delete` 把規則放回原位置並跳到它所在頁）。
- 新增：搜尋或「只看有問題」啟用中會先清除，否則新列不會顯示；新列取得焦點。
- 換頁（上一頁／下一頁）與切換篩選後，表格捲回頂端（`rules_list.scroll_to`）。

## 資料載入/儲存核心（config_service.py）

- `load_replace_rules()` / `save_replace_rules(rules, *, raise_on_error=False)`：委派給 `translation_tool/utils/text_processor.py` 的同名函式，路徑 `REPLACE_RULES_PATH = str(PROJECT_ROOT / "replace_rules.json")`
- 載入失敗或檔案不存在時回傳空 list（UI 顯示空表格）；載入時固定字串規則依 from 長度由長到短排序，正則規則（from 含 `.?*[]()\\` 任一字元者）排在其後，所以畫面順序可能與檔案順序不同
- 替換規則最終流向翻譯引擎：`text_processor.load_replace_rules` / `convert_text` / `recursive_translate` 在翻譯時套用

## 與 lm_config_rules.py 的關係

- RulesView **不直接依賴** `translation_tool/core/lm_config_rules.py`；後者屬翻譯引擎層（API Key 輪替、健康狀態與可翻譯判斷）
- 替換規則是**翻譯引擎的輸入資料**，兩者在不同層次

## 維護注意

1. `_run_on_ui_thread` 用於把背景執行緒結果切回 UI 執行緒（Flet 無內建 thread-safe 更新）。
2. 任何改動 `all_rules_data` 的操作都要依序：`_mark_changed()`、`_run_diagnostics()`（索引有變時）、`_apply_filter()`、`_render_current_page()`。
3. Flet 1.0 的 TextField 錯誤與說明屬性是 `error`、`helper`；設成其他屬性名稱不會送到前端。
