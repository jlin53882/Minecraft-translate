# Translator Skeleton Contract

FTB Quests、KubeJS 與 Markdown translator 都使用同一個流程骨架：

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

- 保留輸入順序與 batch prefix slicing。
- `prepare_translator_items()` 統一 cache rule、hit validation 與 miss list；plugin
  只提供格式特定的 `is_valid_hit` / cache side effect adapter。
- 每個 batch 完成後才更新 cache、輸出、checkpoint 與 progress。
- cache write failure、API failure、取消與額度耗盡不可被 callback 吞成成功。
- `on_translated_item`、`on_batch_flushed`、`on_batch_checkpoint`、`on_progress`
  只提供格式特定的輸出、durable boundary 與 UI 回報，不得改變 loop 的狀態判定。
- `make_progress_hook()` 統一 ETA 與 progress callback 的編排；plugin 只注入
  UI setter、log sink 與必要的文案 formatter。目錄入口可注入既有 cache writer，
  以保留既有測試與整合點的可替換性。

`on_batch_checkpoint(state)` 會在 shared loop 完成 cache flush 與 format flush
之後、progress 之前呼叫。`state` 至少包含 `cache_type`、`processed`、`total`、
`completed_calls` 與該批次的 API `status`。目前三個 plugin 都掛接
`make_checkpoint_adapter()`，保存 fingerprint、target、completed/total 與最後
durable batch metadata；checkpoint 寫入失敗會使 shared loop 回傳 `FAILED`，不會
把未持久化的 boundary 當成成功。這個 hook 是三個 translator 共用的 checkpoint
contract，各格式若需要額外保存 output fingerprint 或 recovery metadata，應在此掛接。

## Plugin-specific responsibilities

| Translator | 保留在 plugin 的差異 |
|---|---|
| FTB Quests | JSON map、檔案級 TouchSet、cache-hit 輸出與 rich-text 回填 |
| KubeJS | tooltip shield/unshield、每檔輸出與 JavaScript 注入前的資料記錄 |
| Markdown | content hash、文件回填、最後一次完整輸出與 rich-text 回填 |

三者共用 batch、cache、flush、取消與結果狀態契約；格式差異不得為了共用而
硬塞進骨架。新增差異時，優先新增明確 hook 或 adapter，避免在 shared loop
加入 translator 名稱分支。

## Checkpoint and recovery boundary

目前 skeleton 保留既有 checkpoint／cache writer 的 ownership：每個 plugin
仍負責自己的檔案 writer 與輸出格式；shared loop 負責統一 cache split、batch 邊界、
cache flush、checkpoint hook 與 status。現況能力盤點如下：

| Translator | 每批 durable 邊界 | 格式特定 recovery 資料 | 狀態 |
|---|---|---|---|
| FTB Quests | `prepare_translator_items` + shared cache flush + JSON adapter | JSON map、file TouchSet | 已由共用 skeleton 覆蓋 |
| KubeJS | `prepare_translator_items` + shared cache flush + JSON adapter | shield/unshield 與 JS output | 已由共用 skeleton 覆蓋 |
| Markdown | `prepare_translator_items` + shared cache flush + JSON adapter | content hash 與文件回填 | 已由共用 skeleton 覆蓋 |

這裡的 checkpoint 是可恢復邊界 contract，但不是完整 crash resume orchestration；若未來改變 checkpoint schema，
必須先補 characterization tests 並獨立記錄輸入 fingerprint、已完成 batch、partial
output 與取消狀態。

## Verification rule

任何 skeleton 變更至少需要：

1. 三個 plugin 各自的行為測試。
2. skeleton 本身的 hook forwarding / ordering 測試。
3. 既有 cache failure、cancel、fallback 與 output tests。
4. full pytest、changed-file Ruff/format 與 CI 最新 head 驗證。
