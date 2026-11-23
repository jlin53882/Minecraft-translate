# Markdown 指南的 ModDB 流水線

`translation_tool/translation_db/md_pipeline.py` 把資料夾內的指南 `.md` 帶過與 KubeJS／FTB 流水線相同的四個階段：
來源分析 → ModDB 同步 → LM 翻譯 → 輸出資源包。預覽永遠只讀來源；正式同步才寫 DB；發布才寫輸出資料夾；來源資料夾不會被修改。

## 階段

| 階段 | 函式 | 說明 |
|---|---|---|
| 來源探索 | `discover_md_source_folders` | 選到模組包根目錄時列出 `kubejs`（底下有 `assets`）與 `resourcepacks/<包>`（底下有 `assets`）；其他資料夾直接回傳自己。回傳「含 `assets` 的那一層」，掃描時相對路徑才是資源包結構 |
| 來源分析 | `scan_md_sources` | 用 `iter_md_files` 找指南，`extract_guide_blocks` 切段落，組成 `KIND_GUIDE` 條目 |
| DB 同步 | `sync_database` | 預覽用 `preview_ingest_many`（不寫入）；正式模式 `TranslationDB.ingest`（只新增、不覆蓋） |
| LM 翻譯 | `translation_runner`（服務層提供） | `run_moddb_translate_service`，只翻本次來源的模組、只翻指南條目 |
| 輸出 | `export_md_overlay` | 以 `TranslationResolver` 查 DB 生效譯文，`render_guide` 組回原檔，寫到輸出資料夾 |

## 條目身分

- 路徑是 `assets|data/<模組>/…` 時：`mod_id` 是模組 ID，鍵值是「中性路徑#段落序號」，與 jar 指南流程（`guide_translation.load_guide`）相同，所以兩邊共用同一批條目。
- 其他資料夾：`mod_id` 是 `md-<來源資料夾名稱>`（小寫、非英數字元換成 `-`）。
- 英文原文（含無語言資料夾的指南）是 DB 的 `en_us`；同名的 `zh_tw` 檔在段落數相同時逐段當既有譯文匯入；沒有任何 `zh_tw` 檔且開啟 `read_zh_cn` 時，以段落數相同的 `zh_cn` 逐段匯入並簡轉繁（`zh_tw` 存在但段落數對不上時不退回 `zh_cn`）。段落數不同的譯文檔整份不採用並記為 `misaligned` 提醒。同一個輸出身分有兩個英文來源時全部略過（`output_conflict`）。

## 輸出

- 路徑：`<輸出>/<來源相對路徑，語言資料夾換成 zh_tw>`；無語言資料夾的指南放內容根目錄下的 `_zh_tw/`。來源是資源包結構時，整個輸出資料夾可直接放進 `resourcepacks`，並補上 `pack.mcmeta`。
- 只輸出「至少有一個段落已有譯文」的檔案；沒有譯文的段落保留原文。譯文要通過 `guide_structure_issues`（標籤、前綴、連結目標、程式碼標記不可被改動），否則該段保留原文。
- 完成檔的人工修改保護沿用 `OutputGuard`（`.md_output_hashes.json`）：輸出檔在上次寫入後被改過就不覆蓋，計入 `skipped_manual_edit` 並提醒。
- 輸出資料夾不可放在來源資料夾內（`MdPipelineError`）。
- 只有發布（`publish`）才寫輸出資料夾；同步 DB 步驟即使填了輸出資料夾也只計算輸出計畫。`pack.mcmeta` 依遊戲版本換算 `pack_format`（`resolve_version_input`、`build_pack_mcmeta`），已存在時不覆寫。
- 掃描提醒中的 `output_conflict`、`read_failed`、`source_missing` 會讓 LM 翻譯整段跳過（`translation.status` 為 `SKIPPED`，摘要為完成但有警告）；`misaligned` 只代表該譯文檔不採用，不阻擋翻譯。LM 翻譯失敗時摘要同樣是完成但有警告。

## 服務層與頁面

- `app/services_impl/md_pipeline_service.py` 的 `run_md_pipeline_service` 接受一個或多個來源資料夾，每個先經 `discover_md_source_folders` 展開（沒按「分析來源」直接輸入模組包根目錄也能得到資源包結構），並排除輸出資料夾本身，再依序各跑一次並合併摘要（數字相加、提醒串接、LM 翻譯狀態取最嚴重者）；預覽以唯讀 SQLite 連線開啟 DB。
- 頁面見 [MD_PIPELINE_VIEW_ARCHITECTURE.md](views/MD_PIPELINE_VIEW_ARCHITECTURE.md)。
- 設定 `translator.md_use_zh_cn_base` 只決定頁面上「讀取 zh_cn 檔」勾選的初始值。

## 效能（ATM10 全部 jar 的 1,198 個 `.md`，單機量測）

掃描 549 個候選檔、390 個來源檔、4,273 個段落：掃描 0.5 秒（冷啟動約 3.7 秒）、DB 同步 0.3 秒、輸出 390 個檔案約 1.5 秒（主要是檔案 I/O）。重複同步（無變更）不新增條目。
