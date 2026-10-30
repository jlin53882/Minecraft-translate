# KubeJS／FTB 流水線頁面架構

## 職責

`KubejsFtbPipelineView` 是薄的 Flet 協調層。它只收集路徑與執行選項、啟動
`launch_page_operation`、輪詢 `TaskSession`，並顯示四個區域：來源、執行、輸出、紀錄。
實際來源識別、掃描、DB 同步與輸出由下列共用核心負責：

| 層 | 入口 | 契約 |
|---|---|---|
| 來源 | `pack_sources.discover_pack_roots` / `scan_pack_sources` | bounded、拒絕 reparse point、穩定 `pack_scope` |
| 協調 | `translation_db.pack_pipeline.run_pack_pipeline` | full／incremental preview、正式同步、manifest、階段進度 |
| UI service | `services_impl.pack_pipeline_service` | 現有 DB、TaskSession、取消與錯誤邊界 |
| 輸出 | `pack_output.export_pack_overlay` | source hash、staging、ownership、原子發布、重掃 |

頁面不執行 JavaScript，不直接建立大量控制項，也不在 event handler 進行大型掃描。
Web 沒有資料夾選擇器結果時，路徑欄位仍是可用的一級輸入；空值或來源分析失敗
不會顯示為已加入。

## 生命週期

按鈕 → `launch_page_operation` → 背景 service → `TaskSession` → `PollerHandle` →
UI 區域更新。`will_unmount` 只停止輪詢，背景工作仍由 operation registry 管理；
`did_mount` 會重新接續輪詢。取消只在掃描／DB／輸出階段邊界生效，取消後未完成
overlay 不發布。

## 長期維護規則

- 不在此頁新增第二套 scanner、parser、format checker、翻譯引擎或資料庫。
- `full` 與 `incremental` 只是同一 scanner／ingest 的同步策略；增量只依完整的
  `(kind, mod_id, source_path, selector, source_hash)` 身分略過未變更項目，不能靠 UI
  自己維護另一份快取或刪除資料庫翻譯。
- `kubejs_lang`、`kubejs_script`、`ftb_quest_lang`、`ftb_quests` 保持獨立 kind，
  但 value 格式診斷與一般 Lang 使用既有共用核心。
- `config/ftbquests` 與 `kubejs` overlay 不得混入 Resource Pack 路徑。
- 任何寫出檔案都要重新解析並由相同 scanner 對齊 selector；不能只相信 writer 計數。
- manifest 的 source mapping、translation plan 與 writeback diff 必須記錄同一條
  來源→DB→selector→overlay target chain，程式版本／HEAD 與 DB 身分也要可回溯。
- 新增來源格式先補來源／DB／writer／重掃測試，再加 UI 顯示；未支援內容要留下
  `unsupported` 或 `parse_failed` 證據。
