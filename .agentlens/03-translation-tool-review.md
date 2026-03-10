# translation_tool 資料夾程式分析報告

> 專案：`Minecraft_translator_flet`
> 範圍：`translation_tool/`
> 重點：核心流程、重複邏輯、冗餘舊碼、模組邊界
> 不包含：Flet UI 結構優化
> 分析日期：2026-03-10

---

## 一句話結論

`translation_tool/` 現在最大的問題不是單點 bug，而是**核心流程已經長成一組互相纏住的大模組**：

- `lang_merger.py` 太胖
- `lm_translator_main.py` 太胖
- `cache_manager.py` 責任太雜
- FTB / KubeJS / MD 三條 pipeline 有大量平行重複邏輯
- 還混有一些明顯的舊碼候選與過時 helper

這一層最需要的不是「再拆更多小函數」，而是：

> **先把大模組按責任切開，再把重複 helper 抽共用。**

---

# 一、目前的結構問題總覽

## 0. 重構前先補 baseline：tests 先全跑一次

目前 `tests/` 底下有 9 個測試模組，加上 1 個 `conftest.py`。這次報告前版沒有把它列進重構前置條件，這是缺口。

### 建議
在任何結構重構前，先做一次 baseline：
- 先跑全部 tests
- 記錄通過/失敗數
- 若本來就有 fail，先記錄是既有問題

否則你後面拆完：
- 不知道是原本就壞
- 還是你這次拆壞

這一步不花俏，但非常重要。

## 1. 核心檔案過胖，而且責任混裝
依掃描結果，幾個主要檔案行數大致如下：

- `translation_tool/core/lang_merger.py`：1471 行
- `translation_tool/core/lm_translator_main.py`：1042 行
- `translation_tool/core/lm_translator.py`：750 行
- `translation_tool/core/ftb_translator.py`：754 行
- `translation_tool/core/kubejs_translator.py`：836 行
- `translation_tool/utils/cache_manager.py`：660 行
- `translation_tool/core/lm_translator_shared.py`：509 行

其中 `lm_translator_shared.py` 雖然不像 `lm_translator_main.py` 那麼顯眼，但它其實是整個 LM 翻譯迴圈的重要中介層：負責 cache split、translation loop、preview、recording。若後續要拆 `lm_translator_main.py`，**一定要把 `lm_translator_shared.py` 一起納入設計**，否則會出現分析斷層。

這些檔案不是單純「長」，而是**同時包太多層級的責任**：
- I/O
- 路徑處理
- 規則判定
- cache
- batch 翻譯
- retry
- 格式修復
- pipeline orchestration
- 結果落盤

這種結構會讓閱讀者非常累，因為每次要追某段邏輯，都會在同一檔裡跨好幾層抽象跳來跳去。

---

# 二、主要模組問題

## A. `lm_translator_main.py`：一支檔案塞了整個翻譯引擎的半數責任

關鍵位置：
- `translation_tool/core/lm_translator_main.py:31` `call_gemini_requests(...)`
- `translation_tool/core/lm_translator_main.py:117` `safe_json_loads_old(...)`
- `translation_tool/core/lm_translator_main.py:146` `safe_json_loads(...)`
- `translation_tool/core/lm_translator_main.py:186` `extract_translatables(...)`
- `translation_tool/core/lm_translator_main.py:265` `set_by_path(...)`
- `translation_tool/core/lm_translator_main.py:423` `translate_batch_smart(...)`

### 問題本質
這支檔案同時處理：
1. HTTP API 呼叫
2. JSON 輸出解析
3. 可翻譯內容抽取
4. 路徑回寫
5. batch profile 判斷
6. retry / key rotation / overload handling
7. 模型 prompt 選擇
8. 結果標準化

這代表它其實不是 `main`，而是：

> `api_client + response_parser + item_extractor + path_writer + retry_engine + batch_runner`

全部混在一起。

### 直接後果
- `translate_batch_smart()` 太重，閱讀成本很高
- 抽取規則與翻譯引擎強耦合
- 路徑回寫 (`set_by_path`) 被埋在翻譯引擎裡，不利重用
- API 層與 domain 層界線不清

### 建議拆分
建議拆成至少四塊：

#### 1. `lm_api_client.py`
- `call_gemini_requests`
- HTTP timeout / request building

#### 2. `lm_response_parser.py`
- `safe_json_loads`
- 模型輸出標準化
- incomplete JSON / fallback parsing

#### 3. `translatable_extractor.py`
- `find_patchouli_json`
- `find_lang_json`
- `is_lang_file`
- `extract_translatables`

#### 4. `translation_path_writer.py`
- `set_by_path`
- `map_lang_output_path`

#### 5. `batch_runner.py`
- `translate_batch_smart`
- retry policy
- overload / timeout / batch shrink policy

### 額外觀察
`translate_batch_smart()` 不是「函數拆太細」，是**根本沒切模組**。這裡不應該再往裡面塞更多小 helper，而是先把責任切出去。

---

## B. `lang_merger.py`：ZIP 合併、lang 修復、內容複製、quarantine 全都在同一支

關鍵位置：
- `translation_tool/core/lang_merger.py:301` `_process_single_mod(...)`
- `translation_tool/core/lang_merger.py:575` `_patch_localized_content_json(...)`
- `translation_tool/core/lang_merger.py:696` `_process_content_or_copy_file(...)`
- `translation_tool/core/lang_merger.py:1300` `merge_zhcn_to_zhtw_from_zip(...)`

### 問題本質
這支檔案裡同時有：
- zip 讀寫 helper
- `.lang` 解析/修復
- JSON 清洗
- 路徑判定
- content file 覆寫/複製
- quarantine 邏輯
- patchouli root 正規化
- 單 mod 處理
- 多 mod merge 主流程

這是一個典型的：

> **同時含格式層、I/O 層、流程層、修復層的超大模組**

### 直接後果
- 想改 `.lang` parser，得進入整個 ZIP merge 巨檔
- 想改 quarantine 或 copy policy，也得理解整個 merge 主流程
- 文件解析與流程協調糾纏在一起

### 建議拆分
建議至少拆成：

#### 1. `zip_io.py`
- `_read_text_from_zip`
- `_read_json_from_zip`
- `_write_bytes_atomic`
- `_write_text_atomic`
- `quarantine_copy_from_zip`

#### 2. `lang_codec.py`
- `try_repair_lang_line`
- `collapse_lang_lines`
- `parse_lang_text`
- `dump_lang_text`
- `is_mc_standard_lang_path`

#### 3. `content_merge.py`
- `_patch_localized_content_json`
- `_process_content_or_copy_file`
- `normalize_patchouli_book_root`

#### 4. `merge_pipeline.py`
- `_process_single_mod`
- `merge_zhcn_to_zhtw_from_zip`
- `export_filtered_pending`
- `remove_empty_dirs`

### 重要判斷
`lang_merger.py` 不是函數拆太細，而是**拆得不對地方**：
- helper 雖然不少
- 但全部還在同一檔
- 導致讀者感受到的是疲勞，不是模組化

---

## C. `lm_translator.py`：對外入口混了過多 orchestration + 殘留 helper

關鍵位置：
- `translation_tool/core/lm_translator.py:113` `build_minimal_dict(...)`
- `translation_tool/core/lm_translator.py:124` `group_by_file(...)`
- `translation_tool/core/lm_translator.py:135` `translate_directory_generator(...)`

### 問題
這支檔案表面上是「對外入口」，但實際上還混有：
- duration helper
- item grouping helper
- directory scan
- cache reload
- parallel extraction
- output writing
- export_lang 分支

### 特別值得注意的點
#### 1. `build_minimal_dict()` 看起來未被使用
- `translation_tool/core/lm_translator.py:113`

#### 2. `group_by_file()` 看起來未被使用
- `translation_tool/core/lm_translator.py:124`

這兩個函數目前只看到定義，沒有看到專案內其他引用。高機率是重構殘留。

#### 3. 與 `lm_translator_main.py` 耦合過重
它直接從 `lm_translator_main` import 很多東西：
- `extract_translatables`
- `find_patchouli_json`
- `find_lang_json`
- `is_lang_file`
- `map_lang_output_path`
- `set_by_path`
- `translate_batch_smart`

這代表 `lm_translator.py` 其實不是在調用一個清楚的 engine API，而是在直接抓對方內部工具來拼流程。

### 建議
- `lm_translator.py` 應該只保留「入口協調」
- 下層應變成明確模組 API，而不是散裝 import
- 未使用 helper 應先確認後移除

---

## D. `cache_manager.py`：快取儲存、shard 管理、search 入口都混在一起

關鍵位置：
- `translation_tool/utils/cache_manager.py:307` `get_cache_size_old()`
- `translation_tool/utils/cache_manager.py:568` `search_cache(...)`

### 問題本質
這支檔案同時做：
- cache 初始化
- 記憶體 cache 維護
- dirty/session tracking
- shard rotation
- atomic save
- active shard 管理
- search engine 對接
- rebuild index
- search query
- find similar

也就是：

> **storage layer + shard layer + index layer + query layer 全混在一起**

### 直接後果
- 搜尋功能要依賴儲存細節
- shard 改動會影響 query 層
- index rebuild 與 cache 落盤糾纏

### 建議拆分
先注意一點：**專案裡已經有 `translation_tool/utils/cache_search.py`**，所以不應忽略既有模組、直接另外發明一個全新 `cache_index.py` 取代它。比較合理的方向是：

#### 1. `cache_store.py`
- 記憶體 cache 結構
- add/get/update
- dirty/session 管理

#### 2. `cache_shards.py`
- `_get_active_shard_path`
- `_rotate_shard_if_needed`
- `_save_entries_to_active_shards`
- atomic write

#### 3. **沿用既有 `cache_search.py` 作為搜尋層核心**
- `cache_search.py`：保留/擴充 FTS 與 fuzzy search engine
- `cache_manager.py`：只保留 search 入口轉接，不再自己混 storage/shard/index 細節
- `rebuild_search_index` / `search_cache` / `find_similar_translations`：要嘛搬進既有 `cache_search.py`，要嘛做一層薄 wrapper，但不要無視既有模組

#### 4. `cache_bootstrap.py`
- initialize / reload
- load shards in parallel

### 明顯舊碼候選
#### `get_cache_size_old()`
- `translation_tool/utils/cache_manager.py:307`
- 目前只看到定義，沒有看到其他引用
- 可列為優先清理候選

---

## E. `config_manager.py`：有 import side effect，不乾淨

關鍵位置：
- `translation_tool/utils/config_manager.py:132` `load_config(...)`
- `translation_tool/utils/config_manager.py:201` `setup_logging(...)`
- `translation_tool/utils/config_manager.py:283` `config = load_config()`
- `translation_tool/utils/config_manager.py:284` `setup_logging(config)`

### 問題
這支檔案在 import 時就直接：
- 載入 config
- 初始化 logging

這會造成：
- import 有副作用
- 測試不乾淨
- CLI / 背景腳本難控制初始化時機
- 其他模組只想用 `load_config()` 也會順便觸發 logging setup

### 建議
把底部兩行移除，保留明確初始化入口：
- 由 `main.py` 或明確 bootstrap 腳本決定何時 setup logging

這點雖然前面 main/app 報告有提過，但這裡從 `translation_tool` 的角度看，仍然是核心結構問題。

---

## F. `text_processor.py`：有明顯舊碼與不乾淨依賴

關鍵位置：
- `translation_tool/utils/text_processor.py:10` `from .config_manager import config`
- `translation_tool/utils/text_processor.py:199` `safe_convert_text_old(...)`

### 問題
#### 1. 匯入全域 `config` 但幾乎沒有真正使用
這種寫法代表 `text_processor.py` 與 `config_manager` 有不必要的靜態耦合，還會放大 import side effect 問題。

#### 2. `safe_convert_text_old()` 高機率是舊碼殘留
- 目前只看到定義，沒有看到其他引用
- 可以列為清理候選

### 建議
- 移除未使用的全域 `config` import
- 若 `safe_convert_text_old()` 已完全被 `safe_convert_text()` 取代，直接刪

---

# 三、重複邏輯

## 1. FTB / KubeJS LM translator 插件大量重複

### FTB 位置
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:51` `read_json_dict`
- `:69` `write_json_dict`
- `:85` `collect_json_files`
- `:100` `should_rename_to_zh_tw`
- `:128` `is_lang_code_segment`
- `:146` `replace_lang_folder_with_zh_tw`
- `:170` `compute_output_path`
- `:262` `count_translatable_keys`
- `:283` `DryRunStats`
- `:305` `is_already_zh`

### KubeJS 位置
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
這不是單純「名字一樣」，而是**幾乎同一套工具函數複製到兩個插件檔案裡**。

### 建議
抽成共用模組，例如：
- `translation_tool/plugins/shared/json_file_helpers.py`
- `translation_tool/plugins/shared/lang_path_rules.py`
- `translation_tool/plugins/shared/lm_dry_run_models.py`

這會立即減少 duplicated maintenance cost。

---

## 2. `is_already_zh` / `_strip_fmt` 類邏輯重複

位置：
- `translation_tool/plugins/ftbquests/ftbquests_lmtranslator.py:305` `is_already_zh`
- `translation_tool/plugins/md/md_lmtranslator.py:60` `_strip_fmt`
- `translation_tool/plugins/md/md_lmtranslator.py:63` `is_already_zh`

### 問題
這類「已是中文 / 移除格式碼再判定」的邏輯屬於通用語言判定，不應該散落在插件內各寫一份。

### 建議
抽到：
- `translation_tool/utils/lang_detection.py`
或
- `translation_tool/core/text_classification.py`

---

## 3. FTB / KubeJS / MD pipeline 結構高度相似，但各寫一套

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

### 問題
這三條線雖然處理對象不同，但實際結構都很像：
- step1 抽取/清理
- step2 LM 翻譯
- step3 inject / 回寫
- dry_run / progress / write_new_cache

目前它們是平行實作，而不是共享 pipeline skeleton。

### 建議
不一定要硬抽成單一 superclass，但至少可以抽：
- `PipelineStepResult`
- `ProgressProxy`
- `dry_run skip policy`
- `common summary logging`

尤其 KubeJS 和 MD 都已經有 step1/2/3 的風格，這裡很適合抽共用 orchestration helper。

---

# 四、可刪除 / 冗餘舊碼候選

## 1. `safe_json_loads_old`
- 檔案：`translation_tool/core/lm_translator_main.py`
- 行號：`117`
- 目前只看到定義，未看到實際引用
- 可列為清理候選

## 2. `safe_convert_text_old`
- 檔案：`translation_tool/utils/text_processor.py`
- 行號：`199`
- 已有 `safe_convert_text()` 新版
- 高機率是舊版保留

## 3. `get_cache_size_old`
- 檔案：`translation_tool/utils/cache_manager.py`
- 行號：`307`
- 名稱本身就已經在告訴你它是舊 API
- 未看到實際引用

## 4. `build_minimal_dict`
- 檔案：`translation_tool/core/lm_translator.py`
- 行號：`113`
- 目前未看到實際引用

## 5. `group_by_file`
- 檔案：`translation_tool/core/lm_translator.py`
- 行號：`124`
- 目前未看到實際引用

### 判斷原則
這幾個我不建議立刻秒刪，但非常適合做一次：
1. 全域引用確認
2. 測試跑一遍
3. 沒人用就刪

---

# 五、過度拆分 vs 真正的問題

你前面有提到一個很重要的觀察：

> 函數拆分太細，讀起來反而疲累。

我對 `translation_tool/` 的判斷是：

## 這層的主問題其實不是「拆太細」
而是：

1. **大檔案沒按模組責任切開**
2. **小 helper 重複散落在多個檔案**
3. **有些地方同時又塞很多小 helper，讓人跳來跳去**

也就是：

> **不是純粹 over-splitting，而是 split boundary 設錯。**

### 典型例子
#### `lm_translator_main.py`
- 應該拆模組，但沒有
- 所以整體太胖

#### FTB / KubeJS plugin helpers
- 有些 helper 太小，卻各自複製一份
- 這種不是「細拆的錯」，而是「沒抽共用」

#### `lang_merger.py`
- helper 不算少，但全部塞同一支
- 導致讀者只覺得累，不覺得模組化

### 所以正確方向不是
- 再多拆 20 個小函數

### 正確方向是
1. 先把巨檔按責任切開
2. 再把真正通用的小 helper 拉共用
3. 最後才整理個別函數大小

---

# 六、建議重構順序

## 第一優先
### 拆 `lm_translator_main.py`
因為這支是 LM 翻譯系統的心臟，而且責任混最多。

## 第二優先
### 拆 `lang_merger.py`
這支現在是 ZIP/lang 合併的神檔，日後一定會越來越難維護。

## 第三優先
### 抽 FTB/KubeJS 共用 helper
立即降低重複碼。

## 第四優先
### 拆 `cache_manager.py`
把 storage / shard / search 分層。

## 第五優先
### 清理 old helpers / import side effects
- `safe_json_loads_old`
- `safe_convert_text_old`
- `get_cache_size_old`
- `build_minimal_dict`
- `group_by_file`
- `config_manager` 底部自動初始化

---

# 七、最後結論

`translation_tool/` 現在最需要的不是「微調幾個函數」，而是做一次**核心邊界整理**。

最值得先做的事是：

1. **把巨檔切成責任明確的模組**
2. **把 FTB/KubeJS/MD 共用規則抽出**
3. **刪掉舊 helper 與過時 API 殘留**
4. **移除 import side effect**

這樣你後面要做優化、除錯、加新格式支援，成本都會低很多。

---

## 本檔建議狀態

- **可直接討論**：是
- **優先處理檔案**：`lm_translator_main.py`、`lang_merger.py`
- **是否需要碰 UI**：不用
- **風險**：中（因為牽涉核心流程，但收益也最大）
