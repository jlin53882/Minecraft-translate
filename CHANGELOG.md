# Changelog

本專案 Changelog 採用「Keep a Changelog」風格，版本號採 Semantic Versioning。

- Keep a Changelog: https://keepachangelog.com/
- SemVer: https://semver.org/

---

## [Unreleased]

### Features
- （預留）

### Improvements
- （預留）

### Refactoring
- （預留）

### Tests
- （預留）

---

## [0.6.0] - 2026-03-12

> 範圍：PR1 ~ PR39（以你目前的 PR 編號切版）

### Features
- Flet 桌面 GUI：提供設定、規則、快取管理、翻譯任務（FTB/KubeJS/Markdown）、JAR 提取、機器翻譯、檔案合併等頁面入口。
- JAR 提取：從模組 JAR 中提取語言檔與 Patchouli 手冊內容（含預覽與報表）。
- 語言合併：支援 `en_us.json` / `zh_cn.json` / `zh_tw.json` 的保守合併策略，並提供 pending export / quarantine 機制。
- AI 翻譯（Gemini）：支援批次翻譯、自動重試、縮批、換 key、節流等策略（以不改行為為原則持續重構）。
- 快取管理：快取分片儲存、全文搜尋索引、歷史版本檢視與套用。
- 品管/檢查：未翻譯條目、簡繁差異、英文殘留、TSV 版簡繁比較等檢查工具（目前 UI 功能線有凍結/暫緩策略）。

### Improvements
- 設定與路徑解析：以 project root 為基準，降低 cwd 漂移造成的找不到 config/資源/快取路徑問題。
- logging：集中化並讓 pipeline 在每次任務啟動時重新同步 logging 設定（維持舊行為、降低 UI 卡頓）。
- 文件：repo 內已有 `ITERATION_SOP.md`、`docs/`、`docs/pr/` 與長期測試風格筆記（例如 `docs/testing-style-note.md`）。

### Refactoring
- Service 分層：主線 canonical services 逐步收斂到 `app/services_impl/*`，並讓 `app/services.py` 退為 QC/checkers 暫緩線 façade（避免主線被歷史包袱綁死）。
- Plugin shared helpers：引入/強化 `translation_tool/plugins/shared/*`，降低多條 pipeline 重複 helper 的風險。

### Tests
- 建立一批 pytest 測試與 guard tests，用於保護：import contract、path resolution、cache/search 契約、以及 refactor 的關鍵回歸點。

---

## [0.7.0] - 2026-03-23

> 範圍：PR10 ~ PR39

### Features
- FTB Quest 翻譯進度條驗證報告（`step6 progress bar validation report`），新增 FTB Quest 抽取格式完整性驗證。

### Improvements
- **翻譯視圖日誌區**：深色 Container 背景替代 styled_card；ListView 移除無效 bgcolor 參數；初始化提示文字；日誌自動滾到底。
- **KubeJS 翻譯 Pipeline**：OpenCC 簡→繁轉換延伸至 `client_scripts` 來源值；跳過 ASCII 藝術字（█▓▒░）；`skip_chinese=False` 邏輯修正；新增 `item.kubejs.*` 翻譯記憶匹配。
- **翻譯進度**：FTB 進度改以檔案數均勻推進（非 chunk 數）。

### Bug Fixes
- `scroll_to(end=True)` → `scroll_to(offset=1.0)`（Flet 0.28.3 API 相容）。
- `log_presenter._entry_color` 移除錯誤 hex 前綴拼接，正確使用 `Colors` enum 的 `.value`。
- `kubejs_translator_clean.py` 補回 `import json`（`jq` 依賴）。
- CI：修復 lint F401（`threading` import 移除又恢復）；Ruff format 6 個檔案。

### Tests
- 3 個 `test_translation_view_characterization.py` characterization tests 新增（翻譯視圖行為迴歸保護）。

---

## [0.6.0] - 2026-03-12
