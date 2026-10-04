# 設定頁 schema（#134）

設定頁的控制項、版面、載入、儲存、說明文字與套用時機，**全部由 `app/views/config/settings_schema.py` 的資料產生**。

```
DEFAULT_CONFIG（預設值唯一來源）
        │
settings_schema.SETTINGS ──► settings_form.build_controls()  控制項
        │                    settings_form.build_pages()      版面（依 LAYOUT；沒列出的設定自動接在所屬卡片後面）
        │                    config_actions.load/save          載入與儲存（型別轉換、空白、下限、驗證）
        └── app/config_apply.py                                套用時機（說明文字、存檔提示、自動重載）
```

## 新增一個一般設定

1. 在 `translation_tool/utils/config_manager.py` 的 `DEFAULT_CONFIG` 加上預設值。
2. 在 `SETTINGS` 加一個 `Setting`，指定 `page`（導覽頁）與 `card`（卡片標題）。

不需要手寫控制項、載入或儲存程式；設定會自動出現在所屬卡片的最後面。需要特殊排版時才在 `LAYOUT` 指定位置（`F` 單一控制項、`R` 一列多欄、`Side` 並排區塊、`Note`／`Head`／`Gap`／`Div` 文字與分隔、`Labeled`／`Cols` 帶標題欄）。

`Setting` 欄位：`kind`（`str`／`int`／`float`／`bool`／`text`／`lines`／`choice`／`custom`／`none`）、`label`、`help`、`weight`（列內欄寬）、`minimum`（儲存時夾住下限）、`blank`（數值留空：`default` 用預設值／`zero` 為 0／`error` 報錯）、`choices`、`validator`、`label_template`／`label_refs`（用目前值組標籤）。`kind="none"` 必須寫 `reason`，說明為什麼不在設定頁。

若這個設定的套用時機不是「下次任務」，在 `app/config_apply.py` 的 `CONFIG_APPLY_RULES` 登記；沒登記的設定顯示「下次執行任務時套用」。

## 專用元件

API 金鑰列與模型列有各自的新增／移除／排序互動，無法由欄位資料描述，仍由 `ConfigView` 手寫，並在 schema 中以 `kind="custom"` 與 `Custom("keys")`／`Custom("models")` 區塊登記。

## 測試如何保證

`tests/test_config_settings_coverage.py`：

- 每個 `DEFAULT_CONFIG` 葉節點都有對應的 `Setting`（沒有的話測試失敗並指出缺哪個）；schema 內沒有已不存在的設定；
- 每個設定的 `kind` 與預設值型別相符、選項合法、驗證器存在、不在設定頁的設定都寫了原因；
- `LAYOUT` 只引用存在的設定且不重複；合併自動版面後每個設定剛好出現一次；
- 真正的 `ConfigView.controls_map` 與 schema 完全一致；
- 載入 `DEFAULT_CONFIG` → 不改動 → 儲存，所有設定的值維持不變（round trip）；
- 空白、下限、非法輸入、驗證器、多行清單的處理；
- 示範：新增一個 `Setting` 後，它自動出現在所屬卡片、有控制項、能載入與儲存。

## 與舊版設定頁的差異

- 版面（卡片、列、欄寬）與舊版逐一比對過一致；頁面在 `Stack` 內的順序改為與導覽列相同（顯示不受影響）。
- 每個欄位的說明文字現在都會附上套用時機（例如「下次執行任務時套用」、`species_cache.*`「需重啟」）。
- 數值欄位載入時一律以字串顯示（舊版部分欄位直接放數字）。
- 數值欄位留空時的行為統一依 `blank`：原本會報錯但說明文字寫「空白用預設值」的欄位，現在確實使用預設值。
