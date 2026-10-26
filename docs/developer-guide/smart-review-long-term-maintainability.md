# Smart Review 長期維護清單

## 文件目的

本文件整理 Minecraft-translate Smart Review 在既有功能完成後，仍需要逐步改善的長期維護項目。

這是一份維護 backlog，不代表目前所有項目都是合併阻擋問題，也不要求一次全部重寫。建議先建立契約測試，再分階段替換內部實作，避免破壞目前已完成的 Smart Review 安全防護與 JSON 歷史資料相容性。

本文件本身只記錄維護 backlog；實作與驗證範圍仍以程式碼、測試與下方連結的權威流程文件為準。

## 已落地的可重複規則

目前功能另外補上幾項與上述 backlog 直接相關、可供後續維護沿用的規則：

- 智慧校對換筆／換頁和歷史重新整理必須在同一個 UI 事件內完成；條目校對的換頁與篩選則在 worker 讀取（畫面先顯示載入中，並以請求序號丟棄過期結果），避免大型資料庫在 UI 執行緒查詢；預載、完整範圍選取、批次寫入等耗時工作同樣放到背景。詳細邊界見 [MODDB_VIEW_ARCHITECTURE.md](views/MODDB_VIEW_ARCHITECTURE.md) 與 [moddb.md](../page_flows/moddb.md)。
- 歷史批次列表先讀取 manifest 前綴，候選分片只在真正進入該頁時解析；projection reconciliation 以資料庫歷史最大 id 作為失效條件，避免每次換頁重掃全部 JSON。
- 「解除人工審核」只改目前生效譯文的審核狀態，不改譯文與來源；資料層必須以譯文、來源、審核者和狀態作 CAS，並寫入可還原的 `review_unmark` history。UI 只有在 `reviewed` 或 `legacy_unknown` 時顯示按鈕。
- 進階設定與產生的 `.codebase-memory/` 索引不應混入產品提交；索引留在本機並由 `.gitignore` 排除，文件只記錄可重現的行為與驗證證據。
- 新批次規劃必須區分 `FAST_ESTIMATE` 與 `EXACT_EXECUTION`：前者只提供不建立完整 context 的唯讀估算，後者在送出前以同一個 `SmartReviewPlanInput`、同版本最近兩份歷史與完整 CAS/context 重新計畫；預估數量不得標成實際送出數量。
- 執行頁的預估背景工作透過 `SmartReviewPlanningController` 接收不可變 request 與 callback；`smart_review_context.RequestTicket` 以 domain、序號、UI generation 與資料庫身分丟棄晚到結果。面板只組輸入與投影 UI，不讓控制器讀取 Flet 私有控制項。
- `SmartReviewHistoryStore` 目前已保留 facade，並由 `HistoryLock`、codec、manifest repository、recovery、query 等既有模組承擔分層責任；本輪沒有再拆出另一層 facade 或改寫 JSON／SQLite 協議，避免無證據的架構擴張。

## 目前範圍與證據

- 前兩輪功能迭代共涵蓋共用批次執行、詳細 retry telemetry、官方術語、Smart Review JSON history、人工套用防護、JSON 分片、config 批次設定、候選狀態、手動編輯重套、篩選、去重、分頁與 history locking。
- Codebase graph 可協助定位呼叫關係，但索引不是每次 commit 都即時重建；最終判斷以目前工作區的 Git 與原始碼為準。

## 優先級定義

- **P1：下一個大型功能前處理**：不一定是目前合併阻擋，但不處理會持續放大維護風險。
- **P2：下一輪維護迭代**：對測試性、效能或擴充性有明顯幫助。
- **P3：長期 backlog**：目前沒有明確故障證據，先保留觀測與設計方向。

## P1：下一個大型功能前處理

### [ ] P1-01 建立統一的 Smart Review 狀態機

**目前問題**

Smart Review 狀態與狀態轉移分散在資料層、history store、服務層與 UI。`pending`、`ready`、`applied`、`rejected`、`stale`、`invalid`、`cancelled`、`request_failed` 等字串在多個模組重複判斷。

**主要檔案**

- `translation_tool/translation_db/smart_review.py`
- `translation_tool/translation_db/smart_review_history.py`
- `translation_tool/translation_db/models.py`
- `app/views/moddb/smart_review_reviewer.py`
- `app/views/moddb/smart_review_marks.py`

**長期修改方向**

- 建立 `SmartReviewStatus` enum 或不可變狀態模型。
- 建立集中式狀態轉移表，明確定義允許的狀態轉換。
- 將 UI 標籤、顏色與可用操作由狀態模型衍生。
- apply、reject、manual edit、cancel 統一經過 transition validator。
- 對非法狀態轉移提供明確錯誤與 audit reason。

**驗收條件**

- 資料層與 UI 使用同一份狀態定義。
- 所有 apply/reject/manual edit/cancel 流程都有狀態機測試。
- 不改變現有 CAS、stale 與人工審核防護。

### [ ] P1-02 正式化 JSON 與 SQLite 的一致性協議

**目前問題**

Smart Review 候選與批次資料保存於 JSON，正式翻譯與 audit history 保存於 SQLite。現行流程會先寫 SQLite，再更新 JSON；JSON 更新失敗時由 reconciliation 補救。這個設計具備安全性，但目前比較像隱含的兩階段投影協議。

**主要檔案**

- `translation_tool/translation_db/smart_review.py`
- `translation_tool/translation_db/smart_review_history.py`
- `translation_tool/translation_db/repository.py`

**長期修改方向**

- 明確定義 SQLite audit 是「寫入事實」，JSON 是「候選狀態投影」。
- 增加 projection generation 或 revision。
- 將同步狀態明確分為 `pending_projection`、`projected`、`projection_failed`、`reconciled`。
- 建立可重複執行且具冪等性的 reconciliation。
- apply、reject、manual edit 統一使用同一個交易上下文。

**注意事項**

不建議把完整候選資料重新搬回 SQLite；目前 JSON 分片正是為了避免資料庫檔案持續膨脹。

### [ ] P1-03 釐清 `CommonBatchExecutor` 的責任邊界

**目前問題**

`CommonBatchExecutor` 已統一批次生命週期、計時、進度與事件，但 retry、配額等待、context-too-large 縮批、validation repair 等行為仍由不同策略或 provider 流程處理。未來維護者可能誤以為所有 retry 已由共用核心統一管理。

**主要檔案**

- `translation_tool/core/common_batch_executor.py`
- `translation_tool/core/lm_translator_main.py`
- `app/views/moddb/smart_review_job.py`

**長期修改方向**

擇一執行：

1. 保留現有行為，將 executor 明確定位為 `BatchLifecycleExecutor`，並補充文件。
2. 抽出明確的 `RetryPolicy`、`QuotaWaitPolicy`、`CancellationPolicy` 與 `ProviderRequestRunner` 介面。

三種業務仍應保留各自的 retry 條件，不要把機翻、本地修復與 Smart Review 的業務邏輯硬合併。

### [ ] P1-04 拆分 `smart_review.py`

**目前問題**

目前檔案同時處理候選選取、篩選、context、官方術語、prompt、provider、retry、strict JSON 驗證、validation repair、history、apply/reject、CAS、stale、去重、分頁與 reconciliation。

**長期拆分方向**

```text
smart_review_selection.py
smart_review_prompt.py
smart_review_context.py
smart_review_provider.py
smart_review_validation.py
smart_review_history_adapter.py
smart_review_apply.py
smart_review_projection.py
smart_review_filters.py
```

保留目前的 `run_smart_review()` 與 apply API 作為 facade，避免一次修改所有呼叫端。

### [ ] P1-05 拆分 `SmartReviewHistoryStore`

**目前問題**

`SmartReviewHistoryStore` 同時負責 JSON 序列化、chunk manifest、atomic replace、程序內鎖、跨程序鎖、recovery、cache、分頁、latest marks、reconciliation 與 corruption handling。

**長期拆分方向**

```text
HistoryLock
HistoryCodec
ChunkManifestRepository
HistoryRecovery
HistoryQuery
HistoryProjection
SmartReviewHistoryStore facade
```

保留現有 facade，先把內部責任拆出，降低檔案鎖、JSON 格式、查詢與復原互相影響的風險。

## P2：下一輪維護迭代

### [ ] P2-01 收斂全域可變狀態

**需要盤點的狀態**

- `SmartReviewHistoryStore._locks`
- `_started_cache`
- `_latest_marks`
- `_corrupt_runs`
- `_recovery_checked`
- `_interprocess_state`
- `smart_review.py` 的 history head cache
- `ReplaceRulesStore` 全域實例
- config cache
- official locale LRU cache
- text matching 的 `lru_cache(maxsize=200_000)`

**長期修改方向**

- 由外層建立 Store/Service 後注入。
- cache 綁定 repository、database session 或明確的 runtime scope。
- 提供 `clear()`、`invalidate()`、`close()`。
- 對以 path 為 key 的 cache 設定清理策略。
- 避免長時間執行的程序因切換大量資料庫而持續累積 cache。

`text_matching` 的 200,000 筆 LRU 目前不建議直接縮小，因為可能降低準確率。後續應先增加 memory usage、hit rate 與 eviction 觀測，再依實際資料決定。

### [ ] P2-02 建立單一 `SmartReviewSettings`

**目前問題**

Smart Review 設定同時出現在 config manager、Smart Review service、`config.example.json` 與 UI settings schema，雖然目前已有預設批次 20、最大批次與 chunk size，但仍存在 alias 與重複定義。

**主要檔案**

- `translation_tool/utils/config_manager.py`
- `translation_tool/translation_db/smart_review.py`
- `app/services_impl/moddb_smart_review_service.py`
- `app/views/config/settings_schema.py`
- `config.example.json`

**長期修改方向**

- 建立不可變的 `SmartReviewSettings`。
- runtime、service、UI 都使用同一個設定物件。
- UI 欄位盡量由設定 metadata 衍生。
- 文件化設定在啟動、任務開始或 Store 建立時的載入時機。

### [ ] P2-03 統一事件模型與 log formatter

**目前問題**

目前有 `attempt`、`smart_review_attempt`、`provider_attempt`、`network_attempt` 等重疊欄位，也有 `api_ms`、`api_seconds`、`response_parse_ms`、`parse_seconds` 等命名差異。核心與 UI job 也各自存在事件格式化邏輯。

**長期修改方向**

- 建立 versioned `BatchEvent` schema。
- 統一 canonical field name 與單位。
- core 只產生結構化事件。
- app.log、UI log、summary 使用不同 sink。
- 僅保留一個中文 formatter。
- 以 contract test 保護 event schema 演進。
- 對舊欄位保留 compatibility adapter。

### [ ] P2-04 拆分 Smart Review UI

**主要檔案**

- `app/views/moddb/smart_review_reviewer.py`
- `app/views/moddb/smart_review_run_panel.py`
- `app/views/moddb/smart_review_job.py`

**目前問題**

UI 類別同時負責控制項、狀態、資料載入、分頁、草稿、套用/reject、格式檢查、evidence、log 與 progress。

**長期拆分方向**

```text
SmartReviewReviewerController
SmartReviewReviewerState
SmartReviewCandidateLoader
SmartReviewEvidencePresenter
SmartReviewActionService
SmartReviewReviewerView
```

另應讓一次導覽操作共用 read session，並考慮預載入前後 candidate，避免下一筆、套用後刷新與統計更新重複開啟資料庫及 JSON。

### [ ] P2-05 優化 latest status 與篩選查詢

目前 Smart Review 狀態需要從最新 JSON history 產生 candidate ID，再透過 SQLite `json_each` 篩選。這能避免把完整候選放進 SQLite，但在 10,000 筆以上可能造成重複解析與大量 ID tuple 建立。

**長期修改方向**

- 建立輕量 status sidecar 或 index。
- 只保存 candidate id、latest run id、status 與時間欄位。
- UI 篩選直接讀取 status index。
- 對 10,000 與 100,000 筆建立 page/filter/apply/reconcile benchmark。

### [ ] P2-06 正式化 JSON schema、版本與修復工具

目前已有 schema version、chunk format、manifest、atomic replace、舊格式相容與 corruption tolerance，但仍缺少完整的維護工具鏈。

**長期修改方向**

- schema migration registry。
- 每個版本的升級函式。
- JSON schema validation。
- manifest/chunk consistency checker。
- CLI 或維護頁面 repair command。
- backup/restore 文件。
- retention、archive、cleanup policy。

分片只能降低單次讀寫成本，不能限制歷史資料的總磁碟大小，因此仍需要長期保存策略。

### [ ] P2-07 補足跨程序鎖與復原測試

需要測試：

- 兩個獨立 Python process 同時寫入。
- process 在 manifest 更新前中斷。
- process 在 chunk replace 後中斷。
- 一個 process apply、另一個 process reconcile。
- lock timeout 與 stale lock。
- 多程序同時執行 recovery。

同一個 process 內的 thread 測試不能完全取代跨程序測試。

## P3：長期 backlog

### [ ] P3-01 抽象官方術語資料模型

目前 `official_locale.py` 已讓原本機翻與 Smart Review 共用術語匹配，方向正確。後續可以再分離：

- immutable terminology index
- matching resolver
- prompt adapter
- evidence adapter

並固定以下資料欄位：

- `source_text`
- `target_text`
- `authority`
- `usage`
- `match_type`
- `confidence`
- `source_key`
- `evidence_id`

補充 Netherite、連字號、底線、大小寫、複合詞與 `block/blocker` 的 golden tests。

### [ ] P3-02 降低對檔案行數 baseline 的依賴

目前 size baseline 可以防止意外膨脹，但不能代表架構健康。後續可加入：

- cyclomatic complexity
- public API 數量
- class method 數量
- module dependency depth
- 單檔案責任數量
- type-check coverage
- 高風險函式變更門檻

行數 baseline 保留作為警告即可，不應作為唯一的架構品質指標。

### [ ] P3-03 補強測試層次

建議增加：

- Smart Review 狀態機 property/state-machine tests。
- JSON/SQLite 一致性 crash-point tests。
- event schema contract tests。
- config/UI/runtime default consistency tests。
- 大批次 page/filter/apply/reconcile benchmark。
- UI 快速切換與非同步結果亂序測試。
- legacy JSON、chunked JSON、舊 SQLite history 混合讀取測試。

### [ ] P3-04 文件與生成流程整理

文件應補充：

- JSON history storage format。
- chunk manifest 與 recovery 流程。
- JSON/SQLite 一致性與 reconciliation。
- config 載入時機。
- retention、backup、repair 操作。
- Smart Review 狀態轉移。
- retry/event schema。

exception inventory 等 generated docs 應由固定命令產生，並在 CI 驗證結果是否與原始碼一致，避免人工維護內容過期。

## 應保留的設計

以下內容目前方向正確，不應在重構時破壞：

- `limit=0` 代表不限筆數。
- `batch_size > 0` 驗證。
- Smart Review 只建立候選，不自動覆蓋正式翻譯。
- CAS、revision、stale snapshot 與人工審核防護。
- JSON 分片、atomic replace、manifest 最後發佈。
- 舊 JSON/SQLite history 相容讀取。
- 詳細 retry、network attempt、Retry-After 與錯誤分類 log。
- 官方術語由原本機翻與 Smart Review 共用。
- 不將完整官方 JSON 或完整資料庫 glossary 傳給 AI。
- history partial index 與 reconciliation。
- 預設 Smart Review 批次數量 20 並由 config 管理。

## 建議執行順序

### 階段一：建立契約，不改變功能

- [ ] 狀態 enum 與 transition table。
- [ ] `SmartReviewSettings`。
- [ ] canonical batch event schema。
- [ ] JSON/SQLite consistency contract tests。

### 階段二：拆分儲存責任

- [ ] `HistoryLock`。
- [ ] `HistoryCodec`。
- [ ] `ChunkManifestRepository`。
- [ ] `HistoryProjection`。
- [ ] 跨程序測試。

### 階段三：整理共用執行核心

- [ ] 釐清 `CommonBatchExecutor` 是否只負責 lifecycle。
- [ ] 抽出 retry/quota policy interface。
- [ ] 統一 log sink 與 timing 欄位。

### 階段四：整理 UI 與查詢

- [ ] 拆分 UI controller/state/view。
- [ ] 一次操作共用 read session。
- [ ] status sidecar/index。
- [ ] 10,000 筆以上效能測試。

### 階段五：文件與維護工具

- [ ] storage schema/migration 文件。
- [ ] recovery/repair 文件。
- [ ] retention/backup 文件。
- [ ] generated docs 與 CI 驗證。

## 最優先的五項

如果下一輪只能先做少量工作，建議依序處理：

1. 統一 Smart Review 狀態機。
2. 正式化 JSON/SQLite 投影一致性。
3. 釐清 `CommonBatchExecutor` 的 retry 責任。
4. 拆分 `SmartReviewHistoryStore`。
5. 隔離長生命週期的全域可變狀態。

這五項完成後，再處理 UI 拆分與大批次效能，風險會比直接繼續堆功能低很多。

## 結論

既有迭代已經解決多項實際功能與資料安全問題，但也讓 Smart Review 成為高責任密度的核心區域。長期維護的重點不是重新設計所有功能，而是把目前已驗證的行為固定成契約，再逐步拆分狀態、儲存、執行核心、UI 與查詢責任。

重構期間應持續保留：人工套用、CAS/stale 防護、JSON history 相容、官方術語共用、retry 可觀測性，以及 SQLite audit 與 JSON 候選的資料邊界。
