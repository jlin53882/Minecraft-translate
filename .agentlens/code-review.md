# Minecraft_translator_flet — code-review

> 分析範圍：`main.py`、`app/`、`translation_tool/`
> 不包含：Flet UI 版面/元件優化
> 分析日期：2026-03-10

---

## 總結論

這個專案目前**不是不能維護**，但已經很明顯進入「功能繼續加下去會越來越難救」的階段。

真正的核心問題有 4 個：

1. **神檔化**
   - `app/services.py`
   - `translation_tool/core/lang_merger.py`
   - `translation_tool/core/lm_translator_main.py`
   - `translation_tool/utils/cache_manager.py`

2. **重複邏輯過多**
   - FTB / KubeJS / MD pipeline 平行複製
   - 多個 plugin 內有相同 helper
   - app service 層有大量一樣的 task wrapper

3. **模組邊界不乾淨**
   - `main.py` 責任開始外溢
   - `config_manager.py` 有 import side effect
   - `services.py` 同時碰 task lifecycle + logging + domain service

4. **舊碼/殘留碼開始累積**
   - old helper
   - 壞引用候選
   - 未使用欄位
   - 註解停用區塊 / 過時入口
   - 根目錄維護腳本與測試腳本的角色未被正式收斂

---

# 0. 重構前置條件（缺這步很危險）

## 先跑 tests 建 baseline

目前 `tests/` 底下有 9 個測試模組，加上 `conftest.py`。前版報告沒有把這件事列成前置條件，這是不完整的。

### 建議
在任何結構重構前，先做：
1. 跑完整 test suite
2. 記錄目前 pass/fail baseline
3. 若本來就有 fail，先標記為既有問題

否則後面重構完成時，你根本無法判斷：
- 是原本就壞
- 還是這次拆壞

---

# 1. 可刪除 / 可清理的冗餘程式碼

> 先列「高機率可刪/可確認後刪」的，不把你已說明的暫停功能誤判成死碼。

## A. 明顯舊碼 / 未使用候選

### 1. `translation_tool/core/lm_translator_main.py:117`
- `safe_json_loads_old(text: str)`
- 已有新版 `safe_json_loads()`
- 目前未看到專案內實際引用
- **建議**：全域確認後刪除

### 2. `translation_tool/utils/text_processor.py:199`
- `safe_convert_text_old(text: str)`
- 已有新版 `safe_convert_text()`
- 目前未看到實際引用
- **建議**：確認後刪除

### 3. `translation_tool/utils/cache_manager.py:307`
- `get_cache_size_old()`
- 名稱本身已標示 old
- 未看到其他引用
- **建議**：確認後刪除

### 4. `translation_tool/core/lm_translator.py:113`
- `build_minimal_dict(items)`
- 目前未看到實際引用
- **建議**：確認後刪除

### 5. `translation_tool/core/lm_translator.py:124`
- `group_by_file(items)`
- 目前未看到實際引用
- **建議**：確認後刪除

---

## B. 壞引用 / 殘留 wrapper 候選

### 6. `app/services.py:916-936`
- `preview_jar_extraction_service(mods_dir, mode)`
- 內部引用：
  - `app/services.py:933` `from translation_tool.core.jar_processor import preview_extraction`
  - `app/services.py:936` `result = preview_extraction(mods_dir, mode)`
- 但實際存在的是：
  - `translation_tool/core/jar_processor.py:313` `preview_extraction_generator(...)`

#### 判斷
這個 service 看起來是：
- 舊介面殘留
- 或未完成重構留下的壞引用
- 而且 `extractor_view` 現在實際走的是 generator 版本，不是這個 wrapper

#### 建議
- 若沒人用：直接刪
- 若想保留：重寫成正確包裝 `preview_extraction_generator`

---

## C. 明顯可刪雜訊

### 7. `app/task_session.py:19`
- `_last_log_flush = 0.0`
- 目前未使用
- **建議**：刪除

### 8. `main.py:96`
- `# ??????`
- 無實際資訊價值
- **建議**：直接刪除

---

## D. 不列為死碼，但建議之後整理的部分

### 9. `main.py:70, 71, 76, 77`
這四個狀態不一樣，不能一起判定：

- `main.py:70`：學名查詢工具，暫停功能，**保留**
- `main.py:71`：你已說明**預計刪除**
- `main.py:76`：對應功能先放著，因為 **core 還沒寫好**
- `main.py:77`：目前 **傾向保留但方案未定**，未來可能保留也可能刪除

所以這裡更精確的分類是：
- 70：保留中的 deferred feature
- 71：planned removal
- 76：blocked by incomplete core
- 77：under evaluation

建議後續不要只靠註解停用，至少補上 disable reason；若之後頁面更多，再改成 feature flag / registry 狀態欄位。

### 10. 根目錄腳本要補角色說明，不要當不存在
- `CLEAN_AND_REBUILD.py`
- `REBUILD_INDEX_NOW.py`
- `test_a1_a3_features.py`
- `test_search_fix.py`

前版總報告幾乎沒提到這幾個根目錄腳本，這不夠完整。

建議把它們分成兩類：
- 維護腳本（maintenance scripts）
- 臨時/根目錄測試腳本（root-level test scripts）

之後要嘛補文件說明，要嘛確認無用後清掉，避免它們長期游離在正式結構之外。

### 11. `main.py:219-222`
- 註解掉的 web mode 啟動區塊
- 若短期不會做 web mode，可移到文件或 example 檔

---

# 2. 重複邏輯

## A. `app/services.py` 的 task wrapper 幾乎整段重複

重複區段：
- `app/services.py:156` `run_lm_translation_service(...)`
- `app/services.py:226` `run_lang_extraction_service(...)`
- `app/services.py:263` `run_book_extraction_service(...)`
- `app/services.py:299` `run_ftb_translation_service(...)`
- `app/services.py:342` `run_kubejs_tooltip_service(...)`
- `app/services.py:383` `run_md_translation_service(...)`
- `app/services.py:425` `run_merge_zip_batch_service(...)`

### 重複內容
這幾支基本都在做：
1. `update_logger_config()`
2. `session.start()`
3. `UI_LOG_HANDLER.set_session(session)`
4. 執行 generator / pipeline
5. 轉發 log/progress/error
6. `session.finish()` / `session.set_error()`
7. `UI_LOG_HANDLER.set_session(None)`

### 建議
抽成共用 runner：
- `run_generator_task(...)`
- `run_callable_task(...)`

這一刀下去，`app/services.py` 會直接瘦非常多。

---

## B. FTB / KubeJS plugin 內有整批重複 helper

### FTB 插件
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:51` `read_json_dict`
- `:69` `write_json_dict`
- `:85` `collect_json_files`
- `:100` `should_rename_to_zh_tw`
- `:128` `is_lang_code_segment`
- `:146` `replace_lang_folder_with_zh_tw`
- `:170` `compute_output_path`
- `:262` `count_translatable_keys`
- `:283` `DryRunStats`

### KubeJS 插件
- `translation_tool/plugins/kubejs/kubejs_tooltip_lmtranslator.py:55` `read_json_dict`
- `:63` `write_json_dict`
- `:69` `collect_json_files`
- `:73` `count_translatable_keys`
- `:80` `should_rename_to_zh_tw`
- `:90` `is_lang_code_segment`
- `:100` `replace_lang_folder_with_zh_tw`
- `:111` `compute_output_path`
- `:158` `DryRunStats`

### 問題
這不是只有概念類似，是**幾乎同一批 helper 直接複製**。

### 建議
抽共用模組：
- `plugins/shared/json_file_helpers.py`
- `plugins/shared/lang_path_rules.py`
- `plugins/shared/dry_run_models.py`

---

## C. `is_already_zh` / 格式碼清洗邏輯重複

### FTB
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:305` `is_already_zh`

### MD
- `translation_tool/plugins/md/md_lmtranslator.py:60` `_strip_fmt`
- `translation_tool/plugins/md/md_lmtranslator.py:63` `is_already_zh`

### 建議
抽成共用語言判定模組：
- `translation_tool/utils/lang_detection.py`

---

## D. FTB / KubeJS / MD pipeline 高度平行

### FTB
- `translation_tool/core/ftb_translator.py:513` `run_ftb_pipeline`

### KubeJS
- `translation_tool/core/kubejs_translator.py:366` `step1_extract_and_clean`
- `:434` `step2_translate_lm`
- `:518` `step3_inject`
- `:562` `run_kubejs_pipeline`

### MD
- `translation_tool/core/md_translation_assembly.py:155` `step1_extract`
- `:306` `step2_translate`
- `:329` `step3_inject`
- `:423` `run_md_pipeline`

### 共同模式
- Step1: extract / clean
- Step2: LM translate
- Step3: inject / write back
- 都有 dry_run / progress / write_new_cache 概念

### 問題
這三條線不是繼承同一個 skeleton，而是各自長成平行版本。

### 建議
不一定要硬抽 superclass，但至少把以下抽共用：
- `ProgressProxy`
- `dry_run skip policy`
- `step summary logging`
- pipeline result schema

---

# 3. 過度拆分的函數 / 建議合併或重切的地方

這專案真正的問題，不是全面「拆太細」，而是：

> **應該按責任拆模組的沒拆，卻在某些檔案內塞了一堆 helper，讓閱讀時要瘋狂跳轉。**

所以這裡我分兩類說。

---

## A. 不是真的拆太細，而是「巨檔內部塞太多 helper」

### 1. `translation_tool/core/lm_translator_main.py`
關鍵函數：
- `:31` `call_gemini_requests`
- `:146` `safe_json_loads`
- `:186` `extract_translatables`
- `:265` `set_by_path`
- `:423` `translate_batch_smart`

### 問題
這不是某個函數該合併，而是這整支檔案需要**重切模組邊界**。

### 建議
拆成：
- `lm_api_client.py`
- `lm_response_parser.py`
- `translatable_extractor.py`
- `translation_path_writer.py`
- `batch_runner.py`

也就是：
- 不是把函數合併
- 是把不該在同一檔的責任分家

---

### 2. `translation_tool/core/lang_merger.py`
關鍵函數：
- `:301` `_process_single_mod`
- `:575` `_patch_localized_content_json`
- `:696` `_process_content_or_copy_file`
- `:1300` `merge_zhcn_to_zhtw_from_zip`

### 問題
這支也不是小函數太多，而是：
- zip io
- `.lang` parser
- JSON patch
- quarantine
- merge pipeline

全部塞在一起。

### 建議
拆成：
- `zip_io.py`
- `lang_codec.py`
- `content_merge.py`
- `merge_pipeline.py`

---

### 3. `translation_tool/utils/cache_manager.py`
### 問題
不是「函數太小」，而是 storage / shard / search / bootstrap 全混在一起。

### 建議
拆成：
- `cache_store.py`
- `cache_shards.py`
- `cache_search.py`（沿用既有模組，擴充 search / index 職責）
- `cache_bootstrap.py`

補充：不要再另外發明一個新的 `cache_index.py` 與現有 `cache_search.py` 平行並存，否則會把搜尋層又做成重複模組。

---

## B. 真的可以合併/抽象化的地方

### 4. `app/services.py` 的任務包裝函數
這裡不是拆太細，而是**同一種流程複製太多遍**。

### 建議
把這幾支改成：
- 薄 wrapper
- 共用 task runner

換句話說，這裡是「該合併共用流程」，不是保留每支各自寫一套。

---

### 5. FTB / KubeJS 內的 path helper
像這些其實就不該在各檔各自留一份：
- `should_rename_to_zh_tw`
- `is_lang_code_segment`
- `replace_lang_folder_with_zh_tw`
- `compute_output_path`

### 建議
抽一份 shared helper，讓 plugin 本身只保留各自真正不同的業務邏輯。

---

# 4. main.py / app / translation_tool 的結構判斷

## main.py
### 問題
- 入口責任開始外溢
- 同時管：視圖註冊、視窗尺寸、背景索引任務、logging 啟動

### 建議
- registry 外移
- startup task 外移
- logging 初始化責任單一化

---

## app/
### 問題
- `app/services.py` 神檔化
- service contract 不一致
- task lifecycle 重複包裝
- `os.getcwd()` 導致路徑耦合
- `preview_jar_extraction_service` 疑似壞引用
- `cache_manager/` 目錄命名債已於 PR6 修正。

### 建議
先拆 `services.py`：
- config_service
- task_runner
- translation_service
- extraction_service
- cache_service
- checker_service
- lookup_service

補註：上述命名問題已在 PR6 完成 rename；以下段落保留作為當時 code review 脈絡參考：
1. 先標記為 naming debt
2. 等重構時統一改名
3. 一次性修所有 import / 測試

---

## translation_tool/
### 問題
- 核心模組過胖
- pipeline 平行重複
- old helper 殘留
- import side effect
- `lm_translator_shared.py` 是重要中介層，前版分析不足

### 建議
先拆：
1. `lm_translator_main.py`
2. `lang_merger.py`
3. `cache_manager.py`

但要特別注意：拆 `lm_translator_main.py` 時，不能忽略 `lm_translator_shared.py`。這兩者要一起設計，否則會出現責任切一半、另一半仍糾纏的問題。

---

# 5. 建議重構順序（**這份是唯一的全域 canonical 順序**）

> 前三份子報告裡出現的「第一優先」是**各自模組內的局部優先順序**；真正跨整個專案的執行順序，以這裡為準。

## 第零步：先建立 baseline
1. 跑完整 tests
2. 記錄 pass/fail baseline
3. 保存目前報告與 INDEX

### 理由
沒有 baseline，後面任何重構都很難判定是本來壞還是拆壞。

---

## 第一優先：先清低風險殘留與壞引用
1. 確認/刪除 old helper
   - `safe_json_loads_old`
   - `safe_convert_text_old`
   - `get_cache_size_old`
   - `build_minimal_dict`
   - `group_by_file`
2. 刪除 `TaskSession._last_log_flush`
3. 清掉 `main.py` 雜訊註解
4. 修正或移除 `preview_jar_extraction_service()`
5. 補根目錄腳本角色說明

### 理由
這些改動風險低，能先把專案表面噪音與明顯錯誤收乾淨。

---

## 第二優先：清理初始化與命名/依賴邊界
1. 移除 `config_manager.py` 底部 import side effect
2. 讓 `main.py` 成為單一啟動入口
3. 把 `main.py` 的 registry / startup task 外移
4. 把 `app/services.py` 的 `os.getcwd()` 改成穩定專案根路徑
5. `cache_manager` 命名債已在 PR6 收斂完成

### 理由
這一層能先把邊界整理乾淨，又不需要碰 UI 版面。

---

## 第三優先：瘦身 app 層
1. 先按 domain 分拆 `app/services.py`
2. 抽共用 task runner
3. 統一 service contract

### 理由
這一層是 app 可讀性的最大瓶頸，而且風險仍低於直接動核心翻譯引擎。

---

## 第四優先：處理 translation_tool 核心巨檔
1. 拆 `lm_translator_main.py`（**連同 `lm_translator_shared.py` 一起設計**）
2. 拆 `lang_merger.py`
3. 抽 plugin shared helpers
4. 重新分層 `cache_manager.py`，並**沿用既有 `cache_search.py`**，不要無視現有搜尋模組

### 理由
這一層風險最高，但收益也最大，應放在前面幾層清乾淨之後再做。

---

# 6. 最後結論

這個專案現在最大的痛點，不是某個函數寫錯，而是：

> **模組邊界已經開始鬆掉，導致神檔、重複邏輯、舊碼殘留一起發生。**

但好消息是，它還沒爛到不能收：
- UI 層你暫時不動，這是對的
- 真正該整理的是 service / pipeline / shared helper
- 只要按順序拆，這專案是能慢慢救回乾淨結構的

我最直接的建議是：

### 先做這 3 件事
1. **先清 old helper + 壞引用**
2. **再拆 `app/services.py`**
3. **最後才拆 `lm_translator_main.py` / `lang_merger.py`**

這樣風險最低，收益最大，也最符合你現在不想動 UI 的限制。
