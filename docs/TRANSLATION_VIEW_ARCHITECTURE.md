# TRANSLATION_VIEW_ARCHITECTURE.md

## 定位
TranslationView（頁面標題「任務翻譯工具」）是 FTB Quests / KubeJS Tooltips / Markdown 三種目標的**批次翻譯工作台**；每個目標都有「步驟可自由勾選」的多步流程（匯出／清理 → LM 翻譯 → 寫回），不處理 JAR Extract / Merge。

## 檔案結構

- `app/views/translation_view.py` — 主視圖（`TranslationView`）
- `app/views/translation/translation_actions.py` — `run_ftb` / `run_kjs` / `run_md` / `start_ui_timer`（`_poll_session` / `_sync_from_session`）/ 執行緒安全更新包裝
- `app/views/translation/translation_panels.py` — `build_path_row` / `build_action_row` / `build_ftb_tab` / `build_kjs_tab` / `build_md_tab`（內部 `_step_row` / `_cache_switch` / `_tab`）
- `app/views/translation/translation_state.py` — `TranslationRunState` dataclass（見下方「關鍵狀態」）
- `app/services_impl/pipelines/ftb_service.py` / `kubejs_service.py` / `md_service.py` — pipeline services（step 參數）

## 架構圖（文字版）
```
TranslationView（ft.Column）
  ├─ kit.page_header「任務翻譯工具」（副標：處理 FTB Quests、KubeJS Tooltip 與 Markdown 文件，步驟可自由勾選）
  └─ body = ft.Row
       ├─ 左（expand=3）：ft.Tabs（TabBar + TabBarView：FTB Quests / KubeJS Tooltips / Markdown）
       │    └─ 每個 tab 由 translation_panels.build_*_tab() 建立（_tab）
       │         ├─ section_card「路徑設定」：build_path_row（輸入／輸出資料夾；MD 另有 md_lang_mode）
       │         ├─ section_card「翻譯步驟」：kit.StepCard 步驟列（背後為 ft.Checkbox）+「寫入新快取」kit.SwitchRow
       │         └─ build_action_row：開始翻譯 / Dry-run 開始模擬翻譯 / Reset
       └─ 右（expand=2）：
            ├─ 執行狀態卡：kit.ProgressRing（progress，預設 0%）+「執行狀態」標籤 + status_chip（預設「尚未開始」）+ cancel_button（「取消」，預設停用）
            └─ section_card「執行日誌」：log_view（LogView tail）+ 標題列右側垃圾桶圖示按鈕（tooltip「清空日誌」）
```

`__init__` 拆成 `_init_translation_state_and_tabs`（狀態、右側控制項、三個 tab）與 `_build_translation_status_and_right_panel`（`ft.Tabs`、service seam、右側面板）。

**Service seam**：`__init__` 中將 service 存到 view 屬性
`self.run_ftb_translation_service` / `self.run_kubejs_tooltip_service` / `self.run_md_translation_service` / `self.TaskSession`，供 actions 讀取（相容 seam）；這些 service 以 try/except 匯入，匯入失敗時為 `None`，執行時 actions 會提示「尚未可用」。

## 各 Tab 步驟

| Tab | 步驟 Checkbox | 額外選項 |
|-----|---------------|----------|
| FTB Quests | `ftb_step_export`（匯出）/ `ftb_step_clean`（清理）/ `ftb_step_translate`（翻譯）/ `ftb_step_inject`（寫回） | `ftb_write_new_cache` Switch |
| KubeJS | `kjs_step_extract`（匯出與清理）/ `kjs_step_translate`（翻譯）/ `kjs_step_inject`（寫回） | `kjs_write_new_cache` Switch |
| Markdown | `md_step_extract`（抽取）/ `md_step_translate`（翻譯）/ `md_step_inject`（寫回） | `md_write_new_cache` Switch + `md_lang_mode` Dropdown（non_cjk_only / cjk_only / all） |

每個 tab 的輸入／輸出欄位為 `ftb_in_dir` / `ftb_out_dir`、`kjs_in_dir` / `kjs_out_dir`、`md_in_dir` / `md_out_dir`；欄位標籤：FTB / KubeJS 為「輸入資料夾（模組包根目錄）」「輸出資料夾（可選）」，MD 為「輸入資料夾（遞迴掃描 .md）」「輸出資料夾（可選）」；欄位右側各有資料夾選擇按鈕。輸出留空時由 service 使用預設位置（hint 分別為 `<input>/Output`、MD 為 `<input>/Output/md`）。

## TranslationActions 流程
```
點擊「開始翻譯」→ view._run_ftb(dry_run=False)
  └→ run_ftb(view, dry_run)                 [translation_actions.py]
       ├─ 已有任務執行中（_ui_timer_running）→ snack「已有翻譯任務執行中」
       ├─ 驗證 in_dir 非空、service/TaskSession 可用（否則 snack）
       ├─ _set_status(「模擬執行」/「執行中」)、progress=0、log_view.clear()
       ├─ view.session = tag_session(TaskSession(), ...); session.start()
       ├─ threading.Thread(worker).start()
       │    └→ run_ftb_translation_service(in_dir, session, output_dir=...,
       │         dry_run, step_export/clean/translate/inject, write_new_cache)
       └─ view._start_ui_timer()        （啟用 cancel_button）
            └→ start_ui_timer(view) → page.run_task(_poll_session)
                 每 _POLL_INTERVAL_SEC（0.2 秒）_sync_from_session：
                 ├─ progress → progress.value
                 ├─ logs → log_view.sync_entries(logs, update=False)
                 └─ status DONE/ERROR → _set_status（任務完成／已取消／任務發生錯誤）
                      + 停止輪詢 + 停用 cancel_button
```
三個 pipeline 皆同一模式（FTB / KubeJS / MD），差異僅在 service 函式與參數（MD 多 `lang_mode`，且以關鍵字 `input_dir=` 傳入）。

**取消**：`cancel_button` → `view._on_cancel()` → `session.request_cancel()`，狀態晶片顯示「正在取消…」；實際在批次之間或等待 API 限流時停止。

## 執行緒安全包裝（ATK-004 / ATK-017 修復）

- `_safe_add_log(view, msg)`：view 已卸載或 session GC 時靜靜忽略
- `_safe_page_update(view)`：view 已卸載時忽略
- worker 內例外 → `_safe_add_log(view, "[UI] 服務執行失敗：…")` + `session.set_error()`

## UI 風格

- 以 `app/ui/kit` 的 `button`、`section_card`、`StepCard`、`SwitchRow`、`ProgressRing` 與 `app/ui/design` 的 `C` 色票組裝畫面；舊的 `components.py` 與 `theme.py` 相容層已移除（#121）。
- `log_view` 為 **LogView widget**（`mode="tail"`、`tail_lines=250`）；`_append_log` 直接走 `log_view.add(line, level="system")`
- `page` 屬性為 `@property`（回傳 `_page`）

## 各 Pipeline Step 行為（core 層邏輯）

### FTB Quests（`translation_tool/core/ftb_translator.py` 的 `run_ftb_pipeline`）
- **Step1 Export / Step2 Clean**：`export_ftbquests_raw_json` / `clean_ftbquests_from_raw`（實作分在 `ftb_translator_export.py` / `ftb_translator_clean.py`）— 讀 `config/ftbquests/quests/lang/{en_us,zh_cn,zh_tw}` 下 `ftb_lang.json` / `ftb_quests.json`，清理三方合併（`deep_merge_3way`：zh_tw > zh_cn轉繁 > en_us，巢狀遞迴合併）
- **Step3 Translate**：需要 Step2 產出的 `en_pending_dir`（沒勾 Step2 會報錯）；呼叫 `plugins/ftbquests/ftbquests_lmtranslator.py` 的 `translate_ftb_pending_to_zh_tw`（LM 批次翻譯，`dry_run` / `write_new_cache` 透傳），輸出到 `<out>/ftbquests/LM翻譯輸出/config/ftbquests/quests/lang`（out 預設為 `<input>/Output`）。**FTB pipeline 不使用** `translate_directory_generator`
- **Step4 Inject**：`dry_run` 時略過；否則讀 Step3 的 `zh_tw` JSON（兩個都不存在 → `FileNotFoundError`），以 `inject_ftbquests_quests_from_zh_tw_json` 注入任務本體、`prepare_ftbquests_lang_template_only` 準備語系模板後以 `inject_ftbquests_zh_tw_from_jsons` 注入介面文字，輸出到 `<out>/ftbquests/完成/config`
- 清理用 `prune_en_us_by_zh_tw`（巢狀）/ `prune_flat_en_by_tw`（扁平）把 zh_tw 已覆蓋的 en key 移除

### KubeJS Tooltips（`translation_tool/core/kubejs_translator.py` 的 `run_kubejs_pipeline`）
- **Step1 Extract**（`step1_extract_and_clean`）：`resolve_kubejs_root`（max_depth=4，優先含 `client_scripts` 的目錄）→ `kubejs_tooltip_extract.extract` → `clean_kubejs_from_raw`（三方合併，`deep_merge_3way_flat` 扁平版：tw > cn轉繁 > en）
- **Step2 Translate**：`step2_translate_lm` → `kubejs_tooltip_lmtranslator.translate_kubejs_pending_to_zh_tw`（LM 批次翻譯，dry_run / write_new_cache 透傳）
- **Step3 Inject**：`step3_inject` → `kubejs_tooltip_inject.inject`（把翻譯 JSON 寫回 kubejs 目錄）
- 三方合併前用 `_is_filled_text` 排除空字串與 `{xxx}` 語言參考格式
- `_read_json_dict_orjson` 處理 BOM 與結尾多餘逗號（容錯解析）

### Markdown（`translation_tool/core/md_translation_steps.py`，由 `md_translation_assembly.py` 的 `run_md_pipeline` 組裝）
- **Step1 Extract**（`step1_extract_impl`）：掃描 MD 檔，依 `lang_mode` 過濾後抽取區塊，寫入 `pending_dir/<rel_md>.json` + `_manifest.json`（schema `md_pending_manifest_blocks_v1`）
  - `non_cjk_only`：比對同義 `zh_tw` 檔，CJK 已譯區塊（`contains_cjk`）過濾；區塊數不一致則 warn 並保留全部
  - **去重複**：相同 `content_hash` 只留一份（`seen_hashes`，重複計入 `duplicate_blocks`）
- **Step2 Translate**（`step2_translate_impl`）：`translate_md_pending_fn` 批次翻譯 pending JSON → translated_dir（dry_run / write_new_cache 透傳）
- **Step3 Inject**（`step3_inject_impl`）：讀原始 MD + 翻譯 JSON，逐區塊套用（`apply_item_to_md_lines_fn`）→ 寫入 final_dir
  - `map_lang_in_rel_path_allow_zh_fn` 回傳 `status` 只認 `SRC_EN` / `SRC_ZH`，其他狀態跳過（`skipped_lang_status`）
  - 來源 MD 不存在 → `skipped_missing_source`；保留原始結尾換行（`ends_with_nl`）

## 與其他 View 的關係

| View | 職責 | 關係 |
|------|------|------|
| ExtractorView | JAR Extract | 平行獨立的頁面；本頁處理的是模組包／KubeJS／Markdown 目錄 |
| LMView | LM 翻譯 | 共用 `lm_translator` / cache 機制 |

各頁由 `app/view_registry.py` 註冊、外殼導覽切換，無父子關係。

## 關鍵狀態（translation_state.py）

```python
@dataclass
class TranslationRunState:
    picker_target_field: object | None = None
    session: object | None = None
    ui_timer_running: bool = False
```

`TranslationView._init_translation_state_and_tabs` 會建立 `self._state = TranslationRunState()`，但實際讀寫的是 view 自己的 `_picker_target_field` / `session` / `_ui_timer_running` 屬性，`_state` 目前沒有被其他程式讀取。

## 維護注意

1. 新增 pipeline 目標：在 `translation_panels` 加 `build_*_tab()`、`translation_actions` 加 `run_*()`、`_build_translation_status_and_right_panel` 加 service seam、`_init_translation_state_and_tabs` 加 tab、`_reset_*_inputs()`。
2. `_reset_*_inputs` 會重設步驟為全 True + write_new_cache True（MD 的 lang_mode 回 non_cjk_only）。
