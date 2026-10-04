# Translator Skeleton Contract

FTB Quests、KubeJS 與 Markdown translator 都使用同一個流程骨架：

```text
items
  → cache split / batch selection
  → translate_batch_smart
  → ordered result handling
  → cache write
  → format-specific flush callback
  → progress / cancellation / final status
```

## Ownership

`translation_tool.core.lm_translator_skeleton` 的 `TranslatorSkeleton` 與
`run_translator_skeleton()` 負責共同的 orchestration。它透過既有的
`translate_items_with_cache_loop()` 維持以下契約：

- 保留輸入順序與 batch prefix slicing。
- 每個 batch 完成後才更新 cache、checkpoint/輸出與 progress。
- cache write failure、API failure、取消與額度耗盡不可被 callback 吞成成功。
- `on_translated_item`、`on_batch_flushed`、`on_progress` 只提供格式特定的輸出與 UI 回報，不得改變 loop 的狀態判定。

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
仍負責自己的檔案 writer 與輸出格式；shared loop 負責 batch 邊界與 status。
因此本文件不宣稱「關閉後背景持續執行」或超出既有 checkpoint 能力的 crash
resume。若未來改變 checkpoint schema，必須先補 characterization tests 並獨立
記錄輸入 fingerprint、已完成 batch、partial output 與取消狀態。

## Verification rule

任何 skeleton 變更至少需要：

1. 三個 plugin 各自的行為測試。
2. skeleton 本身的 hook forwarding / ordering 測試。
3. 既有 cache failure、cancel、fallback 與 output tests。
4. full pytest、changed-file Ruff/format 與 CI 最新 head 驗證。
