# TranslationView 架構

## 定位
TranslationView 位於翻譯流程的第 2 階段（Translate），對應 TRANSLATION_WORKFLOW.md 中的 Translate 區塊。
- **前置依賴**：ExtractorView 從 JAR 抽取 lang/book 檔（階段 1）
- **後置流程**：MergeView 合併翻譯結果（階段 3 → 4）
- TranslationView 本身不處理 Extract / Merge，專注於批次翻譯（LLM API 呼叫）

## 架構圖（文字版）
```
TranslationView（主視圖，Flet ft.Column）
  ├─ TranslationPanels（UI panels，三個 pipeline tab）
  │    ├─ build_ftb_tab()  → FTB Quests（Export→Clean→Translate→Inject）
  │    ├─ build_kjs_tab()   → KubeJS Tooltips（Extract→Translate→Inject）
  │    └─ build_md_tab()    → Markdown Patchouli（Extract→Translate→Inject）
  ├─ TranslationActions（邏輯層，呼叫 pipeline services）
  │    ├─ run_ftb() / run_kjs() / run_md()
  │    └─ start_ui_timer()（定期 poll TaskSession snapshot）
  └─ TranslationState（僅持有一個 dataclass：picker/session/ui_timer 三個 None）
      └─ ExtractorView（獨立視圖，非 TranslationView 子元件，平行存在）
           ├─ ExtractorPanels（Extract Lang / Extract Book tabs）
           ├─ ExtractorActions（呼叫 extract_service）
           └─ ExtractorState（ExtractionStats + PreviewState 兩個 dataclass）
```

## TranslationActions 流程
翻譯按鈕按下後的完整呼叫鏈：
```
使用者點擊「開始翻譯」
  └→ view._run_ftb(dry_run=False)
       └→ run_ftb(view, dry_run=False)
            ├─ 參數驗證（in_dir 必填、service 可用）
            ├─ view.session = TaskSession(); view.session.start()
            ├─ threading.Thread(target=worker, daemon=True).start()
            │    └→ view.run_ftb_translation_service(
            │         in_dir, session, output_dir, dry_run,
            │         step_export/clean/translate/inject, write_new_cache
            │       )
            │         └→ pipeline services（ftb/kubejs/md_service）
            │              └→ translate_batch_smart()（LLM 批次翻譯）
            └→ view._start_ui_timer()
                 └→ 每 0.1s poll session.snapshot() → 更新 progress / log_view / status_chip
```
三個 pipeline（FTB / KubeJS / MD）皆為同一模式：worker 執行 service，ui_timer 負責 UI 更新。

## 與其他 View 的關係
| View | 職責 | 與 TranslationView 的關係 |
|------|------|--------------------------|
| ExtractorView | JAR Extract（階段 1） | 輸出為 TranslationView 的輸入 |
| TranslationView | 批次翻譯（階段 2） | 核心本檔案 |
| LMView | — | 翻譯演算法依賴 `lm_translator_main.py` |
| MergeView | 合併差異鍵（階段 3） | 消費 TranslationView 的產出 |
| BundleView | — | bundler_view.py，未納入 TranslationView 架構 |

TranslationView 與 ExtractorView 是**平行獨立**的 Tab，無父子關係，皆由上層 Navigation 切換。

## 關鍵狀態

### TranslationState（translation_state.py）
```python
@dataclass
class TranslationRunState:
    picker_target_field: object | None = None  # 目前作用中的路徑輸入框
    session: object | None                      # TaskSession 實例（執行緒共享）
    ui_timer_running: bool = False              # UI poller 是否運行中
```

### ExtractorState（extractor_state.py）
```python
@dataclass
class ExtractionStats:          # 提取統計（UI 摘要用）
    success/warnings/failures/total_files: int

@dataclass
class PreviewState:             # 預覽掃描狀態
    progress: float; current/total: int; done: bool; result: dict | None; error: str | None
```
