# TRANSLATION_VIEW_ARCHITECTURE.md

## 定位

TranslationView（頁面標題「任務翻譯工具」）是 Markdown 文件的**批次翻譯工作台**。步驟可自由勾選（抽取 → LM 翻譯 → 寫回）。FTB Quests 與 KubeJS 翻譯不在本頁，已由「KubeJS / FTB 流水線」頁負責。頁面外框與執行狀態卡使用 `pipeline_workbench.py` 的共用契約，與一鍵流水線維持相同的閱讀順序與操作位置。

流程圖見 [page_flows/translation.md](../../page_flows/translation.md)。

## 檔案結構

- `app/views/translation_view.py`：主視圖（`TranslationView`）。
- `app/views/translation/translation_actions.py`：`run_md`、`start_ui_timer`、`stop_ui_timer`、`resume_ui_timer`、`_sync_from_session`，以及執行緒安全包裝。
- `app/views/translation/translation_panels.py`：`build_path_row`、`build_action_row`、`build_md_tab`（內部 `_step_row`、`_cache_switch`、`_tab`）。
- `app/views/pipeline/pipeline_workbench.py`：共用頁首、左右工作台與執行狀態卡。
- `app/services_impl/pipelines/md_service.py`：`run_md_translation_service`（續跑標記與 `run_callable_task`）。

## 架構圖（文字版）

```
TranslationView（ft.Column）
  └─ build_pipeline_workbench（共用頁首與左右工作台）
       ├─ 左：Markdown 流程
       │    ├─ section_card「FTB Quests／KubeJS」：說明已移至流水線頁
       │    └─ build_md_tab()（_tab）
       │         ├─ section_card「路徑設定」：build_path_row（md_in_dir / md_out_dir）與 md_lang_mode
       │         ├─ section_card「翻譯步驟」：PipelineStepRow 垂直步驟列（背後為 ft.Checkbox）+「每次寫入新快取分片」kit.SwitchRow
       │         └─ build_action_row：開始翻譯 / Dry-run 模擬翻譯 / Reset
       └─ 右：
            ├─ 共用執行狀態卡：目前狀態 + 橫向 progress bar + status_chip + cancel_button（「取消」，預設停用）
            └─ section_card「執行日誌」（開始執行或收到第一筆日誌後顯示）：log_view（LogView tail）
```

`__init__` 拆成 `_init_translation_state_and_tabs`（狀態、右側控制項與 Markdown 面板）與 `_build_translation_status_and_right_panel`（右側面板與服務 seam）。

**Service seam**：`__init__` 將服務存到 view 屬性 `self.run_md_translation_service` 與 `self.TaskSession`，供動作層讀取。服務以 try/except 匯入，匯入失敗時為 `None`，執行時動作層會提示「尚未可用」。

## Markdown 步驟與欄位

| 步驟 Checkbox | 說明 |
|---------------|------|
| `md_step_extract` | 抽取待翻譯區塊 |
| `md_step_translate` | LM 批次翻譯 |
| `md_step_inject` | 寫回完成目錄 |

額外選項：`md_write_new_cache`（Switch）與 `md_lang_mode`（Dropdown：`non_cjk_only` / `cjk_only` / `all`，預設 `non_cjk_only`）。

輸入欄位 `md_in_dir` 標籤為「輸入資料夾（遞迴掃描 .md）」，輸出欄位 `md_out_dir` 為「輸出資料夾（可選）」，兩者右側各有資料夾選擇按鈕。輸出留空時由服務使用預設位置 `<input>/Output/md`。

## 動作層流程

```
點擊「開始翻譯」→ view._run_md(dry_run=False)
  └→ run_md(view, dry_run)                  [translation_actions.py]
       ├─ 已有任務執行中（_ui_timer_running）→ snack「已有翻譯任務執行中」
       ├─ 驗證 in_dir 非空、service/TaskSession 可用（否則 snack）
       ├─ _set_status(「模擬執行」/「執行中」)、progress=0、log_view.clear()
       ├─ view.session = _new_translation_session(view, ...)（tag_session(view.TaskSession(), ...)）
       ├─ reserve_page_operation(...)：應用程式關閉中則 snack 並返回
       ├─ _log_task_start()：add_start_log 登記輸入、輸出與步驟設定（start() 由服務層 run_callable_task 呼叫）
       ├─ operation.launch(worker)
       │    └→ view.run_md_translation_service(input_dir, session, output_dir=..., dry_run, ...)
       └─ view._start_ui_timer()        （啟用 cancel_button）
            └→ start_ui_timer(view) → view._poller.start(page, 輪詢函式)
                 每 0.2 秒 _sync_from_session：
                 ├─ progress → progress.value
                 ├─ logs → log_view.sync_entries(logs, update=False)
                 └─ status DONE/ERROR → _set_status（任務完成／已取消／任務發生錯誤）
                      + 停止輪詢 + 停用 cancel_button
```

**Lifecycle**：輪詢由 `view._poller`（`PollerHandle`）持有。`TranslationView.will_unmount()` → `stop_ui_timer`（idempotent，輪詢立刻停止、不再碰控制項，任務照常執行）。`did_mount()` → `resume_ui_timer`（任務仍被追蹤才重新啟動；重複 mount 不累積；卸載期間任務已結束則補上最終狀態）。

**取消**：`cancel_button` → `view._on_cancel()` → `session.request_cancel()`，狀態晶片顯示「正在取消…」；實際在批次之間或等待 API 限流時停止。

## 執行緒安全包裝

- `_safe_add_log(view, msg)`：view 已卸載或 session GC 時靜靜忽略；`mirror=False` 時只寫入畫面日誌，不再重複寫後台。
- `_safe_page_update(view)`：view 已卸載時忽略。
- worker 內例外 → `_report_service_failure`：完整堆疊經 `session_task_scope` 寫入後台 log；畫面顯示「[UI] {label} 服務執行失敗：例外類型與訊息（完整堆疊已寫入後台 log）」，接著 `session.set_error()`，再 `finish()`。

## Markdown 核心流程（`translation_tool/core/md_translation_steps.py`，由 `md_translation_assembly.py` 的 `run_md_pipeline` 組裝）

- **Step1 Extract**（`step1_extract_impl`）：掃描 MD 檔，依 `lang_mode` 過濾後抽取區塊，寫入 `pending_dir/<rel_md>.json` 與 `_manifest.json`（schema `md_pending_manifest_blocks_v1`）。
  - `non_cjk_only`：比對同義 `zh_tw` 檔，CJK 已譯區塊（`contains_cjk`）過濾；區塊數不一致則 warn 並保留全部。
  - 去重複：相同 `content_hash` 只留一份（`seen_hashes`，重複計入 `duplicate_blocks`）。
- **Step2 Translate**（`step2_translate_impl`）：`translate_md_pending_fn` 批次翻譯 pending JSON 到 translated_dir（dry_run / write_new_cache 透傳）。
- **Step3 Inject**（`step3_inject_impl`）：讀原始 MD 與翻譯 JSON，逐區塊套用（`apply_item_to_md_lines_fn`）後寫入 final_dir。
  - `map_lang_in_rel_path_allow_zh_fn` 的 `status` 只認 `SRC_EN` / `SRC_ZH`，其他狀態跳過（`skipped_lang_status`）。
  - 來源 MD 不存在 → `skipped_missing_source`；保留原始結尾換行（`ends_with_nl`）。
- Markdown 只有 `DONE` 才可執行 Step3；`PARTIAL`、`FAILED`、`CANCELLED` 不使用 pending JSON 產生完成目錄，避免未完成翻譯被當成最終輸出。

## 重開後續跑

啟動時若偵測到上次被中斷的 Markdown 任務，使用者在外殼的確認對話框按「續跑」後，外殼切到本頁並呼叫 `TranslationView.resume_interrupted(task)`：把輸入／輸出資料夾、步驟開關與 `lang_mode` 帶回控制項（翻譯步驟一律開啟、dry-run 一律關閉），再走和按下「執行」相同的 `_run_md`。已有任務在跑時不會重複啟動。行為與限制見 `docs/developer-guide/resume-capability.md`。

## 與其他 View 的關係

| View | 職責 | 關係 |
|------|------|------|
| KubeJS／FTB 流水線頁 | KubeJS／FTB 流水線 | 負責 FTB Quests 與 KubeJS，共用 ModDB 與 LM 翻譯服務 |
| LMView | 機器翻譯 | 共用 `lm_translator` 與快取機制 |
| ExtractorView | JAR 提取 | 平行獨立的頁面 |

各頁由 `app/view_registry.py` 註冊、外殼導覽切換，沒有父子關係。

## 關鍵狀態

`TranslationView` 直接持有 `_picker_target_field`、`session`、`_ui_timer_running`、`_step_cards`（面板建立時登記，供 reset 後刷新步驟卡外觀）與 `_poller`，沒有另外的 state 物件。

## 維護注意

1. 新增 Markdown 相關步驟：在 `translation_panels` 的 `build_md_tab` 加入步驟，在 `translation_actions` 的 `run_md` 傳入參數，並在 `resume_interrupted` 還原對應選項。
2. `_reset_md_inputs()` 會重設步驟為全 True、`write_new_cache` 為 False，`lang_mode` 回到 `non_cjk_only`。
