# 支援的資料格式與入口

本頁只列目前程式碼和既有測試明確支持的入口；格式規則仍以 parser、plugin 與測試為準。

| 格式／來源 | 主要入口 | 相關測試或文件 |
|---|---|---|
| Minecraft `lang/*.json` | `translation_tool/core/jar_processor.py`、`translation_tool/core/lang_merger.py` | [`../developer-guide/jar-pipeline.md`](../developer-guide/jar-pipeline.md)、[`../developer-guide/views/MERGE_VIEW_ARCHITECTURE.md`](../developer-guide/views/MERGE_VIEW_ARCHITECTURE.md) |
| Patchouli 書籍 JSON | `translation_tool/core/jar_processor.py` 與合併／翻譯 service | [`../developer-guide/views/EXTRACTOR_VIEW_ARCHITECTURE.md`](../developer-guide/views/EXTRACTOR_VIEW_ARCHITECTURE.md) |
| FTB Quests SNBT | `translation_tool/plugins/ftbquests/` | [`../../app/views/translation_view.py`](../../app/views/translation_view.py) |
| KubeJS Tooltip | `translation_tool/plugins/kubejs/` | [`../../app/views/translation_view.py`](../../app/views/translation_view.py) |
| Markdown | `translation_tool/plugins/md/` | [`../../app/views/translation_view.py`](../../app/views/translation_view.py) |
| Mod 翻譯資料庫 | `translation_tool/translation_db/`（SQLite） | [`../user-guide/mod-translation-db.md`](../user-guide/mod-translation-db.md) |

「支援」在此代表存在對應的本地 parser／plugin 或資料庫路徑，不代表任意自訂檔案都能被自動辨識；
輸入路徑、檔名與選項仍需符合各工具的實際規則。
