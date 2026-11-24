# MD_PIPELINE_VIEW_ARCHITECTURE.md

## 定位

`MarkdownPipelineView`（`app/views/md_pipeline_view.py`）是 Markdown 指南的流水線工作台，嵌入任務翻譯頁的「Markdown」分頁（`embedded=True` 隱藏頁首）。
版面與 KubeJS／FTB 流水線頁相同：左欄「專案路徑」「版本與翻譯設定」「流水線步驟」，右欄執行狀態與 `PipelineProgressPanel`。
後端見 [md-moddb-pipeline.md](../md-moddb-pipeline.md)，流程見 [page_flows/translation.md](../../page_flows/translation.md)。

## 控制項

- `source_field`：來源資料夾／模組包根目錄；`candidate_column` 放 `ft.Checkbox` 候選清單。
- `version_field`：遊戲版本。
- `output_field`：輸出資料夾（正式發布必填）。
- `read_zh_cn`（讀取 zh_cn 檔）：初始值由 `get_md_use_zh_cn_default()` 讀 `translator.md_use_zh_cn_base`，只影響本次執行；`translate_missing`（LM 翻譯待處理）；`lm_db_options`（`LmDbOptions`，與一鍵製作相同的 Mod 資料庫設定卡）。
- 四個步驟：分析來源、預覽同步計畫、同步 ModDB、輸出資源包，以及一鍵執行（先唯讀預覽，成功後由確認對話框決定是否發布）。

## 執行流程

```
按下步驟按鈕 → _start(dry_run, publish)
  ├─ _run_blocker：已有流水線執行中／沒有來源／沒勾選資料夾／沒有版本／發布缺輸出資料夾 → snack
  ├─ selected_sources()：勾選的候選；尚未分析來源時用輸入框路徑
  ├─ tag_session(TaskSession) + launch_page_operation(run_md_pipeline_service)
  └─ 輪詢更新進度、日誌、狀態晶片，結束時顯示摘要
        └─ 一鍵預覽成功 → 確認對話框 → _start(發布)
```

- 同一時間只允許一個流水線執行（類別層級的 `_active_run`）。
- 取消：`session.request_cancel()`，服務在階段之間與掃描迴圈中檢查，取消後不輸出檔案。
- 換頁卸載只停止輪詢，任務照常執行。
- 摘要由 `_render_summary` 寫入執行前的狀態卡，並同步到執行時顯示的「執行結果」卡（`result_card`，執行結束才出現、下一次執行開始時隱藏）：來源檔案、段落、DB 新增／既有、zh_cn／zh_tw 沿用檔數、輸出檔數、尚無譯文段落（其中特殊字元或指南標記與原文不一致、輸出保留原文的段數另列，`blocks_rejected`）、人工修改略過檔數。

## 與其他 View 的關係

來源範圍除了指南 `.md`，也包含 Oracle Index 書的 `.mdx` 與 `_meta.json`（後端行為，頁面沒有額外控制項）。

與 KubeJS／FTB 流水線頁共用 `pipeline_workbench`、`PipelineProgressPanel`、`LmDbOptions` 與同一個 ModDB；兩者各自限制同一時間只有一個執行，彼此不互斥。

## 維護注意

1. 新增輸入選項：在控制項建立處新增、`_start` 傳給 `run_md_pipeline_service`，並在 `MdPipelineOptions` 補欄位。
2. 修改階段名稱或進度分段時，同步 `PipelineProgressPanel` 的步驟名稱。
