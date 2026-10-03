# 設定何時生效（盤點與改善設計）

> 後續追蹤：生效時機改善 → #117；`replace_rules_path` 讀錯位置的 bug → #118。均不在 PR #106 範圍。

> 依程式碼靜態盤點（2026-10-01，PR #106 當時）。**沒有逐項在真實執行中驗證**；
> 標「未確認」的是我沒追到底的地方。設定檔：`config.json`（三層合併：預設值 < `config.example.json` < `config.json`）。

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
| `translator.cache_directory`（顯示） | 狀態列快取資料夾名稱會更新（**但實際快取內容不會跟著換，見下方風險**） |

### B 下次任務才生效（不用重啟）
| 設定 | 備註 |
|---|---|
| `lm_translator.keys` | 每次取 key 都重讀設定檔，**進行中的任務也會看到新增的 key**（例如額度用盡後補一把）。未實測 |
| `lm_translator.models.*.enabled`、`temperature`、`patchouli_system_prompt`、`lang_system_prompt` | 每次呼叫 `translate_batch_smart` 重讀（約每個檔案一次，未確認頻率） |
| `initial_batch_size_*`、`min_batch_size`、`batch_shrink_factor`、`batch_write_interval` | 任務 / 批次開始時讀 |
| `token_budget_*`、`max_output_tokens`、`output_token_factor`、`budget_*` | 每批重讀；學到的預算是行程內狀態 |
| `key_failure_cooldown_sec` | 每次判斷冷卻時讀 |
| `rate_limit.*`、`rpm_cooldown_sec` | 每次請求前讀 |
| `translator.parallel_execution_workers` | 每次建立執行緒池時讀 |
| `translator.output_dir_name`、`ftb_translator.output_dir_name` | 每次任務讀 |
| `lang_merger.*` | 每次合併讀 |
| `extractor.output_folder_names.*`、`jar_extractor.lang_codes` | 提取頁在按下動作時才讀 |
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
| `lm_translator.lm_translate_folder_name` | `lm_view.py` 模組層級常數 `LM_translate_folder_name` |
| `species_cache.cache_directory / cache_filename / wikipedia_language / wikipedia_rate_limit_delay` | `species_cache.py` 模組層級；學名資料庫已初始化後改了不會重載 |
| 視窗大小 | `AppShell.build` 時設定（本來就不需要即時） |

### E 不生效（疑似 bug / 死設定）
| 設定 | 說明 |
|---|---|
| `translator.replace_rules_path` | `lang_merger.py` 兩處（L74、L397）讀的是**頂層** `replace_rules_path`，但設定放在 `translator.replace_rules_path`，所以合併時永遠用預設 `replace_rules.json`。`ftb_translator.py` 與 `variant_comparator.py` 讀對了。→ 改了路徑只有部分流程會用到 |
| `logging.log_dir` | 設定頁有欄位，但錯誤記錄寫死 `Path("logs")`（`exceptions.py`），`log_dir` 未被實際使用（未確認其他位置） |
| `translator.custom_translator_folder`、`translator.enable_cache_saving` | 只找到零星使用，未確認行為 |

## 風險最高的一項：`translator.cache_directory`

- 路徑本身每次都重讀（`_get_cache_root()`），**但記憶體裡已載入的快取內容與搜尋索引不會重載**
  （`initialized` 旗標維持 True）。改路徑後：新寫入會去新資料夾，記憶體仍是舊資料，快取管理頁看到的也是舊資料。
- 這是唯一可能造成資料混在一起的情況，值得處理。

## 改善設計：哪些真的需要「設定後自動套用」

原則：**只為「使用者會預期立刻生效」或「不生效會造成錯誤」的項目做；其餘維持「下次任務生效」並明講。**

| 優先 | 項目 | 作法 | 理由 |
|---|---|---|---|
| 高 | `translator.cache_directory` 變更 | 設定存檔且該值改變時，呼叫 `reload_translation_cache()`、重設搜尋索引，並重新整理快取管理頁與工作台 | 避免新舊資料混用 |
| 高 | `translator.replace_rules_path`（E） | 修正 `lang_merger.py` 兩處讀取位置，補測試 | 設定被忽略；預設值情況下行為不變 |
| 高 | 進行中任務時存檔 | 訂閱存檔事件，若有任務在跑，顯示提示：「已儲存；進行中的任務不受影響，下次啟動才套用（API 金鑰除外）」 | 使用者預期即時生效，需要說清楚 |
| 中 | `lm_translate_folder_name`（D） | 改成使用時才讀（函式，不要模組常數） | 改了得重啟，沒有理由 |
| 中 | `output_bundler.output_zip_name` 提示文字（C） | 打包頁訂閱存檔事件，更新提示 | 提示與實際行為不一致 |
| 中 | `species_cache.*`（D） | 改成使用時才讀，或存檔後呼叫重新初始化；若不改，設定頁標示「需重啟」 | 低頻，至少要標示 |
| 低 | `logging.log_level` | 存檔時立即套用，不等下次任務 | 偵錯時想馬上看到 DEBUG |
| 低 | 日誌行數 | 訂閱存檔事件重建 `LogView` 設定 | 影響小 |
| 低 | `logging.log_dir`（E） | 實際使用或移除欄位 | 死設定 |

### 不建議做的

- **進行中任務套用新的批次大小 / 預算 / 溫度 / 提示詞**：行為會在任務中途改變，結果不可重現，不要做；維持「下次任務生效」。
- 視窗大小即時套用：沒必要。

### 建議的實作形狀（小改動，不新增大架構）

1. 設定頁每個欄位標示生效時機：`即時` / `下次任務` / `需重啟`（由一份中央表 `app/config_apply.py` 提供，設定頁與文件共用）。
2. `config_store` 增加「哪些路徑變了」：`subscribe(callback(changed_paths))`，各頁只處理自己關心的路徑，而不是每次全部重讀。
3. 針對高優先項目各補一個測試（快取目錄變更觸發重載、規則路徑讀對、有任務時的提示）。

## PR-5 實際契約

- 中央 metadata 位於 `app/config_apply.py`，目前對快取根目錄、模型設定、token 上限與 species cache 定義生效時機。
- 既有 `config_store.subscribe(callback)` 維持零參數相容性；需要精準刷新時使用 `subscribe_paths(callback(changed_paths))`，不要求舊 callback 改簽名。
- `translator.cache_directory` 儲存後不會讓現行任務中途換根目錄。下一次啟動或明確呼叫 `reload_translation_cache()` 才採用持久化的新路徑；pending writer 仍綁定目前 active root。
- `lm_translator` 的 per-model `max_output_tokens` 是可選欄位：缺少或 `null` 使用全域值，`0` 表示不送欄位；下一批次讀取，舊版只含 `enabled` 的設定可直接載入。
