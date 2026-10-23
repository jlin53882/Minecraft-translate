# 設定何時生效（盤點與套用契約）

> 設定檔：`config.json`（三層合併：預設值 < `config.example.json` < `config.json`）。
> 生效時機的中央表與現行契約見下方「套用時機契約」與「實際契約」。

> 依程式碼靜態盤點。**沒有逐項在真實執行中驗證**；標「未確認」的是尚未追到底的地方。

## 讀取機制

- `load_config()` / `load_config_shared()` 以**檔案修改時間**判斷快取是否過期，檔案一變下次讀取就是新值。
  所以「每次用到才讀」的設定，存檔後**不用重啟**。
- 存檔後 `config_store.notify_saved()` 會通知訂閱者；目前只有外殼（頂列、狀態列）訂閱。
- 因此「是否即時生效」完全取決於**使用端什麼時候讀**：

| 類型 | 讀取時機 | 存檔後 |
|---|---|---|
| A 即時 | 外殼訂閱存檔事件 | 立即更新畫面 |
| B 下次任務 | 每次啟動任務 / 每次呼叫時才讀 | 下一次任務（或下一批 / 下一個檔案）生效；**進行中的任務多半不受影響** |
| C 開啟頁面時 | 頁面建立時讀一次，頁面之後保留 | 不會更新，直到重啟 |
| D 啟動時 | 模組載入 / 程式啟動時讀一次 | 必須重啟 |
| E 不生效 | 讀錯位置或沒有人使用 | 改了沒用（疑似 bug / 死設定） |

## 逐項分類

### A 即時
| 設定 | 說明 |
|---|---|
| `ui.theme_mode` | 切換即套用並寫入 |
| `lm_translator.keys`、`lm_translator.models`（顯示） | 頂列 API 狀態、狀態列模型名稱會更新 |
| `translator.cache_directory`（顯示） | 狀態列快取資料夾名稱會更新（**但實際快取內容要經重載才會換，見下方「風險最高的一項」**） |

### B 下次任務才生效（不用重啟）
| 設定 | 備註 |
|---|---|
| `lm_translator.keys` | 每次取 key 都重讀設定檔，**進行中的任務也會看到新增的 key**（例如額度用盡後補一把）。未實測 |
| `lm_translator.models.*.enabled`、`temperature`、`patchouli_system_prompt`、`lang_system_prompt` | 每次呼叫 `translate_batch_smart` 重讀（約每個檔案一次，未確認頻率） |
| `initial_batch_size_*`、`min_batch_size`、`batch_shrink_factor`、`batch_write_interval` | 任務 / 批次開始時讀 |
| `token_budget_*`、`max_output_tokens`、`output_token_factor`、`budget_*` | 每批重讀；學到的預算是行程內狀態 |
| `key_failure_cooldown_sec` | 每次判斷冷卻時讀 |
| `rate_limit.*`、`rpm_cooldown_sec` | 每次請求前讀 |
| `translator.parallel_execution_workers` | 每次建立執行緒池時讀；解析規則與各用途的預設／上限見 `translation_tool/utils/worker_count.py`（正整數才有效，無效值退回該用途預設） |
| `translator.output_dir_name`、`ftb_translator.output_dir_name` | 每次任務讀 |
| `lang_merger.*` | 每次合併讀 |
| `extractor.output_folder_names.*`、`jar_extractor.lang_codes` | 提取頁在按下動作時才讀 |
| `extractor.skip_zh_cn_extract` | 提取頁建立時讀為預設值；每次操作仍可用頁面開關覆寫 |
| `translator.custom_translator_folder` | 每次 FTB 任務建立時讀取，下一次任務生效 |
| `output_bundler.output_zip_name`（實際打包） | 打包時讀；**但輸入框提示文字是頁面建立時讀的（見 C）** |
| `logging.log_level`、`logging.log_format` | 每次啟動流水線時 `update_logger_config` 重新套用 |
| `lm_translator.translator.*`、`lm_translator.patchouli.dir_names` | 每次任務讀 |

### C 開啟頁面時讀一次（頁面保留，所以之後不更新）
| 設定 | 位置 |
|---|---|
| `output_bundler.output_zip_name`（提示文字） | `bundler_view._load_output_zip_from_config` |
| 日誌顯示行數（`ui.log` tail 設定） | `lm_view` 建立 `LogView` 時 |
| 一鍵流水線預設資料夾名稱 | `PipelineConfig.__init__`（每次開 dialog 才建立，**實際上等於每次生效**，未確認） |

### D 啟動時讀一次（需重啟）
| 設定 | 位置 |
|---|---|
| `species_cache.cache_directory / cache_filename / wikipedia_language / wikipedia_rate_limit_delay` | `species_cache.py` 模組層級；學名資料庫已初始化後改了不會重載 |
| 視窗大小 | `AppShell.build` 時設定（不需要即時） |

### E 不生效（歷史相容欄位）
| 設定 | 說明 |
|---|---|
| `extractor.target_language` | 歷史相容欄位；追查不到正式 caller，目前不生效，不應在 UI 宣稱可調整 |
| `translator.cjk_ratio_threshold` | 歷史相容欄位；追查不到 caller 或明確判定規則，目前不生效，不應臆造語意 |

## 風險最高的一項：`translator.cache_directory`

- 路徑本身每次都重讀（`_get_cache_root()`），但記憶體裡已載入的快取內容與搜尋索引只在重載時更新。
- 若只改路徑而不重載，新寫入會去新資料夾，記憶體仍是舊資料，快取管理頁看到的也是舊資料；這是唯一可能造成資料混在一起的情況。
- 存檔後的重載時機見下方「快取資料夾變更」。

## 自動套用的判準

原則：**只為「使用者會預期立刻生效」或「不生效會造成錯誤」的項目做；其餘維持「下次任務生效」並明講。**

| 項目 | 現行作法 |
|---|---|
| `translator.cache_directory` | 存檔且值改變時由 `CacheRootReloader` 處理（見「快取資料夾變更」） |
| `translator.replace_rules_path` | 合併流程每次執行時讀 `translator` 區段，下次執行生效 |
| 進行中任務時存檔 | 依 `app/config_apply.py` 的 timing 顯示提示（進行中任務不受影響者：下次任務才套用；`lm_translator.keys` 例外，見上方 B） |
| `lm_translate_folder_name`（D） | 使用時才讀（`get_lm_translate_folder_name()`），不是模組常數 |
| `output_bundler.output_zip_name` 提示文字（C） | 打包頁訂閱 `config_store.subscribe_paths`，存檔即時更新提示 |
| `species_cache.*`（D） | timing 為 `restart`，設定頁與存檔提示標示「需重新啟動」 |
| `logging.log_level` | 存檔時立即套用（timing `immediate`） |
| 日誌行數 | 機器翻譯頁在每次開始任務時讀最新設定 |
| `logging.log_dir` | 錯誤記錄每次寫入時讀；應用日誌需重啟才換資料夾 |

### 不建議做的

- **進行中任務套用新的批次大小 / 預算 / 溫度 / 提示詞**：行為會在任務中途改變，結果不可重現，不要做；維持「下次任務生效」。
- 視窗大小即時套用：沒必要。

### 實作形狀

1. 設定頁的生效時機說明與存檔提示由中央表 `app/config_apply.py` 提供（時機值見下方「套用時機契約」）。
2. `config_store.subscribe_paths(callback(changed_paths))` 讓各頁只處理自己關心的路徑；既有 `subscribe(callback)` 維持零參數相容。
3. 高優先項目的行為以測試鎖住（快取目錄變更觸發重載、規則路徑讀對、有任務時的提示）。

## 實際契約

- 中央 metadata 位於 `app/config_apply.py`，目前對快取根目錄、模型設定、token 上限與 species cache 定義生效時機。
- 既有 `config_store.subscribe(callback)` 維持零參數相容性；需要精準刷新時使用 `subscribe_paths(callback(changed_paths))`，不要求舊 callback 改簽名。
- `translator.cache_directory` 儲存後不會讓現行任務中途換根目錄。下一次啟動或明確呼叫 `reload_translation_cache()` 才採用持久化的新路徑；pending writer 仍綁定目前 active root。
- `lm_translator` 的 per-model `max_output_tokens` 是可選欄位：缺少或 `null` 使用全域值，`0` 表示不送欄位；下一批次讀取，只含 `enabled` 的舊設定可直接載入。

## 套用時機契約

中央表在 `app/config_apply.py`：設定頁的說明文字、存檔提示與自動重載都以它為準。

| timing | 意義 | 存檔後行為 |
|---|---|---|
| `immediate` | 立即套用 | 不提醒（例如 `logging.log_level`／`log_format`、深淺色） |
| `next_request` | 進行中的任務在下次 API 請求讀到 | 有任務時提醒「會在下一批次／下次請求套用」 |
| `next_batch` | 進行中的任務在下一批次讀到 | 同上 |
| `next_task` | 進行中的任務不受影響（沒登記的設定預設為此） | 有任務時提醒「進行中的任務不受影響，下次任務才套用」 |
| `when_idle` | 沒任務時立即套用，有任務則等任務結束 | `translator.cache_directory`：自動重載快取與搜尋索引（見下） |
| `restart` | 需重啟 | 不論有沒有任務都提醒「需重新啟動」（`species_cache.*`、`logging.log_dir`） |

### 快取資料夾變更（`translator.cache_directory`）

存檔且該值改變時（`app/shell/config_effects.py::CacheRootReloader`）：

- **沒有任務進行中**：在背景執行緒呼叫 `cache_reload_service()`（重載快取並重建搜尋索引），完成後刷新工作台與快取頁並提示。
- **有任務進行中**：不動；任務全部結束後才重載。現行任務不中途換快取根目錄，避免任務寫入新舊兩個資料夾。
- 重載期間又有新變更：重載結束後再跑一次；重載失敗只記錄並提示，仍使用舊快取。

### 其他生效行為

- 機器翻譯輸出資料夾預設名稱、打包 `output_zip_name` 都是使用時才讀；打包頁的輸入框提示訂閱存檔事件即時更新。
- `logging.log_level`／`log_format` 存檔後立即套用；機器翻譯頁日誌行數在每次開始任務時讀最新設定。
- 錯誤記錄（`errors_*.log`）每次寫入時讀 `logging.log_dir`；應用日誌需重啟才換資料夾。
- `translator.enable_cache_saving`：`cache_manager.save_translation_cache` 每次寫入前讀取，下次寫入生效。
- `translator.replace_rules_path`：合併流程每次執行時讀取（`translator` 區段），下次執行生效。
- 不做：進行中任務中途改批次大小、預算、溫度或提示詞等引擎行為（結果不可重現）；這些設定依上表在「下一批次／下次請求／下次任務」讀取。
