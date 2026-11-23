# Translator Skeleton Contract

目錄翻譯（`lm_translator`）與 Mod DB 翻譯都使用同一個流程骨架：

```text
items
  → shared cache split / batch selection
  → translate_batch_smart
  → ordered result handling
  → cache write
  → format-specific flush callback
  → checkpoint hook
  → progress / cancellation / final status
```

## Ownership

`translation_tool.core.lm_translator_skeleton` 的 `prepare_translator_items()`、
`TranslatorSkeleton` 與 `run_translator_skeleton()` 負責共同的 orchestration。
它透過既有的
`translate_items_with_cache_loop()` 維持以下契約：

- 保留輸入順序與 batch prefix slicing。原文為空白字串的項目沒有可翻譯內容，
  loop 入口先用 `_split_blank_items` 排除（計為未翻譯），不讓它們進入批次。checkpoint 狀態的 `resume_index` 是原清單中尚未處理的起點，續跑清單用它切片，不用 `processed`。
  引擎入口 `translate_batch_smart` 對直接呼叫者也守同一個位置契約：空白項目不送進翻譯流程，
  由 `_merge_blank_items` 標記 `_untranslated` 放回原位；整批都是空白時原樣回傳並給 `AUTO`；
  非空白項目沒有結果時結果停在該位置，不會用空白項目掩蓋失敗。`_validate_batch_items`
  仍會丟棄缺少 `text` 欄位、`text` 為 `None` 或不是 dict 的項目；這類項目沒有放回原位的處理，
  會讓結果位置與輸入錯位。呼叫端組項目時必須明確帶字串 `text`（現有的 Mod DB 與目錄翻譯呼叫端都是如此），新增呼叫端時不要依賴引擎替你丟掉不合格項目。
  共用迴圈遇到缺少 `text` 的項目會回報 `FAILED`，不會靜默略過。
- `prepare_translator_items()` 統一 cache rule、hit validation 與 miss list；呼叫端
  只提供格式特定的 `is_valid_hit` / cache side effect adapter。
- 每個 batch 完成後才更新 cache、輸出、checkpoint 與 progress。
- cache write failure、API failure、取消與額度耗盡不可被 callback 吞成成功。
- `on_translated_item`、`on_batch_flushed`、`on_batch_checkpoint`、`on_progress`
  只提供格式特定的輸出、durable boundary 與 UI 回報，不得改變 loop 的狀態判定。
- `make_progress_hook()` 統一 ETA 與 progress callback 的編排；呼叫端只注入
  UI setter、log sink 與必要的文案 formatter。目錄入口可注入既有 cache writer，
  以保留既有測試與整合點的可替換性。

`on_batch_checkpoint(state)` 會在 shared loop 完成 cache flush 與 format flush
之後、progress 之前呼叫。`state` 至少包含 `cache_type`、`processed`、`total`、
`completed_calls` 與該批次的 API `status`。`make_checkpoint_adapter()` 是這個 hook 的 JSON adapter 工廠，保存 fingerprint、target、
completed/total 與最後 durable batch metadata；checkpoint 寫入失敗會使 shared loop 回傳
`FAILED`，不會把未持久化的 boundary 當成成功。目前正式流程沒有使用這個工廠：目錄翻譯
以 `lm_translator.save_checkpoint` 掛接 `on_batch_checkpoint`，Mod DB 翻譯的進度存在資料庫本身。

目錄入口 `translate_directory_generator()` 只負責 phase 編排：驗證與掃描、抽取、
cache split／輸出、dry-run 預覽，以及呼叫 shared translation phase。抽取、cache-hit
套用、預覽輸出與批次 callback 都由獨立 helper 負責，避免入口重新累積格式與 lifecycle
細節。

## 呼叫端的責任

呼叫端（目錄翻譯、Mod DB 翻譯）仍負責自己的輸出格式；batch、cache、flush、取消與結果狀態契約由
skeleton 統一。格式差異不得為了共用而硬塞進骨架：新增差異時，優先新增明確 hook 或 adapter，
避免在 shared loop 加入呼叫端名稱分支。

## Checkpoint and recovery boundary

skeleton 保留既有 checkpoint／cache writer 的 ownership：呼叫端負責自己的檔案 writer 與輸出格式；
shared loop 負責統一 cache split、batch 邊界、cache flush、checkpoint hook 與 status。

這裡的 checkpoint 是可恢復邊界 contract，但不是完整 crash resume orchestration；若未來改變 checkpoint schema，
必須先補 characterization tests 並獨立記錄輸入 fingerprint、已完成 batch、partial
output 與取消狀態。清理規則固定為：只有 shared loop 回報 `DONE` 且累計
`processed >= total` 的完整完成才可 `clear_checkpoint()`；`CANCELLED`、`FAILED`、
`PARTIAL`、`ALL_KEYS_EXHAUSTED`、未完整的 `DONE` 與 dry-run 都必須保留 checkpoint，
讓下一次執行仍可恢復。所有新增 terminal status 都應預設走保留路徑，並補上對應
regression test；`PARTIAL` 是 batch engine 的受保護部分完成狀態，不等同於完整成功。

## Verification rule

任何 skeleton 變更至少需要：

1. 各呼叫端（目錄翻譯、Mod DB 翻譯）的行為測試。
2. skeleton 本身的 hook forwarding / ordering 測試。
3. 既有 cache failure、cancel、fallback 與 output tests。
4. full pytest、changed-file Ruff/format 與 CI 最新 head 驗證。
