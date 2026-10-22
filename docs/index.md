# Minecraft-translate 文件入口

本頁是文件庫的導覽首頁。README 保留產品介紹、安裝與最短使用方式；較完整的操作說明、架構契約與貢獻規則依讀者分層放在本目錄。

## 先從你的目標開始

| 目標 | 入口 |
|---|---|
| 第一次安裝並啟動 | [`getting-started/installation.md`](getting-started/installation.md) → [`getting-started/quickstart.md`](getting-started/quickstart.md) |
| 了解一般翻譯、單頁工具與一鍵流水線的差異 | [`user-guide/translation-workflow.md`](user-guide/translation-workflow.md) |
| 使用 Mod 翻譯資料庫 | [`user-guide/mod-translation-db.md`](user-guide/mod-translation-db.md) |
| 了解系統邊界與資料流 | [`developer-guide/architecture.md`](developer-guide/architecture.md) |
| 修改某個子系統前找程式碼與測試 | [`developer-guide/codebase-ownership.md`](developer-guide/codebase-ownership.md) |
| 追查某個 View、長任務或快取契約 | [`developer-guide/views/`](developer-guide/views/)、[`developer-guide/operation-lifecycle.md`](developer-guide/operation-lifecycle.md)、[`developer-guide/cache.md`](developer-guide/cache.md)、[`developer-guide/cache-lifecycle.md`](developer-guide/cache-lifecycle.md) |
| 修改設定、格式或產生文件 | [`reference/configuration.md`](reference/configuration.md)、[`reference/formats.md`](reference/formats.md)、[`generated/exception-inventory.md`](generated/exception-inventory.md) |
| 重新產生或檢查生成文件 | [`contributing/generated-docs.md`](contributing/generated-docs.md) |
| 執行測試、開 PR 或發版 | [`contributing/testing.md`](contributing/testing.md)、[`contributing/pr-workflow.md`](contributing/pr-workflow.md)、[`contributing/release.md`](contributing/release.md) |

## 文件分類

### 使用者指南

- [`user-guide/translation-workflow.md`](user-guide/translation-workflow.md)：一般／單頁工具、一鍵製作與 Mod DB 參與流程的分界。
- [`user-guide/mod-translation-db.md`](user-guide/mod-translation-db.md)：資料庫掃描、查詢、校對、批次操作與寫回規則。
- [`user-guide/troubleshooting.md`](user-guide/troubleshooting.md)：只收錄能由目前程式碼、設定或測試支持的排查方式。

### 開發者指南

- [`developer-guide/architecture.md`](developer-guide/architecture.md)：全系統地圖、主要資料流、入口與依賴來源。
- [`developer-guide/codebase-ownership.md`](developer-guide/codebase-ownership.md)：子系統 → 原始碼 → 權威文件 → 測試的對照。
- [`developer-guide/views/`](developer-guide/views/)：各 View 的元件、呼叫鏈與 service 邊界；每份文件的識別字範圍由 `tests/test_view_docs_identifiers.py` 宣告。
- [`page_flows/README.md`](page_flows/README.md)：頁面操作到背景工作與結果回到 UI 的流程圖。
- [`developer-guide/smart-review-status.md`](developer-guide/smart-review-status.md)：智慧校對候選狀態機、允許的轉移與行程內快取清除入口。
- [`developer-guide/operation-lifecycle.md`](developer-guide/operation-lifecycle.md)、[`developer-guide/worker-thread-audit.md`](developer-guide/worker-thread-audit.md)：長任務、取消、關閉與 worker/UI 邊界。
- [`developer-guide/jar-pipeline.md`](developer-guide/jar-pipeline.md)、[`developer-guide/translation-engine-skeleton.md`](developer-guide/translation-engine-skeleton.md)：引擎資料流與共用翻譯骨架。

### Reference、Generated 與 ADR

- [`reference/configuration.md`](reference/configuration.md) 與 [`reference/configuration-apply-timing.md`](reference/configuration-apply-timing.md) 以設定 schema 和套用程式碼為準。
- [`reference/formats.md`](reference/formats.md) 對照目前 parser／plugin 入口，不承諾未被程式碼支持的格式。
- [`generated/exception-inventory.md`](generated/exception-inventory.md) 由 `tools/gen_exception_inventory.py` 產生，不手動編輯。
- [`adr/`](adr/0001-long-running-tasks-after-window-close.md) 保存有日期的架構決策。

## 維護規則

1. 程式碼、設定 schema、測試與實際執行結果優先於 Markdown 互相引用。
2. 詳細契約只放一份 authoritative 文件；其他頁面提供摘要和連結，不複製第二份規格。
3. 動態數量使用可重現命令查詢，不在常態文件固定快照；歷史文件若保留快照，必須標明日期與歷史性質。
4. 移動文件後使用相對連結；文件檢查會驗證本地路徑、錨點與 View ownership。
5. 本目錄只放長期維護文件；測試風險盤點、實機驗證清單、UI 設計稿與收尾紀錄這類時間點作業紀錄不放在這裡，由維護者保存在 Obsidian 的 `2-Projects/Minecraft-translate/`（設計稿、測試風險盤點與 Windows 驗證清單在其下的 `docs-archive/`）。
