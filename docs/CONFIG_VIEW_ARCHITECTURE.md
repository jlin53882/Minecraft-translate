# Config View 架構

## 定位

ConfigView（`app/views/config_view.py`）是全域設定頁：以**左側導覽 + 右側內容**的兩欄佈局，管理 `config.json` 的設定區段。設定頁可編輯 logging / translator / ftb_translator / lm_translator / lang_merger / extractor / output_bundler / species_cache；`ui`（`ui.theme_mode`）、`jar_extractor.lang_codes` 等沒有指定 `page` 的設定不在此頁編輯。頁面頂部為 `build_header`（標題「設定」，副標「所有設定會寫入 config.json；變更後請按「儲存」，部分項目需重新啟動才會套用」），底部為 `build_footer` 固定橫幅（左側提示「提示：修改後請務必點擊儲存」，右側「儲存所有設定」按鈕）。預設進入「一般設定」頁，每個設定欄位下方有說明文字（含套用時機與用途）。

## 檔案結構

```
translation_tool/utils/config_schema.py  ← 設定的唯一來源：Setting（路徑、型別、預設值、套用時機、是否敏感、驗證器…）
app/views/config_view.py                 ← 主視圖（導覽、內容切換、金鑰列／模型列兩個專用元件；導覽項 `NAV_ITEMS` 由 `NAV_PAGES` 衍生）
app/views/config/
  ├─ settings_schema.py                  ← 設定頁特有的「導覽頁（NAV_PAGES）」與「版面（LAYOUT）」；未列在版面的設定自動接在所屬卡片後面
  ├─ settings_form.py                    ← 由 schema 產生控制項與頁面（make_control / build_controls / build_pages）
  ├─ config_actions.py                   ← 泛化的載入／儲存（load_config_into_view / save_config_from_view）
  └─ config_form.py                      ← 共用 UI 工具（build_card / build_header / build_footer / build_key_row / build_key_field）
```

新增一個一般設定只需在 `config_schema.SETTINGS` 加一個 `Setting`（見 `docs/CONFIG_SETTINGS_SCHEMA.md`）；
`DEFAULT_CONFIG`、`config.example.json`、套用時機表、機密清單與設定頁都由它產生。

## 導覽結構（NAV_PAGES）

導覽頁由 `settings_schema.NAV_PAGES` 定義（`config_view.NAV_ITEMS` 由它衍生，左側導覽寬 210、標題「設定分類」）；每一頁的卡片依 `Setting.page` / `Setting.card` 與 `LAYOUT` 自動組成，內容區以 Stack 疊放各頁、只顯示目前選取者：

| id | label | 內容 |
|----|-------|------|
| `general` | 一般設定 | 依序為「日誌設定 (Logging)」、「翻譯與處理設定 (Translator)」、「成品打包器 (Output Bundler)」三張卡，欄位單欄堆疊：logging（log_level / log_dir / log_format）、translator（`output_dir_name`、`replace_rules_path`、`cache_directory`、`parallel_execution_workers`、`enable_cache_saving`、`custom_translator_folder`）、`ftb_translator.output_dir_name`、`output_bundler.output_zip_name` |
| `api_models` | API & 模型設定 | 「API 金鑰設定」卡（`_keys_panel`：標題「API 金鑰 (API Keys)」旁「+」新增；每列為遮罩輸入框＋顯示/隱藏眼睛＋刪除鈕）與「模型設定」卡（`_models_panel`：標題「模型清單 (Models List)」＋「新增模型名稱」輸入框＋「+」；每列為序號、勾選啟用、模型名、`max_output_tokens`「模型上限」欄（空白表示用全域值）、上移／下移／刪除） |
| `translation_behavior` | 翻譯行為設定 | 「基本設定」卡（同一列：temperature、rate_limit.timeout、sleep_seconds_between_batches、`lm_translate_folder_name`）與「過濾條件與目錄」卡（skip_terms、translatable_keywords、patchouli 目錄名稱三個多行欄並排；`short_text_skip_len` 在其下方）。內容：temperature、rate_limit.timeout、sleep_seconds_between_batches、`lm_translate_folder_name`、skip_terms、translatable_keywords、patchouli 目錄名稱、short_text_skip_len |
| `merger` | 語言合併器設定 | 單張「語言合併器設定 (Lang Merger)」卡，由上而下：pending 資料夾命名／整理資料夾名稱／key 最小出現次數／隔離資料夾名稱（兩欄兩列）、「語系過濾設定」（zh 英文含量閾值）、「Patchouli 進階設定」（翻譯來源優先級勾選、en_us 跳過門檻）、「檔案合併(階段 2)」（合併 XX_extracted → assets/） |
| `prompts` | 提示詞管理 | 「提示詞 (System Prompts)」卡：Patchouli 與 Lang 兩個 system prompt 多行欄左右並排 |
| `species_lookup` | 學名查詢管理 | 「學名查詢設定 (Species Cache)」卡，四欄單欄堆疊：species_cache 設定（cache_directory、cache_filename、wikipedia 語言與速率） |
| `batch_limits` | 批次與限制 | 「批次大小與限制」卡（多列並排欄位，含「啟用 Token 預算切批」勾選）：initial_batch_size_* 各格式、min_batch_size、batch_shrink_factor、rpm_cooldown_sec、max_output_tokens、token 預算、金鑰失敗冷卻、batch_write_interval |
| `extractor` | Jar 提取設定 | 「JAR 輸出資料夾命名」卡：extractor.output_folder_names（extract / preview 各三種）、skip_zh_cn_extract；target_language 僅作舊設定相容，不宣稱會生效（版面中以 `Note` 說明） |

## 呼叫鏈

```
[建構] ConfigView.__init__ → _init_controls()
  → settings_form.build_controls(controls_map)       依 schema 為每個一般設定建立控制項
  → settings_form.build_pages(controls_map, ...)     依 NAV_PAGES + resolved_layout() 組成各頁卡片

[載入] ConfigView.load_config → load_config_into_view(view, config)   [config_actions.py]
      ├─ 依 schema 逐項把 config 值填入 view.controls_map[path]（數值一律以字串顯示）
      ├─ list 欄位（dir_names / skip_terms / translatable_keywords）以 \n 合併填入多行輸入
      ├─ models：清空 models_column → add_model_row() 逐個重建
      └─ keys：清空 key_fields / keys_column → _build_key_field + _build_key_row 重建

[儲存] save_config_clicked()
  → save_config_from_view(view, load_config_json_fn, save_config_json_fn, validate_api_keys_from_ui_fn, registry)
      ├─ load_config_json() → 三層合併結果作為基底
      ├─ 依 schema 從 controls_map 收集新值（blank / 下限 / 驗證器 / label 模板由 Setting 欄位決定）
      ├─ validate_api_keys_from_ui(api_keys)（lm_config_rules.py）
      │    （欄位格式錯誤 / 金鑰驗證失敗 → 顯示錯誤 snack 並回傳 False，不寫檔）
      ├─ save_config_json(new_config)   [config_service.py]
      │    ├─ normalization：`lang_merger.process_zh_cn_files` 為 false 時強制關閉相依的 skip 子選項
      │    ├─ 在 `config_store.write_lock()` 內呼叫 config_manager.save_config
      │    └─ 成功後 `config_store.notify_saved(...)` 通知訂閱者
      ├─ view.load_config() → 重新載入刷新 UI
      ├─ registry 中已建立的 ExtractorView → refresh_config_defaults()
      └─ 顯示「設定已成功儲存」snack
套用時機（app/config_apply.py，見 CONFIG_APPLY_TIMING.md）僅用於欄位說明文字（`apply_timing_note`），儲存流程本身不依它重載快取。
```

## 主要方法（config_view.py）

| 方法 | 職責 |
|------|------|
| `_build_nav_column` / `_on_nav_click` / `_rebuild_nav` | 左側導覽（`_build_nav_item` 單項容器） |
| `_build_content_area` / `_show_content` | 依 nav_id 切換內容卡片 |
| `_keys_panel` / `_models_panel` | 兩個無法由欄位資料描述的專用元件（以 `Custom("keys")`／`Custom("models")` 在版面登記） |
| `add_model_row` / `move_model_row` / `remove_model_by_checkbox` / `on_add_model_clicked` | models 動態列（上下移動 + 勾選啟用） |
| `add_key_row` / `remove_key_row` | API keys 動態列 |
| `load_config` | 委派 `load_config_into_view` |
| `save_config_clicked` | 委派 `save_config_from_view` |
| `set_registry` / `page` | 外部注入 view registry / page 屬性 |

## 關鍵設計

- **schema 單一來源**：控制項、版面、載入、儲存、預設值、套用時機、機密標記都由 `Setting` 衍生；`controls_map` 以 `"section.key"` 為索引，但不再需要在多處手動同步（測試 `test_config_settings_coverage.py` 檢查 schema、預設值、版面與真實控制項完全一致）。
- **動態列模式**：models 與 keys 都是「按鈕新增 + checkbox/關閉鈕刪除」的可變列；models 支援排序（`move_model_row` 後 `_refresh_model_order_labels`）。
- **三層 config 合併**：儲存基底來自 `load_config_json()`（config.json > config.example.json > DEFAULT_CONFIG），儲存後使用者的「預設值」固化進 config.json。
- **API keys 驗證**：儲存前由 `translation_tool/core/lm_config_rules.py:validate_api_keys_from_ui` 驗證格式。

## 三層合併細節（config_manager.py:load_config）

合併順序：`deep_merge(deep_merge(DEFAULT_CONFIG, example), user_config)` — user 覆蓋 example 覆蓋 DEFAULT_CONFIG：

- **Layer 3** `DEFAULT_CONFIG`：由 `config_schema.build_default_config()` 建出的保底（所有欄位都有定義值）
- **Layer 2** `config.example.json`：repo 原始碼一部分（新版本補欄位用），不存在或解析失敗回傳 `{}`
- **Layer 1** `config.json`：使用者實際值；檔案不存在時跳過，JSON 解析失敗時直接回傳 base（用預設）

**例外規則**：`lm_translator.models` 刻意**不做 deep merge**（視為使用者資料，完全替換），避免預設模型列表與使用者設定混在一起誤啟用。

`deep_merge` 本身：key 已存在且兩側皆 dict → 遞迴合併；否則 override 值直接取代（list/str 整組覆蓋）。

## 載入驗證（ATK-C-2，啟動即爆炸）

`load_config` 最後對合併結果做型別驗證，失敗拋 `ConfigValidationError`：

- `lm_translator.keys` 必須是 list（不接受 str）
- `lm_translator.initial_batch_size_*` 必須是 int
- `lm_translator.parallel_execution_workers` 必須 int > 0
- `lm_translator.temperature` 必須 0.0~2.0 數字
- `lm_translator.models` 必須是 dict，每個模型為 object，`max_output_tokens` 為非負整數或 null
- token 預算相關欄位（`token_budget_enabled`、`max_output_token_budget`、`max_input_token_budget`、`max_output_tokens`、`output_token_factor`、`budget_min_scale`、`budget_recover_after`、`budget_recover_factor`）型別與範圍；`key_failure_cooldown_sec` 必須為 >= 0 的數字
- `translator.parallel_execution_workers` 必須 int > 0
- 偵測 `iniital_*`（拼錯）舊鍵 → 僅 warn deprecation（不再被引擎讀取）

## 安全寫入與提交點

`save_config` 使用與目標檔同目錄的唯一暫存檔：先完成 JSON 序列化、
`flush`、檔案 `fsync` 與暫存檔讀回解析，再以 `os.replace` 原子發布，
最後重新讀取目標檔驗證。提交前任何失敗都保留原本的 `config.json`，
並清理本次暫存檔；Windows 目標檔被占用時 `os.replace` 失敗也不會截斷舊檔。

原子替換保證讀者不會看到半份已發布 JSON，但不等同「斷電後最後一次寫入必定存在」。
支援目錄 `fsync` 的平台會在替換後同步父目錄；Windows 仍以檔案 flush/fsync、
原子 replace 與明確失敗回報為契約。替換後的讀回驗證若失敗會回傳 `False`，
但不盲目回滾，避免覆蓋提交點後另一個合法 writer 的更新。

`get_models_config` 只回傳 `{model: {"enabled": bool}}`，外部亂寫 list/str 被忽略。

## 維護注意

1. 新增一般設定：在 `config_schema.SETTINGS` 加一個 `Setting`，再執行 `python tools/gen_config_example.py`；不需要改 `config_view.py`、`config_actions.py` 或 `settings_form.py`。若欄位沒有明確 runtime caller，必須列入相容/棄用清單，不得只新增看似可調整的輸入框。
2. list 欄位在 UI 是「每行一個元素」的多行 TextField，載入用 `\n` join、儲存用 splitlines 過濾空行。
3. `save_config_from_view` 的 registry 參數會在儲存後通知已建立的 ExtractorView 執行 `refresh_config_defaults()`，重新同步 config-backed UI defaults；尚未建立的頁面則於建立時讀取最新設定。

## PR-A 設定稽核結論

- `extractor.skip_zh_cn_extract`：設定頁提供預設值；ExtractorView 建立時載入，提取/預覽仍保留每次操作的手動覆寫，最後由 regex/generator 實際套用。
- `translator.custom_translator_folder`：設定頁提供 round-trip；FTB translator 每次任務讀取並傳給 `load_custom_translations`。
- `logging.log_format`：設定頁提供 round-trip；既有 `update_logger_config` formatter 契約保留，下一次流水線啟動套用。
- `extractor.target_language`、`translator.cjk_ratio_threshold`：保留舊設定以避免升級遺失，但歷史追查沒有正式 caller/可證明規則；列為 `DEPRECATED_CONFIG_KEYS`，不在 UI 製造無效控制項。
