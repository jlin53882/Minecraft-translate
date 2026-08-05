# TRANSLATION_VIEW_ARCHITECTURE.md

## 定位
TranslationView 是翻譯流程第 2 階段（Translate）的**批次翻譯工作台**，專注於 FTB / KubeJS / Markdown 三種目標的 LLM 批次翻譯（不處理 Extract / Merge）。
- **前置依賴**：ExtractorView 從 JAR 抽取 lang/book 檔（階段 1）
- **後置流程**：MergeView 合併翻譯結果（階段 3 → 4）

## 檔案結構

- `app/views/translation_view.py` — 主視圖（約 348 行）
- `app/views/translation/translation_actions.py` — `run_ftb` / `run_kjs` / `run_md` / `start_ui_timer` / 執行緒安全更新包裝
- `app/views/translation/translation_panels.py` — `build_path_row` / `build_action_row` / `build_ftb_tab` / `build_kjs_tab` / `build_md_tab`
- `app/views/translation/translation_state.py` — `TranslationRunState` dataclass
- `app/services_impl/pipelines/ftb_service.py` / `kubejs_service.py` / `md_service.py` — pipeline services（step 參數）

## 架構圖（文字版）
```
TranslationView（ft.Column）
  ├─ header（標題 + 清空日誌按鈕）
  ├─ body = ft.Row
  │    ├─ 左（expand=2）：ft.Tabs（FTB Quests / KubeJS Tooltips / Markdown）
  │    │    └─ 每個 tab 由 translation_panels.build_*_tab() 建立
  │    │         ├─ styled_card「路徑設定」：build_path_row(in_dir / out_dir)
  │    │         ├─ styled_card「步驟與選項」：step Checkbox
  │    │         └─ build_action_row：開始翻譯 / Dry-run / Reset + 尾隨 switch
  │    └─ 右（expand=1）：執行狀態 styled_card（status_chip + progress）+ log_view（LogView tail）
  └─ summary_card
```

**Service seam**：`__init__` 中將 service 存到 view 屬性
`self.run_ftb_translation_service` / `self.run_kubejs_tooltip_service` / `self.run_md_translation_service` / `self.TaskSession`，供 actions 讀取（相容 seam）。

## 各 Tab 步驟

| Tab | 步驟 Checkbox | 額外選項 |
|-----|---------------|----------|
| FTB Quests | Step1 Export / Step2 Clean / Step3 Translate / Step4 Inject | `ftb_write_new_cache` Switch |
| KubeJS | Step1 Extract / Step2 Translate / Step3 Inject | `kjs_write_new_cache` Switch |
| Markdown | Step1 Extract / Step2 Translate / Step3 Inject | `md_write_new_cache` Switch + `md_lang_mode` Dropdown（non_cjk_only / cjk_only / all） |

## TranslationActions 流程
```
點擊「開始翻譯」→ view._run_ftb(dry_run=False)
  └→ run_ftb(view, dry_run)                 [translation_actions.py]
       ├─ 驗證 in_dir 非空、service/TaskSession 可用（否則 snack）
       ├─ _set_status(「模擬執行」/「執行中」)、progress=0、log_view.clear()
       ├─ view.session = TaskSession(); session.start()
       ├─ threading.Thread(worker).start()
       │    └→ run_ftb_translation_service(in_dir, session, output_dir,
       │         dry_run, step_export/clean/translate/inject, write_new_cache)
       └─ view._start_ui_timer()
            └→ start_ui_timer(view) 每 0.1s poll session.snapshot()
                 ├─ progress → progress_bar
                 ├─ logs → log_view.sync_entries(logs) + scroll_to(1.0)
                 └─ status DONE/ERROR → _set_status + 停止 timer
```
三個 pipeline 皆同一模式（FTB / KubeJS / MD），差異僅在 service 函式與參數（MD 多 `lang_mode`）。

## 執行緒安全包裝（ATK-004 / ATK-017 修復）

- `_safe_add_log(view, msg)`：view 已卸載或 session GC 時靜靜忽略
- `_safe_page_update(view)`：view 已卸載時忽略
- worker 內例外 → `session.add_log("[UI] 服務執行失敗")` + `session.set_error(str(ex))`

## UI 風格

- 全部共用 `app/ui/components.py` 的 `styled_card` / `primary_button` / `secondary_button`（一致性調整只需改該檔）
- `log_view` 為 **LogView widget**（`mode="tail"`、`tail_lines=250`）；`_append_log` 直接走 `log_view.add(line, level="system")`
- `page` 屬性為 `@property`（2026-08-01 PR #85 修正 snack bar 問題）

## 與其他 View 的關係

| View | 職責 | 關係 |
|------|------|------|
| ExtractorView | JAR Extract（階段 1） | 輸出為 TranslationView 輸入 |
| MergeView | 合併差異鍵（階段 3） | 消費 TranslationView 產出 |
| LMView | LM 翻譯 | 共用 `lm_translator` / cache 機制 |

TranslationView 與 ExtractorView 是**平行獨立** Tab，無父子關係，由 Navigation 切換。

## 關鍵狀態（translation_state.py）

```python
@dataclass
class TranslationRunState:
    picker_target_field: object | None = None   # 目前作用中的路徑輸入框
    session: object | None                      # TaskSession 實例（執行緒共享）
    ui_timer_running: bool = False              # UI poller 是否運行中
```

## 維護注意

1. 新增 pipeline 目標：在 `translation_panels` 加 `build_*_tab()`、`translation_actions` 加 `run_*()`、`__init__` 加 service seam、`_reset_*_inputs()`。
2. `_reset_*_inputs` 會重設步驟為全 True + write_new_cache True（MD 的 lang_mode 回 non_cjk_only）。
