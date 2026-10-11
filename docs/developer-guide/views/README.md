# View 架構文件

本目錄保存各頁的 UI 元件、呼叫鏈、service 邊界、生命週期與變更注意事項。頁面操作到背景工作之間的短流程見 [`page_flows`](../../page_flows/README.md)；整體系統地圖見 [`../architecture.md`](../architecture.md)。

每份 View 文件的程式碼 locality 由 [`tests/test_view_docs_identifiers.py`](../../../tests/test_view_docs_identifiers.py) 的 `DOC_SCOPES` 宣告。新增或移動文件時，若沒有同步加入 ownership，測試必須失敗，而不是默默略過。

| 頁面／子系統 | 架構文件 |
|---|---|
| 一鍵流水線 | [Pipeline](PIPELINE_VIEW_ARCHITECTURE.md) |
| JAR 提取 | [Extractor](EXTRACTOR_VIEW_ARCHITECTURE.md) |
| 語系合併 | [Merge](MERGE_VIEW_ARCHITECTURE.md) |
| 機器翻譯 | [LM](LM_VIEW_ARCHITECTURE.md) |
| 任務翻譯 | [Translation](TRANSLATION_VIEW_ARCHITECTURE.md) |
| QC | [QC](QC_VIEW_ARCHITECTURE.md) |
| 未翻譯檢查器 | [Untranslated checker](UNTRANSLATED_CHECKER_ARCHITECTURE.md) |
| 翻譯校對 | [Icon preview](ICON_VIEW_ARCHITECTURE.md) |
| 快取管理 | [Cache](CACHE_VIEW_ARCHITECTURE.md) |
| 替換規則 | [Rules](RULES_VIEW_ARCHITECTURE.md) |
| 學名查詢 | [Lookup](LOOKUP_VIEW_ARCHITECTURE.md) |
| 資源包打包 | [Bundler](BUNDLER_VIEW_ARCHITECTURE.md) |
| 設定 | [Config](CONFIG_VIEW_ARCHITECTURE.md) |
