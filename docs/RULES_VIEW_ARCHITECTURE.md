# RulesView 架構文件

## RulesView 在整個流程中的位置

RulesView 是翻譯前置工具，屬於 `replace_rules.json` 的管理介面。

```
翻譯流程：
  RulesView（維護替換規則）→ ConfigView（設定翻譯參數）→ Translation Workflow（執行翻譯）
```

RulesView 負責編輯 `replace_rules.json`（From/To 替換對），這些規則在翻譯時由翻譯引擎讀取並套用。

---

## RulesTable 的結構

**Columns（`ft.DataTable`）**：

| 欄位 | 說明 |
|------|------|
| `#` | 流水號（灰色，唯讀） |
| `原文 (簡體)` | TextField（底線框，可編輯，on_change 即時驗證） |
| `替換為 (繁體)` | TextField（底線框，可編輯，on_change 即時驗證） |
| `操作` | IconButton（刪除按鈕） |

**Row 資料**：`all_rules_data[dict]`，每筆含 `from`/`to`/`_rid`，`_rid` 為 UI 專用穩定 ID。

**驗證規則**：即時驗證 Regex 語法、重複檢查、群組引用合理性。

---

## RulesActions 提供的操作

| 函式 | 職責 |
|------|------|
| `start_reload_thread()` | 觸發背景重新載入規則 |
| `start_save_thread()` | 觸發背景儲存規則至磁碟 |
| `perform_reload()` | 執行實際載入邏輯（線上程中呼叫） |
| `calc_total_pages()` | 計算總頁數（math.ceil） |
| `validate_rule()` | 驗證單筆規則的 Regex/群組引用正確性 |
| `translate_regex_error()` | 將 Python re.error 翻譯為中文提示 |

**分頁策略**：每頁 `page_size=20`，背景執行緒載入，避免 UI 阻塞。

---

## 與 lm_config_rules.py 的關係

- RulesView **不直接依賴** `lm_config_rules.py`
- `lm_config_rules.py` 屬於翻譯引擎層（`translation_tool/core/`），專責 API Key 輪替與提示詞管理
- RulesView 管理的替換規則（`replace_rules.json`）是**翻譯引擎的輸入資料**，兩者在不同層次

---

## 搜尋與排序

- **搜尋**：Debounce 300ms，支援普通關鍵字與 Regex 模式（以 `/` 包裹）
- **排序**：From 字典序 / From 長度
- 搜尋結果单独分頁（不影響原始 `all_rules_data`）
