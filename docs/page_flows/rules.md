# 替換規則頁（`rules`）處理流程

> 入口：`app/views/rules_view.py` 的 `RulesView`。詳細元件說明見 [`View 架構文件`](../developer-guide/views/RULES_VIEW_ARCHITECTURE.md)。

## 職責

替換規則頁用來編輯 `replace_rules.json`（位於專案根目錄，路徑由 `app/services_impl/config_service.py` 的 `REPLACE_RULES_PATH` 指定）。每條規則包含 `from`（正則表達式）與 `to`（替換內容）。頁面提供：

- 分頁表格（每頁 50 列）的逐列編輯與刪除，以及新增空白列。
- 搜尋（關鍵字或 `/正則/` 模式）、「只看有問題的規則」篩選與排序（依 From 字典序或長度）。
- 規則診斷：交叉引用、譯詞含自己的原詞、原詞重複、前後空白、原詞含 s2twp 會轉換的字，在該列顯示警示圖示（懸停看原因），不阻擋儲存。
- 刪除附「復原」；有未儲存變更時顯示標示，重新載入前會確認。
- 即時測試區：以目前尚未儲存的規則套用到輸入文字。
- 重新載入（從磁碟讀回）與儲存（驗證後寫回磁碟）。

頁面本身不做正則替換，替換邏輯由 `translation_tool/utils/text_processor.py` 的 `apply_replace_rules` 提供，這裡只負責把目前的規則餵給它。

`RulesView` 由 `RulesWidgetsMixin`（`app/views/rules/rules_widgets.py`）負責控制項的建構，列的建立由 `app/views/rules/rules_table.py` 的 `create_rule_row` 負責。

## 主要流程

### 初次載入

```mermaid
flowchart TD
    A["RulesView.__init__"] --> B["_init_controls 與版面組裝"]
    B --> C["_initial_load"]
    C --> D["launch_page_operation（規則初始載入，DRAIN_ONLY）"]
    D --> E["背景 run：_load_rules_core → load_replace_rules 讀檔"]
    E --> F{"讀檔成功？"}
    F -- 是 --> G["_run_on_ui_thread(_handle_reload_success)"]
    F -- 否 --> H["_run_on_ui_thread(_handle_reload_failure)，顯示錯誤提示"]
    G --> I["補上 _rid、current_page 設為 1、_render_current_page"]
    I --> J{"頁面已掛載？"}
    J -- 否 --> K["暫存到 _pending_ui_calls，等 did_mount 執行"]
    J -- 是 --> L["直接在 event loop 執行"]
```

`_run_on_ui_thread` 是背景執行緒與 UI 之間的橋接點：頁面尚未掛上時把呼叫放入 `_pending_ui_calls`，`did_mount` 再依序執行。

### 重新載入

```mermaid
flowchart TD
    A["重新載入按鈕（reload_rules_clicked）"] --> B["start_reload_thread"]
    B --> C["loading_indicator 顯示，提示「正在重新載入規則」"]
    C --> D["launch_page_operation（規則重新載入）"]
    D --> E["perform_reload：讀檔並 _run_on_ui_thread"]
    E --> F["_handle_reload_success 或 _handle_reload_failure"]
```

重新載入會以磁碟內容覆蓋記憶體中的全部規則。有未儲存的變更時，`reload_rules_clicked` 先以 `confirm_discard_changes` 確認，使用者選「放棄變更並重新載入」才執行；成功後搜尋條件保留，結果換成新載入的規則。

### 搜尋與排序

```mermaid
flowchart TD
    A["搜尋框 on_change（on_search）"] --> B{"關鍵字為空？"}
    B -- 是 --> C["取消 debounce，_do_search 清除搜尋"]
    B -- 否 --> D["_search_debouncer.call（300 毫秒）"]
    D --> E["_do_search：去除空白，判斷 /…/ 是否為正則模式"]
    E --> F["逐條 _rule_matches 比對 from、to、comment、category"]
    F --> G{"有結果？"}
    G -- 否 --> H["提示找不到，重新渲染目前頁"]
    G -- 是 --> I["search_results 設定後回到第 1 頁渲染"]
```

比對在 `rules_filter.rule_matches`，一律忽略大小寫。正則模式下語法錯誤會退回一般字串比對並提示。目前條件存在 `_query` 與 `_only_issues`，`search_results` 由 `_apply_filter` 依它們重算；刪除、復原、排序與重載之後都會重算，所以畫面不會顯示已刪除或過期的規則。搜尋中「#」欄是規則在全部清單中的位置。排序（`on_sort_change`）對 `all_rules_data` 做 in-place 排序、重跑診斷與過濾並回到第 1 頁，排序結果只存在記憶體中，要儲存才會寫入磁碟。

### 逐列編輯與即時驗證

```mermaid
flowchart TD
    A["from 或 to 輸入變更（on_text_change）"] --> B["依 _rid 找到 all_rules_data 的索引"]
    B --> C["更新該列的 from 或 to 欄位值"]
    C --> D["_refresh_row_status：呼叫 check_rule"]
    D --> E{"通過？"}
    E -- 是 --> F["清除紅框與錯誤文字"]
    E -- 否 --> G["紅框與錯誤文字顯示原因"]
```

`rules_validation.check_rule`（`validate_rule` 取其訊息）依序檢查：`from` 不可為空、`from` 必須能被 `re.compile` 編譯（錯誤訊息由 `translate_regex_error` 翻譯）、`from` 不可與其他列重複、`to` 中的 `\N` 或 `$N` 群組參照不可超過群組數、`to` 不可含無效跳脫。出錯的欄位紅框並以 `error` 顯示原因（from 的問題在 from 欄，群組引用與跳脫在 to 欄）；from 含正則符號時只在 from 下方提醒「將視為正規表達式」。`_refresh_row_status` 一次掃描建立索引，整頁 50 列的檢查是 O(n)，換頁與跳到錯誤列時也會套用。

編輯同時 `_mark_changed()` 標記未儲存，停頓 0.6 秒後 `_rediagnose_after_edit` 重跑規則診斷並刷新警示圖示。診斷邏輯在 `translation_tool/utils/replace_rules_diagnostics.py` 的 `diagnose_rules`。

新增列（`add_row_clicked`）會在清單末端加入 `from` 與 `to` 皆為空字串的列，跳到最後一頁並把游標放進新列；搜尋或「只看有問題」啟用中會先清除。刪除列（`delete_row_clicked`）依 `_rid` 找到索引後刪除並重算過濾，頁碼超出時回退，提示上的「復原」（`_undo_delete`）把規則放回原位。

### 即時測試

`on_test_change` 會把 `from` 非空且可編譯的規則組成清單，呼叫 `apply_replace_rules` 處理測試輸入文字，並顯示「已套用 N 條規則，文字有變更」或「沒有符合的規則」。規則使用的是目前記憶體中的內容，不必先儲存。

### 儲存（驗證在背景執行）

```mermaid
flowchart TD
    A["儲存按鈕（save_rules_clicked）"] --> B{"_saving 旗標已設定？"}
    B -- 是 --> C["直接返回"]
    B -- 否 --> D["複製 all_rules_data 為 snapshot，設定 _saving"]
    D --> E["_launch_rule_validation：launch_page_operation（規則驗證與儲存）"]
    E --> F["背景：_validate_all(snapshot)"]
    F --> G{"有錯誤？"}
    G -- 是 --> H["回傳 failure，不寫入磁碟"]
    G -- 否 --> I["過濾 from 為空的列，save_replace_rules(raise_on_error=True)"]
    H --> J["finish：顯示第 N 條規則錯誤，跳到該頁重新渲染"]
    I --> K["finish：顯示規則已成功儲存"]
```

`_validate_all` 先以 `build_from_index` 建立 `{from: [索引...]}` 字典，讓重複檢查達到 O(n)。驗證失敗時 `finish` 以 `_reveal_rule` 跳到錯誤列所在的頁面（該列不在搜尋結果內時先清除搜尋），渲染時對畫面上的列套用驗證，所以該列會顯示紅框與原因。儲存成功且期間沒有新的編輯（`_edit_seq` 未變）時清除未儲存標示。

若 `run_task` 不存在（沒有 event loop），`finish` 會經由 `_run_on_ui_thread` 回到 UI。

## 背景任務與 UI 更新

- **背景任務**：初次載入、重新載入、儲存（驗證加寫檔）三者都透過 `launch_page_operation` 送進頁面的 operation registry。三者皆使用 `CancellationPolicy.NON_CANCELLABLE`（不可取消）與 `ShutdownPolicy.DRAIN_ONLY`（關閉時排空既有任務）。儲存使用 `CommitPolicy.PARTIAL_ALLOWED` 與 `DurabilityPolicy.USER_ACTION`。
- **UI 更新**：背景執行緒只做讀檔、驗證與寫檔；所有控制項變更都透過 `_run_on_ui_thread` 或 `run_task` 排回 event loop。`worker_done` 事件與 `_wait_for_validation_and_save` 協程負責在 worker 結束後呼叫 `finish`。
- **搜尋 debounce**：`Debouncer` 綁定頁面，`will_unmount` 會取消尚未執行的搜尋。
- **列的重用**：`_render_current_page` 不清空表格，而是重用既有列（`_fill_rule_row`），多出的列以 `visible=False` 隱藏，避免每次翻頁重建 50 列的 `TextField`。
- **防重複提交**：`_saving` 旗標在儲存期間阻擋重複點擊；`finish` 一律把它清回 `False`。

## 狀態與資料

- **`RulesTableState`**（`app/views/rules/rules_state.py`）：`page_size`（50）、`current_page`、`total_pages`、`rid_seq`。`RulesView` 的 `current_page`、`total_pages` 等屬性由它初始化。
- **`all_rules_data`**：記憶體中的完整規則清單，每筆為 `{"from", "to", "_rid"}`。`_rid` 是 UI 用的穩定序號（`_new_rid` 遞增），不寫入磁碟。`_find_index_by_rid` 以它找回資料位置。
- **搜尋與篩選狀態**：`_query`（`RuleQuery` 或 `None`）、`_only_issues`、`search_results`（`None` 表示未過濾）、`search_keyword`、`search_regex`。
- **診斷與未儲存**：`_issues`（`{rid: [RuleIssue]}`）、`_has_unsaved`、`_edit_seq`、`_row_status`（各列目前的驗證結果，用來只更新有變的列）。
- **儲存內容**：`to_saved_rules` 產生寫入 `replace_rules.json` 的內容：只保留 `from` 非空的規則，移除 `_rid`，`comment`、`category` 等其他欄位原樣保留。
- **執行緒共享**：`_ui_lock` 保護 `_page` 與 `_pending_ui_calls`；`_pending_ui_calls` 只在頁面尚未掛載時使用。

## 錯誤處理

- **讀檔失敗**：`_handle_reload_failure` 關閉載入指示器並以 `show_snack` 顯示「載入規則時發生錯誤」；背景端同時以 `log_error` 記錄含 traceback 的訊息。
- **驗證失敗**：不寫入磁碟，跳到第一個錯誤列所在頁面並顯示「第 N 條規則錯誤」。
- **儲存失敗**：`save_replace_rules(raise_on_error=True)` 拋出的例外會被 `validate_and_save` 捕捉並記錄到 `result["error"]`，`finish` 顯示「儲存規則時發生錯誤」，然後重新拋出以讓 operation 記錄錯誤。
- **應用程式關閉中**：`launch_page_operation` 回傳假值時，儲存會顯示「應用程式正在關閉，未開始規則驗證或儲存」並清除 `_saving`。
- **正則錯誤**：即時測試與搜尋都會忽略無法編譯的 `from`（即時測試直接排除，搜尋退回一般字串比對），不會中斷頁面。
- **注意**：`to_saved_rules` 會過濾 `from` 為空的列，但驗證已先拒絕空的 `from`，因此實際上不會寫入空列。
