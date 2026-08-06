# Minecraft Translator v0.8.0

Tag: `v0.8.0`
Title: **Minecraft Translator v0.8.0**

> 範圍：PR40 ~ PR63

## Major features
- **KubeJS reverse_index**：實作雙軌 reverse_index 去重，解決 pending 檔案 key 對應問題。
- **顏色字元校驗**：新增 `color_char_checker` 校驗 Minecraft 顏色字元。
- **Rich Text Shield**：新增 `rich_text_shield` 脫殼模組，保護 URL / OpenCC 轉換路徑。
- **Icon Preview 重構**：Icon Model 解析重構、JAR 目錄模式、雙軌 zh_tw 讀取、即時搜尋 UI。
- **多執行緒 JAR 掃描**：新增 `jar_browser` 工具（15 個測試），`jar_processor` 內部改用之。
- **Icon 索引效能**：ThreadPoolExecutor + 預建立 icon 索引（icon 索引建立 5 分 → 1-2 分）。

## Improvements
- Type annotations 分批補齊：`icon_classifier` / `ui_logging_handler` / `merge_view` / pipeline services。
- L2 磁碟快取：icon 快取目錄改為專案根目錄。

## Bug Fixes
- Code Review 全量修復（28 issues，2026-03-25）。
- Icon Preview：重複覆蓋 bug、Phase4 進度條、模組搜尋分頁修復。
- Audit 修復：LRU ZipFile fd leak、KubeJS O(N²) 優化、py.typed 標記。
- API Key `Authorization: Bearer` header 修正 + Race Condition 修復。

## Tests
- Icon Preview 單元測試補寫 30 tests。

---

## What's Changed
完整變更清單：https://github.com/jlin53882/Minecraft-translate/compare/v0.7.0...v0.8.0
