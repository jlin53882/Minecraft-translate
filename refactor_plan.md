# Extractor Module Refactoring Plan

## 目標
將 `ExtractorView`、`extractor_dialog.py`、`extractor_actions.py` 等文件中的業務邏輯拆分為 **UI → Actions → Service** 三層，消除職責混亂、執行緒安全問題。

### 步驟（按週期）
1. **Week 1‑2** – 完成 `extract_service.py` 的完整功能（lang / book / dual）。
2. **Week 3** – 把 View 內的 UI 事件簡化，所有抽取邏輯搬到 Actions。<br>Actions 將：驗證參數、啟動 Service、返回 `TaskSession` 給 View。
3. **Week 4‑5** – 把 Dialog 的進度/日誌更新改為 poller 讀取 `TaskSession`，不直接操作 UI 控制項。<br>Preview 功能同理。
4. **Week 6** – 移除所有 `view.* = …` 的寫法；View 僅負責建立 UI、事件綁定與輪詢。<br>Actions 只調用 Service 並把結果傳回。
5. **Week 7‑8** – 完成單元測試、CI，推送 PR。