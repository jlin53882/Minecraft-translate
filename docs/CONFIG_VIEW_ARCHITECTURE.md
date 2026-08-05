# Config View 架構

## 定位

ConfigView（`app/views/config_view.py`）是全域設定頁：以**左側導覽 + 右側內容**的兩欄佈局，管理 `config.json` 的所有設定區段（logging / translator / lm_translator / lang_merger / extractor / output_bundler / species_cache / ftb_translator）。

## 檔案結構

```
app/views/config_view.py             ← 主視圖（UI 組裝 + NAV_ITEMS + 動態 rows）
app/views/config/
  ├─ config_actions.py               ← 載入/儲存邏輯（load_config_into_view / save_config_from_view）
  └─ config_form.py                  ← UI 建構工具（build_card / build_header / build_footer / build_key_row / build_key_field）
```

## 導覽結構（NAV_ITEMS）

| id | label | 對應內容區 |
|----|-------|-----------|
| `general` | 一般設定 | logging.log_level / log_dir、translator.output_dir_name、replace_rules_path、cache_directory、parallel_execution_workers 等 |
| `api_models` | API & 模型設定 | `_build_lm_keys_card`（keys 動態列）+ `_build_lm_models_card`（models 動態列，可上移/下移/刪除） |
| `translation_behavior` | 翻譯行為設定 | `_build_lm_basic_card` / `_build_lm_filter_card`（temperature、batch sizes、skip_terms、translatable_keywords） |
| `merger` | 語言合併器設定 | `_build_lang_merger_card`（pending 資料夾命名、門檻值、patchouli 開關） |
| `prompts` | 提示詞管理 | `_build_lm_prompts_card`（patchouli/lang system prompt） |
| `species_lookup` | 學名查詢管理 | species_cache 設定（cache_directory / wikipedia_language / rate_limit_delay） |
| `batch_limits` | 批次與限制 | `_build_lm_batch_card`（initial_batch_size_* 各格式、min_batch_size、batch_shrink_factor） |
| `extractor` | Jar 提取設定 | extractor.output_folder_names（lang/book/dual 的 extract/preview 資料夾名） |

## 呼叫鏈

```
[載入] ConfigView.__init__ → load_config()
  → load_config_into_view(view, config)   [config_actions.py]
      ├─ 逐項把 config 值填入 view.controls_map[key] 的 UI 控制項
      ├─ list 欄位（dir_names / skip_terms / translatable_keywords）以 \n 合併填入多行輸入
      ├─ models：清空 models_column → add_model_row() 逐個重建
      └─ keys：清空 key_fields / keys_column → _build_key_field + _build_key_row 重建

[儲存] save_config_clicked()
  → save_config_from_view(view, load_config_json_fn, save_config_json_fn, validate_api_keys_from_ui_fn, registry)
      ├─ load_config_json() → 三層合併結果作為基底
      ├─ 從 controls_map 收集新值寫回 dict（list 欄位 splitlines 轉回 list）
      ├─ validate_api_keys_from_ui(api_keys)（lm_config_rules.py）
      ├─ save_config_json(new_config) → 觸發 normalization
      ├─ view.load_config() → 重新載入刷新 UI
      └─ registry 中 extractor view 若有 refresh_output_dir_helper → 呼叫（更新 helper 文案）
```

## 主要方法（config_view.py）

| 方法 | 職責 |
|------|------|
| `_build_nav_column` / `_on_nav_click` / `_rebuild_nav` | 左側導覽（`_build_nav_item` 單項容器） |
| `_build_content_area` / `_show_content` | 依 nav_id 切換內容卡片 |
| `_build_lm_*_card` 系列 | 各設定區卡片 |
| `add_model_row` / `move_model_row` / `remove_model_by_checkbox` / `on_add_model_clicked` | models 動態列（上下移動 + 勾選啟用） |
| `add_key_row` / `remove_key_row` | API keys 動態列 |
| `load_config` | 委派 `load_config_into_view` |
| `save_config_clicked` | 委派 `save_config_from_view` |
| `set_registry` / `page` | 外部注入 view registry / page 屬性 |

## 關鍵設計

- **controls_map 約定**：所有可設定控制項以 `"section.key"` 字串註冊進 `controls_map`，載入/儲存都以此為索引 — 新增設定項目時兩邊都要同步加。
- **動態列模式**：models 與 keys 都是「按鈕新增 + checkbox/關閉鈕刪除」的可變列；models 支援排序（`move_model_row` 後 `_refresh_model_order_labels`）。
- **三層 config 合併**：儲存基底來自 `load_config_json()`（config.json > config.example.json > DEFAULT_CONFIG），儲存後使用者的「預設值」固化進 config.json。
- **API keys 驗證**：儲存前由 `translation_tool/core/lm_config_rules.py:validate_api_keys_from_ui` 驗證格式。

## 三層合併細節（config_manager.py:load_config）

合併順序：`deep_merge(deep_merge(DEFAULT_CONFIG, example), user_config)` — user 覆蓋 example 覆蓋 DEFAULT_CONFIG：

- **Layer 3** `DEFAULT_CONFIG`：程式碼內建的「唯一真相來源」保底（所有欄位都有定義值）
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
- `lm_translator.models` 必須是 dict
- `translator.parallel_execution_workers` 必須 int > 0
- 偵測 `iniital_*`（拼錯）舊鍵 → 僅 warn deprecation（不再被引擎讀取）

`save_config`：寫入後**重新讀檔驗證**（可 dump 即代表結構乾淨）；`get_models_config` 只回傳 `{model: {"enabled": bool}}`，外部亂寫 list/str 被忽略。

## 維護注意

1. 新增 config 欄位時，需同步更新：`_init_controls`（建立控制項）、`load_config_into_view`（載入）、`save_config_from_view`（儲存）三處。
2. list 欄位在 UI 是「每行一個元素」的多行 TextField，載入用 `\n` join、儲存用 splitlines 過濾空行。
3. `save_config_from_view` 的 registry 參數用來在儲存後通知 extractor view 重新整理 helper 文案（若 extractor 頁尚未 mount，其內部有 try/except 防護）。
