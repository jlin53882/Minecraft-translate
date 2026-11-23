# TRANSLATION_VIEW_ARCHITECTURE.md

## 定位

TranslationView（頁面標題「任務翻譯工具」）是翻譯來源的入口頁，提供三個分頁：FTB Quests、KubeJS Tooltips 與 Markdown。三個分頁都嵌入流水線工作台元件（`embedded=True` 隱藏頁首），流程與側欄的流水線頁相同，不在本頁另外維護。

流程圖見 [page_flows/translation.md](../../page_flows/translation.md)。

## 檔案結構

- `app/views/translation_view.py`：主視圖（`TranslationView`），頁首與三個分頁，沒有自己的背景任務。
- `app/views/kubejs_ftb_pipeline_view.py`：FTB／KubeJS 元件，兩個分頁與側欄頁共用。
- `app/views/md_pipeline_view.py`：Markdown 流水線元件，見 [MD_PIPELINE_VIEW_ARCHITECTURE.md](MD_PIPELINE_VIEW_ARCHITECTURE.md)。

## 架構圖（文字版）

```
TranslationView（ft.Column）
  ├─ kit.page_header（任務翻譯工具）
  └─ ft.Tabs
       ├─ 分頁「FTB Quests」：KubejsFtbPipelineView(mode="ftb", embedded=True)
       ├─ 分頁「KubeJS Tooltips」：KubejsFtbPipelineView(mode="kubejs", embedded=True)
       └─ 分頁「Markdown」：MarkdownPipelineView(embedded=True)
```

`__init__` 建立三個子視圖後呼叫 `_build_source_tabs` 組成分頁。

## 重開後續跑

舊版 Markdown 三步驟任務被中斷時，啟動後外殼的續跑對話框仍可能列出它；使用者按「續跑」後外殼呼叫 `TranslationView.resume_interrupted(task)`：切到 Markdown 分頁並提示重新執行流水線，不再續跑舊流程。

## 與其他 View 的關係

| View | 職責 | 關係 |
|------|------|------|
| KUBEJS / FTB 流水線頁 | KubeJS／FTB 流水線 | 與本頁兩個分頁共用同一個元件與 ModDB 流程 |
| LMView | 機器翻譯 | 共用 ModDB 翻譯服務與快取機制 |
| ExtractorView | JAR 提取 | 平行獨立的頁面 |

## 維護注意

1. FTB／KubeJS 的流程修改改在 `kubejs_ftb_pipeline_view.py`，Markdown 的改在 `md_pipeline_view.py`，不要在分頁中另外實作。
2. 新增分頁時同步 `_build_source_tabs` 的 `length`、`TabBar` 與 `TabBarView`。
