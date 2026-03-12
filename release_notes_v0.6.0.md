# Minecraft Translator v0.6.0

Tag: `v0.6.0`
Title: **Minecraft Translator v0.6**

## Summary
這是專案第一個「正式 release 化」的里程碑版本：把 PR1~PR39 的累積成果整理成可追溯的版本號與 Release Notes。

本版核心目標：**可用、可維護、可持續重構**（並用測試護欄保住行為契約）。

---

## Major features
- **Flet 桌面 GUI**：多頁面入口（設定/規則/快取/翻譯任務/JAR 提取/機器翻譯/檔案合併）。
- **JAR 提取 + 預覽**：從模組 jar 取出語言檔與 Patchouli 手冊內容，並能產出預覽/報表。
- **語言合併流程**：保守合併 `en_us` / `zh_cn` / `zh_tw`，並提供 pending export、quarantine 等防呆路徑。
- **Gemini 批次翻譯引擎**：批次翻譯、自動重試/縮批/換 key/節流策略，支援多種翻譯來源（lang / patchouli / FTB / KubeJS / Markdown）。
- **快取管理**：分片儲存、全文搜尋索引、history 檢視與套用。
- **品質檢查工具**：未翻譯、簡繁差異、英文殘留、TSV 差異比較（部分功能線依目前策略凍結/暫緩）。

---

## Refactoring highlights
- **Service 分層完成一個重要里程碑**：主線改以 `app/services_impl/*` 為 canonical services，`app/services.py` 退為 QC/checkers 的 façade（減少歷史包袱影響主線重構）。
- **Shared helpers 收斂**：引入/強化 `translation_tool/plugins/shared/*`，減少 pipeline 重複 helper。
- **路徑解析與 logging 初始化更可控**：降低 import-time side effects，並讓任務啟動時可刷新 logger 設定。

---

## Tests
- 已建立 pytest 測試與 guard tests，優先保：import contract、path resolution、cache/search 契約、refactor 高風險回歸點。

---

## Upgrade notes
- `config.json` 含 API key，請勿提交；建議以 `config.example.json` 為基準建立本地設定。

---

## What’s next (v0.7.0)
- PR40~PR58：non-UI pipeline 拆分、shared 收斂、service lifecycle 抽共用、UI characterization tests 先行，最後才拆大型 view。
