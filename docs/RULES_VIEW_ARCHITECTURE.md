# RULES_VIEW_ARCHITECTURE.md

## 定位

RulesView 是翻譯前置工具，`replace_rules.json`（From/To 替換對）的管理介面。翻譯引擎讀取這些規則套用在譯文上。

```
RulesView（維護替換規則）→ ConfigView（翻譯參數）→ Translation Workflow（執行翻譯）
```

## 檔案結構

- `app/views/rules_view.py` — 主視圖（約 742 行）
- `app/views/rules/rules_actions.py` — 載入/儲存/驗證/分頁等操作
- `app/views/rules/rules_state.py` — `RulesTableState` dataclass（**page_size=50**、current_page、total_pages、rid_seq）
- `app/views/rules/rules_table.py` — `create_rule_row(view, from_text, to_text, rid, display_no)` 建立 DataRow
- `app/services_impl/config_service.py` — `load_replace_rules()` / `save_replace_rules()`（包裝 `load_rules_core` / `save_rules_core`，路徑 `REPLACE_RULES_PATH`）

## RulesTable 結構（ft.DataTable）

| 欄位 | 說明 |
|------|------|
| `#` | 流水號（灰色，唯讀） |
| `原文 (簡體)` | TextField（UNDERLINE、multiline、on_change 即時驗證） |
| `替換為 (繁體)` | TextField（同上） |
| `操作` | IconButton（刪除，`data=rid`） |

- 每列 `DataRow(data=rid)`，`_rid` 為 UI 專用穩定 ID（`_new_rid()` 分配）
- `from_field.data = {'rid': rid, 'field': 'from'}` 供 `on_text_change` 定位
- 每頁 50 條（`RulesTableState.page_size`）

## RulesActions 提供的操作

| 函式 | 職責 |
|------|------|
| `start_reload_thread(view)` | 背景執行緒重載（顯示 loading_indicator + snack） |
| `perform_reload(view)` | `_load_rules_core()` → UI 執行緒 `_handle_reload_success/failure` |
| `start_save_thread(view, clean_rules)` | 背景執行緒 `save_replace_rules(clean_rules)` → snack 成功/失敗 |
| `calc_total_pages(total, page_size)` | `math.ceil` 總頁數（0 條時回 1） |
| `validate_rule(view, src, dst, all_rules, current_index)` | Regex 語法 / 重複 / 群組引用（`\1`/`$1` 超出群組數）/ 無效跳脫 |
| `translate_regex_error(err)` | Python re.error → 中文提示 |

## 搜尋與排序

- **搜尋**：`on_search` debounce 300ms（`threading.Timer`）→ `_do_search(keyword)`
  - 以 `/` 開頭與結尾（長度>2）→ **Regex 模式**（`re.search(..., IGNORECASE)`）
  - 搜尋欄位：`from` / `to` / `comment` / `category`
  - 結果存 `search_results`，`_render_current_page` 依此分頁（不影響 `all_rules_data`）
- **排序**：`sort_box`（from_asc 字典序 / from_len 長度）→ 直接排序 `all_rules_data` + 回到第 1 頁

## 資料載入/渲染流程

```
_initial_load() [thread]
  → _load_rules_core() = load_replace_rules()
  → _handle_reload_success(rules_data)
      → all_rules_data 初始化 → _render_current_page()

_render_current_page()
  ├─ 資料來源：search_results（搜尋模式）或 all_rules_data（全部）
  ├─ 計算 start/end 分頁切片 → create_rule_row()
  ├─ 更新 page_info / total_count / prev/next disabled
  └─ page.update()
```

## 編輯驗證

`on_text_change` → 更新 `all_rules_data[index][field]` → `validate_row_ui(rid)`：
- 合法 → 清除紅框與 error_text
- 不合法 → `from`/`to` 欄位紅框 + `error_text`（如「正則表達式缺少結尾括號「)」。」）

## 與 lm_config_rules.py 的關係

- RulesView **不直接依賴** `lm_config_rules.py`；後者屬翻譯引擎層（API Key 輪替與提示詞管理）
- 替換規則是**翻譯引擎的輸入資料**，兩者在不同層次

## 維護注意

1. 舊版文件標示 page_size=20 已修正為 **50**（`RulesTableState`）。
2. `_run_on_ui_thread` 用於把背景執行緒結果切回 UI 執行緒（Flet 無內建 thread-safe 更新）。
3. `delete_row_clicked` / `add_row_clicked` 操作 `all_rules_data` 後需 `_render_current_page()`。
