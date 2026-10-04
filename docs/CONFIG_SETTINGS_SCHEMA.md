# 設定頁 schema（#134）

設定的欄位、型別、預設值、說明、套用時機、是否敏感與驗證，**全部以 `translation_tool/utils/config_schema.py` 的 `SETTINGS` 為唯一來源**；預設設定、設定頁、套用時機提示與機密遮蔽都由它衍生。

```
translation_tool/utils/config_schema.SETTINGS（唯一來源：欄位、預設值、說明、時機、敏感、驗證）
        │
        ├─► config_manager.DEFAULT_CONFIG                      預設值
        ├─► settings_form.build_controls()                     控制項
        ├─► settings_form.build_pages()                        版面（依 LAYOUT；沒列出的設定自動接在所屬卡片後面）
        ├─► config_actions.load/save                           載入與儲存（型別轉換、空白、下限、驗證）
        ├─► app/config_apply.py                                套用時機（說明文字、存檔提示、自動重載）
        └─► config_manager（sensitive）                        已知機密登錄（輸出遮蔽）
```

## config.example.json 由 schema 產生

`config.example.json` 是打包版第一次啟動時複製成 `config.json` 的範本，**不要手動編輯**。新增或修改設定後執行：

```
python tools/gen_config_example.py          # 覆寫 config.example.json
python tools/gen_config_example.py --check  # 只檢查，不一致回傳 1
```

`tests/test_config_example_generated.py` 在 CI 檢查檔案與 schema 逐字一致，所以忘了重新產生會讓測試失敗。

## 新增一個一般設定

**只需要在 `translation_tool/utils/config_schema.py` 的 `SETTINGS` 加一個 `Setting`。** 不需要改 `DEFAULT_CONFIG`、設定頁、套用時機表或機密遮蔽；最後執行一次 `python tools/gen_config_example.py` 更新範本：

```python
Setting(
    "translator.my_option", "int", "我的設定",        # 路徑、型別、標籤
    "general", C_TRANSLATOR,                          # 設定頁的頁面與卡片
    "說明文字",
    default=5,                                        # 預設值（DEFAULT_CONFIG 由此建出）
    minimum=1,                                        # 儲存時夾住下限
    timing="next_batch", timing_note="下一批次讀取。",  # 套用時機（不寫就是「下次任務」）
    sensitive=False,                                  # True：值登錄為機密，所有輸出出口遮蔽
)
```

| schema 欄位 | 衍生出來的東西 |
|---|---|
| `default` | `config_manager.DEFAULT_CONFIG`（`build_default_config()`）、三層合併的最底層、載入時的預設值、`config.example.json`（見下） |
| `kind` / `label` / `help` / `page` / `card` / `weight` | 設定頁的控制項與卡片；沒列在 `LAYOUT` 的設定自動接在所屬卡片最後面 |
| `minimum` / `blank` / `validator` | 儲存時的轉換、夾住下限、空白處理與驗證 |
| `timing` / `timing_note` | `app/config_apply.py::CONFIG_APPLY_RULES`（設定頁說明、存檔提示、自動重載） |
| `sensitive` | `config_manager` 載入設定時登錄為已知機密（`redaction.register_secrets`） |

需要特殊排版時才在 `app/views/config/settings_schema.py` 的 `LAYOUT` 指定位置（`F` 單一控制項、`R` 一列多欄、`Side` 並排區塊、`Note`／`Head`／`Gap`／`Div` 文字與分隔、`Labeled`／`Cols` 帶標題欄）。

`kind`：`str`／`int`／`float`／`bool`／`text`／`lines`／`choice`／`custom`／`none`。`blank`（數值留空）：`default` 用預設值／`zero` 為 0／`error` 報錯。`kind="none"` 必須寫 `reason`，說明為什麼不在設定頁。

schema 本身不依賴 Flet 或 `app`（引擎層與設定頁共用同一份資料）；設定頁特有的導覽頁與版面在 `app/views/config/settings_schema.py`。

只有兩種資訊不在 `Setting` 內：

- 不是設定葉節點的套用時機：`ui_logging.tail_lines`（不在 `DEFAULT_CONFIG`）與 `lm_translator.models.*.max_output_tokens`（模型名稱是動態 key），在 `app/config_apply.py` 以明確的額外規則登記。
- `DEPRECATED_CONFIG_KEYS`（舊設定相容用的欄位清單）。

## 與舊的 `lm_config_schema.py`

舊的最小 schema（只涵蓋 `lm_translator`、沒有程式使用、預設值曾與 `DEFAULT_CONFIG` 漂移）已移除，預設值改以 `config_schema.SETTINGS` 為唯一來源；`lm_config_schema.py` 只剩引擎端讀取每個模型輸出上限覆寫的 `model_output_token_cap()`。

## 專用元件

API 金鑰列與模型列有各自的新增／移除／排序互動，無法由欄位資料描述，仍由 `ConfigView` 手寫，並在 schema 中以 `kind="custom"` 與 `Custom("keys")`／`Custom("models")` 區塊登記。

## 測試如何保證

`tests/test_config_settings_coverage.py`：

- `DEFAULT_CONFIG` 等於 `build_default_config()`（預設值由 schema 建出）；每個 `DEFAULT_CONFIG` 葉節點都有對應的 `Setting`；schema 內沒有多餘的設定；
- 套用時機表由 schema 衍生、每個非預設的時機都有說明；名稱像機密的設定必須標示 `sensitive`；
- 每個設定的 `kind` 與預設值型別相符、選項合法、驗證器存在、不在設定頁的設定都寫了原因；
- `LAYOUT` 只引用存在的設定且不重複；合併自動版面後每個設定剛好出現一次；
- 真正的 `ConfigView.controls_map` 與 schema 完全一致；
- 載入 `DEFAULT_CONFIG` → 不改動 → 儲存，所有設定的值維持不變（round trip）；
- 空白、下限、非法輸入、驗證器、多行清單的處理；
- 示範：新增一個 `Setting`（含 `default`）後，預設設定自動包含它、自動出現在所屬卡片、有控制項、能載入與儲存；`sensitive` 設定的值會登錄為機密。

## 與舊版設定頁的差異

- 版面（卡片、列、欄寬）與舊版逐一比對過一致；頁面在 `Stack` 內的順序改為與導覽列相同（顯示不受影響）。
- 每個欄位的說明文字現在都會附上套用時機（例如「下次執行任務時套用」、`species_cache.*`「需重啟」）。
- 數值欄位載入時一律以字串顯示（舊版部分欄位直接放數字）。
- 數值欄位留空時的行為統一依 `blank`：原本會報錯但說明文字寫「空白用預設值」的欄位，現在確實使用預設值。
